# redactcat

A production-grade FastAPI service for redacting PII from documents. Users submit text, PDFs, or images; AWS Comprehend detects PII entities; users review and confirm suggested redactions; the service delivers a permanently redacted output. All job data is ephemeral — deleted after download.

## Tech Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Framework | FastAPI | Async-ready, OpenAPI docs out of the box, first-class dependency injection |
| ORM | SQLAlchemy 2.0 | `Mapped`/`mapped_column` declarative syntax, explicit session control |
| Auth | PyJWT + bcrypt | passlib is incompatible with bcrypt 5.x; rolling own keeps the dependency surface minimal |
| Validation | Pydantic v2 | Co-designed with FastAPI; field-level constraints enforced at the request boundary |
| Package management | uv | Near-instant installs, lockfile determinism, replaces pip + venv |
| Linting | Ruff | Single tool replaces flake8, isort, and pyupgrade |
| Testing | pytest + SQLite in-memory | Fast, fully isolated, no external services required for CI |
| PII detection | AWS Comprehend | Managed NLP; sync API handles up to 100KB inline with no infrastructure to maintain |
| File extraction | PyMuPDF + Textract | PyMuPDF for text-native PDFs; Textract for scanned PDFs and images |
| Redaction | PyMuPDF | `add_redact_annot` + `apply_redactions` produces permanent, non-reversible redaction |
| File storage | S3 (ephemeral) | Files deleted immediately after download; presigned URLs for secure delivery |

## Architecture Decisions

**Stateless JWT + rotating refresh tokens**
Access tokens are short-lived JWTs (30min) with no server-side storage. Refresh tokens are opaque strings stored in the `refresh_tokens` table. Each `/auth/refresh` call rotates the pair — the old row is deleted and a new token pair is issued. Logout is a single DB delete. This avoids a token blacklist while giving stolen refresh tokens only a one-use window before detection.

**Naive UTC datetimes**
All datetimes are computed in UTC and stored timezone-naive (`datetime.now(UTC).replace(tzinfo=None)`). SQLite has no native timezone support; PostgreSQL `TIMESTAMP WITHOUT TIME ZONE` accepts naive values. The UTC convention is enforced at the application layer — no mixed-offset data enters the DB.

**Cross-user isolation via 404**
Endpoints that access user-owned resources raise `404` (not `403`) when a resource exists but belongs to another user. This avoids confirming resource existence to unauthorized callers.

**Ephemeral job storage**
Job files are deleted from S3 immediately after the user downloads the redacted output. The only persistent record is a `UsageEvent` row with aggregate stats (no PII). This limits data retention exposure and eliminates a class of compliance risk.

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
| PATCH | /users/me | ✓ | Update email or password |
| DELETE | /users/me | ✓ | Delete account and all active sessions |
| POST | /jobs/text | ✓ | Submit text; runs Comprehend PII detection; returns job + entities |
| GET | /jobs/{id}/entities | ✓ | Re-fetch detected entities for a job |
| POST | /jobs/{id}/redact | ✓ | Confirm entity IDs to redact; returns redacted text; deletes job |

Interactive docs available at `http://localhost:8000/docs` when the dev server is running.

## Quickstart

```bash
# Install dependencies
uv sync

# Copy and fill in environment variables
cp .env.example .env

# Start the dev server
uv run uvicorn app.main:app --reload

# Run tests
uv run pytest

# Lint
uv run ruff check .
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

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | No | `sqlite:///./redactcat.db` | SQLAlchemy connection string |
| `APP_ENV` | No | `development` | `development` or `production` |
| `JWT_SECRET` | **Yes** | — | Secret for signing JWTs (32+ chars); app will not start without it |
| `JWT_ALGORITHM` | No | `HS256` | JWT signing algorithm |
| `JWT_EXPIRE_MINUTES` | No | `30` | Access token lifetime in minutes |
| `REFRESH_TOKEN_EXPIRE_DAYS` | No | `30` | Refresh token lifetime in days |
| `AWS_PROFILE` | Yes† | — | Named AWS profile from `~/.aws/credentials`; provides credentials and region |
| `S3_BUCKET` | Yes† | — | S3 bucket for ephemeral job file storage |

†Required for job features (Comprehend, Textract, S3). Auth endpoints run locally without AWS credentials.

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
│   ├── routers/           # Feature routers — one file per domain
│   │   ├── auth.py        # Register, login, logout, token refresh
│   │   ├── health.py      # Health check
│   │   └── users.py       # User profile (get, update, delete)
│   └── services/          # Business logic and AWS integrations
│       ├── cleanup.py     # Post-download job deletion
│       ├── detection.py   # AWS Comprehend PII detection
│       ├── extraction.py  # Text extraction (PyMuPDF, Textract)
│       ├── redaction.py   # Apply redactions (PyMuPDF / string substitution)
│       └── storage.py     # S3 upload/download/delete
├── tests/
│   ├── conftest.py        # Fixtures: engine, db session, TestClient
│   ├── test_auth.py       # Auth endpoint tests
│   ├── test_health.py     # Health check test
│   └── test_users.py      # User profile endpoint tests
├── infra/
│   ├── main.tf            # Provider + S3 backend
│   ├── variables.tf       # region, app_name
│   ├── outputs.tf         # ECR URL, App Runner URL, nameservers
│   ├── ecr.tf             # ECR repository
│   ├── s3.tf              # Job storage bucket
│   ├── iam.tf             # App Runner roles + GitHub Actions OIDC
│   ├── ssm.tf             # JWT_SECRET parameter
│   ├── app_runner.tf      # App Runner service + custom domain
│   ├── dns.tf             # Route 53 hosted zone + records
│   └── architecture.drawio # AWS resource diagram
├── .github/
│   └── workflows/
│       ├── ci.yml         # Lint + test on pull requests
│       └── deploy.yml     # Build, push to ECR, deploy to App Runner on main
├── .env.example           # Environment variable template
└── pyproject.toml         # Dependencies and tool config
```

## How It Works

1. **Submit** — user POSTs text or uploads a file (PDF, image)
2. **Detect** — AWS Comprehend scans for PII entities; Textract extracts text from scanned files
3. **Review** — API returns detected entities with locations for user confirmation
4. **Redact** — user submits confirmed entity IDs; service applies permanent redactions via PyMuPDF
5. **Deliver** — user downloads the redacted output; all job data and S3 objects are deleted

See `CLAUDE.md` for contributor conventions.

## AI-Assisted Development

This project uses [Claude Code](https://claude.com/claude-code) as a development tool. Every change — architecture decisions, implementation, tests, and documentation — is reviewed and approved by the developer before it is committed. The expectation is that the developer can explain any decision, trade-off, or line of code in the repository.

The workflow is deliberate: plan the approach, implement, review the diff, run tests, then commit. AI accelerates execution but does not replace engineering judgment — all technical decisions and their rationale are documented in the Architecture Decisions section of this README and in `CLAUDE.md`.
