# headofcontext-sdk-python — brief

Thin Python client for the HeadOfContext service. Read this before changing anything.

## Rules
- **Only dependency: `httpx`.** Never import `headofcontext` (the core). The service API is the sole boundary.
- **The contract is `contract/openapi.json`**, copied from `headofcontext/docs/openapi.json` at a given core version. Bump it with `scripts/sync_contract.sh`, never edit it by hand.
- The client never decides anything. It transports tokens and returns the service's decisions. A denial is a normal result (`outcome == "DENY"`), an authentication or token failure raises.
- Tokens are never logged or included in exceptions; only fingerprints.
- Code, docstrings, commits in English (Conventional Commits). Python 3.11+, `mypy --strict`, `ruff`.
- Tests: unit tests with `httpx.MockTransport` cover every method; contract tests (`tests/contract`) run against a live service when `HOC_API_URL` and `HOC_KEYCLOAK_URL` are set, and are skipped otherwise.

## Commands
```
uv sync
uv run pytest tests/unit
HOC_API_URL=http://localhost:8000 HOC_KEYCLOAK_URL=http://localhost:8180 uv run pytest tests/contract
uv run ruff check . && uv run mypy --strict src
```
