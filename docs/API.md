# API reference

Everything below can be imported from `confirm_gate`, unless a section names another module. The package ships `py.typed`, so type checkers see the signatures as written here.

- [Three ways in](#three-ways-in)
- [`GatedServer`](#gatedserver): build the gate into your own `MCPServer`
- [`wrap_upstream`](#wrap_upstream): proxy any MCP server
- [`build_app`](#build_app): the proxy as a web app, with bearer auth and the approval page
- [`device_routes`](#device_routes): the device channel's HTTP routes
- [`Gate`](#gate): proposals, evidence, confirmation, more time, receipts
- [`DeviceRegistry`](#deviceregistry): pairing and signed requests
- [Stores](#stores): the `Store` protocol, `MemoryStore`, `SqliteStore`, `DynamoStore`
- [Receipts](#receipts)
- [The consent parser](#the-consent-parser)
- [Command line](#command-line)

## Three ways in

| you have | use | the agent sees |
|---|---|---|
| a Python server you're writing | `GatedServer` | your tools, plus `confirm` and `pending_status` |
| any MCP server (in-process, URL, stdio) | `wrap_upstream` (a `Server`), or `build_app` (a ready web app) | the upstream's tools, gated, plus `confirm` and `pending_status` |
| nothing yet, and you just want to see it work | `confirm-gate demo` | `list_notes`, `write_note`, `confirm`, `pending_status` |

The rule is the same in all three. A read runs at once. A write returns `pending_user_confirmation` and runs only when `confirm` gets evidence that the user's own paired device made. The agent may call `confirm`, but it can't produce the evidence, so on its own it's always refused.

## `GatedServer`

```python
GatedServer(mcp: MCPServer, *, store: Store | None = None, clock=time.time,
            user: Callable[[], str] = lambda: "default", on_event=None,
            registry: ActionRegistry | None = None, install_confirm: bool = True)
```

This registers gated tools on an `MCPServer`.

| argument | meaning |
|---|---|
| `store` | where card sets, evidence, devices and receipts live (default: a new `MemoryStore`) |
| `user` | called on every tool call to name the user it acts for. Each card set belongs to that user, and only that user's devices can answer it |
| `on_event` | called with each [gate event](#events). An exception in it is logged and never breaks the gate |
| `install_confirm` | adds the `confirm(pending_id, evidence_ref="")` and `pending_status(pending_id)` tools. Its `pending_status` only answers for the current `user()`'s own card sets |

Attributes: `mcp`, `store`, `registry`, `gate` (a [`Gate`](#gate)), `devices` (a [`DeviceRegistry`](#deviceregistry) on the same store) and `user`.

### `@gs.read(name=None, *, description=None, pass_user=False)`

This registers a read tool. It runs at once and is listed with `readOnlyHint: true`. With `pass_user=True`, the function receives `user_id=` from `user()`. It's left out of the tool's schema, so a caller can't pass one.

### `@gs.write(name=None, *, risk="low", preview=None, description=None, pass_user=False)`

This registers a write tool. Calling the tool only proposes the write. The function itself runs later, inside `confirm`, once the user's evidence chooses it.

| argument | meaning |
|---|---|
| `risk` | `"low"`, `"high"`, or a function of the arguments that returns one. A high-risk card needs a second, separately nonced yes |
| `preview` | a function of the arguments that returns the card text (default: `name(arg='value', …)`) |
| `description` | the tool description (default: the docstring). A note is added saying the tool waits for the user's own confirmation |
| `pass_user` | the function receives `user_id`: the owner of the card set, at confirm time. It's never an argument the caller can pass |

The tool is listed with `readOnlyHint: false, destructiveHint: true`. Its reply:

```json
{"status": "pending_user_confirmation", "pending_id": "p_…", "preview": ["Unlock the front door"],
 "stage": "choose", "message": "Waiting for the user to confirm on their device. Nothing has changed yet."}
```

The decorators return the original function unchanged, so you can still call it directly in tests.

Under the hood, both decorators register an `Action` in an `ActionRegistry` (`registry.read(...)`, `registry.write(...)`, `registry.add(Action(...))`, `registry.get(name)`). You only need these if you drive a `Gate` without MCP.

## `wrap_upstream`

```python
wrap_upstream(upstream, *, store=None, clock=time.time, user=lambda: "default", reads=(), writes=(),
              high_risk=(), trust_annotations=True, name="confirm-gate", on_event=None) -> tuple[Server, Gate]
```

This builds a gated proxy for `upstream`, which can be an `MCPServer` or `Server` object, a Streamable HTTP URL, or `mcp.StdioServerParameters`. It returns a low-level MCP `Server` and its `Gate`. To pair devices, make a `DeviceRegistry(gate.store)`.

How each upstream tool is sorted:

1. It matches `writes` (a name or glob pattern, such as `delete_*`): gated.
2. It matches `reads`: passes through.
3. `trust_annotations=True` (the default) and the upstream marks it `readOnlyHint`: passes through.
4. Anything else: gated. Unknown means write.

Set `trust_annotations=False` for a server you don't control. Then only `reads` pass through (this is `--strict` on the command line). Tools matching `high_risk` need a second yes.

The proxy lists the upstream's tools again on every `tools/list`, and calls the upstream (opening a new client session) for each read and for each confirmed write. A gated tool's listing gets the gate note added to its description, the annotations `readOnlyHint: false, destructiveHint: true`, and no `outputSchema`, because a pending reply isn't the upstream's result.

## `build_app`

```python
build_app(upstream, *, store: Store, token: str, user: str = "me", reads=None, writes=None,
          high_risk=None, trust_annotations=True) -> tuple[Starlette, DeviceRegistry]
```

This is the app that `confirm-gate serve` runs. It's importable as `confirm_gate.build_app`; `confirm_gate.cli.build_app` still works too. It calls `wrap_upstream` with the same options and serves:

| path | for | auth |
|---|---|---|
| `/mcp` | the agent (Streamable HTTP, stateless, JSON responses) | `Authorization: Bearer <token>`, or 401 |
| `/approve` | the built-in approval page: pair, see cards, tap, More time | none: it pairs with a code, then signs every request |
| `/device/*` | the [device routes](#device_routes) | signed by the device key (`/device/pair` takes a pairing code) |

It returns the app and its `DeviceRegistry`. Call `devices.start_pairing(user)` to get a code to show the user. Serve the app with any ASGI server, for example `uvicorn.run(app, host="127.0.0.1", port=8800)`. Its device routes take taps, plus transcripts from voice devices. They don't transcribe audio, so for voice evidence, mount `device_routes` yourself with a recogniser.

## `device_routes`

```python
device_routes(gate, devices, *, transcribe: Callable[[bytes], str], transcriber=None, media_sink=None,
              prefix="/device", max_audio_bytes=2_000_000, client_ip=None) -> list[Route]
```

These are Starlette routes for the user's device. Mount them next to `/mcp`, and never give the agent a device key. Every route except `pair` needs a request signed as described under [`DeviceRegistry`](#deviceregistry).

| route | body | answer |
|---|---|---|
| `POST /device/pair` | `{"code", "name"}` | `{"device_id", "user_id", "key", "name"}`, or 403 |
| `GET /device/pending` | | `{"pending": [card set with its "nonce", …]}` |
| `POST /device/evidence` | `{"pending_id", "nonce", "kind": "tap", "option": n}` or `"answer": "yes" \| "no"`, optional `"input": "touch" \| "key" \| "switch"` | `{"evidence_ref", "decision"}`, or 409 |
| | `{"pending_id", "nonce", "kind": "voice", "audio_b64"}` | the same. Audio over `max_audio_bytes` gets 413 before it's decoded |
| | `{"pending_id", "nonce", "kind": "transcript", "transcript"}` | the same, only from a device paired with a `transcriber` |
| `POST /device/confirm` | `{"pending_id", "evidence_ref"}` | the result of [`Gate.confirm`](#confirming) |
| `POST /device/more-time` | `{"pending_id"}` | the public view with `expires_in_s` and `extensions_left`. 404 for an unknown set or another user's, 409 for one that isn't waiting |
| `GET /device/receipts` | | `{"receipts": […]}` for the device's user |

| argument | meaning |
|---|---|
| `transcribe` | your speech recogniser: audio bytes in, text out |
| `transcriber` | the recogniser's name, recorded in voice receipts |
| `media_sink` | `(audio, device, pending_id) -> ref`, to keep the recording. The ref goes into the receipt |
| `client_ip` | names the caller for the per-source pairing budget. Behind a proxy, return the address the proxy saw |

## `Gate`

```python
Gate(store: Store, registry: ActionRegistry, *, clock=time.time, on_event=None)
```

Class attributes you can override: `pending_ttl = 120.0` (seconds a card set waits), `final_ttl = 60.0` (the second yes), `evidence_ttl = 120.0`, `max_extensions = 10`, `max_extend_s = 600.0`.

### Proposing

`propose(user_id, options, *, origin="user_words", context=None, stage=None, parent_id=None) -> PendingSet`

This opens a card set of 1 to 5 options. Each option is `(action, args)` or `(action, args, card_text)`, and each action must be a registered write. The set gets a one-time nonce, which is stored and never returned to MCP. A high-risk card is chosen first and then asks a second yes. Pass `stage="final"` to open a set at the final yes or no on purpose. `origin` is `"user_words"` or `"assistant"`, and it's recorded in the receipt. Raises `ValueError` for an unknown action, a read, or the wrong number of options.

`PendingSet` is a frozen dataclass: `pending_id`, `user_id`, `nonce`, `stage`, `origin`, `options`, `issued_at`, `expires_at`. Only code on the device side should ever see its `nonce`.

### Reading state

- `pending_status(pending_id, *, user_id=None) -> dict`: the public view, which never includes the nonce: `pending_id`, `state` (`waiting_for_user`, `expired`, `confirmed`, `cancelled`, `withdrawn` or another `withdraw_open` reason, or `unknown`), `stage`, `origin`, `options` (`n`, `text`, `action`, `args`, `risk`) and `expires_in_s`. With `user_id`, another user's set is reported as `unknown`.
- `public_view(pending) -> dict`: the same view for a `PendingSet` or a stored document.
- `device_pending(device) -> list[dict]` and `device_view(device, pending_id) -> dict | None`: open sets **with** their nonces, for a paired device only.

### Evidence (device side)

- `submit_tap(device, pending_id, nonce, *, option=None, answer=None, input=None)`: `option` picks a card. `answer="yes"` answers a final card, and `answer="no"` cancels. `input` is `touch` (the default), `key` or `switch`.
- `submit_voice(device, pending_id, nonce, audio, *, transcribe, media_ref=None, transcriber=None)`: the server transcribes the audio itself, and the same recording can't be used twice.
- `submit_transcript(device, pending_id, nonce, transcript)`: only for a device paired with a `transcriber`.

Each returns `{"evidence_ref": "ev_…", "decision": {...}}`, or raises `EvidenceRejected` (wrong nonce, another user's set, already answered, expired, reused).

### Confirming

`confirm(pending_id, evidence_ref=None) -> dict` (and `await aconfirm(...)` in async code)

This runs the chosen option only if every check passes. Anyone may call it, the agent included. The result's `status` is one of:

| status | meaning |
|---|---|
| `done` | it ran. Includes `result`, `receipt` and `message` (one sentence, such as "Done: Unlock the front door. Confirmed by your tap.") |
| `needs_final_yes` | a high-risk card was chosen. A new final card set (`pending_id`, `card`) waits for a second yes |
| `cancelled` | the user said no. Nothing changed |
| `refused` | a check failed. Includes `reason` and `checks` (each `{"name", "ok"}`). Nothing changed |
| `failed` | the action itself raised. Includes `error` |

Every result carries `timing.gate_ms` (the time the gate added) and `timing.action_ms`. The checks, in order: `user_evidence_exists`, `evidence_is_for_this_card_set`, `card_set_exists`, `from_a_paired_device`, `card_set_still_open`, `card_set_not_expired`, `evidence_not_expired`, `evidence_not_used`, `user_words_choose_a_card`, `action_unchanged_since_shown`, `action_is_registered_write`, `nonce_consumed_once`, `evidence_consumed_once`.

### More time, withdrawing, receipts

- `extend(pending_id, *, user_id, seconds=None) -> dict`: moves a waiting set's deadline to now plus its own time (or `seconds`, up to `max_extend_s`), never earlier than it was, at most `max_extensions` times per set. It returns the public view plus `extensions_left`. It raises `KeyError` for an unknown set or another user's, `NotWaiting` for a set that's answered, withdrawn, expired or out of more time, and `ValueError` for a bad `seconds`. Call it only from a request signed by the user's own device: it's never an MCP tool.
- `withdraw_open(user_id, *, reason="withdrawn") -> int`: closes every open set for the user without running anything.
- `receipts(user_id, limit=20) -> list[dict]`: the user's receipts, newest first.

### Events

`on_event` receives dicts with `type` and `at`, plus fields for that type. The types are `proposed`, `evidence`, `refused`, `cancelled`, `needs_final_yes`, `done`, `failed`, `withdrawn` and `extended`. Events never carry a nonce.

## `DeviceRegistry`

```python
DeviceRegistry(store: Store, *, clock=time.time)
```

- `start_pairing(user_id, *, transcriber="", minted_by="") -> str`: a one-time 6-digit code, valid for `pairing_ttl` (600 s). Show it to the user, never to the agent. `transcriber` marks a device that sends its own transcripts. `minted_by` is the paired device that asked for the code: the code stops working if that device is unpaired first.
- `pair(code, *, name="", source="") -> Device`: exchanges a code for a device key. Each attempt takes from two budgets before the code is checked: `max_attempts` (5) per `source` and `global_max_attempts` (30) for everyone, per `attempt_window` (600 s). It raises `PairingError`.
- `issue(user_id, *, name="", transcriber="", paired_via="") -> Device`: creates a device directly, for callers that already know the user.
- `set_transcriber(device_id, transcriber) -> bool`, `revoke(device_id) -> bool`, `get(device_id) -> Device | None`.
- `authenticate(headers, method, path, body) -> Device`: checks a signed request. It raises `AuthError` if it's unsigned, from an unknown or revoked device, outside ±`skew` (60 s), signed wrongly, or replayed.

`Device` has `device_id`, `user_id`, `key`, `name` and `transcriber`. A device signs each request with `sign_request(key, method, path, timestamp, body)` and sends the headers `x-device-id`, `x-timestamp` and `x-signature`:

```
signature = HMAC-SHA256(key, METHOD \n PATH \n TIMESTAMP \n SHA256(BODY))
```

`PATH` includes the query string. Each signature is accepted once.

## Stores

### The `Store` protocol

Documents are JSON-able dicts, addressed by `(kind, key)`.

| method | contract |
|---|---|
| `put(kind, key, doc)` | write or overwrite |
| `get(kind, key) -> dict \| None` | read |
| `put_if_absent(kind, key, doc) -> bool` | **atomic**: the first writer wins (fingerprints, signatures, pairing codes) |
| `set_once(kind, key, field, value, updates=None) -> bool` | **atomic**: sets `field` (and `updates`) only if `field` is still empty. This is how a nonce or a piece of evidence is used exactly once |
| `scan(kind, **equals) -> list[dict]` | every doc of a kind whose top-level fields equal `equals` (the gate always filters by `user_id`) |
| `add(kind, key, amount=1, *, expires_at=None) -> int` | **atomic** counter. Returns the new value |
| `update(kind, key, change) -> dict \| None` | compare-and-swap: `change(doc)` returns the new doc, or `None` to leave it as it is. It's retried on conflict, so a write landing in between is never lost |

A custom store must make the atomic methods truly atomic. The tests in `tests/test_store.py` run against every store, and they're the way to check a new one.

### The three stores

- **`MemoryStore()`**: in process, with a lock. Use it for tests and single-process demos (`confirm-gate demo` uses it).
- **`SqliteStore(path)`**: one file, safe across threads and processes (WAL, `BEGIN IMMEDIATE`). `purge(now=None, *, keep_days=30) -> int` deletes rows more than 30 days past their expiry. It also runs by itself every 500 writes.
- **`DynamoStore(table_name, *, client=None, region=None)`**: one on-demand DynamoDB table. It needs boto3: `pip install "confirm-gate-mcp[dynamodb] @ git+https://github.com/Prashant-thakur77/confirm-gate-mcp"` (`confirm-gate-mcp[dynamodb]` once it's on PyPI). `DynamoStore.create_table(table_name, *, client=...)` creates it with the key `pk` and two indexes, `kind-index` and `user-kind-index`. One-time writes are conditional writes. Rows get a `ttl` 30 days after they expire. Turn on TTL for that attribute so DynamoDB deletes them. If an index is missing (an older table), `scan` reads the table instead. An IAM refusal raises an error, and is never mistaken for a missing index.

## Receipts

`Gate.confirm` returns a receipt with every `done` result, and stores it for `Gate.receipts`. Schema `confirm-gate.receipt/1`:

| field | contents |
|---|---|
| `receipt_id` | `rcpt_` plus a digest of the rest of the receipt |
| `who` | `user_id`, `device_id` |
| `what` | `action`, `args`, `shown_as` (the card text), `risk`, `params_hash`, `origin`, `options_shown`, `option_chosen` |
| `when` | `proposed_at`, `evidence_at`, `confirmed_at`, `executed_at` (ISO 8601, UTC) |
| `how` | `channel` (`tap`, `voice` or `transcript`), `input` (`touch`, `key`, `switch` or `voice`), `pending_id`, `nonce_sha256` (12 hex characters of the nonce's hash, never the nonce) |
| `evidence` | `evidence_id`, `channel`, `input`, `transcript` (the server's own, for voice), `decision`, `fingerprint`, `media_ref`, `transcriber` |
| `context` | `heard`, `asr` and `utterance_ref`, if the proposer passed them |
| `checks` | every check, by name, with `ok` |
| `result` | what the action returned |
| `chain` | for a high-risk action, the first stage (`pending_id`, `evidence_id`, `heard`) |

These helpers are in `confirm_gate.receipt`:

- `verify_fingerprint(receipt) -> bool`: `False` if the receipt was edited after it was issued. It's a digest, not a signature.
- `to_text(receipt) -> str`: one sentence for a screen reader or a speaker.
- `to_markdown(receipt) -> str`: a short summary for a log or a chat.

## The consent parser

`decide(transcript, n_options, *, stage="choose", echo="") -> Decision` turns words into `Decision(kind, option, heard)`, where `kind` is `choose`, `yes`, `no` or `unclear`. At the `choose` stage, a clear card number picks that card, and "yes" picks only when there's exactly one card. At the `final` stage, only yes or no counts, and every word must be known or be one of `echo` (the final card's own text). `normalise(text)` is the comparable form it works on. The README's [Voice](../README.md#voice) section lists what counts.

## Command line

`confirm-gate demo` and `confirm-gate serve` both print the agent's endpoint and bearer token, the approval page and a pairing code (valid for 10 minutes), then serve until Ctrl+C.

### `confirm-gate demo`

This runs the gate in front of a built-in notes server with one read (`list_notes`) and one write (`write_note`). There's nothing else to install. It keeps everything in memory by default.

| flag | default | meaning |
|---|---|---|
| `--host` | `127.0.0.1` | bind address |
| `--port` | `8800` | port |
| `--user` | `me` | the user the cards belong to |
| `--token` | generated | the agent's bearer token |
| `--db` | `:memory:` | a SQLite file, to keep the pairing and receipts between runs |

### `confirm-gate serve`

This gates an upstream MCP server. Exactly one of `--upstream` and `--upstream-cmd` is required.

| flag | default | meaning |
|---|---|---|
| `--upstream URL` | | an upstream Streamable HTTP URL |
| `--upstream-cmd CMD` | | an upstream stdio command, split like a shell would split it |
| `--strict` | off | ignore the upstream's `readOnlyHint`: only `--reads` pass through (`trust_annotations=False`) |
| `--reads` | | comma-separated tool names or glob patterns that pass through |
| `--writes` | | tools or patterns that are always gated, even if marked read-only |
| `--high-risk` | | tools or patterns that need a second, separate yes |
| `--host` | `127.0.0.1` | bind address. Put anything reachable from other machines behind HTTPS |
| `--port` | `8800` | port |
| `--user` | `me` | the user the cards belong to |
| `--token` | generated | the agent's bearer token. Set it to keep client configs working across restarts |
| `--db` | `~/.confirm-gate/gate.db` | a SQLite file, or `:memory:` |
