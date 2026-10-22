# Changelog

## 0.8.0 (2026-10-22)

- **On PyPI:** `pip install confirm-gate-mcp` (published by GitHub trusted publishing, with no stored token).

- **DynamoStore never mistakes an IAM refusal for a missing index.** A policy covering the table but not its indexes made every lookup fall back, silently, to a full-table scan; the refusal is now raised.

- **`confirm-gate demo`**: the gate in front of a built-in notes server with one read (`list_notes`) and one write (`write_note`), in memory, with nothing else to install. Like `serve`, it prints the endpoint, the bearer token, the approval page and a pairing code, and then the steps to see a card. The README's "60 seconds" is built on it: install, run `confirm-gate demo`, pair on `/approve`, call `write_note` from MCP Inspector, tap the card, and get a receipt.
- **`build_app` is exported from `confirm_gate`.** `from confirm_gate.cli import build_app` still works.
- **The package is typed:** it ships `py.typed`.
- **`uvicorn` is a declared dependency.** The command line always imported it, but it used to arrive only through `mcp`.
- **Package metadata:** project URLs (homepage, source, changelog, issues, docs) and classifiers (Python 3.12 and 3.13, Development Status 4 - Beta, Typing :: Typed).
- **Docs:** `docs/API.md` covers `GatedServer`, `wrap_upstream`, `build_app`, `device_routes`, `Gate`, `DeviceRegistry`, the `Store` protocol and its three stores, receipt fields and every CLI flag. The README has client configs for Claude Code, `mcp.json` (Cursor, VS Code), MCP Inspector and Strands, and shows `serve --strict` with the official filesystem server. The install lines use the GitHub URL until the package is on PyPI.
- **Community files:** `CONTRIBUTING.md` (setup, the failing-test-first rule for the gate, release steps with the trusted-publishing workflow), `CODE_OF_CONDUCT.md`, `SECURITY.md` (supported versions, private advisories), issue forms and a pull request template.

## 0.7.2 (2026-10-22)

- **A lone high-risk card still asks twice.** It used to open straight at the final yes/no, so one yes released it, unlike the same action offered beside other cards. Now every high-risk card is chosen first and then asks a second, separately nonced yes; a "no" still cancels at once. To open a set at the final question on purpose, pass `stage="final"` to `propose`.

## 0.7.1 (2026-10-22)

- **More time on the approval page**: a signed device route, `POST /device/more-time {"pending_id"}`, calls `Gate.extend` for the device's own user (404 for an unknown set or another user's, 409 for one no longer waiting or out of more time, 200 with `expires_in_s` and `extensions_left`, never the nonce). The built-in `/approve` page shows a live countdown with a large, keyboard-reachable **More time** button beside it, hidden once a set has no more time left, and a polite warning at 30 s: "30 seconds left. Choose More time to keep these cards." The route is not an MCP tool.

## 0.7.0 (2026-10-22)

- **More time** (WCAG 2.2.1 Timing Adjustable): `Gate.extend(pending_id, *, user_id, seconds=None)` moves a waiting card set's deadline to now + its own time (120 s, or 60 s for the second yes; never earlier than it was), at most `Gate.max_extensions` (10) times per set. It refuses another user's set as unknown (`KeyError`) and an answered, withdrawn or expired one (`NotWaiting`), returns the public view with `extensions_left` (never the nonce), and emits an `extended` event. It is not an MCP tool of `GatedServer` or `wrap_upstream`.
- **`Store.update(kind, key, change)`**: a compare-and-swap rewrite, retried on conflict, in every store. A write landing between its read and its write (a nonce consumed) is never lost, and DynamoDB's one-time markers survive it.

## 0.6.1 (2026-10-22)

- The `evidence` event carries the tap's `input` (touch, key or switch), so an activity log can say how the user answered.

## 0.6.0 (2026-10-22)

- **Pairing codes made by a device** (`start_pairing(user, minted_by=device_id)`) stop working if that device is unpaired first, and the new device records `paired_via`.
- **Consent parser:** negated auxiliaries ("wouldn't", "can't", "cannot"...) are a no; a question is still a question after a filler or a reflexive yes ("um, is two the fan"); a polite request ("can I have two") is not a question; "the number one" and "the option one" choose one.
- `/device/evidence` answers 400 when `input` is not a string.

## 0.5.0 (2026-10-22)

- **Receipts record how the user answered on the device:** `submit_tap(..., input="touch" | "key" | "switch")` (and `input` on `/device/evidence`); the receipt's `how.input` and `evidence.input` name it, and `to_text` says "your tap", "your key press" or "your switch". An unknown input is refused.
- **Consent parser**, from a 7,093-case stress test: a transcript that starts like a question ("is two the fan", "kya do number…") chooses nothing, since recognisers drop the "?"; in "the/that ___ one" the "one" is a pronoun, never option one ("the to one"); "go ahead" is a yes as a phrase.

## 0.4.1 (2026-10-22)

