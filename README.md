# redactcat

A FastAPI service for detecting and redacting PII from text and PDF documents. Users submit content; AWS Comprehend detects PII entities; users review and confirm redactions; the service returns redacted output. Text passes through in-memory — no PII is stored at any point. PDFs use ephemeral S3 storage between scan and redact, deleted immediately after the redacted file is delivered.

## Tech Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Framework | FastAPI | Async-ready, OpenAPI docs out of the box, first-class dependency injection |
| ORM | SQLAlchemy 2.0 | `Mapped`/`mapped_column` declarative syntax, explicit session control |
| Migrations | Alembic | Schema migrations with autogenerate; runs automatically on container startup before accepting traffic |
| Auth | PyJWT + bcrypt | passlib is incompatible with bcrypt 5.x; rolling own keeps the dependency surface minimal |
| Validation | Pydantic v2 | Co-designed with FastAPI; field-level constraints enforced at the request boundary |
| Package management | uv | Near-instant installs, lockfile determinism, replaces pip + venv |
| Linting | Ruff | Single tool replaces flake8, isort, and pyupgrade |
| Type checking | mypy + pydantic plugin | Catches type mismatches at call boundaries statically, independent of test coverage |
| Testing | pytest + SQLite in-memory | Fast, fully isolated, no external services required for CI |
| PII detection | AWS Comprehend | Managed NLP; sync API handles up to 100KB inline with no infrastructure to maintain |
| PDF text extraction | AWS Textract | Handles scanned PDFs where text isn't directly exposed; sync API for single-page documents |
| PDF redaction | PyMuPDF | Applies permanent black-box redactions at bounding box coordinates |
| Face detection | AWS Rekognition | Detects faces in embedded PDF images; sync inline-bytes API, no infrastructure to maintain |
| Barcode/QR detection | pyzbar + libzbar0 | Decodes QR codes and barcodes rendered as vector or raster content on the page |

## Architecture Decisions

**Stateless JWT + rotating refresh tokens**
Access tokens are short-lived JWTs (30min) with no server-side storage. Refresh tokens are opaque strings stored in the `refresh_tokens` table. Each `/auth/refresh` call rotates the pair — the old row is deleted and a new token pair is issued. Logout is a single DB delete. This avoids a token blacklist while giving stolen refresh tokens only a one-use window before detection.

**Stateless text scan and redact**
Text passes through in-memory — no PII is written to the database or S3 at any point. `/text/scan` returns the source text alongside detected entities; the client can POST that response body directly to `/text/redact` after filtering. The client controls which entities to redact (by confidence threshold, entity type, etc.) and can produce multiple redacted variants from a single scan.

**Minimal-stateful PDF scan and redact**
PDFs use a session model: `/pdf/scan` uploads the source file to S3, schedules a one-time cleanup Lambda ~1 hour out, and returns detected entities with bounding boxes and an `expires_at` timestamp. `/pdf/redact` accepts the filtered entity list and applies redactions; it can be called multiple times on the same job within the TTL window — each call produces a distinct redacted file with its own presigned download URL. Jobs expire after 1 hour (410 Gone). The Lambda owns all cleanup: it deletes every S3 object under the job prefix and the DB row at expiry. Only the `Job` row (job_id, user_id, s3_key) persists between calls — entity data and bounding boxes travel in HTTP payloads, never written to the DB. The scan response shape matches the redact request body exactly; the client filters the entity list and POSTs it back without reshaping.

A single `/pdf/scan` call runs three detection pipelines in sequence:
1. **Text PII** — S3 → Textract (text + word bboxes) → Comprehend (PII at character offsets) → map offsets back to word bboxes. Textract is used rather than PyMuPDF so that scanned PDFs with no directly exposed text are handled correctly.
2. **Faces** — if the page has embedded images, the rendered page pixmap (JPEG) is sent to Rekognition `detect_faces`; each face becomes a `REKOGNITION` entity with a normalized bbox.
3. **Barcodes/QR codes** — the rendered page pixmap (grayscale) is passed to pyzbar `decode`; decoded symbols become `PYZBAR` entities with decoded text and normalized bboxes derived from the polygon corners. This runs unconditionally because QR codes and barcodes are often vector graphics that don't appear in the embedded image list.

All three sources return bounding boxes as normalized 0–1 fractions of page dimensions. The `source` field on each entity (`COMPREHEND`, `REKOGNITION`, or `PYZBAR`) lets the client distinguish detection origin.

**Long-lived API keys**
Processing endpoints (`/text/*`, `/pdf/*`) accept either a short-lived JWT or a long-lived API key as a Bearer token. Keys are `rcat_`-prefixed, generated with `secrets.token_urlsafe(32)`, and stored as SHA-256 hashes (deterministic lookup, unlike bcrypt). One key per user; `POST /users/me/api-key` generates or rotates. Account management endpoints (`/users/me/*`, `/auth/*`) remain JWT-only — a key cannot manage itself.

