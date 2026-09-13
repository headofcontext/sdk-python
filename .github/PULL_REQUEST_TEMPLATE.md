<!--
The title is the commit on main and the release-note line: a Conventional Commit
(`feat(scope): …`, `fix(scope): …`, `docs: …`, `chore: …`; `feat!:` for a breaking change).
Describe the why in a line or two, the what as a short list.
-->

## Why

## What

## Checklist

- [ ] Unit test over `httpx.MockTransport` for every method touched
- [ ] Contract test in `tests/contract` if the change follows a service change
- [ ] `contract/openapi.json` synced with `scripts/sync_contract.sh`, never edited by hand
- [ ] No token in a log line or an exception message, fingerprints only
