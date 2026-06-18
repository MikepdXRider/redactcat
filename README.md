# redactcat

A FastAPI service for redacting PII from documents. Users submit text, PDFs, or images; AWS Comprehend detects PII entities; users review and confirm suggested redactions; the service delivers a permanently redacted output. All job data is ephemeral — deleted after download.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- AWS credentials with access to Comprehend, Textract, and S3

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

The API is served at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | No | SQLAlchemy connection string. Defaults to `sqlite:///./redactcat.db` |
| `APP_ENV` | No | `development` or `production`. Defaults to `development` |
| `JWT_SECRET` | Yes (non-dev) | Secret key for signing JWT tokens |
| `AWS_ACCESS_KEY_ID` | Yes | AWS credentials for Comprehend, Textract, S3 |
| `AWS_SECRET_ACCESS_KEY` | Yes | AWS credentials |
| `AWS_REGION` | Yes | AWS region (e.g. `us-east-1`) |
| `S3_BUCKET` | Yes | S3 bucket name for ephemeral job file storage |

## Project Structure

```
redactcat/
├── app/
│   ├── config.py          # Settings (env vars via pydantic-settings)
│   ├── database.py        # SQLAlchemy engine, session, and base model
│   ├── dependencies.py    # Shared FastAPI dependencies (auth, etc.)
│   ├── main.py            # FastAPI app entry point
│   ├── models.py          # All SQLAlchemy ORM models
│   ├── schemas.py         # All Pydantic request/response schemas
│   ├── modules/           # Feature routers
│   │   ├── auth.py        # Registration and login
│   │   ├── health.py      # Health check
│   │   └── jobs.py        # Job creation, entity retrieval, redaction
│   └── services/          # Business logic and AWS integrations
│       ├── cleanup.py     # Post-download job deletion
│       ├── detection.py   # AWS Comprehend PII detection
│       ├── extraction.py  # Text extraction (PyMuPDF, Textract)
│       ├── redaction.py   # Apply redactions (PyMuPDF / string substitution)
│       └── storage.py     # S3 upload/download/delete
├── tests/
│   ├── conftest.py        # Shared fixtures (in-memory DB, TestClient)
│   ├── test_auth.py
│   ├── test_health.py
│   └── test_jobs.py
├── .env.example           # Environment variable template
└── pyproject.toml         # Dependencies and tool config
```

See `CLAUDE.md` for contributor conventions.

## How It Works

1. **Submit** — user POSTs text or uploads a file (PDF, image)
2. **Detect** — AWS Comprehend scans for PII entities; Textract extracts text from non-text files
3. **Review** — API returns a list of detected entities with locations for user confirmation
4. **Redact** — user submits confirmed entity IDs; service applies permanent redactions
5. **Deliver** — user downloads the redacted output; all job data is deleted
