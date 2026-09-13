import httpx
import pytest

from headofcontext_client import (
    Forbidden,
    HeadOfContext,
    HeadOfContextError,
    InvalidRequest,
    Unauthorized,
    Unavailable,
    scope,
)
from tests.unit.conftest import DECISION, FakeService


async def test_issue_sends_scope_and_bearer(client: HeadOfContext, service: FakeService) -> None:
    session = await client.issue("user-jwt", scope(read=["document:*"], act=["tool:mail.*"]))
    method, path, body, auth = service.requests[-1]
    assert (method, path) == ("POST", "/v1/tokens/issue")
    assert auth == "Bearer agent-bearer"
    assert body == {
        "user_token": "user-jwt",
        "scope": {
            "capabilities": [
                {"kind": "read", "resource": "document:*"},
                {"kind": "act", "resource": "tool:mail.*"},
            ]
        },
    }
    assert session.chain.subject == "user:alice" and session.token == "b64.token.value"
    assert "b64.token.value" not in repr(session)


async def test_filter_maps_back_to_objects(client: HeadOfContext, service: FakeService) -> None:
    session = await client.issue("user-jwt", scope(read=["document:*"]))
    hits = [
        {"doc": "document:a-ok", "text": "..."},
        {"doc": "document:secret", "text": "..."},
        {"doc": None},
    ]
    result = await session.filter(hits, id_of=lambda h: h["doc"])
    assert result.kept == [hits[0]]
    assert result.dropped == ["document:secret", "invalid"]
    assert service.requests[-1][2]["items"] == [
        {"id": "document:a-ok"},
        {"id": "document:secret"},
        {"id": None},
    ]


async def test_gate_and_redeem(client: HeadOfContext, service: FakeService) -> None:
    session = await client.issue("user-jwt", scope(act=["tool:*"]))
    result = await session.gate("tool:mail.send", {"to": "bob"})
    assert result.allowed and not result.pending
    decision = await session.redeem("req-1", "tool:mail.send", {"to": "bob"})
    assert decision.allowed and decision.decision_id == "d1"
    assert service.requests[-1][2]["request_id"] == "req-1"


async def test_pending_approval(client: HeadOfContext, service: FakeService) -> None:
    service.responses["/v1/actions/gate"] = (
        200,
        {
            "decision": {**DECISION, "outcome": "REQUIRE_APPROVAL", "reason": "approval_required"},
            "approval": {
                "request_id": "req-9",
                "subject": "user:alice",
                "actor": "agent:assistant",
                "delegation_depth": 0,
                "tool": "tool:payment.send",
                "args_hash": "h",
                "status": "pending",
                "created_at": "2026-09-07T12:00:00+00:00",
                "expires_at": "2026-09-07T13:00:00+00:00",
                "approval_reason": "approval_required",
            },
        },
    )
    session = await client.issue("user-jwt", scope(act=["tool:*"]))
    result = await session.gate("tool:payment.send", {"amount": 5})
    assert result.pending and result.approval is not None and result.approval.request_id == "req-9"


async def test_approvals_use_the_user_token(client: HeadOfContext, service: FakeService) -> None:
    await client.pending_approvals("user-jwt")
    assert service.requests[-1][3] == "Bearer user-jwt"
    service.responses["/v1/approvals/req-1/resolve"] = (
        200,
        {
            "request_id": "req-1",
            "subject": "user:alice",
            "actor": "agent:assistant",
            "delegation_depth": 0,
            "tool": "tool:x",
            "args_hash": "h",
            "status": "approved",
            "created_at": None,
            "expires_at": None,
            "resolved_by": "user:carol",
            "approval_reason": "approval_required",
        },
    )
    approval = await client.resolve_approval("carol-jwt", "req-1", approved=True, reason="ok")
    assert approval.status == "approved" and service.requests[-1][2] == {
        "approved": True,
        "reason": "ok",
    }


async def test_memory_roundtrip(client: HeadOfContext, service: FakeService) -> None:
    session = await client.issue("user-jwt", scope(read=["memory:*"], remember=["document:*"]))
    memory = await session.remember("Summary", derived_from=["document:hr-1"])
    assert memory.memory_id == "memory:m1" and memory.derived_from == ["document:hr-1"]
    assert await session.recall("summary") == []
    await session.forget(memory.memory_id)
    assert service.requests[-1][1] == "/v1/memory/forget"


async def test_delegate_and_revoke(client: HeadOfContext, service: FakeService) -> None:
    session = await client.issue("user-jwt", scope(act=["tool:*"]))
    child = await session.delegate("agent:mailer", scope(act=["tool:mail.*"]))
    assert service.requests[-1][2]["to_actor"] == "agent:mailer" and child.token
    await session.revoke("done")
    assert service.requests[-1][2] == {"token": "b64.token.value", "reason": "done"}


