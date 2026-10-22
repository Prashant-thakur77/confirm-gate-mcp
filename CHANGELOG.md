# Changelog

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
