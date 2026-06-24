"""RedactCat MCP server.

Standalone script distributed via GET /mcp/server.py. Runs as a local stdio
MCP process and proxies calls to the RedactCat API at REDACTCAT_API_URL.

This file is not imported by the FastAPI app — it is served as a plain-text
download and executed by the user's MCP client. Do not add FastAPI or
SQLAlchemy imports here.

Dependencies (installed by the install script into a dedicated venv):
    mcp, httpx, python-dotenv

To migrate to a pip-installable package:
    1. Move this file to a new repo as redactcat_mcp/server.py
    2. Add pyproject.toml with:
           [project.scripts]
           redactcat-mcp = "redactcat_mcp.server:main"
    3. Publish to PyPI — no code changes required. Config simplifies to:
           {"command": "redactcat-mcp"} or {"command": "uvx", "args": ["redactcat-mcp"]}
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv(Path.home() / ".redactcat-mcp" / ".env")

_API_KEY = os.environ.get("REDACTCAT_API_KEY", "")
_API_URL = os.environ.get("REDACTCAT_API_URL", "https://api.redactcat.com")
_TIMEOUT = 120.0  # PDF scan spans Textract + Comprehend and can take 30–60 seconds

if not _API_KEY:
    raise RuntimeError(
        "REDACTCAT_API_KEY is not set. "
        "Run the install script or set the environment variable before starting the server."
    )

mcp = FastMCP("redactcat")


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=_API_URL,
        headers={"Authorization": f"Bearer {_API_KEY}"},
        timeout=_TIMEOUT,
    )


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        body = response.json()
        detail = body.get("detail", response.text)
        if isinstance(detail, dict) and detail.get("error") == "token_limit_reached":
            detail = (
                f"Monthly token limit reached "
                f"({detail['tokens_used']}/{detail['tokens_allowed']} tokens used). "
                f"Resets in {detail['resets_in_days']} day(s)."
            )
    except Exception:
        detail = response.text
    raise RuntimeError(f"HTTP {response.status_code}: {detail}")


@mcp.tool()
def scan_text(text: str) -> dict[str, Any]:
    """Detect PII entities in plain text using AWS Comprehend.

    Returns the original text alongside detected entities with character offsets,
    entity types, and confidence scores. Pass the result directly to redact_text
    to apply redactions — the response shape matches the redact request body.

    Args:
        text: Plain text to scan for PII (1–5000 characters).
    """
    with _client() as client:
        try:
            response = client.post("/text/scan", json={"text": text})
        except httpx.TimeoutException:
            raise RuntimeError("Request timed out scanning text.")
    _raise_for_status(response)
    return response.json()


@mcp.tool()
def redact_text(
    text: str,
    entities: list[dict[str, Any]],
    replacement: str = "[REDACTED]",
) -> dict[str, Any]:
    """Replace detected PII entities in text with a redaction placeholder.

    Pass the text and entities from scan_text, optionally filtered by type or
    confidence. Returns the redacted string.

    Args:
        text: The original text that was scanned.
        entities: List of entity objects from scan_text to redact.
        replacement: String substituted for each PII span. Defaults to
            "[REDACTED]". Pass "" to delete the PII span entirely.
    """
    with _client() as client:
        try:
            response = client.post(
                "/text/redact",
                json={"text": text, "entities": entities, "replacement": replacement},
            )
        except httpx.TimeoutException:
            raise RuntimeError("Request timed out redacting text.")
    _raise_for_status(response)
    return response.json()


@mcp.tool()
def scan_pdf(file_path: str) -> dict[str, Any]:
    """Detect PII entities in a single-page PDF.

    Uploads the PDF to RedactCat, which runs three detection pipelines:
    Textract + Comprehend for text PII (with word-level bounding boxes),
    Rekognition for faces in embedded images, and pyzbar for barcodes and
    QR codes. Returns a job ID, all detected entities with normalized bounding
    boxes, and a TTL expiry time (~1 hour after scan).

    Pass the job ID and filtered entities to redact_pdf to generate a redacted
    copy. The job expires ~1 hour after scanning (HTTP 410 after expiry).

    Args:
        file_path: Absolute path to a local PDF file (single-page, ≤ 10 MB).
    """
    path = Path(file_path)
    if not path.is_file():
        raise RuntimeError(f"File not found: {file_path}")
    pdf_bytes = path.read_bytes()
    with _client() as client:
        try:
            response = client.post(
                "/pdf/scan",
                files={"file": (path.name, pdf_bytes, "application/pdf")},
            )
        except httpx.TimeoutException:
            raise RuntimeError(
                "Request timed out scanning PDF. "
                "PDF scan can take 30–60 seconds — try again or check your connection."
            )
    _raise_for_status(response)
    return response.json()


@mcp.tool()
def redact_pdf(
    job_id: str,
    entities: list[dict[str, Any]],
) -> dict[str, Any]:
    """Generate a redacted copy of a previously scanned PDF.

    Applies permanent black-box redactions over the specified entities and
    returns a pre-signed download URL valid until the job expires. Multiple
    calls on the same job produce separate redacted files that coexist until
    the TTL elapses.

    Args:
        job_id: The job ID returned by scan_pdf.
        entities: List of entity objects from scan_pdf to redact. May be
            filtered by type, source (COMPREHEND / REKOGNITION / PYZBAR),
            or confidence before passing.
    """
    with _client() as client:
        try:
            response = client.post(
                "/pdf/redact",
                json={"job_id": job_id, "entities": entities},
            )
        except httpx.TimeoutException:
            raise RuntimeError("Request timed out redacting PDF.")
    _raise_for_status(response)
    return response.json()


@mcp.tool()
def get_usage_summary() -> dict[str, Any]:
    """Return current token usage and remaining budget for the billing period.

    Shows tokens consumed this calendar month, the monthly limit (50,000),
    tokens remaining, and the UTC date the budget resets.
    """
    with _client() as client:
        try:
            response = client.get("/usage/summary")
        except httpx.TimeoutException:
            raise RuntimeError("Request timed out fetching usage summary.")
    _raise_for_status(response)
    return response.json()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
