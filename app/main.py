"""FastAPI app entry point.

Routers are registered here with their URL prefixes; this is the only place they
are wired to the app. Schema is owned by Alembic — no create_all at startup.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.routers.auth import router as auth_router
from app.routers.health import router as health_router
from app.routers.mcp import router as mcp_router
from app.routers.pdf import router as pdf_router
from app.routers.text import router as text_router
from app.routers.usage import router as usage_router
from app.routers.users import router as users_router

_DESCRIPTION = """
**redactcat** detects and redacts PII from plain text and single-page PDFs.

## Authentication

All endpoints require a `Bearer` token in the `Authorization` header. Two token types are accepted:

- **JWT access token** — returned by `POST /auth/login` and `POST /auth/register`. Valid for 30 minutes.
- **Long-lived API key** — generated via `POST /users/me/api-key`, prefixed with `rcat_`. Accepted on `/text/*` and `/pdf/*` endpoints; not accepted on `/users/*` or `/auth/*` account management endpoints.

```
Authorization: Bearer <token_or_api_key>
```

## Token Budget

Each account has a monthly budget of **50,000 tokens**. Scan endpoints consume tokens; redact endpoints are free. The budget resets on the 1st of each calendar month (midnight UTC).

| Operation | Token cost |
|---|---|
| Text scan (Comprehend) | 1 per character, min 300 billed |
| PDF scan — page (Textract) | 1,500 per page |
| PDF scan — faces (Rekognition) | 1,000 per image |
| PDF redact | 0 |
| Text redact | 0 |

When the budget is exhausted, scan endpoints return **429** with a structured body:

```json
{"detail": {"error": "token_limit_reached", "tokens_used": 50000, "tokens_allowed": 50000, "resets_in_days": 8}}
```

## Data Retention

**Text** is processed in memory only — no PII is written to the database or storage.

**PDFs** are stored ephemerally in S3 and deleted automatically approximately **1 hour** after scanning (the job TTL). The `expires_at` field in scan and redact responses marks this deadline.

## Error Responses

All errors use FastAPI's standard format:

```json
{"detail": "<message>"}
```

The 429 token-limit error uses a structured `detail` object instead (see above).

## PDF File Constraints

- `Content-Type` must be `application/pdf`
- File must begin with the `%PDF` magic bytes
- Maximum size: **10 MB**
- Only **single-page** PDFs are supported

## MCP Integration

Use RedactCat as a native tool in Claude Code, Claude Desktop, Cursor, or any MCP-compatible AI client.

```bash
curl -sSL https://api.redactcat.com/mcp/install.sh | bash
```

The install script creates an isolated Python environment, prompts for your API key, and prints the exact config entry to add to your client. See `GET /mcp/install.sh` and `GET /mcp/server.py` below.
"""

_TAGS = [
    {
        "name": "auth",
        "description": "Register, log in, log out, and rotate tokens. Access tokens are short-lived JWTs (30 min); refresh tokens are rotated on each use.",
    },
    {
        "name": "users",
        "description": "Read and update the authenticated user's profile, and manage the long-lived API key.",
    },
    {
        "name": "text",
        "description": "Stateless PII scan and redact for plain text. No data is persisted — text is processed in memory only.",
    },
    {
        "name": "pdf",
        "description": "Stateful PII scan and redact for single-page PDFs. PDFs are stored ephemerally in S3 for approximately one hour. Scan once, redact as many versions as needed within the TTL window.",
    },
    {
        "name": "usage",
        "description": "View the current billing period's token consumption and per-event history.",
    },
    {
        "name": "mcp",
        "description": "Download the MCP server and install script. One-liner setup: `curl -sSL https://api.redactcat.com/mcp/install.sh | bash`. Exposes five tools: `scan_text`, `redact_text`, `scan_pdf`, `redact_pdf`, `get_usage_summary`.",
    },
    {
        "name": "health",
        "description": "Service health check.",
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="redactcat",
    version="0.1.0",
    description=_DESCRIPTION,
    openapi_tags=_TAGS,
    lifespan=lifespan,
)

app.include_router(health_router, prefix="/health")
app.include_router(auth_router, prefix="/auth")
app.include_router(users_router, prefix="/users")
app.include_router(usage_router, prefix="/usage")
app.include_router(text_router, prefix="/text")
app.include_router(pdf_router, prefix="/pdf")
app.include_router(mcp_router, prefix="/mcp")
