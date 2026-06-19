# redactcat

A FastAPI service for detecting and redacting PII from text. Users submit text; AWS Comprehend detects PII entities; users review and confirm redactions; the service returns a redacted string. Text passes through in-memory — no PII is stored at any point.

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

## Architecture Decisions

**Stateless JWT + rotating refresh tokens**
Access tokens are short-lived JWTs (30min) with no server-side storage. Refresh tokens are opaque strings stored in the `refresh_tokens` table. Each `/auth/refresh` call rotates the pair — the old row is deleted and a new token pair is issued. Logout is a single DB delete. This avoids a token blacklist while giving stolen refresh tokens only a one-use window before detection.

**Stateless text scan and redact**
Text passes through in-memory — no PII is written to the database or S3 at any point. `/text/scan` returns the source text alongside detected entities; the client can POST that response body directly to `/text/redact` after filtering. The client controls which entities to redact (by confidence threshold, entity type, etc.) and can produce multiple redacted variants from a single scan. PDF support will introduce a stateful flow (`/pdf/*`) with S3 ephemeral storage when implemented.

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
| PATCH | /users/me | ✓ | Update email or password |
| DELETE | /users/me | ✓ | Delete account and all active sessions |
| POST | /text/scan | ✓ | Detect PII entities in text; returns source text + entity list |
| POST | /text/redact | ✓ | Apply redactions to text; returns redacted string |

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

## Quickstart

```bash
# Install dependencies
uv sync

# Copy and fill in environment variables
cp .env.example .env

# Apply migrations to local DB
uv run alembic upgrade head

# Start the dev server
uv run uvicorn app.main:app --reload

# Run tests
uv run pytest

# Lint
uv run ruff check .
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
| `AWS_PROFILE` | Yes† | — | Named AWS profile from `~/.aws/credentials`; provides credentials and region |

†Required for text scan/redact (Comprehend). Auth and user endpoints run without AWS credentials.

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
│   │   ├── text.py        # PII scan and redaction (stateless)
│   │   └── users.py       # User profile (get, update, delete)
│   └── services/
│       ├── detection.py   # AWS Comprehend PII detection
│       └── redaction.py   # Text redaction (string substitution)
├── tests/
│   ├── conftest.py        # Fixtures: engine, db session, TestClient
│   ├── test_auth.py       # Auth endpoint tests
│   ├── test_detection.py  # Comprehend service unit tests (botocore Stubber)
│   ├── test_health.py     # Health check test
│   ├── test_text.py       # /text/scan and /text/redact endpoint tests
│   └── test_users.py      # User profile endpoint tests
├── infra/                 # Terraform — ECR, App Runner, S3, IAM, SSM, Route 53
├── .github/workflows/
│   ├── ci.yml             # Lint + test on pull requests
│   └── deploy.yml         # Build, push to ECR, deploy to App Runner on main
├── .env.example           # Environment variable template
└── pyproject.toml         # Dependencies and tool config
```

## How It Works

1. **Scan** — user POSTs text to `/text/scan`; AWS Comprehend detects PII entities and returns them with character offsets and confidence scores
2. **Review** — client filters the entity list (by type, confidence, or any other criteria)
3. **Redact** — user POSTs the original text and selected entities to `/text/redact`; service applies substitutions and returns the redacted string

See `CLAUDE.md` for contributor conventions.

## AI-Assisted Development

This project is built with [Claude Code](https://claude.com/claude-code). Development conventions, architectural constraints, and contribution expectations are documented in `CLAUDE.md` — contributors are expected to read and follow it. All changes, regardless of how they were produced, are the responsibility of the person who commits them.
