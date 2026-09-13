"""The client. Every call carries the agent's bearer token; delegated calls carry the biscuit."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

import httpx

from headofcontext_client.auth import StaticToken, TokenProvider
from headofcontext_client.errors import (
    Forbidden,
    HeadOfContextError,
    InvalidRequest,
    Unauthorized,
    Unavailable,
)
from headofcontext_client.models import (
    Approval,
    Decision,
    FilterResult,
    GateResult,
    Inspection,
    IssuedToken,
    Mandate,
    Memory,
)

T = TypeVar("T")
API = "/v1"


def fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def default_id_of(item: Any) -> Any:
    if isinstance(item, dict):
        return item.get("id")
    return getattr(item, "id", None)


class HeadOfContext:
    def __init__(
        self,
        base_url: str,
        *,
        agent_token: TokenProvider | str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._agent = StaticToken(agent_token) if isinstance(agent_token, str) else agent_token
        self._http = httpx.AsyncClient(
            base_url=self._base, transport=transport, timeout=timeout_seconds
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> HeadOfContext:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- tokens --------------------------------------------------------------------------------

    async def issue(
        self, user_token: str, scope: dict[str, Any], *, ttl_minutes: int | None = None
    ) -> Session:
        body: dict[str, Any] = {"user_token": user_token, "scope": scope}
        if ttl_minutes is not None:
            body["ttl_minutes"] = ttl_minutes
        issued = IssuedToken.from_json(await self._post("/tokens/issue", body))
        return Session(self, issued)

    async def issue_from_mandate(
        self, mandate_id: str, scope: dict[str, Any], *, ttl_minutes: int | None = None
    ) -> Session:
        """A session under a standing mandate: no human token needed (ADR 0016)."""
        body: dict[str, Any] = {"mandate_id": mandate_id, "scope": scope}
        if ttl_minutes is not None:
            body["ttl_minutes"] = ttl_minutes
        issued = IssuedToken.from_json(await self._post("/tokens/issue", body))
        return Session(self, issued)

    async def attenuate(self, token: str, to_actor: str, scope: dict[str, Any]) -> IssuedToken:
        return IssuedToken.from_json(
            await self._post(
                "/tokens/attenuate", {"token": token, "to_actor": to_actor, "scope": scope}
            )
        )

    async def inspect(self, token: str) -> Inspection:
        return Inspection.from_json(await self._post("/tokens/inspect", {"token": token}))

    async def revoke(self, token: str, reason: str = "revoked by holder") -> Inspection:
        return Inspection.from_json(
            await self._post("/tokens/revoke", {"token": token, "reason": reason})
        )

    # -- read ----------------------------------------------------------------------------------

    async def filter(
        self,
        token: str,
        items: Sequence[T],
        *,
        id_of: Callable[[T], Any] | None = None,
        relation: str = "viewer",
        strategy: str = "auto",
    ) -> FilterResult[T]:
        """Keep the items whose document the subject may view, in the index's order."""
        extract = id_of or default_id_of
        refs = [extract(item) for item in items]
        payload = [{"id": ref if isinstance(ref, str) else None} for ref in refs]
        data = await self._post(
            "/read/filter",
            {"token": token, "items": payload, "relation": relation, "strategy": strategy},
        )
        kept_refs = set(data.get("kept", []))
        kept = [item for item, ref in zip(items, refs, strict=True) if ref in kept_refs]
        return FilterResult(
            kept=kept, dropped=list(data.get("dropped", [])), strategy=str(data.get("strategy", ""))
        )

    # -- actions -------------------------------------------------------------------------------

    async def gate(self, token: str, tool: str, args: dict[str, Any] | None = None) -> GateResult:
        return GateResult.from_json(
            await self._post("/actions/gate", {"token": token, "tool": tool, "args": args or {}})
        )

    async def redeem(
        self, token: str, request_id: str, tool: str, args: dict[str, Any] | None = None
    ) -> Decision:
        return Decision.from_json(
            await self._post(
                "/actions/redeem",
                {"token": token, "request_id": request_id, "tool": tool, "args": args or {}},
            )
        )

    # -- approvals (human caller) --------------------------------------------------------------

    async def pending_approvals(self, user_token: str) -> list[Approval]:
        data = await self._request("GET", "/approvals", None, bearer=user_token)
        return [Approval.from_json(r) for r in data.get("requests", [])]

    async def resolve_approval(
        self, user_token: str, request_id: str, *, approved: bool, reason: str = ""
    ) -> Approval:
        data = await self._request(
            "POST",
            f"/approvals/{request_id}/resolve",
            {"approved": approved, "reason": reason},
            bearer=user_token,
        )
        return Approval.from_json(data)

    # -- mandates (ADR 0016), by the human -----------------------------------------------------

    async def create_mandate(
        self,
        user_token: str,
        agent: str,
        scope: dict[str, Any],
        *,
        expires_in_hours: int,
        max_token_ttl_minutes: int | None = None,
    ) -> Mandate:
        body: dict[str, Any] = {
            "agent": agent,
            "scope": scope,
            "expires_in_hours": expires_in_hours,
        }
        if max_token_ttl_minutes is not None:
            body["max_token_ttl_minutes"] = max_token_ttl_minutes
        return Mandate.from_json(await self._request("POST", "/mandates", body, bearer=user_token))

    async def list_mandates(self, user_token: str) -> list[Mandate]:
        data = await self._request("GET", "/mandates", None, bearer=user_token)
        return [Mandate.from_json(m) for m in data.get("mandates", [])]

    async def revoke_mandate(
        self, user_token: str, mandate_id: str, *, reason: str = "revoked by subject"
    ) -> Mandate:
        data = await self._request(
            "DELETE", f"/mandates/{mandate_id}", {"reason": reason}, bearer=user_token
        )
        return Mandate.from_json(data)

    # -- memory --------------------------------------------------------------------------------

    async def remember(
        self, token: str, content: str, *, derived_from: Sequence[str] = ()
    ) -> Memory:
        return Memory.from_json(
            await self._post(
                "/memory/remember",
                {"token": token, "content": content, "derived_from": list(derived_from)},
            )
        )

    async def recall(self, token: str, query: str, *, limit: int = 10) -> list[Memory]:
        data = await self._post("/memory/recall", {"token": token, "query": query, "limit": limit})
        return [Memory.from_json(m) for m in data.get("memories", [])]

    async def forget(self, token: str, memory_id: str) -> None:
        await self._post("/memory/forget", {"token": token, "memory_id": memory_id})

    # -- misc ----------------------------------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        """Liveness: the process serves requests."""
        return self._parse(await self._get(f"{API}/health"))

    async def ready(self) -> dict[str, Any]:
        """Readiness (ADR 0020): ``{"status": "ready" | "not_ready", "checks": {name: str}}``.

        A ``not_ready`` answer comes with HTTP 503 and is returned as data, never raised: the
        probe's job is to say which dependency fails. A 503 without a readiness body (a proxy
        answering for a pod that is down) is reported the same way, with no checks.
        """
        response = await self._get(f"{API}/ready")
        if response.status_code != 503:
            return self._parse(response)
        try:
            data = response.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and "status" in data:
            return data
        return {"status": "not_ready", "checks": {}}

    # -- internals -----------------------------------------------------------------------------

    async def _get(self, url: str) -> httpx.Response:
        try:
            return await self._http.get(url)
        except httpx.HTTPError as exc:
            raise Unavailable("transport_error", type(exc).__name__) from exc

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", path, body, bearer=await self._agent.token())

    async def _request(
        self, method: str, path: str, body: dict[str, Any] | None, *, bearer: str
    ) -> dict[str, Any]:
        try:
            response = await self._http.request(
                method, f"{API}{path}", json=body, headers={"Authorization": f"Bearer {bearer}"}
            )
        except httpx.HTTPError as exc:
            raise Unavailable("transport_error", type(exc).__name__) from exc
        return self._parse(response)

    @staticmethod
    def _parse(response: httpx.Response) -> dict[str, Any]:
        if response.status_code == 200:
            data = response.json()
            return data if isinstance(data, dict) else {"value": data}
        reason, detail = "http_error", response.text[:200]
        try:
            payload = response.json()
            if isinstance(payload, dict):
                reason = str(payload.get("reason", reason))
                detail = str(payload.get("detail", detail))
        except ValueError:
            pass
        status = response.status_code
        if status == 401:
            raise Unauthorized(reason, detail, status=status)
        if status == 403:
            raise Forbidden(reason, detail, status=status)
        if status == 422:
            raise InvalidRequest("invalid_request", detail, status=status)
        if status >= 500:
            raise Unavailable(reason, detail, status=status)
        raise HeadOfContextError(reason, detail, status=status)


