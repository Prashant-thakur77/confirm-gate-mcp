"""Put the gate in front of MCP tools.

Two ways in:

* :class:`GatedServer` — decorate your own tools as ``read`` or ``write`` on an
  ``MCPServer``. Reads run. Writes return a pending card set and run only after
  ``confirm`` sees the user's evidence.
* :func:`wrap_upstream` — proxy any existing MCP server (in-process, URL or
  stdio). Tools marked ``readOnlyHint`` pass through; every other tool is treated
  as a write. Unknown means write: the safe default.
"""

from __future__ import annotations

import functools
import inspect
import json
import time
from collections.abc import Callable, Iterable
from typing import Any

import mcp_types as types
from mcp import Client
from mcp.server import MCPServer, Server
from mcp_types import ToolAnnotations

from .actions import Action, ActionRegistry, Risk
from .evidence import DeviceRegistry
from .gate import Gate
from .store import MemoryStore, Store

GATE_NOTE = (
    " This changes something, so it does not run now: it creates a pending card the user must "
    "confirm with the user's own voice or tap on their paired device. Tell the user what you proposed."
)

CONFIRM_DESCRIPTION = (
    "Release a pending action. Needs an evidence_ref, which only the user's paired device can create "
    "from the user's own voice or tap. You cannot create, guess or relay one: if the user has not "
    "confirmed on their device, this refuses and nothing changes."
)


def proposal_reply(gate: Gate, pending_id: str) -> dict[str, Any]:
    view = gate.pending_status(pending_id)
    return {
        "status": "pending_user_confirmation",
        "pending_id": pending_id,
        "preview": [o["text"] for o in view.get("options", [])],
        "stage": view.get("stage"),
        "message": "Waiting for the user to confirm on their device. Nothing has changed yet.",
    }


class GatedServer:
    """Register gated tools on an ``MCPServer``."""

    def __init__(
        self,
        mcp: MCPServer,
        *,
        store: Store | None = None,
        clock: Callable[[], float] = time.time,
        user: Callable[[], str] = lambda: "default",
        on_event: Callable[[dict[str, Any]], None] | None = None,
        registry: ActionRegistry | None = None,
        install_confirm: bool = True,
    ) -> None:
        self.mcp = mcp
        self.store = store if store is not None else MemoryStore()
        self.registry = registry if registry is not None else ActionRegistry()
        self.gate = Gate(self.store, self.registry, clock=clock, on_event=on_event)
        self.devices = DeviceRegistry(self.store, clock=clock)
        self.user = user
        if install_confirm:
            self._install_confirm()

    def read(self, name: str | None = None, *, description: str | None = None,
             pass_user: bool = False) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a read tool. With ``pass_user`` the tool gets ``user_id`` from the server, not the caller."""

        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = name or fn.__name__
            self.registry.read(tool_name, pass_user=pass_user)(fn)
            exposed = fn
            if pass_user:
                sig = inspect.signature(fn, eval_str=True)
                public = _without_user(sig)

                @functools.wraps(fn)
                def with_user(*args: Any, **kwargs: Any) -> Any:
                    return fn(*args, **kwargs, user_id=self.user())

                _expose(with_user, public, sig.return_annotation)
                exposed = with_user
            self.mcp.add_tool(exposed, name=tool_name, description=description or inspect.getdoc(fn),
                              annotations=ToolAnnotations(read_only_hint=True))
            return fn

        return deco

    def write(
        self,
        name: str | None = None,
        *,
        risk: Risk | Callable[[dict[str, Any]], Risk] = "low",
        preview: Callable[[dict[str, Any]], str] | None = None,
        description: str | None = None,
        pass_user: bool = False,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a write tool. Calling it only proposes; ``confirm`` with user evidence runs it.

        With ``pass_user`` the executor receives the ``user_id`` of the card set's owner at confirm
        time; callers cannot pass one.
        """

        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = name or fn.__name__
            self.registry.write(tool_name, risk=risk, preview=preview, pass_user=pass_user)(fn)
            sig = inspect.signature(fn, eval_str=True)
            public = _without_user(sig) if pass_user else sig

            @functools.wraps(fn)
            def propose(*args: Any, **kwargs: Any) -> dict[str, Any]:
                bound = public.bind(*args, **kwargs)
                bound.apply_defaults()
                ps = self.gate.propose(self.user(), [(tool_name, dict(bound.arguments))], origin="assistant")
                return proposal_reply(self.gate, ps.pending_id)

            _expose(propose, public, dict[str, Any])
            self.mcp.add_tool(propose, name=tool_name,
                              description=(description or inspect.getdoc(fn) or tool_name) + GATE_NOTE,
                              annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
            return fn

        return deco

    def _install_confirm(self) -> None:
        gate = self.gate

        async def confirm(pending_id: str, evidence_ref: str = "") -> dict[str, Any]:
            return await gate.aconfirm(pending_id, evidence_ref)

        def pending_status(pending_id: str) -> dict[str, Any]:
            """Whether a pending card is still waiting, confirmed, cancelled or expired. Never runs anything."""
            return gate.pending_status(pending_id)

        self.mcp.add_tool(confirm, name="confirm", description=CONFIRM_DESCRIPTION,
                          annotations=ToolAnnotations(read_only_hint=False))
        self.mcp.add_tool(pending_status, name="pending_status",
                          annotations=ToolAnnotations(read_only_hint=True))


def _without_user(sig: inspect.Signature) -> inspect.Signature:
    return sig.replace(parameters=[p for n, p in sig.parameters.items() if n != "user_id"])


def _expose(fn: Callable[..., Any], sig: inspect.Signature, returns: Any) -> None:
    """Make MCP build the tool schema from *sig*, not from the wrapped executor."""
    fn.__signature__ = sig.replace(return_annotation=returns)  # type: ignore[attr-defined]
    fn.__annotations__ = {**{k: p.annotation for k, p in sig.parameters.items()}, "return": returns}
    if hasattr(fn, "__wrapped__"):
        del fn.__wrapped__  # type: ignore[attr-defined]


def _text_result(payload: Any) -> types.CallToolResult:
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)])


