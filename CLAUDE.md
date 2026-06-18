# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Working Directory

All commands run from the project root. The virtual environment is managed by `uv`.

## Commands

```bash
# Install dependencies
uv sync

# Run all tests
uv run pytest

# Run a single test
uv run pytest tests/test_health.py::test_health_returns_ok

# Run tests verbose
uv run pytest -v

# Lint
uv run ruff check .

# Start dev server
uv run uvicorn app.main:app --reload
```

**Dev database:** If models change, delete `redactcat.db` and restart — there are no migrations in development. Tests always use a fresh in-memory database. Alembic migrations will be added before any production/RDS deployment.

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
  modules/           # Feature routers — one file per domain
  services/          # Business logic and external integrations
    detection.py     # AWS Comprehend PII detection
    extraction.py    # Text extraction from PDFs and images (Textract)
    redaction.py     # Apply redactions (PyMuPDF for files, substitution for text)
    storage.py       # S3 upload/download/delete helpers
    cleanup.py       # Post-download job and file deletion
```

### Adding Feature Modules

Create a file in `app/modules/` that defines `router = APIRouter(...)`, then register it explicitly in `app/main.py` with `app.include_router(...)`. See `app/modules/README.md` for the pattern.

### Models (`app/models.py`)

All SQLAlchemy ORM models live in a single file. They share `Base` from `app/database.py` and can reference each other via relationships. Do not define models inside module files.

### Schemas (`app/schemas.py`)

All Pydantic schemas live in a single file. Naming conventions:
- `XRead` — response DTOs (returned from endpoints)
- `XCreate` — request body schemas (accepted by endpoints)
- All schemas use `ConfigDict(from_attributes=True)` to read from ORM objects

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

JWT tokens are signed with the `JWT_SECRET` env var using HS256. Passwords are hashed with bcrypt via `passlib`.

### Cross-User Isolation

Every endpoint that accesses a job or resource must verify ownership before returning data or performing actions. Raise `404` (not `403`) when a resource belongs to another user — do not confirm its existence.

```python
job = db.get(Job, job_id)
if not job or job.user_id != current_user.id:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
```

### Job Lifecycle

```
pending → processing → awaiting_review → complete → [deleted]
```

- `pending` — job row created, file uploaded to S3 (file jobs) or text stored
- `processing` — extraction and Comprehend detection in progress
- `awaiting_review` — entities persisted, waiting for user redaction selection
- `complete` — redacted output ready for download
- deleted — triggered on download; job row, entities, and S3 objects removed; `UsageEvent` preserved

### Ephemeral Storage (S3)

Job files are not retained after the user downloads the redacted output. On download:
1. Generate a short-TTL presigned S3 URL for the redacted file
2. Delete original and redacted S3 objects
3. Delete all DB rows for the job except the `UsageEvent`

The `UsageEvent` row is the only persistent record of a job. It records aggregate stats (file type, entity count) for analytics — no PII content.

### Request Lifecycle

1. FastAPI resolves dependencies (`get_db`, `get_current_user`) before calling the endpoint
2. Endpoint runs queries against the injected `Session`
3. FastAPI serializes the return value using the `response_model` Pydantic schema
4. `get_db` closes the session in `finally` after the response is sent

### Database Sessions

Use `Depends(get_db)` to inject a session. Commit explicitly with `db.commit()`. After a write + commit, re-fetch with `joinedload` (single record) to return a fully populated response — `db.refresh()` does not load relationships.

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

The `client` fixture in `tests/conftest.py` spins up an isolated in-memory SQLite database for every test. Override `get_db` via `app.dependency_overrides` — never touch the production engine. AWS service calls (Comprehend, Textract, S3) are always mocked in tests.

## Code Standards

- Type hints on all functions and return values
- Pydantic schemas for all request/response shapes
- No premature abstraction; name things accurately
- No dead code, commented-out blocks, or unresolved TODOs in final output
- Every function should do one thing and be nameable in plain English

## Git

- Commit after each meaningful, working unit of change
- Commit message format: `type(scope): short description`
  - Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`
  - Example: `feat(redactions): add text redaction endpoint`
- Run `uv run ruff check .` and `uv run pytest` before committing — never commit code that fails either
- Never bundle unrelated changes in one commit

## After Every Meaningful Change

Before committing, explicitly state the answers to these four questions and wait for user confirmation:

1. **What could go wrong with this?** — at least one weakness or risk
2. **What did I assume?** — anything that could break under different conditions
3. **Does CLAUDE.md need an update?** — if a decision or pattern was established, document it
4. **Does the README need an update?** — if the public-facing setup or structure changed

## What Not To Do

- Don't add features beyond the current task — note ideas in conversation instead
- Don't add dependencies without flagging and justifying them
- Don't refactor outside the scope of the current task
- Don't generate boilerplate and leave it uncustomized
- Don't proceed past ambiguity — ask first
