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

# Start dev server
uv run uvicorn app.main:app --reload

# Build Docker image
docker build -t redactcat .

# Run Docker container locally
docker run --rm -p 8000:8000 --env-file .env redactcat
```

**Dev database:** If models change, delete `redactcat.db` and restart — there are no migrations in development. Tests always use a fresh in-memory database. See open issues for the RDS and Alembic migration roadmap.

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
    detection.py     # AWS Comprehend PII detection
    extraction.py    # Text extraction from PDFs and images (Textract)
    redaction.py     # Apply redactions (PyMuPDF for files, substitution for text)
    storage.py       # S3 upload/download/delete helpers
    cleanup.py       # Post-download job and file deletion
```

### Adding Feature Modules

Create a file in `app/routers/` that defines `router = APIRouter(...)`, then register it explicitly in `app/main.py` with `app.include_router(...)`. See `app/routers/README.md` for the pattern.

### Models (`app/models.py`)

All SQLAlchemy ORM models live in a single file. They share `Base` from `app/database.py` and can reference each other via relationships. Do not define models inside module files.

### Schemas (`app/schemas.py`)

All Pydantic schemas live in a single file. Naming conventions:
- `XRead` — response DTOs (returned from endpoints)
- `XCreate` — request body schemas (accepted by endpoints)
- `XUpdate` — partial update schemas (all fields optional)
- `XLogin` — auth input schemas; intentionally omit validation constraints (e.g. no `min_length` on password) so wrong credentials always return 401, never 422
- All schemas use `ConfigDict(from_attributes=True)` to read from ORM objects

Shared validation values that appear in multiple schemas (e.g. password length) must be defined as a module-level constant and referenced by name — not repeated inline:

```python
PASSWORD_MIN_LENGTH = 8

class UserCreate(BaseModel):
    password: str = Field(min_length=PASSWORD_MIN_LENGTH)
```

### Services (`app/services/`)

Business logic and external service calls belong in service modules, not in router handlers. Routers orchestrate; services do work. Each service file owns one integration or domain concern.

### Auth & `get_current_user`

All protected endpoints depend on `get_current_user` from `app/dependencies.py`. This dependency decodes and validates the JWT, looks up the user, and raises `401` if invalid.

```python
from app.dependencies import get_current_user
from app.models import User

@router.get("/")
def my_endpoint(current_user: User = Depends(get_current_user)) -> ...:
    ...
```

JWT tokens are signed with the `JWT_SECRET` env var using HS256. `JWT_SECRET` is required — the app will not start without it. Passwords are hashed directly with bcrypt (`bcrypt.hashpw` / `bcrypt.checkpw`). passlib was removed due to incompatibility with bcrypt 5.x.

**Token pattern:** access tokens are stateless JWTs (30min, no DB storage). Refresh tokens are opaque strings (`secrets.token_urlsafe(32)`) stored in the `refresh_tokens` table. Each `POST /auth/refresh` call **rotates** the pair — the old refresh token row is deleted and a new one is issued. Logout is a DB delete of the refresh token row; the access token expires naturally.

### Cross-User Isolation

Every endpoint that accesses a job or resource must verify ownership before returning data or performing actions. Raise `404` (not `403`) when a resource belongs to another user — do not confirm its existence.

```python
job = db.get(Job, job_id)
if not job or job.user_id != current_user.id:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
```

### Job Lifecycle

Text jobs: created with entities → user reviews → redacted output returned → job deleted.

File jobs (not yet implemented) will introduce async processing stages and a `status` column. Status is intentionally omitted until there is an observable intermediate state that clients need to act on.

### Ephemeral Storage (S3)

Job files are not retained after the user downloads the redacted output. On download:
1. Generate a short-TTL presigned S3 URL for the redacted file
2. Delete original and redacted S3 objects
3. Delete all DB rows for the job except the `UsageEvent`

The `UsageEvent` row is the only persistent record of a job. It records aggregate stats (file type, entity count) for analytics — no PII content.

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

`tests/conftest.py` provides three fixtures that compose together:

- `engine` — creates a fresh in-memory SQLite engine with `StaticPool`, runs `create_all`, tears down with `drop_all` after the test
- `db` — yields a `Session` bound to the engine for **DB-level assertions** (use when behavior isn't surfaced by any endpoint response)
- `client` — yields a `TestClient` with `get_db` overridden to use the same engine

`StaticPool` forces all connections to reuse one underlying connection so the test's `db` session sees data committed by the app during a request. Override `get_db` via `app.dependency_overrides` — never touch the production engine. AWS service calls (Comprehend, Textract, S3) are always mocked in tests.

When adding tests for endpoints that call AWS services, mock at the service-function level using `unittest.mock.patch`. Add the specific patch targets here as each service is built.

Current patch targets:
- `app.routers.jobs.detect_pii_entities` — mock in `tests/test_jobs.py` to control Comprehend output without a real AWS call

To test a service function in isolation (e.g., verifying the Comprehend call shape and response mapping), use `botocore.stub.Stubber` — it is built into botocore and requires no additional dependency. See `tests/test_detection.py` for the pattern.

## Code Standards

- Type hints on all functions and return values
- Pydantic schemas for all request/response shapes
- No premature abstraction; name things accurately
- No dead code, commented-out blocks, or unresolved TODOs in final output
- Every function should do one thing and be nameable in plain English

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

## After Every Meaningful Change

Before committing, explicitly state the answers to these five questions and wait for user confirmation:

1. **What could go wrong with this?** — at least one weakness or risk
2. **What did I assume?** — anything that could break under different conditions
3. **Does CLAUDE.md need an update?** — if a decision or pattern was established, document it
4. **Does the README need an update?** — if the public-facing setup or structure changed
5. **Is test coverage complete?** — for each new endpoint: auth enforcement, input validation, cross-user isolation (if applicable), exact response shape, and any DB-side effects not surfaced by HTTP. Name any gaps.

## What Not To Do

- Don't add features beyond the current task — note ideas in conversation instead
- Don't add dependencies without flagging and justifying them
- Don't refactor outside the scope of the current task
- Don't generate boilerplate and leave it uncustomized
- Don't proceed past ambiguity — ask first