@pytest.mark.parametrize(
    ("status", "payload", "error"),
    [
        (401, {"reason": "identity_error", "detail": "bad"}, Unauthorized),
        (403, {"reason": "token_revoked", "detail": "revoked"}, Forbidden),
        (422, {"detail": [{"msg": "bad"}]}, InvalidRequest),
        (503, {"reason": "engine_unavailable", "detail": "down"}, Unavailable),
    ],
)
async def test_error_mapping(
    client: HeadOfContext, service: FakeService, status: int, payload: dict, error: type
) -> None:
    service.responses["/v1/tokens/inspect"] = (status, payload)
    with pytest.raises(error) as exc:
        await client.inspect("some-token")
    if status != 422:
        assert exc.value.reason == payload["reason"]


async def test_transport_error_is_unavailable() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    async with HeadOfContext(
        "http://hoc.test", agent_token="t", transport=httpx.MockTransport(down)
    ) as hoc:
        with pytest.raises(Unavailable):
            await hoc.health()


async def test_health(client: HeadOfContext) -> None:
    assert (await client.health())["status"] == "ok"


async def test_ready(client: HeadOfContext, service: FakeService) -> None:
    body = await client.ready()
    assert body["status"] == "ready" and body["checks"]["openfga"] == "ok"
    method, path, _, auth = service.requests[-1]
    assert (method, path, auth) == ("GET", "/v1/ready", None)


async def test_not_ready_is_data_not_an_error(
    client: HeadOfContext, service: FakeService
) -> None:
    """ADR 0020: a 503 with a readiness body is the probe's answer, not a transport failure."""
    service.responses["/v1/ready"] = (
        503,
        {"status": "not_ready", "checks": {"postgres": "ok", "openfga": "error: refused"}},
    )
    body = await client.ready()
    assert body["status"] == "not_ready" and body["checks"]["openfga"].startswith("error:")


async def test_ready_behind_a_dead_proxy_is_not_ready(
    client: HeadOfContext, service: FakeService
) -> None:
    service.responses["/v1/ready"] = (503, "upstream connect error")
    body = await client.ready()
    assert body["status"] == "not_ready" and body["checks"] == {}


async def test_ready_other_errors_still_raise(
    client: HeadOfContext, service: FakeService
) -> None:
    service.responses["/v1/ready"] = (429, {"reason": "rate_limited", "detail": "slow down"})
    with pytest.raises(HeadOfContextError) as exc:
        await client.ready()
    assert exc.value.status == 429 and exc.value.reason == "rate_limited"


async def test_mandates_by_the_human(client: HeadOfContext, service: FakeService) -> None:
    mandate = await client.create_mandate(
        "user-jwt",
        "agent:assistant",
        scope(act=["tool:mail.*"]),
        expires_in_hours=48,
        max_token_ttl_minutes=15,
    )
    method, path, body, auth = service.requests[-1]
    assert (method, path, auth) == ("POST", "/v1/mandates", "Bearer user-jwt")
    assert body == {
        "agent": "agent:assistant",
        "scope": {"capabilities": [{"kind": "act", "resource": "tool:mail.*"}]},
        "expires_in_hours": 48,
        "max_token_ttl_minutes": 15,
    }
    assert mandate.active and mandate.mandate_id == "m1"

    listed = await client.list_mandates("user-jwt")
    assert service.requests[-1][:2] == ("GET", "/v1/mandates")
    assert [m.mandate_id for m in listed] == ["m1"]

    await client.revoke_mandate("user-jwt", "m1", reason="done")
    method, path, body, auth = service.requests[-1]
    assert (method, path, body, auth) == (
        "DELETE",
        "/v1/mandates/m1",
        {"reason": "done"},
        "Bearer user-jwt",
    )


async def test_issue_from_mandate_uses_agent_credentials_only(
    client: HeadOfContext, service: FakeService
) -> None:
    session = await client.issue_from_mandate("m1", scope(act=["tool:mail.send"]), ttl_minutes=10)
    method, path, body, auth = service.requests[-1]
    assert (method, path, auth) == ("POST", "/v1/tokens/issue", "Bearer agent-bearer")
    assert body == {
        "mandate_id": "m1",
        "scope": {"capabilities": [{"kind": "act", "resource": "tool:mail.send"}]},
        "ttl_minutes": 10,
    }
    assert "user_token" not in body and session.token == "b64.token.value"
