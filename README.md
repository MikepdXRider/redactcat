# redactcat

**redactcat** is a deployed PII detection and redaction API. It accepts plain text, single-page PDFs, and JPEG/PNG images, routing each through a multi-stage AWS detection pipeline — Comprehend for text PII, Textract for OCR, Rekognition for face detection, and pyzbar for barcodes and QR codes. Users review detected entities before redaction; no PII is stored in the database at any point.

Live at `https://api.redactcat.com` · [Interactive docs](https://api.redactcat.com/docs)

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
| Text/image extraction | AWS Textract | Handles scanned PDFs and images where text isn't directly exposed; sync S3-source API |
| PDF redaction | PyMuPDF | Applies permanent black-box redactions at bounding box coordinates |
| Image redaction | Pillow | Draws filled black rectangles over bbox regions in JPEG/PNG images |
| Face detection | AWS Rekognition | Detects faces in standalone images and embedded PDF images; sync inline-bytes API |
| Barcode/QR detection | pyzbar + libzbar0 | Decodes QR codes and barcodes rendered as vector or raster content on the page |

## Design Decisions

**Stateless JWT + rotating refresh tokens**
Access tokens are short-lived JWTs (30min) with no server-side storage. Refresh tokens are opaque strings stored in the `refresh_tokens` table. Each `/auth/refresh` call rotates the pair — the old row is deleted and a new token pair is issued. Logout is a single DB delete. This avoids a token blacklist while giving stolen refresh tokens only a one-use window before detection.

**Stateless text scan and redact**
Text passes through in-memory — no PII is written to the database or S3 at any point. `/text/scan` returns the source text alongside detected entities; the client can POST that response body directly to `/text/redact` after filtering. The client controls which entities to redact (by confidence threshold, entity type, etc.) and can produce multiple redacted variants from a single scan.

**Minimal-stateful PDF and image scan and redact**
PDFs and images share an identical session model: the scan endpoint uploads the source file to S3, schedules a one-time cleanup Lambda ~1 hour out, and returns detected entities with bounding boxes and an `expires_at` timestamp. The redact endpoint accepts the filtered entity list and applies redactions; it can be called multiple times on the same job within the TTL window — each call produces a distinct redacted file with its own presigned download URL. Jobs expire after 1 hour (410 Gone). The Lambda owns all cleanup: it deletes every S3 object under the job prefix and the DB row at expiry. Only the `Job` row (job_id, user_id, s3_key) persists between calls — entity data and bounding boxes travel in HTTP payloads, never written to the DB. The scan response shape matches the redact request body exactly; the client filters the entity list and POSTs it back without reshaping.

A single `/pdf/scan` call runs three detection pipelines in sequence:
1. **Text PII** — S3 → Textract (text + word bboxes) → Comprehend (PII at character offsets) → map offsets back to word bboxes. Textract is used rather than PyMuPDF so that scanned PDFs with no directly exposed text are handled correctly.
2. **Faces** — if the page has embedded images, the rendered page pixmap (JPEG) is sent to Rekognition `detect_faces`; each face becomes a `REKOGNITION` entity with a normalized bbox.
3. **Barcodes/QR codes** — the rendered page pixmap (grayscale) is passed to pyzbar `decode`; decoded symbols become `PYZBAR` entities with decoded text and normalized bboxes derived from the polygon corners. This runs unconditionally because QR codes and barcodes are often vector graphics that don't appear in the embedded image list.

A single `/image/scan` call runs two detection pipelines in sequence:
1. **Text PII** — S3 → Textract (text + word bboxes) → Comprehend (PII at character offsets) → map offsets back to word bboxes. Same pipeline as PDF; `extract_text_from_s3_object` accepts JPEG/PNG via S3 reference identically.
2. **Faces** — image bytes are sent to Rekognition `detect_faces` unconditionally (unlike PDF, where face detection is conditional on embedded raster images).

All sources return bounding boxes as normalized 0–1 fractions of page/image dimensions. The `source` field on each entity (`COMPREHEND`, `REKOGNITION`, or `PYZBAR`) lets the client distinguish detection origin.

**Long-lived API keys**
Processing endpoints (`/text/*`, `/pdf/*`, `/image/*`) accept either a short-lived JWT or a long-lived API key as a Bearer token. Keys are `rcat_`-prefixed, generated with `secrets.token_urlsafe(32)`, and stored as SHA-256 hashes (deterministic lookup, unlike bcrypt). One key per user; `POST /users/me/api-key` generates or rotates. Account management endpoints (`/users/me/*`, `/auth/*`) remain JWT-only — a key cannot manage itself.

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

## MCP Integration

Use RedactCat as a native tool in Claude Desktop, Cursor, or any MCP-compatible AI client.

```bash
curl -sSL https://api.redactcat.com/mcp/install.sh | bash
```

The install script:
1. Downloads the MCP server to `~/.redactcat-mcp/server.py`
2. Creates an isolated Python venv with the required dependencies
3. Prompts for your API key and saves it to `~/.redactcat-mcp/.env` (permissions: 600)
4. Prints the exact config entry to add to your MCP client

**Tools exposed:**

| Tool | Description |
|---|---|
| `scan_text` | Detect PII in plain text (returns entities with offsets + confidence) |
| `redact_text` | Replace detected entities with a redaction placeholder |
| `scan_pdf` | Detect PII in a single-page PDF (text, faces, barcodes); returns job_id + bboxes |
| `redact_pdf` | Generate a redacted PDF and return a pre-signed download URL |
| `get_usage_summary` | Check current token usage and remaining monthly budget |

The API key is stored locally and never sent through the AI's conversation context. A future `pip install redactcat-mcp` / `uvx redactcat-mcp` distribution is planned; the server script is structured as a package entry point so the migration requires only a `pyproject.toml`.

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
| POST | /image/scan | ✓‡§ | Upload JPEG or PNG; runs Textract (text PII) and Rekognition (faces); returns job_id + entities with bboxes |
| POST | /image/redact | ✓‡ | Apply redactions to image; returns presigned download URL and expires_at; can be called multiple times per job |
| GET | /mcp/server.py | — | Download the RedactCat MCP server script |
| GET | /mcp/install.sh | — | Download the one-liner MCP install script |

† JWT only — API keys cannot manage themselves.
‡ Accepts JWT or API key.
§ Scan endpoints enforce a calendar-month token budget (50,000 tokens, resets on the 1st). Exceeding the budget returns `429 Too Many Requests` with `{"error": "token_limit_reached", "tokens_used": <n>, "tokens_allowed": 50000, "resets_in_days": <n>}`. Redact endpoints are not gated — enforcement applies only at the scan step where AWS cost is incurred.

Interactive docs available at `https://api.redactcat.com/docs`.

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

### Image workflow

```
POST /image/scan
Body:    multipart/form-data — file (JPEG or PNG, max 5 MB)
Returns: { "job_id": 1, "expires_at": "2026-01-01T01:00:00", "entities": [{ "source", "entity_type", "text", "start_offset", "end_offset", "confidence", "bboxes": [{ "left", "top", "width", "height" }] }] }

POST /image/redact
Body:    { "job_id": 1, "entities": [...] }                    ← filtered scan response
Returns: { "download_url": "https://...", "expires_at": "..." } ← presigned S3 URL (TTL matches expires_at)
```

Same session model as PDF — filter the `entities` array and POST it back to redact. `source` is `COMPREHEND` (text PII), `REKOGNITION` (face), or `PYZBAR` (QR code/barcode). Bounding boxes are normalized 0–1 fractions of image dimensions. Constraints: JPEG or PNG only, 5 MB max (Textract synchronous + Rekognition inline-bytes limit). Jobs expire after 1 hour. Multiple redact calls on the same job each produce a distinct output file.

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

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | No | `sqlite:///./redactcat.db` | SQLAlchemy connection string |
| `APP_ENV` | No | `development` | `development` or `production` |
| `JWT_SECRET` | **Yes** | — | Secret for signing JWTs (32+ chars); app will not start without it |
| `JWT_ALGORITHM` | No | `HS256` | JWT signing algorithm |
| `JWT_EXPIRE_MINUTES` | No | `30` | Access token lifetime in minutes |
| `REFRESH_TOKEN_EXPIRE_DAYS` | No | `30` | Refresh token lifetime in days |
| `S3_BUCKET` | **Yes** | — | S3 bucket name for ephemeral PDF and image job storage; app will not start without it |
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
│   ├── mcp/
│   │   ├── server.py      # Standalone MCP server script (served as a download; not imported by the app)
│   │   └── install.sh     # One-liner install script (served as a download)
│   ├── routers/
│   │   ├── auth.py        # Register, login, logout, token refresh
│   │   ├── health.py      # Health check
│   │   ├── image.py       # Image PII scan and redaction (stateful, S3-backed)
│   │   ├── mcp.py         # /mcp/server.py and /mcp/install.sh — public distribution endpoints
│   │   ├── pdf.py         # PDF PII scan and redaction (stateful, S3-backed)
│   │   ├── text.py        # Text PII scan and redaction (stateless)
│   │   ├── usage.py       # /usage/summary and /usage/history — current-month token reporting
│   │   └── users.py       # User profile (get, update, delete) and API key management
│   └── services/
│       ├── auth.py           # API key prefix constant and hash helper — shared by dependencies.py and routers/users.py
│       ├── barcodes.py       # pyzbar QR code and barcode detection from rendered page pixmap
│       ├── detection.py      # AWS Comprehend PII detection
│       ├── extraction.py     # AWS Textract document text extraction + word bbox mapping (PDF and image)
│       ├── image_redaction.py # Pillow black-box image redaction at normalized bbox coordinates
│       ├── redaction.py      # Text redaction (string substitution) and PDF redaction (PyMuPDF)
│       ├── rekognition.py    # AWS Rekognition face detection
│       ├── storage.py        # S3 upload, download, presigned URL
│       └── usage.py          # Usage event recording — token costs per AWS call, best-effort DB insert
├── tests/
│   ├── conftest.py              # Fixtures: engine, db session, TestClient
│   ├── test_auth_router.py      # Auth endpoint tests
│   ├── test_barcodes_service.py # pyzbar barcode service unit tests
│   ├── test_detection_service.py # Comprehend service unit tests (botocore Stubber)
│   ├── test_health_router.py    # Health check test
│   ├── test_mcp_router.py       # /mcp/server.py and /mcp/install.sh endpoint tests
│   ├── test_migrations.py       # Alembic upgrade/downgrade integration tests
│   ├── test_image_router.py     # /image/scan and /image/redact endpoint tests
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

## AI-Assisted Development

Built with [Claude Code](https://claude.com/claude-code) as the primary development tool. All architecture, design decisions, and code review are the author's. Development conventions and architectural constraints are documented in `CLAUDE.md`.
