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

Every endpoint that accesses a stored resource must verify ownership before returning data or performing actions. Raise `404` (not `403`) when a resource belongs to another user — do not confirm its existence.

```python
record = db.get(Model, record_id)
if not record or record.user_id != current_user.id:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
```

Stateless endpoints (`/text/*`) have no stored resources and require no ownership check.

### Text Flow (Stateless)

Text passes through in-memory — no PII is written to the database or S3 at any point.

1. `POST /text/scan` — calls Comprehend, returns `{ text, entities[] }`
2. Client filters entities (by type, confidence, etc.)
3. `POST /text/redact` — applies substitutions, returns `{ redacted_text }`

The scan response shape matches the redact request body exactly, so the client can POST the scan response directly to redact without reshaping. `replacement` defaults to `"[REDACTED]"`; empty string deletes PII.

PDF support will be a separate stateful flow (`/pdf/*`) with S3 ephemeral storage when implemented.

### Ephemeral Storage (S3) — Future PDF Flow

PDF files will not be retained after the user downloads the redacted output. On download:
1. Generate a short-TTL presigned S3 URL for the redacted file
2. Delete original and redacted S3 objects
3. Delete all DB rows for the job

This section will be expanded when the PDF flow is implemented.

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

`tests/conftest.py` provides three fixtures that compose together:

- `engine` — creates a fresh in-memory SQLite engine with `StaticPool`, runs `create_all`, tears down with `drop_all` after the test
- `db` — yields a `Session` bound to the engine for **DB-level assertions** (use when behavior isn't surfaced by any endpoint response)
- `client` — yields a `TestClient` with `get_db` overridden to use the same engine

`StaticPool` forces all connections to reuse one underlying connection so the test's `db` session sees data committed by the app during a request. Override `get_db` via `app.dependency_overrides` — never touch the production engine. AWS service calls (Comprehend, Textract, S3) are always mocked in tests.

When adding tests for endpoints that call AWS services, mock at the service-function level using `unittest.mock.patch`. Add the specific patch targets here as each service is built.

Current patch targets:
- `app.routers.text.detect_pii_entities` — mock in `tests/test_text.py` to control Comprehend output without a real AWS call

To test a service function in isolation (e.g., verifying the Comprehend call shape and response mapping), use `botocore.stub.Stubber` — it is built into botocore and requires no additional dependency. See `tests/test_detection.py` for the pattern.

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
