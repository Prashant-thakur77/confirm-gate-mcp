## What and why

<!-- What this changes, and the problem it solves. Link the issue if there is one. -->

## Checklist

- [ ] `pytest -q` passes.
- [ ] `ruff check .` passes.
- [ ] If this touches the gate (gate, evidence, device routes, consent, stores, proxy, receipts, pairing), it starts with a test in `tests/` that fails without the change. Name it here:
- [ ] No nonce, device key or signature reaches an MCP result, a log, an event or a receipt.
- [ ] `CHANGELOG.md` has a line under `## Unreleased` if a user would notice the change.
- [ ] `README.md` and `docs/API.md` match any changed function, flag or receipt field.
