# CLAUDE.md

## Working Directory

All commands run from the project root. The virtual environment is managed by `uv`.

## Commands

```bash
# Install dependencies
uv sync

# Run all tests
uv run pytest

# Run a single test
uv run pytest tests/path/to/test.py::test_name

# Run tests verbose
uv run pytest -v

# Lint
uv run ruff check .

# Type check
uv run mypy app

# Start dev server
uv run uvicorn app.main:app --reload

# macOS only: pyzbar requires libzbar0 (Homebrew installs to a non-standard path).
# Install once, then prefix test and server commands with DYLD_LIBRARY_PATH:
#   brew install zbar
#   DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run pytest
#   DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run uvicorn app.main:app --reload
# Or add `export DYLD_LIBRARY_PATH=/opt/homebrew/lib` to your shell profile.

# Build Docker image
docker build -t redactcat .

# Run Docker container locally
docker run --rm -p 8000:8000 --env-file .env redactcat
```

```bash
# Apply all pending migrations (fresh local setup, or after model changes)
uv run alembic upgrade head

# Generate a new migration after editing models
uv run alembic revision --autogenerate -m "description"

# Roll back one migration
uv run alembic downgrade -1

# Roll back ALL migrations (empty DB)
uv run alembic downgrade base
```

**Dev database:** After model changes, generate a migration and run `uv run alembic upgrade head`. For a clean local reset, delete `redactcat.db` and re-run `uv run alembic upgrade head`. Tests always use a fresh in-memory database and bypass migrations entirely.

**Alembic gotchas:**
- `--autogenerate` is a starting point — always review the generated file before committing. Column renames look like drop+add (data loss), PostgreSQL type casts require a `USING` clause that autogenerate omits, and server defaults and check constraints are not always detected.
- Schema and data are separate concerns. Autogenerate handles schema only. If a type change requires reshaping existing values, write the transformation manually inside the migration file using `op.execute()` for bulk SQL or a SQLAlchemy session for Python-level logic.
- If `downgrade()` cannot safely reverse a data transformation, raise `NotImplementedError` rather than silently corrupting data.

## Architecture

### Project Layout

```
app/
  config.py          # Settings (env vars via pydantic-settings)
  database.py        # SQLAlchemy engine, session factory, Base, get_db
  dependencies.py    # Shared FastAPI dependencies (get_current_user, etc.)
  main.py            # FastAPI app entry point — registers routers
  models.py          # All SQLAlchemy ORM models
  schemas.py         # All Pydantic request/response schemas
  routers/           # Feature routers — one file per domain
  services/          # Business logic and external integrations
    auth.py          # API key prefix constant and hash helper — shared by dependencies.py and routers/users.py to avoid a circular import
    detection.py     # AWS Comprehend PII detection → list[DetectedEntity]
    redaction.py     # Text redaction (string substitution, in-memory)
```

### Adding Feature Modules

Create a file in `app/routers/` that defines `router = APIRouter(...)`, then register it explicitly in `app/main.py` with `app.include_router(...)`. See `app/routers/README.md` for the pattern.

### Models (`app/models.py`)

All SQLAlchemy ORM models live in a single file. They share `Base` from `app/database.py` and can reference each other via relationships. Do not define models inside module files.

### Schemas (`app/schemas.py`)

All Pydantic schemas live in a single file. Naming conventions:
- `XRead` — response DTOs (returned from endpoints)
- `XCreate` — request body schemas for operations that create a DB record
- `XRequest` — request body schemas for stateless operations (no record created)
- `XUpdate` — partial update schemas (all fields optional)
- `XLogin` — auth input schemas; intentionally omit validation constraints (e.g. no `min_length` on password) so wrong credentials always return 401, never 422
- Only schemas that read from ORM objects use `ConfigDict(from_attributes=True)`; stateless schemas omit it

Shared validation values that appear in multiple schemas (e.g. password length) must be defined as a module-level constant and referenced by name — not repeated inline:

