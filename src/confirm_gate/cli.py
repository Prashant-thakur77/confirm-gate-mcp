"""``confirm-gate serve``: put the gate in front of any MCP server, with a built-in approval page.

    confirm-gate serve --upstream http://127.0.0.1:8000/mcp
    confirm-gate serve --upstream-cmd "npx -y @modelcontextprotocol/server-filesystem /tmp/sandbox"
    confirm-gate serve --upstream https://example.com/mcp --strict --reads "get_*,list_*" --high-risk "delete_*"

Agents connect to ``http://HOST:PORT/mcp`` with ``Authorization: Bearer <token>`` (printed at start).
The user opens ``http://HOST:PORT/approve`` on their phone or laptop, enters the pairing code (printed
at start), and approves or declines each write there. The agent never sees a nonce or a device key.
"""

from __future__ import annotations

import argparse
import hmac
import secrets
import shlex
from importlib import resources
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from .device import device_routes
from .evidence import DeviceRegistry
from .proxy import wrap_upstream
from .store import MemoryStore, SqliteStore, Store


class BearerOnMcp:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/mcp") and scope["method"] != "OPTIONS":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            got = headers.get("authorization", "")
            if not (got.startswith("Bearer ") and hmac.compare_digest(got[7:].strip(), self.token)):
                await JSONResponse({"error": "missing or wrong bearer token"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_app(upstream: Any, *, store: Store, token: str, user: str = "me", reads: list[str] | None = None,
              writes: list[str] | None = None, high_risk: list[str] | None = None,
              trust_annotations: bool = True) -> tuple[Starlette, DeviceRegistry]:
    """The gated proxy app: ``/mcp`` for the agent, ``/device/*`` and ``/approve`` for the user."""
    from mcp.server.transport_security import TransportSecuritySettings

    server, gate = wrap_upstream(upstream, store=store, user=lambda: user, reads=reads or (), writes=writes or (),
                                 high_risk=high_risk or (), trust_annotations=trust_annotations)
    devices = DeviceRegistry(store)
    app = server.streamable_http_app(stateless_http=True, json_response=True,
                                     transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    page = resources.files("confirm_gate").joinpath("approve.html").read_text()

    async def approve(request: Request) -> HTMLResponse:
        return HTMLResponse(page)

    app.router.routes.extend(device_routes(gate, devices, transcribe=lambda audio: "") + [Route("/approve", approve)])
    app.add_middleware(BearerOnMcp, token=token)
    return app, devices


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="confirm-gate", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="gate an upstream MCP server")
    src = s.add_mutually_exclusive_group(required=True)
    src.add_argument("--upstream", help="upstream Streamable HTTP URL, e.g. http://127.0.0.1:8000/mcp")
    src.add_argument("--upstream-cmd", help="upstream stdio command, e.g. 'uvx mcp-server-time'")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8800)
    s.add_argument("--user", default="me")
    s.add_argument("--token", help="agent bearer token (default: generated and printed)")
    s.add_argument("--db", default=str(Path.home() / ".confirm-gate" / "gate.db"), help="SQLite file, or :memory:")
    s.add_argument("--reads", default="", help="comma-separated tools or patterns (list_*) to treat as reads")
    s.add_argument("--writes", default="", help="comma-separated tools or patterns to gate even if marked read-only")
    s.add_argument("--high-risk", default="", help="comma-separated tools or patterns that need a second yes")
    s.add_argument("--strict", action="store_true",
                   help="ignore the upstream's readOnlyHint: only the tools in --reads pass through ungated")
    return ap


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)

    import uvicorn

    if args.upstream_cmd:
        from mcp import StdioServerParameters

        parts = shlex.split(args.upstream_cmd)
        upstream: Any = StdioServerParameters(command=parts[0], args=parts[1:])
    else:
        upstream = args.upstream
    store: Store = MemoryStore() if args.db == ":memory:" else SqliteStore(args.db)
    token = args.token or secrets.token_urlsafe(24)
    split = lambda v: [x.strip() for x in v.split(",") if x.strip()]  # noqa: E731
    app, devices = build_app(upstream, store=store, token=token, user=args.user, reads=split(args.reads),
                             writes=split(args.writes), high_risk=split(args.high_risk),
                             trust_annotations=not args.strict)
    code = devices.start_pairing(args.user)
    base = f"http://{args.host}:{args.port}"
    print(f"\n  confirm-gate in front of {args.upstream or args.upstream_cmd}\n"
          f"  Agent MCP endpoint: {base}/mcp   (Authorization: Bearer {token})\n"
          f"  Approve on your device: {base}/approve   pairing code {code} (10 minutes)\n", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
