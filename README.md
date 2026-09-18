# headofcontext-client

[![ci](https://github.com/headofcontext/sdk-python/actions/workflows/ci.yml/badge.svg)](https://github.com/headofcontext/sdk-python/actions/workflows/ci.yml)
[![pypi](https://img.shields.io/pypi/v/headofcontext-client.svg)](https://pypi.org/project/headofcontext-client/)
[![license](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
![python](https://img.shields.io/badge/python-3.11%2B-blue.svg)

The Python client for [HeadOfContext](https://github.com/headofcontext/headofcontext), the
authorization layer for teams where everyone builds agents: each agent inherits the rights of
the person behind it, never more, under one principal chain, for READ, ACT, DELEGATE and
REMEMBER.

The client is thin on purpose. It depends on `httpx` only, it carries tokens to the service and
returns the service's decisions, and it never decides anything itself. It is written against
[`contract/openapi.json`](contract/openapi.json), the OpenAPI contract published by the service.

## Install

```bash
pip install headofcontext-client
```

Python 3.11 or later. The package is typed (`py.typed`) and entirely async.

## Quick start

```python
import os

from headofcontext_client import HeadOfContext, KeycloakClientCredentials, scope

# The agent authenticates with its own OAuth 2.0 client credentials.
agent = KeycloakClientCredentials(
    issuer="https://keycloak.example.com/realms/acme",
    client_id="assistant",
    client_secret=os.environ["ASSISTANT_SECRET"],
)

async with HeadOfContext("https://headofcontext.example.com", agent_token=agent) as hoc:
    # The user is present: a session bound to (user, this agent, scope).
    session = await hoc.issue(
        user_access_token, scope(read=["document:*", "memory:*"], act=["tool:mail.*"])
    )

    # READ: keep only what the user may see, before anything reaches the model.
    hits = await session.filter(index_results, id_of=lambda h: h["document_id"])

    # ACT: authorize a tool call under the user's identity.
    result = await session.gate("tool:mail.send", {"to": "bob@acme.example"})
    if result.allowed:
        send_mail(...)
    elif result.pending:
        park(result.approval.request_id)

    # DELEGATE: hand a narrower token to a sub-agent with its own credentials.
    child = await session.delegate("agent:mailer", scope(act=["tool:mail.send"]))

    # REMEMBER: memory with provenance, recalled only by those who may read its sources.
    await session.remember("Summary…", derived_from=["document:hr-1"])
    memories = await session.recall("salary grid")
```

`agent_token` accepts a `KeycloakClientCredentials`, a `StaticToken`, a raw string, or any object
with an `async def token() -> str` method (the `TokenProvider` protocol).

## Results and errors

A denial is a result, never an exception: `Decision.outcome` is `"ALLOW"`, `"DENY"` or
`"REQUIRE_APPROVAL"`, with the service's `reason`. Only failures raise:

| Exception        | When                                                          |
| ---------------- | ------------------------------------------------------------- |
| `Unauthorized`   | The agent or user credentials are not accepted.               |
| `Forbidden`      | A token problem: `scope_escalation`, `token_revoked`, `mandate_revoked`, `approver_not_authorized`. |
| `InvalidRequest` | The request does not match the contract.                      |
| `Unavailable`    | The service cannot answer.                                    |

Every exception carries the service's `reason` code. Tokens never appear in exception messages
or logs, only their fingerprints.

## Guarding tool functions

`ToolGuard` wraps a tool so every call goes through `gate` first, with the bound arguments as
the call's arguments. Denials become a message the model can read, or an exception with
`on_deny="raise"`.

```python
from headofcontext_client import ToolGuard

guard = ToolGuard(session, tool_map={"send_mail": "tool:mail.send"})
send_mail = guard.wrap_async("send_mail", send_mail)  # or guard.wrap for a sync function

await send_mail(to="bob@acme.example")
# "HeadOfContext denied tool 'send_mail': scope_escalation. Do not retry."
```

## Approvals

A gated tool can answer `REQUIRE_APPROVAL`: the call is parked under a `request_id`, bound to
the hash of its arguments. A human resolves it with their own access token, then the agent
redeems it once, with the same tool and arguments.

```python
result = await session.gate("tool:hr.export", {"year": 2026})
request_id = result.approval.request_id  # result.pending is True

for req in await hoc.pending_approvals(manager_access_token):
    ...
await hoc.resolve_approval(manager_access_token, request_id, approved=True, reason="ok")

decision = await session.redeem(request_id, "tool:hr.export", {"year": 2026})
# ALLOW once, then DENY with reason "approval_consumed".
```

The approver must hold `tool:<id>#approver` in the service's model, directly or through a group.
`pending_approvals` lists only those requests, and resolving another one raises `Forbidden` with
reason `approver_not_authorized`.

## Mandates

A mandate is a standing authorization from a human to an agent: created while the human is
present, used by the agent alone afterwards, revoked in one call. The subject stays the human;
every token issued under the mandate is a subset of its scope and dies with it.

```python
# The human, once.
mandate = await hoc.create_mandate(
    user_access_token,
    "agent:assistant",
    scope(act=["tool:mail.*"]),
    expires_in_hours=48,
    max_token_ttl_minutes=15,
)
await hoc.list_mandates(user_access_token)

# The agent, later, with its own client credentials only.
session = await hoc.issue_from_mandate(mandate.mandate_id, scope(act=["tool:mail.send"]))

# The human, when done. Tokens already issued are dead before the next decision;
# a new issue raises Forbidden with reason "mandate_revoked".
await hoc.revoke_mandate(user_access_token, mandate.mandate_id, reason="done")
```

## Tokens

- `session.delegate(to_actor, scope)`, or `hoc.attenuate(token, to_actor, scope)` on a raw
  token, returns a narrower token for a sub-agent. A wider scope raises `Forbidden` with reason
  `scope_escalation`. The sub-agent uses it with its own credentials: `mailer.gate(child.token, ...)`.
- `session.revoke()`, or `hoc.revoke(token)`, invalidates that token and everything attenuated
  from it. Later calls raise `Forbidden` with reason `token_revoked`. Revoking a token never
  revokes the mandate it was issued under.
- `hoc.inspect(token)` returns the principal chain without deciding anything.

## Probes

- `await hoc.health()` is liveness: `{"status": "ok", ...}` while the process serves requests.
- `await hoc.ready()` is readiness: `{"status": "ready" | "not_ready", "checks": {"postgres":
  "ok", "openfga": "ok", "connectors": "fresh", "schema": "ok"}}`. A `not_ready` answer (HTTP
  503) is returned as data, not raised, so you can read which check fails.

## Compatibility

`contract/openapi.json` is the contract of the service commit recorded in `contract/core-ref`.
The client works with any service that serves that contract, and CI runs `tests/contract`
against exactly that commit. When the service API changes, `scripts/sync_contract.sh` copies
the new contract and pins the new commit.

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src
uv run pytest tests/unit
```

The contract tests run against a live service with the core's ACME fixtures and are skipped
otherwise:

```bash
HOC_API_URL=http://localhost:8000 HOC_KEYCLOAK_URL=http://localhost:8180 uv run pytest tests/contract
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the rules and [SECURITY.md](SECURITY.md) for how
to report a vulnerability. Releases follow Conventional Commits; the notes are on the
[releases page](https://github.com/headofcontext/sdk-python/releases).

## License

Apache 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