```python
PASSWORD_MIN_LENGTH = 8

class UserCreate(BaseModel):
    password: str = Field(min_length=PASSWORD_MIN_LENGTH)
```

### Services (`app/services/`)

Business logic and external service calls belong in service modules, not in router handlers. Routers orchestrate; services do work. Each service file owns one integration or domain concern.

**boto3 client instantiation:** two patterns exist and the choice drives how the service is stubbed in tests.
- **Per-call** (`detection.py`): `boto3.client(...)` is created inside the function on each call. Stub by constructing a client, wrapping it in `Stubber`, and patching `app.services.<module>.boto3.client` to return it (see `tests/test_detection.py`).
- **Module-level** (`rekognition.py`, `storage.py`): the client is created once at import time (e.g. `_rekognition = boto3.client(...)`). Stub by importing that client object and wrapping it directly in `Stubber` — no patching needed (see `tests/test_rekognition.py`).

Either is acceptable; match the surrounding module and pick the stubbing approach to fit.

### Auth & `get_current_user`

All protected endpoints depend on `get_current_user` from `app/dependencies.py`. This dependency decodes and validates the JWT, looks up the user, and raises `401` if invalid.

```python
from app.dependencies import get_current_user
from app.models import User

@router.get("/")
def my_endpoint(current_user: User = Depends(get_current_user)) -> ...:
    ...
```

For endpoints that should also accept a long-lived API key (currently `/text/*` and `/pdf/*`), use `get_current_user_any_auth` instead. It detects the `rcat_` prefix and dispatches to either the API key or JWT path. Both paths return a `User` object, so handlers are auth-method-agnostic. Use `get_current_user` (JWT-only) for all account management endpoints — a key must not be able to rotate or revoke itself.

```python
from app.dependencies import get_current_user_any_auth

@router.post("/redact")
def redact(current_user: User = Depends(get_current_user_any_auth)) -> ...:
    ...
```

For scan endpoints specifically, use `enforce_token_limit` instead of `get_current_user_any_auth`. It wraps `get_current_user_any_auth` and additionally checks the user's current calendar-month token usage against `STRAY_TOKEN_LIMIT` (50,000 tokens). Returns `429` with structured JSON if the budget is exhausted. Enforcement gates the scan step where AWS cost is incurred — redact endpoints are free and must not use this dependency. The billing window matches what `GET /usage/summary` reports, so the number a user sees in the summary is exactly the number checked against their limit.

```python
from app.dependencies import enforce_token_limit

@router.post("/scan")
def scan(current_user: User = Depends(enforce_token_limit)) -> ...:
    ...
```

JWT tokens are signed with the `JWT_SECRET` env var using HS256. `JWT_SECRET` is required — the app will not start without it. `S3_BUCKET` is also required — the app will not start without it. Passwords are hashed directly with bcrypt (`bcrypt.hashpw` / `bcrypt.checkpw`). passlib was removed due to incompatibility with bcrypt 5.x.

**Token pattern:** access tokens are stateless JWTs (30min, no DB storage). Refresh tokens are opaque strings (`secrets.token_urlsafe(32)`) stored in the `refresh_tokens` table. Each `POST /auth/refresh` call **rotates** the pair — the old refresh token row is deleted and a new one is issued. Logout is a DB delete of the refresh token row; the access token expires naturally.

**API key pattern:** one key per user, stored as a SHA-256 hash (deterministic lookup). The raw key is returned once on generation and never stored. `POST /users/me/api-key` generates or rotates; `GET` returns metadata only; `DELETE` revokes. `last_used_at` is updated on every authenticated request.

### Cross-User Isolation

Every endpoint that accesses a stored resource must verify ownership before returning data or performing actions. Raise `404` (not `403`) when a resource belongs to another user — do not confirm its existence.

```python
record = db.get(Model, record_id)
if not record or record.user_id != current_user.id:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
```

