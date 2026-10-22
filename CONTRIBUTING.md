# Contributing

Thanks for helping. Bug reports, fixes, new stores, docs and examples are all welcome.

## Set up

Python 3.12 or 3.13.

```bash
git clone https://github.com/Prashant-thakur77/confirm-gate-mcp
cd confirm-gate-mcp
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

`pytest -q` runs the whole suite offline in a few seconds. The `DynamoStore` tests run on moto. Five tests are skipped without a real DynamoDB table: two integration tests, and three thread races that moto can't simulate. To run the integration tests, point them at a table you own:

```bash
CONFIRM_GATE_IT_TABLE=my-test-table AWS_REGION=us-east-1 pytest -q tests/test_store.py
```

Lint with [ruff](https://docs.astral.sh/ruff/) before you push: `ruff check .`

To try your change by hand, run `confirm-gate demo` and follow the README's "60 seconds".

## The one rule: a change near the gate starts with a failing test

The gate's promise is that **nothing the agent can reach can release a write**. Any change that touches it starts with a test in `tests/` that fails before your change and passes after. That covers `gate.py`, `evidence.py`, `device.py`, `consent.py`, `store.py`, `proxy.py`, receipts, pairing, nonces and signatures. Put the failing test in the pull request so a reviewer can see it fail on `main`.

The tests check state rather than return values: a test asserts that nothing changed (the note wasn't written, the door is still locked), not just that the reply said "refused".

Never put a nonce or a device key into an MCP result, a log line, an event or a receipt. `tests/test_safety.py` checks this (for example `test_public_view_never_contains_the_nonce`), and any new way for data to leave the gate needs a test like it.

## Pull requests

- Keep each pull request to one change, and say why it's needed.
- Add a line under `## Unreleased` in `CHANGELOG.md` for anything a user would notice.
- If a public function, flag or receipt field changes, update `README.md` and `docs/API.md`.
- `pytest -q` and `ruff check .` pass.

## Releasing (maintainers)

1. Pick the version (semantic versioning; while the version is 0.x, a breaking change bumps the minor version).
2. Set it in `pyproject.toml` (`version`) and in `src/confirm_gate/__init__.py` (`__version__`).
3. In `CHANGELOG.md`, rename `## Unreleased` to `## X.Y.Z (YYYY-MM-DD)`.
4. Commit, tag `vX.Y.Z` and push the tag.
5. Publish a GitHub release for the tag. This runs [`.github/workflows/publish.yml`](.github/workflows/publish.yml), which builds the sdist and wheel, runs `twine check --strict`, and uploads to PyPI with trusted publishing, so no token is stored anywhere. You can also start the workflow by hand from the Actions tab (`workflow_dispatch`).

One-time setup, before the first upload: on pypi.org, add a pending trusted publisher for this repository with the workflow `publish.yml` and the environment `pypi`, and create a `pypi` environment in the repository settings.
