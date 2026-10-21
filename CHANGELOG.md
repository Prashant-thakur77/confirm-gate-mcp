# Changelog

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