Stateless endpoints (`/text/*`) have no stored resources and require no ownership check.

For endpoints where concurrent access is a concern, use `DELETE ... RETURNING` to atomically claim and verify ownership in one statement rather than SELECT + Python check + DELETE:

```python
from sqlalchemy import delete

stmt = (
    delete(Model)
    .where(Model.id == record_id, Model.user_id == current_user.id)
    .returning(Model.col_a, Model.col_b)  # raw columns, not the ORM class
)
row = db.execute(stmt).first()
if not row:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
db.commit()  # commit immediately so concurrent callers see the deletion
```

Return raw columns (not `.returning(Model)`) — returning the ORM class causes SQLAlchemy to track the deleted instance and raises `ObjectDeletedError` on commit.

### Text Flow (Stateless)

Text passes through in-memory — no PII is written to the database or S3 at any point.

1. `POST /text/scan` — calls Comprehend, returns `{ text, entities[] }`
2. Client filters entities (by type, confidence, etc.)
3. `POST /text/redact` — applies substitutions, returns `{ redacted_text }`

The scan response shape matches the redact request body exactly, so the client can POST the scan response directly to redact without reshaping. `replacement` defaults to `"[REDACTED]"`; empty string deletes PII.

PDF is a separate stateful flow (`/pdf/*`) backed by S3 ephemeral storage. A Job is a 1-hour session window — scan once, redact as many versions as needed within the TTL:

1. `POST /pdf/scan` — validates single-page PDF, uploads to S3, schedules a one-time EventBridge Scheduler Lambda ~1 hour out, extracts text via Textract, detects PII with Comprehend, maps character offsets to word-level bboxes, returns `{ job_id, entities[], expires_at }` with bboxes embedded. Scheduling is best-effort; if it fails, the scan still succeeds and the S3 lifecycle rule is the fallback.
2. Client filters entities
3. `POST /pdf/redact` — checks job ownership (404) and TTL (410), applies PyMuPDF redactions, uploads to a unique key `redacted_{token}.pdf` within the job prefix, returns `{ download_url, expires_at }`. Does **not** delete the Job row or original S3 object — multiple redact calls on the same job produce distinct output files that coexist under the prefix. Lambda owns all cleanup at expiry.

The Lambda fires at `job.created_at + JOB_TTL`, deletes every S3 object under the job prefix (original + all redacted versions), and deletes the Job row. Orphaned S3 objects (where the Job row was never committed) are also cleaned up because the Lambda is scheduled immediately after `upload_to_s3()` succeeds, before any downstream call can fail.

Only the `Job` row (job_id, user_id, original_s3_key) and source PDF persist between calls. Entity data and bboxes travel in HTTP payloads — no PII is written to the DB.

### Ephemeral Storage (S3)

All S3 cleanup is handled by the per-job Lambda, which fires ~1 hour after scan:
1. Lists all objects under the job prefix (`pdfs/{user_id}/{token}/`)
2. Deletes each object (original PDF + all redacted versions)
3. Deletes the Job DB row

The S3 bucket lifecycle rule (1-day expiration) is a fallback for any objects the Lambda misses.

### SQLite FK Enforcement

All SQLite engine instances must have `PRAGMA foreign_keys=ON` applied via a SQLAlchemy event listener so FK constraints and cascades match PostgreSQL in production. The pattern is established in `app/database.py` (dev server) and `tests/conftest.py` (test suite). Any new SQLite engine — test helpers, migration tests, scripts — must follow the same pattern:

```python
@event.listens_for(engine, "connect")
def set_sqlite_pragma(conn, _):
    conn.execute("PRAGMA foreign_keys=ON")
```

### Database Sessions

Use `Depends(get_db)` to inject a session. Commit explicitly with `db.commit()`.

After a write + commit, how to return the updated object depends on the response schema:

