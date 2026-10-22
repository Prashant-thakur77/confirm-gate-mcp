"""``confirm-gate demo``: a newcomer sees a pending card with nothing else installed."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from confirm_gate import sign_request
from confirm_gate.cli import main, parser

HDR = {"content-type": "application/json", "accept": "application/json, text/event-stream",
       "mcp-protocol-version": "2025-11-25"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture()
def demo(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Iterator[dict[str, Any]]:
    """Run ``confirm-gate demo`` as a user would, then serve the app it built on a free port."""
    started: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: started.update(app=app, **kw))
    main(["demo", "--token", "tok"])
    printed = capsys.readouterr().out
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(started["app"], host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    try:
        yield {"client": httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=20), "printed": printed,
               "started": started}
    finally:
        server.should_exit = True


def _call(c: httpx.Client, method: str, params: dict[str, Any], token: str | None = "tok") -> httpx.Response:
    h = dict(HDR)
    if token:
        h["authorization"] = f"Bearer {token}"
    return c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, headers=h)


def _tool(c: httpx.Client, name: str, args: dict[str, Any]) -> Any:
    out = _call(c, "tools/call", {"name": name, "arguments": args}).json()["result"]
    return json.loads(out["content"][0]["text"])


def test_demo_is_a_subcommand_with_the_serve_defaults() -> None:
    args = parser().parse_args(["demo"])
    assert args.cmd == "demo" and args.host == "127.0.0.1" and args.port == 8800 and args.db == ":memory:"


def test_demo_prints_the_endpoint_token_approval_page_and_pairing_code(demo: dict[str, Any]) -> None:
    printed, started = demo["printed"], demo["started"]
    assert started["host"] == "127.0.0.1" and started["port"] == 8800
    assert "http://127.0.0.1:8800/mcp" in printed and "Authorization: Bearer tok" in printed
    assert "http://127.0.0.1:8800/approve" in printed
    assert any(w.isdigit() and len(w) == 6 for w in printed.replace("(", " ").split())  # the pairing code
    assert "write_note" in printed and "list_notes" in printed  # it says what to try


def test_demo_lists_one_read_and_one_write(demo: dict[str, Any]) -> None:
    c = demo["client"]
    assert _call(c, "tools/list", {}, token=None).status_code == 401
    tools = {t["name"]: t for t in _call(c, "tools/list", {}).json()["result"]["tools"]}
    assert set(tools) == {"list_notes", "write_note", "confirm", "pending_status"}
    assert tools["list_notes"]["annotations"]["readOnlyHint"] is True
    assert tools["write_note"]["annotations"]["readOnlyHint"] is False
    assert "confirm" in tools["write_note"]["description"]


def test_demo_write_waits_for_a_tap_and_the_read_passes_through(demo: dict[str, Any]) -> None:
    c = demo["client"]
    assert _tool(c, "list_notes", {}) == {}
    proposal = _tool(c, "write_note", {"name": "shopping", "text": "milk"})
    assert proposal["status"] == "pending_user_confirmation"
    assert proposal["preview"] == ["write_note(name='shopping', text='milk')"]
    assert _tool(c, "list_notes", {}) == {}  # nothing changed
    assert _tool(c, "confirm", {"pending_id": proposal["pending_id"], "evidence_ref": "the user said yes"})[
        "status"] == "refused"
    assert _tool(c, "list_notes", {}) == {}


def test_demo_pairing_code_and_approval_page_release_the_write(demo: dict[str, Any]) -> None:
    c = demo["client"]
    code = next(w for w in demo["printed"].replace("(", " ").split() if w.isdigit() and len(w) == 6)
    assert "Approve actions" in c.get("/approve").text
    proposal = _tool(c, "write_note", {"name": "shopping", "text": "milk"})
    d = c.post("/device/pair", json={"code": code, "name": "phone"}).json()

    def signed(method: str, path: str, payload: Any = None) -> dict[str, Any]:
        body = b"" if payload is None else json.dumps(payload).encode()
        path = f"{path}?r={time.perf_counter_ns()}"
        ts = int(time.time())
        h = {"x-device-id": d["device_id"], "x-timestamp": str(ts),
             "x-signature": sign_request(d["key"], method, path, ts, body), "content-type": "application/json"}
        return c.request(method, path, content=body or None, headers=h).json()

    card = signed("GET", "/device/pending")["pending"][0]
    assert card["pending_id"] == proposal["pending_id"]
    ev = signed("POST", "/device/evidence", {"pending_id": card["pending_id"], "nonce": card["nonce"],
                                             "kind": "tap", "option": 1})
    done = signed("POST", "/device/confirm", {"pending_id": card["pending_id"], "evidence_ref": ev["evidence_ref"]})
    assert done["status"] == "done" and done["receipt"]["who"]["device_id"] == d["device_id"]
    assert _tool(c, "list_notes", {}) == {"shopping": "milk"}
    assert _tool(c, "pending_status", {"pending_id": proposal["pending_id"]})["state"] == "confirmed"
