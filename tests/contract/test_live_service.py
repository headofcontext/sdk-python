"""Contract tests against a live HeadOfContext service with the ACME realm and fixtures.

    HOC_API_URL=http://localhost:8000 HOC_KEYCLOAK_URL=http://localhost:8180 \
        uv run pytest tests/contract
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from headofcontext_client import Forbidden, HeadOfContext, KeycloakClientCredentials, scope

API = os.environ.get("HOC_API_URL")
KC = os.environ.get("HOC_KEYCLOAK_URL")
pytestmark = pytest.mark.skipif(
    not API or not KC, reason="HOC_API_URL and HOC_KEYCLOAK_URL not set"
)

# ACME fixtures from the core checkout (sibling by default); the literals are the same
# people and document as of the fixtures' current generation, for a run without the checkout.
FIXTURES = Path(
    os.environ.get(
        "HOC_ACME_FIXTURES",
        str(Path(__file__).resolve().parents[2] / ".." / "headofcontext/fixtures/acme/generated"),
    )
)
KNOWN_USERS = {"rh": "samir.vincent", "direction": "alice.martin", "magasin-lille": "ines.leroy"}
KNOWN_DOCS = {("internal", "rh"): "document:acme-0020"}

RH_USER = os.environ.get("HOC_CONTRACT_USER", KNOWN_USERS["rh"])
STORE_USER = os.environ.get("HOC_CONTRACT_OTHER_USER", KNOWN_USERS["magasin-lille"])
HR_DOC = os.environ.get("HOC_CONTRACT_HR_DOC", KNOWN_DOCS[("internal", "rh")])

FULL_SCOPE = scope(
    read=["document:*", "memory:*"], remember=["document:*", "memory:*"], act=["tool:*"]
)


def _fixture(name: str) -> list[dict[str, Any]] | None:
    path = FIXTURES / name
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    assert isinstance(data, list)
    return data


def username_of(department: str) -> str:
    """A non-intern member of the department, from the fixtures when they are at hand."""
    users = _fixture("users.json")
    if users is None:
        return KNOWN_USERS[department]
    return str(
        next(u["username"] for u in users if u["department"] == department and not u["intern"])
    )


def document_of(confidentiality: str, department: str) -> str:
    docs = _fixture("documents.json")
    if docs is None:
        return KNOWN_DOCS[(confidentiality, department)]
    return str(
        next(
            d["id"]
            for d in docs
            if d["confidentiality"] == confidentiality and d["department"] == department
        )
    )


def user_token(username: str) -> str:
    response = httpx.post(
        f"{KC}/realms/acme/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "headofcontext",
            "client_secret": "hoc-dev-secret",
            "username": username,
            "password": "password",
            "scope": "openid",
        },
        timeout=10,
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


@pytest.fixture
async def hoc() -> HeadOfContext:
    agent = KeycloakClientCredentials(
        issuer=f"{KC}/realms/acme", client_id="assistant", client_secret="assistant-dev-secret"
    )
    client = HeadOfContext(str(API), agent_token=agent)
    yield client  # type: ignore[misc]
    await client.aclose()


async def test_health(hoc: HeadOfContext) -> None:
    assert (await hoc.health())["status"] == "ok"


async def test_ready(hoc: HeadOfContext) -> None:
    """ADR 0020: the running stack is ready and every dependency is named."""
    body = await hoc.ready()
    assert body["status"] == "ready", body
    assert set(body["checks"]) >= {"postgres", "openfga", "connectors", "schema"}


async def test_issue_filter_gate(hoc: HeadOfContext) -> None:
    rh = await hoc.issue(user_token(RH_USER), scope(read=["document:*"], act=["tool:*"]))
    store = await hoc.issue(user_token(STORE_USER), scope(read=["document:*"], act=["tool:*"]))
    assert rh.chain.subject == f"user:{RH_USER}"
    assert (await rh.filter([{"id": HR_DOC}])).kept == [{"id": HR_DOC}]
    assert (await store.filter([{"id": HR_DOC}])).kept == []
    assert (await rh.gate("tool:mail.send", {})).allowed


async def test_delegation_and_revocation(hoc: HeadOfContext) -> None:
    rh = await hoc.issue(user_token(RH_USER), scope(act=["tool:*"]))
    child = await rh.delegate("agent:mailer", scope(act=["tool:mail.*"]))
    mailer = HeadOfContext(
        str(API),
        agent_token=KeycloakClientCredentials(
            issuer=f"{KC}/realms/acme", client_id="mailer", client_secret="mailer-dev-secret"
        ),
    )
    try:
        assert (await mailer.gate(child.token, "tool:mail.send", {})).allowed
        with pytest.raises(Forbidden):
            await mailer.gate(child.token, "tool:finance.report", {})
        await mailer.revoke(child.token, "done")
        with pytest.raises(Forbidden) as exc:
            await mailer.gate(child.token, "tool:mail.send", {})
        assert exc.value.reason == "token_revoked"
    finally:
        await mailer.aclose()
    assert (await rh.gate("tool:mail.send", {})).allowed


async def test_mandate_lifecycle(hoc: HeadOfContext) -> None:
    """ADR 0016: the human mandates once, the agent issues alone, one call revokes."""
    human = user_token(username_of("direction"))
    mandate = await hoc.create_mandate(
        human,
        "agent:assistant",
        scope(act=["tool:mail.*"]),
        expires_in_hours=1,
        max_token_ttl_minutes=15,
    )
    assert mandate.active and mandate.subject == f"user:{username_of('direction')}"
    assert mandate.mandate_id in {m.mandate_id for m in await hoc.list_mandates(human)}

    session = await hoc.issue_from_mandate(mandate.mandate_id, scope(act=["tool:mail.send"]))
    assert session.chain.subject == mandate.subject
    assert any(r.startswith("mandate:") for r in session.issued.revocation_ids)
    assert (await session.gate("tool:mail.send", {"to": "x"})).allowed

    revoked = await hoc.revoke_mandate(human, mandate.mandate_id, reason="done")
    assert revoked.status == "revoked"
    with pytest.raises(Forbidden) as dead:
        await session.gate("tool:mail.send", {"to": "x"})
    assert dead.value.reason == "token_revoked"
    with pytest.raises(Forbidden) as again:
        await hoc.issue_from_mandate(mandate.mandate_id, scope(act=["tool:mail.send"]))
    assert again.value.reason == "mandate_revoked"


async def test_memory_with_provenance(hoc: HeadOfContext) -> None:
    """A memory derived from an HR document is recalled by HR, never by a store employee."""
    rh = await hoc.issue(user_token(username_of("rh")), FULL_SCOPE)
    store = await hoc.issue(user_token(username_of("magasin-lille")), FULL_SCOPE)
    hr_doc = document_of("internal", "rh")
    memory = await rh.remember("Grille salaires 2026: +3 % vendeurs", derived_from=[hr_doc])
    assert memory.derived_from == [hr_doc] and memory.written_for == rh.chain.subject
    try:
        assert [m.memory_id for m in await rh.recall("salaires", limit=5)] == [memory.memory_id]
        assert await store.recall("salaires", limit=5) == []
    finally:
        await rh.forget(memory.memory_id)
    assert memory.memory_id not in {m.memory_id for m in await rh.recall("salaires", limit=5)}


async def test_approval_roundtrip(hoc: HeadOfContext) -> None:
    """ADR 0008 + 0017: parked, resolved by an approver only, redeemed exactly once."""
    rh = await hoc.issue(user_token(username_of("rh")), scope(act=["tool:*"]))
    args = {"year": 2026}
    parked = await rh.gate("tool:hr.export", args)
    assert parked.pending and parked.approval is not None
    request_id = parked.approval.request_id

    stranger = user_token(username_of("magasin-lille"))
    assert request_id not in {r.request_id for r in await hoc.pending_approvals(stranger)}
    with pytest.raises(Forbidden) as refused:
        await hoc.resolve_approval(stranger, request_id, approved=True)
    assert refused.value.reason == "approver_not_authorized"

    boss = user_token(username_of("direction"))
    assert request_id in {r.request_id for r in await hoc.pending_approvals(boss)}
    resolved = await hoc.resolve_approval(boss, request_id, approved=True, reason="ok")
    assert resolved.status == "approved"

    assert (await rh.redeem(request_id, "tool:hr.export", args)).allowed
    replay = await rh.redeem(request_id, "tool:hr.export", args)
    assert replay.outcome == "DENY" and replay.reason == "approval_consumed"