- **Flat response (no relationships)** — `db.refresh(obj)` is fine; it reloads the row's own columns.
- **Response includes relationships** — re-fetch with `joinedload` instead. `db.refresh()` does not eagerly load relationships; accessing them afterward fires a lazy query per relationship during Pydantic serialization (N+1).

### Eager Loading

SQLAlchemy defaults to lazy loading, which fires a separate query per row during Pydantic serialization. Always eager-load relationships explicitly.

- **List endpoints** — use `selectinload`: one additional `SELECT ... WHERE id IN (...)`
- **Single record** — use `joinedload`: single SQL JOIN query

### Datetimes

Use naive (timezone-unaware) datetimes throughout:

```python
from datetime import UTC, datetime
now = datetime.now(UTC).replace(tzinfo=None)
```

### Error Handling

Use FastAPI's `HTTPException` only. No bare exceptions, no custom error classes.

```python
from fastapi import HTTPException, status
raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
```

### Testing

Before writing tests for any new endpoint, invoke the `api-testing` skill (`/api-testing`). It encodes the full coverage matrix for this project — auth enforcement, cross-user isolation, DTO shape, DB-level assertions, and auth lifecycle. Running it proactively prevents gap reviews after the fact.

`tests/conftest.py` provides four fixtures that compose together:

