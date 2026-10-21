# confirm-gate-mcp

**Confirm-before-act for MCP.** Put it in front of any MCP server. Read tools pass straight through. Every write tool becomes a pending card, and only the user's own tap or voice, on a device they paired, can release it.

The guarantee doesn't depend on a prompt. The agent never holds the one-time nonce or the device key that a confirmation needs, so it can't approve its own action, however it's instructed. A prompt injection can make the agent *ask*, but it can't make anything *happen*.

```
agent ──MCP (bearer token)──▶  confirm-gate  ──▶ upstream MCP server
                                  │  ▲
           nonce + cards ─────────┘  └──── signed evidence (tap / voice)
                              user's paired device  (/approve page or your own app)
```

## 60 seconds

```bash
pip install "git+https://github.com/Prashant-thakur77/confirm-gate-mcp"
confirm-gate serve --upstream-cmd "uvx mcp-server-time"       # or --upstream http://host:port/mcp
```

It prints three things:

- the agent's endpoint, `http://127.0.0.1:8800/mcp`, with a bearer token;
- the approval page, `http://127.0.0.1:8800/approve`;
- a 6-digit pairing code.

Open the page, enter the code, and point your agent at the endpoint. When the agent calls a write tool, it gets back:

```json
{"status": "pending_user_confirmation", "pending_id": "p_3f…", "preview": ["write_note(name='a', text='hi')"],
 "message": "Waiting for the user to confirm on their device. Nothing has changed yet."}
```

The card appears on your page, and nothing happens until you approve it there.

## In your own server (20 lines)

[`examples/wrap_any_server.py`](examples/wrap_any_server.py) wraps an in-process server. The same `build_app(upstream, …)` call accepts any of these:

- an `MCPServer` object;
- a Streamable HTTP URL;
- `StdioServerParameters`.

To build the gate into a server you're writing, register tools through `GatedServer`:

```python
from mcp.server import MCPServer
from confirm_gate import GatedServer, SqliteStore

mcp = MCPServer("home")
gs = GatedServer(mcp, store=SqliteStore("gate.db"))

@gs.read()
def door_status() -> dict:
    """Is the front door locked?"""
    return {"locked": True}

@gs.write(risk=lambda a: "low" if a["locked"] else "high",
          preview=lambda a: "Lock the front door" if a["locked"] else "Unlock the front door")
def door_lock(locked: bool) -> dict:
    """Lock or unlock the front door."""
    ...  # runs only after the user confirms
```

Then mount `confirm_gate.device_routes(gs.gate, gs.devices, transcribe=...)` next to `/mcp` for your device app.

## What `confirm` checks

Anyone may call `confirm(pending_id, evidence_ref)`, the agent included. It executes only if every check passes, and each check is listed by name in the receipt:

| check | refuses |
|---|---|
| `user_evidence_exists` | no evidence, a made-up id, "the user said yes" |
| `evidence_is_for_this_card_set` | evidence from another card set |
| `from_a_paired_device` | an unknown or revoked device, or another user's device |
| `card_set_still_open` | a second confirm of the same set (replay) |
| `card_set_not_expired`, `evidence_not_expired` | stale cards (120 s; 60 s for the second yes) |
| `evidence_not_used` | evidence used before |
| `user_words_choose_a_card` | "no", "not two", "um", or a number on a yes/no card |
| `action_unchanged_since_shown` | arguments that changed after the user chose |
| `nonce_consumed_once` | racing confirms: exactly one wins (atomic in every store) |

The device channel adds its own protections:

- Every request is HMAC-signed with the device key.
- Each signature is accepted once, inside a ±60 s window.
- Pairing codes are one-time, valid for 10 minutes, and lock after 5 wrong tries.
- A voice recording's fingerprint can't be used twice.
- High-risk actions need a second, separately nonced yes.

## Voice

Voice evidence is audio. The server transcribes it with your recogniser, so the words come from the user's own recording, never from the model's account of what they said. `confirm_gate.consent.decide` turns the words into a decision:

- **Picks a card:** "two", "the second one", "number 2", "option three". In Hinglish: "doosra", "do number", "teen number", "दो नंबर".
- **Confirms the final card:** "yes", "haan", "theek hai".
- **Cancels:** "no", "cancel", "nahin", "mat karo".
- **Refused:** everything else, including a bare "do", "not two", "yes no", or a sound-alike such as "to".

## Stores

- `MemoryStore`: tests and single process.
- `SqliteStore`: one file, safe across threads and processes.
- `DynamoStore`: one on-demand table, conditional writes for one-time use.

All three give the same `put_if_absent` / `set_once` guarantees, and the same 216 tests run against each.

## Receipts

Every executed write leaves a receipt with:

- who confirmed (user and device);
- what ran (action, arguments, and the card text shown);
- when it was proposed, confirmed and executed;
- how it was confirmed: tap or voice, the server's transcript, and the audio fingerprint;
- every check, by name.

`receipt.verify_fingerprint(r)` detects a receipt edited after it was issued.

## License

MIT