class Session:
    """A biscuit bound to this client's agent. Every call re-verifies it server-side."""

    def __init__(self, client: HeadOfContext, issued: IssuedToken) -> None:
        self._client = client
        self.issued = issued

    @property
    def token(self) -> str:
        return self.issued.token

    @property
    def chain(self) -> Any:
        return self.issued.chain

    def __repr__(self) -> str:
        chain = self.issued.chain
        return (
            f"Session(subject={chain.subject!r}, actor={chain.actor!r}, "
            f"token=sha256:{fingerprint(self.token)})"
        )

    async def filter(
        self,
        items: Sequence[T],
        *,
        id_of: Callable[[T], Any] | None = None,
        relation: str = "viewer",
    ) -> FilterResult[T]:
        return await self._client.filter(self.token, items, id_of=id_of, relation=relation)

    async def gate(self, tool: str, args: dict[str, Any] | None = None) -> GateResult:
        return await self._client.gate(self.token, tool, args)

    async def redeem(
        self, request_id: str, tool: str, args: dict[str, Any] | None = None
    ) -> Decision:
        return await self._client.redeem(self.token, request_id, tool, args)

    async def delegate(self, to_actor: str, scope: dict[str, Any]) -> IssuedToken:
        """Returns the attenuated token to hand to the sub-agent (which has its own credentials)."""
        return await self._client.attenuate(self.token, to_actor, scope)

    async def revoke(self, reason: str = "revoked by holder") -> Inspection:
        return await self._client.revoke(self.token, reason)

    async def remember(self, content: str, *, derived_from: Sequence[str] = ()) -> Memory:
        return await self._client.remember(self.token, content, derived_from=derived_from)

    async def recall(self, query: str, *, limit: int = 10) -> list[Memory]:
        return await self._client.recall(self.token, query, limit=limit)

    async def forget(self, memory_id: str) -> None:
        await self._client.forget(self.token, memory_id)