**Naive UTC datetimes**
All datetimes are computed in UTC and stored timezone-naive (`datetime.now(UTC).replace(tzinfo=None)`). SQLite has no native timezone support; PostgreSQL `TIMESTAMP WITHOUT TIME ZONE` accepts naive values. The UTC convention is enforced at the application layer — no mixed-offset data enters the DB.

**Cross-user isolation via 404**
Endpoints that access user-owned resources raise `404` (not `403`) when a resource exists but belongs to another user. This avoids confirming resource existence to unauthorized callers.

**Neon PostgreSQL in production, SQLite locally**
The deployed service connects to [Neon](https://neon.tech) — serverless PostgreSQL. The connection string is stored in SSM Parameter Store and injected into App Runner at runtime. Local development uses SQLite (`DATABASE_URL` defaults to `sqlite:///./redactcat.db`) for zero-config setup. All datetime and schema decisions are PostgreSQL-compatible; migrating to RDS is a connection string change if Neon's constraints (no VPC placement, scale-to-zero cold starts) become a problem.

**Single `models.py` and `schemas.py`**
All ORM models and Pydantic schemas live in one file each. This avoids circular imports, makes the full data model visible at a glance, and keeps `Base.metadata.create_all` deterministic.

**Direct bcrypt (no passlib)**
passlib's bcrypt backend raises a `ValueError` on initialization against bcrypt 5.x (removed `__about__` attribute, strict 72-byte enforcement). bcrypt is used directly via `hashpw`/`checkpw`.

## API Reference

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | /health | — | Health check |
| POST | /auth/register | — | Register and auto-login; returns access + refresh token pair |
| POST | /auth/login | — | Login with credentials; returns token pair |
| POST | /auth/logout | ✓ | Delete refresh token row; invalidates session |
| POST | /auth/refresh | — | Rotate refresh token; returns new token pair |
| GET | /users/me | ✓ | Get current user profile |
| GET | /usage/summary | ✓ | Token usage, allowance, and remaining tokens for the current calendar month |
| GET | /usage/history | ✓ | All usage events for the current calendar month, newest first |
| PATCH | /users/me | ✓ | Update email or password |
| DELETE | /users/me | ✓ | Delete account and all active sessions |
| POST | /users/me/api-key | ✓† | Generate or rotate long-lived API key; returns the full key once |
| GET | /users/me/api-key | ✓† | Get API key metadata (prefix, created_at, last_used_at); 404 if none exists |
| DELETE | /users/me/api-key | ✓† | Revoke API key |
| POST | /text/scan | ✓‡§ | Detect PII entities in text; returns source text + entity list |
| POST | /text/redact | ✓‡ | Apply redactions to text; returns redacted string |
| POST | /pdf/scan | ✓‡§ | Upload single-page PDF; runs Textract (text PII), Rekognition (faces), and pyzbar (barcodes/QR); returns job_id + entities with bboxes |
| POST | /pdf/redact | ✓‡ | Apply redactions to PDF; returns presigned download URL and expires_at; can be called multiple times per job |

† JWT only — API keys cannot manage themselves.
‡ Accepts JWT or API key.
§ Scan endpoints enforce a calendar-month token budget (50,000 tokens, resets on the 1st). Exceeding the budget returns `429 Too Many Requests` with `{"error": "token_limit_reached", "tokens_used": <n>, "tokens_allowed": 50000, "resets_in_days": <n>}`. Redact endpoints are not gated — enforcement applies only at the scan step where AWS cost is incurred.

Interactive docs available at `http://localhost:8000/docs` when the dev server is running.

### Text workflow

```
POST /text/scan
Body:    { "text": "My name is John Doe and my SSN is 123-45-6789" }
Returns: { "text": "...", "entities": [{ "entity_type", "text", "start_offset", "end_offset", "confidence" }] }

POST /text/redact
Body:    { "text": "...", "entities": [...], "replacement": "[REDACTED]" }
Returns: { "redacted_text": "My name is [REDACTED] and my SSN is [REDACTED]" }
```

The scan response can be posted directly to redact — filter the `entities` array first to select which PII to redact. `replacement` is optional and defaults to `"[REDACTED]"`; pass an empty string to delete PII rather than substitute it.

### PDF workflow

```
POST /pdf/scan
Body:    multipart/form-data — file (PDF, single page only)
Returns: { "job_id": 1, "expires_at": "2026-01-01T01:00:00", "entities": [{ "source", "entity_type", "text", "start_offset", "end_offset", "confidence", "bboxes": [{ "left", "top", "width", "height" }] }] }

POST /pdf/redact
Body:    { "job_id": 1, "entities": [...] }                    ← filtered scan response
Returns: { "download_url": "https://...", "expires_at": "..." } ← presigned S3 URL (TTL matches expires_at)
```

The scan response can be posted directly to redact — filter the `entities` array to select which PII to redact. `source` is `COMPREHEND`, `REKOGNITION`, or `PYZBAR`. Bounding boxes are normalized (0–1 fractions of page dimensions) regardless of detection source. Constraints: single-page PDFs only, 10 MB max. Jobs expire after 1 hour (`expires_at`). The presigned URL TTL matches `expires_at` — all URLs from a job are valid until the job window closes and the Lambda deletes the underlying S3 objects. Multiple redact calls on the same job each produce a distinct output file.

## Quickstart

```bash
# Install dependencies
uv sync

# Copy and fill in environment variables
cp .env.example .env

# Apply migrations to local DB
uv run alembic upgrade head

# Start the dev server (AWS_PROFILE required for Comprehend, Textract, S3)
AWS_PROFILE=<your-profile> uv run uvicorn app.main:app --reload

# Run tests
uv run pytest

# Lint
uv run ruff check .

# Type check
uv run mypy app
```

### Local database

```bash
# Generate a migration after editing models
uv run alembic revision --autogenerate -m "description"

# Apply pending migrations
uv run alembic upgrade head

# Roll back all migrations (empty DB)
uv run alembic downgrade base

# Reset completely — delete the file and re-migrate
rm redactcat.db && uv run alembic upgrade head
```

## Running with Docker

Day-to-day development uses the local server (`uv run uvicorn app.main:app --reload`). Docker is for verifying the production image locally before deploying.

```bash
# Build
docker build -t redactcat .

# Run locally
docker run --rm -p 8000:8000 --env-file .env redactcat
```

The CI/CD pipeline targets `linux/amd64` when pushing to ECR — no platform flag needed for local development.

## Infrastructure & Deployment

Infrastructure is managed with Terraform in `infra/`. All AWS resources (ECR, App Runner, S3, IAM, SSM, Route 53) are defined there. See [infra/architecture.drawio](infra/architecture.drawio) for a full resource diagram.

**Deploying a new version** (manual):
```bash
docker build --platform linux/amd64 -t redactcat .
docker tag redactcat:latest <ecr_url>:latest

aws ecr get-login-password --region us-west-2 | \
  docker login --username AWS --password-stdin <ecr_url>
docker push <ecr_url>:latest

aws apprunner start-deployment \
  --service-arn <service_arn> \
  --region us-west-2
```

Merging to `main` triggers `.github/workflows/deploy.yml` automatically via OIDC — no AWS credentials stored in GitHub.

**Database migrations** run automatically on container startup — `alembic upgrade head` executes before uvicorn accepts traffic. Multiple App Runner instances starting simultaneously are safe; Alembic uses a DB-level lock so only one applies pending migrations. For a database with existing tables but no `alembic_version` row, drop and recreate the tables rather than stamping — auto-deploy leaves no window to stamp after merge.

**Infrastructure changes must be applied before deploying code** — any PR that adds a new IAM permission or AWS resource requires `terraform apply` before merging to avoid a window where the deployed code calls services it isn't yet authorized to use.

**First-time infrastructure setup — set secrets after `terraform apply`:**

`terraform apply` initializes SSM parameters with placeholder values. Before the app will start, set the real values:

```bash
aws ssm put-parameter \
  --name "/redactcat/JWT_SECRET" \
  --value "<your-secret>" \
  --type SecureString \
  --overwrite \
  --region us-west-2

aws ssm put-parameter \
  --name "/redactcat/DATABASE_URL" \
  --value "postgresql://<user>:<password>@<host>/<db>?sslmode=require" \
  --type SecureString \
  --overwrite \
  --region us-west-2
```

Subsequent `terraform apply` runs will not overwrite these values. Only required again if the infrastructure is fully destroyed and rebuilt.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | No | `sqlite:///./redactcat.db` | SQLAlchemy connection string |
| `APP_ENV` | No | `development` | `development` or `production` |
| `JWT_SECRET` | **Yes** | — | Secret for signing JWTs (32+ chars); app will not start without it |
| `JWT_ALGORITHM` | No | `HS256` | JWT signing algorithm |
| `JWT_EXPIRE_MINUTES` | No | `30` | Access token lifetime in minutes |
| `REFRESH_TOKEN_EXPIRE_DAYS` | No | `30` | Refresh token lifetime in days |
| `S3_BUCKET` | **Yes** | — | S3 bucket name for ephemeral PDF job storage; app will not start without it |
| `AWS_PROFILE` | Yes† | — | Named AWS profile from `~/.aws/credentials`; provides credentials and region |

†Required locally for Comprehend, Textract, Rekognition, and S3. Not used in production — App Runner uses the instance IAM role. Set in shell (`AWS_PROFILE=x uv run uvicorn ...`); not injected automatically from `.env`.

## Project Structure

```
redactcat/
├── app/
│   ├── config.py          # Settings (env vars via pydantic-settings)
│   ├── database.py        # SQLAlchemy engine, session factory, Base, get_db
│   ├── dependencies.py    # Shared FastAPI dependencies (get_current_user)
│   ├── main.py            # FastAPI app entry point — registers routers
│   ├── models.py          # All SQLAlchemy ORM models
│   ├── schemas.py         # All Pydantic request/response schemas
│   ├── routers/
│   │   ├── auth.py        # Register, login, logout, token refresh
│   │   ├── health.py      # Health check
│   │   ├── pdf.py         # PDF PII scan and redaction (stateful, S3-backed)
│   │   ├── text.py        # Text PII scan and redaction (stateless)
│   │   ├── usage.py       # /usage/summary and /usage/history — current-month token reporting
│   │   └── users.py       # User profile (get, update, delete) and API key management
│   └── services/
│       ├── auth.py        # API key prefix constant and hash helper — shared by dependencies.py and routers/users.py
│       ├── barcodes.py    # pyzbar QR code and barcode detection from rendered page pixmap
│       ├── detection.py   # AWS Comprehend PII detection
│       ├── extraction.py  # AWS Textract PDF text extraction + word bbox mapping
│       ├── redaction.py   # Text redaction (string substitution) and PDF redaction (PyMuPDF)
│       ├── rekognition.py # AWS Rekognition face detection in embedded images
│       ├── storage.py     # S3 upload, download, presigned URL
│       └── usage.py       # Usage event recording — token costs per AWS call, best-effort DB insert
├── tests/
│   ├── conftest.py              # Fixtures: engine, db session, TestClient
│   ├── test_auth_router.py      # Auth endpoint tests
│   ├── test_barcodes_service.py # pyzbar barcode service unit tests
│   ├── test_detection_service.py # Comprehend service unit tests (botocore Stubber)
│   ├── test_health_router.py    # Health check test
│   ├── test_migrations.py       # Alembic upgrade/downgrade integration tests
│   ├── test_pdf_router.py       # /pdf/scan and /pdf/redact endpoint tests
│   ├── test_rekognition_service.py # Rekognition service unit tests (botocore Stubber)
│   ├── test_redaction_service.py # PDF redaction service unit tests
│   ├── test_text_router.py      # /text/scan and /text/redact endpoint tests
│   ├── test_usage_router.py     # /usage/summary and /usage/history endpoint tests
│   ├── test_usage_service.py    # Usage event recording service unit tests
│   └── test_users_router.py     # User profile and API key management endpoint tests
├── infra/                 # Terraform — ECR, App Runner, S3, IAM, SSM, Route 53
├── .github/workflows/
│   ├── ci.yml             # Lint + test on pull requests
│   └── deploy.yml         # Build, push to ECR, deploy to App Runner on main
├── .env.example           # Environment variable template
└── pyproject.toml         # Dependencies and tool config
```

## How It Works

**Text:**
1. **Scan** — user POSTs text to `/text/scan`; AWS Comprehend detects PII entities and returns them with character offsets and confidence scores
2. **Review** — client filters the entity list (by type, confidence, or any other criteria)
3. **Redact** — user POSTs the original text and selected entities to `/text/redact`; service applies substitutions and returns the redacted string

**PDF:**
1. **Scan** — user POSTs a single-page PDF to `/pdf/scan`; service uploads to S3, schedules a one-time EventBridge Lambda ~1 hour out, then runs three detection pipelines: Textract → Comprehend (text PII with word bboxes), Rekognition (faces in embedded images), and pyzbar (barcodes/QR codes from the rendered page). Returns `{ job_id, entities[], expires_at }` where each entity carries a `source` field
2. **Review** — client filters the entity list
3. **Redact** — user POSTs `{ job_id, entities[] }` to `/pdf/redact`; service checks TTL (410 if expired), downloads the PDF from S3, applies PyMuPDF black-box redactions, uploads to a unique key, and returns `{ download_url, expires_at }`. Can be called multiple times on the same job — each call produces a distinct file. The Lambda owns all cleanup at expiry: deletes every S3 object under the job prefix and the DB row

See `CLAUDE.md` for contributor conventions.

## AI-Assisted Development

This project is built with [Claude Code](https://claude.com/claude-code). Development conventions, architectural constraints, and contribution expectations are documented in `CLAUDE.md` — contributors are expected to read and follow it. All changes, regardless of how they were produced, are the responsibility of the person who commits them.
