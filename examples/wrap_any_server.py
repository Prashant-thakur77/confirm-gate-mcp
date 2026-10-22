"""Gate any MCP server: writes wait for a human tap on their own device; reads pass straight through.

    pip install confirm-gate-mcp
    python examples/wrap_any_server.py
"""
import secrets

import uvicorn
from confirm_gate import SqliteStore, build_app
from mcp.server import MCPServer
from mcp_types import ToolAnnotations

notes: dict[str, str] = {}
upstream = MCPServer("notes")  # any MCP server works here: an MCPServer, a URL, or StdioServerParameters


@upstream.tool()
def write_note(name: str, text: str) -> str:
    """Save a note."""
    notes[name] = text
    return "saved"


@upstream.tool(annotations=ToolAnnotations(read_only_hint=True))
def read_note(name: str) -> str:
    """Read a note."""
    return notes.get(name, "")


token = secrets.token_urlsafe(16)
app, devices = build_app(upstream, store=SqliteStore("gate.db"), token=token, user="me")
print(f"agent -> http://127.0.0.1:8800/mcp   Authorization: Bearer {token}")
print(f"you   -> http://127.0.0.1:8800/approve   pairing code {devices.start_pairing('me')}")
uvicorn.run(app, host="127.0.0.1", port=8800)