- `engine` — creates a fresh in-memory SQLite engine with `StaticPool`, runs `create_all`, tears down with `drop_all` after the test
- `db` — yields a `Session` bound to the engine for **DB-level assertions** (use when behavior isn't surfaced by any endpoint response)
- `client` — yields a `TestClient` with `get_db` overridden to use the same engine; unhandled server exceptions are re-raised into the test process (`raise_server_exceptions=True`)
- `client_no_raise` — same as `client` but with `raise_server_exceptions=False`; use when you need to assert on a 500 status code or inspect DB side effects after an unhandled server error

`StaticPool` forces all connections to reuse one underlying connection so the test's `db` session sees data committed by the app during a request. Override `get_db` via `app.dependency_overrides` — never touch the production engine. AWS service calls (Comprehend, Textract, S3) are always mocked in tests.

When adding tests for endpoints that call AWS services, mock at the service-function level using `unittest.mock.patch`. Add the specific patch targets here as each service is built.

Current patch targets:
- `app.routers.text.detect_pii_entities` — mock in `tests/test_text_router.py` to control Comprehend output without a real AWS call
- `app.routers.pdf.upload_to_s3` — mock in `tests/test_pdf_router.py` to skip real S3 upload
- `app.routers.pdf.extract_text_from_pdf_s3` — mock in `tests/test_pdf_router.py` to return controlled Textract output
- `app.routers.pdf.detect_pii_entities` — mock in `tests/test_pdf_router.py` to control Comprehend output
- `app.routers.pdf.download_from_s3` — mock in `tests/test_pdf_router.py` (redact endpoint)
- `app.routers.pdf.generate_presigned_url` — mock in `tests/test_pdf_router.py` (redact endpoint)
- `app.routers.pdf.apply_pdf_redactions` — mock in `tests/test_pdf_router.py` (redact endpoint)
- `app.routers.pdf.detect_faces` — mock in `tests/test_pdf_router.py` (scan endpoint, face detection)
- `app.routers.pdf.detect_barcodes` — mock in `tests/test_pdf_router.py` (scan endpoint, QR/barcode detection)
- `app.routers.pdf.schedule_job_expiry` — mocked via autouse fixture in `tests/test_pdf_router.py`; individual tests that assert call args patch it explicitly at test level
- `app.services.usage.record_usage_event` — do not mock in router tests; let it write real rows and assert via the `db` fixture. Test the helper in isolation in `tests/test_usage_service.py`.

`app/routers/users.py` and `app/routers/usage.py` have no AWS calls and no patch targets. Tests for `/usage/*` endpoints seed `UsageEvent` rows directly via the `db` fixture in `tests/test_usage_router.py`.

`get_current_user_any_auth` is **not mocked** in tests. Tests that call text/pdf endpoints with an API key seed a real key row by calling `POST /users/me/api-key` in test setup, then pass the returned key as a Bearer token. No patching needed — the dependency reads the real in-memory test DB.

To test an AWS service function in isolation (e.g., verifying the Comprehend call shape and response mapping), use `botocore.stub.Stubber` — it is built into botocore and requires no additional dependency. See `tests/test_detection_service.py` for the pattern.

Non-botocore service integrations have no Stubber. The barcode service wraps pyzbar, so `tests/test_barcodes_service.py` stubs the native boundary by patching `app.services.barcodes.decode` and runs the real bbox math against a real pixmap.

## Code Standards

- Type hints on all functions and return values
- Pydantic schemas for all request/response shapes
- No premature abstraction; name things accurately
- No dead code, commented-out blocks, or unresolved TODOs in final output
- Every function should do one thing and be nameable in plain English

### Comments and docstrings

Use `#` for in-code comments. Reserve `"""..."""` docstrings for module-level only (top of file) and extremely important decisions that genuinely require multi-line explanation. Never add docstrings to classes or functions as a matter of routine.

Every Python module in `app/` and `alembic/` must have a top-level docstring explaining why the file exists, how it connects to the rest of the app, and any design decisions embedded in it. Use PEP 257 multi-line format — summary line, blank line, body, closing `"""` on its own line:

```python
"""Auth router.

Stateless JWTs (30min access token) paired with rotating opaque refresh tokens
stored in the DB. Each /refresh call replaces the old token row; /logout deletes
it. The access token expires naturally — no blacklist needed.
"""
```

Test files do not need docstrings — the filename is sufficient.

## Linting Hook

A `PostToolUse` hook in `.claude/settings.json` runs ruff automatically after every `Write`, `Edit`, or `MultiEdit` on a `.py` file. It auto-fixes what it can, then blocks if unfixable issues remain (undefined names, syntax errors).

**Consequence for multi-step edits:** any intermediate file state must be lint-clean. If a change requires adding an import and the code that uses it, or removing code and its import together, do it in a single `Write` of the full file — not as two sequential `Edit` calls. Two-step edits that create a lint-invalid intermediate state will be blocked by the hook.

## Git

- Commit after each meaningful, working unit of change
- Commit message format: `type(scope): short description`
  - Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`
  - Example: `feat(redactions): add text redaction endpoint`
- Run `uv run ruff check .` and `uv run pytest` before committing — never commit code that fails either
- Never bundle unrelated changes in one commit
- **Never push directly to `main`** — all changes go through a feature branch and PR, regardless of size. Main has branch protection; admin bypass is not acceptable without explicit user direction.
- To bring a feature branch up to date with main: `git checkout <branch> && git merge origin/main` — do not switch to main to do this

## After Every Meaningful Change

Before committing, explicitly state the answers to these five questions and wait for user confirmation:

1. **What could go wrong with this?** — at least one weakness or risk
2. **What did I assume?** — anything that could break under different conditions
3. **Does CLAUDE.md need an update?** — if a decision or pattern was established, document it
4. **Does the README need an update?** — check every section that could be affected: Project Structure (new files in `app/` or `tests/`), API Reference (new or changed endpoints), Architecture Decisions (new patterns), Environment Variables (new vars), and How It Works (changed flows). Update before committing, not as a follow-up.
5. **Is test coverage complete?** — for each new endpoint: auth enforcement, input validation, cross-user isolation (if applicable), exact response shape, and any DB-side effects not surfaced by HTTP. Name any gaps.

## What Not To Do

- Don't add features beyond the current task — note ideas in conversation instead
- Don't add dependencies without flagging and justifying them
- Don't refactor outside the scope of the current task
- Don't generate boilerplate and leave it uncustomized
- Don't proceed past ambiguity — ask first
