# Security

confirm-gate-mcp is a security control: its job is to make sure that nothing the agent can reach can release a write. A way around that is the most important kind of bug this project can have, and it should be reported privately.

## Supported versions

Only the latest minor release gets security fixes. Older versions don't, so upgrade to get one.

| version | supported |
|---|---|
| latest 0.x minor (see [releases](https://github.com/Prashant-thakur77/confirm-gate-mcp/releases)) | yes |
| anything older | no |

## Reporting a vulnerability

Report it through a [GitHub private security advisory](https://github.com/Prashant-thakur77/confirm-gate-mcp/security/advisories/new). Please don't open a public issue, pull request or discussion about it.

Include what you can:

- the version or commit;
- the store (`MemoryStore`, `SqliteStore` or `DynamoStore`) and how the gate is run (`confirm-gate serve`, `wrap_upstream`, `GatedServer`);
- the steps, ideally as a failing test in the style of `tests/test_safety.py`;
- what an attacker gains.

The maintainer will confirm receipt, agree a fix and a disclosure date with you, and credit you in the advisory if you'd like that.

## In scope

- A write that runs without evidence from the user's own paired device.
- A nonce, device key or signature that reaches the agent, an MCP result, a log or a receipt.
- Replays: evidence, a signed device request, a voice recording or a nonce used twice.
- A pairing code that can be guessed or brute-forced past its budgets.
- The consent parser deciding "yes" or a card number from words that don't clearly mean it.
- The arguments that run differing from the card the user chose.
- A receipt that can be edited without `verify_fingerprint` noticing.

## Out of scope

These are documented limits, listed under "What it does not protect against" in the README:

- what a read tool returns to the agent;
- a paired device that is itself compromised;
- what a compromised upstream server does with a call it was allowed to make;
- `confirm-gate serve` exposed over plain HTTP beyond localhost.
