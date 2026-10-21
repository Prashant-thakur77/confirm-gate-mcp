# Changelog

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
