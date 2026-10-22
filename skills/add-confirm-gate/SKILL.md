---
name: add-confirm-gate
description: Put confirm-before-act in front of an MCP server, so an AI agent can read freely but every write (send, buy, delete, unlock) waits for the user's own tap or voice on a paired device. Use it when someone asks to make an MCP server or agent safer, add human approval, stop prompt injection from causing actions, or gate destructive tools.
---

# Add the confirm gate to an MCP server

[confirm-gate-mcp](https://github.com/Prashant-thakur77/confirm-gate-mcp) (MIT) proxies or wraps an MCP server. Reads pass through; every write becomes a pending card. Only evidence from the user's own paired device can release it. The agent never holds the one-time nonce or the device key, so no prompt can make it approve its own action.

## 1. Pick the way in

- **A server you don't control** (a third-party or stdio server): run the proxy, with no code changes.
- **A server you're writing in Python**: register tools through `GatedServer`.

## 2a. Proxy any server

```bash
pip install confirm-gate-mcp
confirm-gate serve --upstream-cmd "<the server's stdio command>" --strict \
    --reads "get_*,list_*,search_*,read_*" --high-risk "delete_*,send_*,pay_*"
# or: --upstream https://host/mcp
```

- **Always use `--strict` for servers you didn't write.** MCP tool annotations are hints from the server, and `--strict` ignores its `readOnlyHint`.
- List the true reads in `--reads` (names or glob patterns). Everything else is gated.
- Put irreversible tools in `--high-risk`: they need a second, separate yes.
- The command prints the agent's endpoint and bearer token, the approval page (`/approve`) and a pairing code. Pair the user's phone on the approval page, and point the agent at the endpoint.

## 2b. Build it into your own server

```python
from mcp.server import MCPServer
from confirm_gate import GatedServer, SqliteStore   # DynamoStore on AWS

mcp = MCPServer("home")
gs = GatedServer(mcp, store=SqliteStore("gate.db"))

@gs.read()
def door_status() -> dict: ...

@gs.write(risk=lambda a: "low" if a["locked"] else "high",
          preview=lambda a: "Lock the front door" if a["locked"] else "Unlock the front door")
def door_lock(locked: bool) -> dict: ...   # runs only after the user confirms
```

Mount `confirm_gate.device_routes(gs.gate, gs.devices, transcribe=...)` beside `/mcp` for the user's device app, or reuse the built-in approval page. Give each write a `preview`: a plain sentence the user will read on the card.

## 3. Check it

- A write tool returns `{"status": "pending_user_confirmation", ...}` and nothing changes.
- The agent's own `confirm(pending_id, "the user said yes")` is refused.
- After a tap on the paired device, `confirm` runs it once, and a second confirm is refused (replay).
- No tool result, log or receipt contains a nonce.
- With `--strict`, a tool the upstream marks read-only is still gated unless it's listed in `--reads`.

## Don't

- Don't give the agent the device key, the pairing code or the approval page session.
- Don't mark a tool as a read because its name sounds harmless. Read what it does.
- Don't build approval into the chat ("reply yes to continue"). That runs through the agent's own channel, which is what the gate exists to avoid.