def _flatten(result: types.CallToolResult) -> Any:
    texts = [c.text for c in result.content if isinstance(c, types.TextContent)]
    if result.structured_content is not None:
        return result.structured_content
    return texts[0] if len(texts) == 1 else texts


def wrap_upstream(
    upstream: Any,
    *,
    store: Store | None = None,
    clock: Callable[[], float] = time.time,
    user: Callable[[], str] = lambda: "default",
    reads: Iterable[str] = (),
    writes: Iterable[str] = (),
    high_risk: Iterable[str] = (),
    name: str = "confirm-gate",
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[Server[Any], Gate]:
    """A gated proxy for *upstream* (an ``MCPServer``, ``Server``, URL or stdio params).

    Returns the proxy server and its gate (pair devices with ``DeviceRegistry(gate.store)``).
    """
    store = store if store is not None else MemoryStore()
    registry = ActionRegistry()
    gate = Gate(store, registry, clock=clock, on_event=on_event)
    force_read, force_write, high = set(reads), set(writes), set(high_risk)
    tools: dict[str, types.Tool] = {}

    def is_read(tool: types.Tool) -> bool:
        if tool.name in force_write:
            return False
        if tool.name in force_read:
            return True
        return bool(tool.annotations and tool.annotations.read_only_hint)

    async def refresh() -> None:
        async with Client(upstream) as c:
            listed = (await c.list_tools()).tools
        for tool in listed:
            tools[tool.name] = tool
            if tool.name not in registry:
                kind = "read" if is_read(tool) else "write"
                registry.add(Action(tool.name, kind, _forwarder(upstream, tool.name),
                                    "high" if tool.name in high else "low", None, tool.description or ""))

    confirm_tool = types.Tool(
        name="confirm", description=CONFIRM_DESCRIPTION,
        input_schema={"type": "object", "properties": {"pending_id": {"type": "string"},
                                                        "evidence_ref": {"type": "string"}},
                      "required": ["pending_id"]},
        annotations=ToolAnnotations(read_only_hint=False),
    )
    status_tool = types.Tool(
        name="pending_status", description="Whether a pending card is still waiting, confirmed, cancelled or expired.",
        input_schema={"type": "object", "properties": {"pending_id": {"type": "string"}}, "required": ["pending_id"]},
        annotations=ToolAnnotations(read_only_hint=True),
    )

    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        await refresh()
        shown: list[types.Tool] = []
        for tool in tools.values():
            if is_read(tool):
                shown.append(tool)
            else:
                shown.append(tool.model_copy(update={
                    "description": (tool.description or tool.name) + GATE_NOTE,
                    "annotations": ToolAnnotations(read_only_hint=False, destructive_hint=True),
                    "output_schema": None,  # a pending reply is not the upstream tool's result
                }))
        return types.ListToolsResult(tools=[*shown, confirm_tool, status_tool])

    async def on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        args = dict(params.arguments or {})
        if params.name == "confirm":
            return _text_result(await gate.aconfirm(args.get("pending_id"), args.get("evidence_ref")))
        if params.name == "pending_status":
            return _text_result(gate.pending_status(str(args.get("pending_id", ""))))
        if params.name not in tools:
            await refresh()
        tool = tools.get(params.name)
        if tool is None:
            return types.CallToolResult(content=[types.TextContent(type="text", text=f"unknown tool {params.name}")],
                                        is_error=True)
        if is_read(tool):
            async with Client(upstream) as c:
                return await c.call_tool(params.name, args)
        ps = gate.propose(user(), [(params.name, args)], origin="assistant")
        return _text_result(proposal_reply(gate, ps.pending_id))

    server: Server[Any] = Server(name, on_list_tools=on_list_tools, on_call_tool=on_call_tool)
    return server, gate


def _forwarder(upstream: Any, tool_name: str) -> Callable[..., Any]:
    """The executor for one upstream write: call the real tool, only ever from ``Gate.confirm``."""

    async def call(**args: Any) -> Any:
        async with Client(upstream) as c:
            return _flatten(await c.call_tool(tool_name, args))

    return call
