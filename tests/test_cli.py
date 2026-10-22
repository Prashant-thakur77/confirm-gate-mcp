"""The standalone proxy over real HTTP: bearer-gated /mcp, the approval page, and a human releasing a write."""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any

import httpx
import uvicorn
from confirm_gate import MemoryStore, sign_request
from confirm_gate.cli import build_app
from mcp.server import MCPServer
from mcp_types import ToolAnnotations

HDR = {"content-type": "application/json", "accept": "application/json, text/event-stream",
       "mcp-protocol-version": "2025-11-25"}


def _rpc(client: httpx.Client, token: str | None, method: str, params: dict[str, Any]) -> httpx.Response:
    h = dict(HDR)
    if token:
        h["authorization"] = f"Bearer {token}"
    return client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, headers=h)


def test_a_human_releases_the_write_and_the_agent_cannot() -> None:
    notes: dict[str, str] = {}
    upstream = MCPServer("notes")

    @upstream.tool()
    def write_note(name: str, text: str) -> str:
        """Save a note."""
        notes[name] = text
        return "saved"

    @upstream.tool(annotations=ToolAnnotations(read_only_hint=True))
    def read_note(name: str) -> str:
        """Read a note."""
        return notes.get(name, "")

    app, devices = build_app(upstream, store=MemoryStore(), token="tok", user="me")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    try:
        c = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=20)
        assert _rpc(c, None, "tools/list", {}).status_code == 401
        assert "Approve actions" in c.get("/approve").text
        out = _rpc(c, "tok", "tools/call", {"name": "write_note", "arguments": {"name": "a", "text": "hi"}})
        proposal = json.loads(out.json()["result"]["content"][0]["text"])
        assert proposal["status"] == "pending_user_confirmation" and notes == {}
        refused = _rpc(c, "tok", "tools/call", {"name": "confirm", "arguments": {
            "pending_id": proposal["pending_id"], "evidence_ref": "trust me"}})
        assert json.loads(refused.json()["result"]["content"][0]["text"])["status"] == "refused"

        d = c.post("/device/pair", json={"code": devices.start_pairing("me"), "name": "phone"}).json()

        def signed(method: str, path: str, payload: Any = None) -> dict[str, Any]:
            body = b"" if payload is None else json.dumps(payload).encode()
            path = f"{path}?r={time.perf_counter_ns()}"
            ts = int(time.time())
            h = {"x-device-id": d["device_id"], "x-timestamp": str(ts),
                 "x-signature": sign_request(d["key"], method, path, ts, body), "content-type": "application/json"}
            return c.request(method, path, content=body or None, headers=h).json()

        pending = signed("GET", "/device/pending")["pending"][0]
        ev = signed("POST", "/device/evidence", {"pending_id": pending["pending_id"], "nonce": pending["nonce"],
                                                 "kind": "tap", "option": 1})
        done = signed("POST", "/device/confirm", {"pending_id": pending["pending_id"], "evidence_ref": ev["evidence_ref"]})
        assert done["status"] == "done"
        assert notes == {"a": "hi"}
    finally:
        server.should_exit = True


def test_strict_mode_and_patterns_on_the_command_line() -> None:
    from confirm_gate.cli import parser

    args = parser().parse_args(["serve", "--upstream", "http://x/mcp", "--strict", "--reads", "get_*,list_*"])
    assert args.strict is True and args.reads == "get_*,list_*"
    assert parser().parse_args(["serve", "--upstream", "http://x/mcp"]).strict is False


def test_build_app_is_public_and_the_old_import_still_works() -> None:
    import confirm_gate
    from confirm_gate.cli import build_app as from_cli

    assert confirm_gate.build_app is from_cli and "build_app" in confirm_gate.__all__


def test_the_package_says_it_is_typed() -> None:
    from importlib import resources

    assert resources.files("confirm_gate").joinpath("py.typed").is_file()
