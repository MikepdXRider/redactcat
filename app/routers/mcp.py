"""MCP distribution router.

Serves the RedactCat MCP server script and install script as public downloads.
No authentication required — the scripts contain no secrets. The user's API key
is prompted during install and stored locally at ~/.redactcat-mcp/.env with
restricted permissions (600).
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["mcp"])

_MCP_DIR = Path(__file__).parent.parent / "mcp"


@router.get(
    "/server.py",
    response_class=PlainTextResponse,
    summary="Download the RedactCat MCP server script",
)
def get_server_script() -> str:
    return (_MCP_DIR / "server.py").read_text()


@router.get(
    "/install.sh",
    response_class=PlainTextResponse,
    summary="Download the one-liner MCP install script",
)
def get_install_script() -> str:
    return (_MCP_DIR / "install.sh").read_text()
