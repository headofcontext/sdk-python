# headofcontext-client

Thin Python client for the [HeadOfContext](https://github.com/headofcontext/headofcontext) service:
the single authorization layer for enterprise AI agents (READ, ACT, DELEGATE, REMEMBER).

```bash
pip install headofcontext-client
```

```python
from headofcontext_client import HeadOfContext, KeycloakClientCredentials, scope

agent = KeycloakClientCredentials(
    issuer="https://keycloak.example.com/realms/acme",
    client_id="assistant",
    client_secret=os.environ["ASSISTANT_SECRET"],
)
hoc = HeadOfContext("https://headofcontext.example.com", agent_token=agent)

# The user is present: get a root biscuit for (user, this agent, scope).
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

# DELEGATE: hand a narrower token to a sub-agent (its own client credentials).
child = await session.delegate("agent:mailer", scope(act=["tool:mail.send"]))

# REMEMBER: provenance-aware memory.
await session.remember("Summary…", derived_from=["document:hr-1"])
memories = await session.recall("salary grid")
```

Denials are results (`outcome == "DENY"`), never exceptions. Authentication and token failures
raise `Unauthorized` / `Forbidden`; an unavailable service raises `Unavailable`.

## Approvals

A gated tool can answer `REQUIRE_APPROVAL`: the call is parked under a `request_id`, bound to
the hash of its arguments. A human resolves it with their own access token, then the agent
redeems it once, with the same tool and arguments.

```python
# The agent parks the call.
result = await session.gate("tool:hr.export", {"year": 2026})
request_id = result.approval.request_id          # result.pending is True

# A human lists and resolves what they may approve.
for req in await hoc.pending_approvals(manager_access_token):
    ...
await hoc.resolve_approval(manager_access_token, request_id, approved=True, reason="ok")

# The agent redeems: ALLOW once, then DENY with reason "approval_consumed".
decision = await session.redeem(request_id, "tool:hr.export", {"year": 2026})
```

Since ADR 0017 the approver must hold `tool:<id>#approver` in OpenFGA (directly or through a
group); `pending_approvals` only lists those requests, and resolving another one raises
`Forbidden` with reason `approver_not_authorized`. The subject may approve their own request
only when the service runs with `HOC_ALLOW_SELF_APPROVAL=true`.

## Mandates

A mandate is a standing authorization from a human to an agent (ADR 0016): created while the
human is present, used by the agent alone afterwards, revoked in one call. The subject stays
the human; every token issued under the mandate is a subset of its scope and dies with it.

```python
# The human, once.
mandate = await hoc.create_mandate(
    user_access_token, "agent:assistant", scope(act=["tool:mail.*"]),
    expires_in_hours=48, max_token_ttl_minutes=15,
)
await hoc.list_mandates(user_access_token)

# The agent, later, with its own client credentials only.
session = await hoc.issue_from_mandate(mandate.mandate_id, scope(act=["tool:mail.send"]))

# The human, when done: tokens already issued are dead before the next decision,
# and a new issue raises Forbidden with reason "mandate_revoked".
await hoc.revoke_mandate(user_access_token, mandate.mandate_id, reason="done")
```

## Tokens

`session.delegate(to_actor, scope)` (`hoc.attenuate` on a raw token) returns a narrower token
for a sub-agent; a wider scope raises `Forbidden` with reason `scope_escalation`. The
sub-agent uses it with its own credentials: `mailer.gate(child.token, ...)`.
`session.revoke()` (`hoc.revoke(token)`) invalidates that token and everything attenuated from
it; later calls raise `Forbidden` with reason `token_revoked`. Revoking a token never revokes
the mandate it was issued under. `hoc.inspect(token)` returns the chain without deciding
anything.

## Probes

`await hoc.health()` is liveness: `{"status": "ok", ...}` while the process serves requests.
`await hoc.ready()` is readiness (ADR 0020): `{"status": "ready" | "not_ready",
"checks": {"postgres": "ok", "openfga": "ok", "connectors": "fresh", "schema": "ok"}}`. A
`not_ready` answer (HTTP 503) is returned as data, not raised, so you can read which check
fails.

The client depends on `httpx` only and is written against `contract/openapi.json`.