- **An Agent Skill**, `skills/add-confirm-gate/SKILL.md` (the open SKILL.md format): it teaches a coding agent to gate an MCP server, from choosing the proxy or `GatedServer` to checking that the agent's own `confirm` is refused.

## 0.4.0 (2026-10-22)

For gating a server you didn't write.

- **Strict mode.** `wrap_upstream(..., trust_annotations=False)` and `confirm-gate serve --strict` ignore the upstream's `readOnlyHint`: only the tools you list as reads pass through, and everything else waits for the user. A server that marks a delete as read-only no longer slips past the gate.
- **Glob patterns** in `reads`, `writes` and `high_risk` (`--reads "get_*,list_*" --high-risk "delete_*"`).
- **The approval page shows a wrapped tool's call readably:** the tool's name, then each argument on its own line, wrapped instead of running off the card. A lone high-risk card is labelled as the final check it is, rather than "asks twice".
- **`examples/strands_agent.py`:** a Strands agent on Amazon Bedrock working through the gate.
- CI builds the wheel, runs `twine check`, and installs it to run the command.

## 0.3.1 (2026-10-21)

Third adversarial review, of 0.3.0 itself.

- **Pairing budgets per source.** `pair(code, source=...)` takes one unit of a per-source budget (`max_attempts`, 5) and one of a global ceiling (`global_max_attempts`, 30) per window, so one guesser can no longer lock out the owner, and guessing from many addresses is still bounded. `device_routes(client_ip=...)` names the caller behind a proxy.
- **`DynamoStore.set_once` never loses a concurrent write** to another field of the same document (an optimistic `doc = :old` condition, re-read on conflict). Before, a revocation landing between the read and the write could be dropped.
- **`set_transcriber` is a one-time conditional write**, so it can never undo a revocation; idempotent for the same recogniser.
- **Consent:** on the final card only the card's fixed words may be said back, never its quoted free text (a message the agent wrote) or an unreadable word. Every question-shaped phrase counts, not just the first ("yes do it, is it the front door" is unclear). More hedges ("never mind", "scratch that", "rehne do", "chhodo", "sorry", "I suppose") and time phrases ("two seconds", "bas do second", "another second").

## 0.3.0 (2026-10-21)

Second adversarial review. The core guarantee still held; these close the gaps around it.

- **Pairing lockout cannot be reset.** Every attempt takes one unit of a shared, time-windowed budget (an atomic counter) *before* the code is looked up, so parallel guesses cannot outrun it, and a right code gives its unit back. `start_pairing` no longer resets anything. New `DeviceRegistry.attempt_window`.
- **`DeviceRegistry.issue(user_id)`** creates a device directly, for callers that already know the user (an operator terminal, a fresh sandbox user), with no pairing code.
- **Store gains `add(kind, key, amount, expires_at=)`**: an atomic counter (DynamoDB `ADD`, a SQLite upsert, a lock in memory). *Breaking for custom `Store` implementations.*
- **Transcript evidence only from voice devices.** A device must be paired with `start_pairing(user, transcriber="…")` (or marked with `set_transcriber`) to send its own transcripts, and the receipt names that recogniser, never one the request chose. `submit_transcript` no longer takes `source`. *Breaking.*
- **Consent parser:** "a second" / "ek second" / "just a second" are time, not option 2. Hedges (maybe, probably, actually, I think, shayad, sochne do, ruk, thehro, kal, and their Devanagari forms) are undecided. On the final (high-risk) card no unknown word is allowed, except the card's own words ("yes, unlock the front door").
- **`pending_status`** answers only for the caller's own card sets.

## 0.2.0 (2026-10-21)

Security hardening after an independent adversarial review. The core guarantee held: no write executed without the user's own evidence.

- **Consent parser:** hesitations, questions and qualifiers are never a yes. For example "ok hang on", "okay, who is it", "yes but", and in Hinglish "haan kaun hai". Unreadable Devanagari words count as unknown words instead of disappearing.
- **Transcript evidence** is deduplicated per card set.
- **Voice evidence** over `max_audio_bytes` (default 2 MB) is refused with 413 before decoding.
- **`DynamoStore`** reads one user's rows through a `user-kind-index` (partition user, sort kind), with fallbacks for older or backfilling tables. Rows get a TTL 30 days after they expire or happen.
- **`SqliteStore`** has a per-user column and index, and `purge()` removes rows 30 days past their time.
- **`Gate.device_view`** reads one open card set consistently. **`Gate.withdraw_open`** closes a user's open sets without running anything.
- **`Gate.submit_transcript`** accepts evidence from paired voice devices that recognise speech themselves.
- **`confirm`** results carry `timing.gate_ms` and `timing.action_ms`.

## 0.1.0 (2026-10-20)

First release:

- pending card sets with device-only one-time nonces;
- tap and voice evidence;
- atomic one-time consumption on memory, SQLite and DynamoDB;
- receipts;
- `GatedServer` and `wrap_upstream`;
- `confirm-gate serve` with an approval page.
