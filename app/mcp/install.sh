#!/usr/bin/env bash
# RedactCat MCP install script
# Usage: curl -sSL https://api.redactcat.com/mcp/install.sh | bash
set -euo pipefail

INSTALL_DIR="$HOME/.redactcat-mcp"
SERVER_URL="https://api.redactcat.com/mcp/server.py"
INSTALL_URL="https://api.redactcat.com/mcp/install.sh"

# ── Check Python 3.10+ ────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "Error: python3 is required but not found. Install Python 3.10 or newer." >&2
    exit 1
fi

PYTHON_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PYTHON_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]; }; then
    echo "Error: Python 3.10 or newer is required (found $(python3 --version))." >&2
    exit 1
fi

# ── Download MCP server ───────────────────────────────────────────────────────
echo "Creating install directory at $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"

echo "Downloading RedactCat MCP server..."
if ! curl -sSfL "$SERVER_URL" -o "$INSTALL_DIR/server.py"; then
    echo "Error: Failed to download server from $SERVER_URL" >&2
    exit 1
fi

# ── Create isolated Python environment ───────────────────────────────────────
echo "Creating isolated Python environment..."
python3 -m venv "$INSTALL_DIR/venv"

echo "Installing dependencies..."
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet "mcp>=1.28,<2" "httpx>=0.27" "python-dotenv>=1.0"

# ── Prompt for API key ────────────────────────────────────────────────────────
echo ""
printf "Enter your RedactCat API key: "
read -rs REDACTCAT_API_KEY < /dev/tty
echo ""

if [ -z "$REDACTCAT_API_KEY" ]; then
    echo "Error: API key cannot be empty." >&2
    exit 1
fi

# ── Save API key ──────────────────────────────────────────────────────────────
printf "REDACTCAT_API_KEY=%s\n" "$REDACTCAT_API_KEY" > "$INSTALL_DIR/.env"
chmod 600 "$INSTALL_DIR/.env"
echo "API key saved to $INSTALL_DIR/.env (permissions: 600)"

# ── Print config snippet ──────────────────────────────────────────────────────
PYTHON_BIN="$INSTALL_DIR/venv/bin/python"
SERVER_PATH="$INSTALL_DIR/server.py"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Installation complete!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Add the following to your MCP client config, then restart the client:"
echo ""
cat <<CONFIG
{
  "mcpServers": {
    "redactcat": {
      "command": "$PYTHON_BIN",
      "args": ["$SERVER_PATH"]
    }
  }
}
CONFIG
echo ""
echo "Common config file locations:"
echo "  Claude Code (global)     ~/.claude/settings.json"
echo "  Claude Code (project)    .claude/settings.json"
echo "  Claude Desktop (macOS)   ~/Library/Application Support/Claude/claude_desktop_config.json"
echo "  Claude Desktop (Windows) %APPDATA%\\Claude\\claude_desktop_config.json"
echo "  Cursor                   ~/.cursor/mcp.json"
echo ""
echo "Or add via the Claude Code CLI:"
echo "  claude mcp add redactcat $PYTHON_BIN -- $SERVER_PATH"
echo ""
echo "To update to the latest server version, re-run:"
echo "  curl -sSL $INSTALL_URL | bash"
