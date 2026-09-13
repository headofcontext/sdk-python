"""A fake HeadOfContext service over httpx.MockTransport, shaped by contract/openapi.json."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from headofcontext_client import HeadOfContext

CHAIN = {
    "subject": "user:alice",
    "actor": "agent:assistant",
    "root_actor": "agent:assistant",
    "depth": 0,
    "scope": {"capabilities": [{"kind": "read", "resource": "document:*"}]},
    "delegation": [],
}
DECISION = {
    "outcome": "ALLOW",
    "reason": "allowed",
    "decision_id": "d1",
    "resource": "tool:mail.send",
    "timestamp": "2026-09-07T12:00:00+00:00",
    "engine_latency_ms": 1.5,
}


MANDATE = {
    "mandate_id": "m1",
    "subject": "user:alice",
    "agent": "agent:assistant",
    "scope": {"capabilities": [{"kind": "act", "resource": "tool:mail.*"}]},
    "status": "active",
    "created_at": "2026-09-08T09:00:00+00:00",
    "expires_at": "2026-09-10T09:00:00+00:00",
    "max_token_ttl_minutes": 15,
    "created_by": "user:alice",
    "revoked_at": None,
    "revocation_reason": None,
}


class FakeService:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
        self.responses: dict[str, tuple[int, Any]] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append(
            (request.method, request.url.path, body, request.headers.get("Authorization"))
        )
        if request.url.path in self.responses:
            status, payload = self.responses[request.url.path]
            return httpx.Response(status, json=payload)
        return self._default(request.url.path, body)

    def _default(self, path: str, body: dict[str, Any] | None) -> httpx.Response:
        if path.endswith("/tokens/issue") or path.endswith("/tokens/attenuate"):
            return httpx.Response(
                200,
                json={
                    "token": "b64.token.value",
                    "chain": CHAIN,
                    "expires_at": "2026-09-07T13:00:00+00:00",
                    "revocation_ids": ["r1"],
                },
            )
        if path.endswith("/tokens/inspect") or path.endswith("/tokens/revoke"):
            return httpx.Response(
                200,
                json={
                    "chain": CHAIN,
                    "expires_at": "2026-09-07T13:00:00+00:00",
                    "revocation_ids": ["r1"],
                },
            )
        if path.endswith("/read/filter"):
            ids = [i["id"] for i in (body or {})["items"]]
            kept = [i for i in ids if isinstance(i, str) and i.endswith("ok")]
            return httpx.Response(
                200,
                json={
                    "kept": kept,
                    "dropped": [i or "invalid" for i in ids if i not in kept],
                    "strategy": "batch",
                },
            )
        if path.endswith("/actions/gate"):
            return httpx.Response(200, json={"decision": DECISION, "approval": None})
        if path.endswith("/actions/redeem"):
            return httpx.Response(200, json=DECISION)
        if path.endswith("/mandates") or "/mandates/" in path:
            return httpx.Response(
                200,
                json=MANDATE if not path.endswith("/mandates") or body else {"mandates": [MANDATE]},
            )
        if path.endswith("/approvals"):
            return httpx.Response(200, json={"requests": []})
        if path.endswith("/memory/remember"):
            return httpx.Response(
                200,
                json={
                    "memory_id": "memory:m1",
                    "content": (body or {})["content"],
                    "derived_from": (body or {})["derived_from"],
                    "written_for": "user:alice",
                    "written_by": "agent:assistant",
                    "created_at": None,
                },
            )
        if path.endswith("/memory/recall"):
            return httpx.Response(200, json={"memories": []})
        if path.endswith("/memory/forget"):
            return httpx.Response(
                200,
                json={
                    "memory_id": (body or {})["memory_id"],
                    "content": "",
                    "derived_from": [],
                    "written_for": "user:alice",
                    "written_by": "agent:assistant",
                    "created_at": None,
                },
            )
        if path.endswith("/health"):
            return httpx.Response(
                200, json={"status": "ok", "service": "headofcontext", "connectors": "fresh"}
            )
        if path.endswith("/ready"):
            return httpx.Response(
                200,
                json={
                    "status": "ready",
                    "checks": {
                        "postgres": "ok",
                        "openfga": "ok",
                        "connectors": "fresh",
                        "schema": "ok",
                    },
                },
            )
        return httpx.Response(404, json={"reason": "not_found", "detail": path})


@pytest.fixture
def service() -> FakeService:
    return FakeService()


@pytest.fixture
async def client(service: FakeService) -> HeadOfContext:
    hoc = HeadOfContext(
        "http://hoc.test",
        agent_token="agent-bearer",
        transport=httpx.MockTransport(service.handler),
    )
    yield hoc  # type: ignore[misc]
    await hoc.aclose()
