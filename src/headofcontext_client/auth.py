"""Bearer token providers for the agent (client credentials) and for humans (their own token)."""

from __future__ import annotations

import time
from typing import Protocol

import httpx

from headofcontext_client.errors import Unauthorized


class TokenProvider(Protocol):
    async def token(self) -> str: ...


class StaticToken:
    def __init__(self, token: str) -> None:
        self._token = token

    async def token(self) -> str:
        return self._token


class KeycloakClientCredentials:
    """OAuth 2.0 client credentials against an OIDC issuer; caches the token until near expiry."""

    def __init__(
        self,
        *,
        issuer: str,
        client_id: str,
        client_secret: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10.0,
        refresh_margin_seconds: float = 30.0,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._transport = transport
        self._timeout = timeout_seconds
        self._margin = refresh_margin_seconds
        self._cached: tuple[str, float] | None = None

    async def token(self) -> str:
        if self._cached and self._cached[1] - self._margin > time.monotonic():
            return self._cached[0]
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as http:
            try:
                response = await http.post(
                    f"{self._issuer}/protocol/openid-connect/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                    },
                )
                response.raise_for_status()
                body = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise Unauthorized(
                    "client_credentials_failed", "could not obtain an agent token"
                ) from exc
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise Unauthorized("client_credentials_failed", "issuer returned no access token")
        expires_in = float(body.get("expires_in", 300))
        self._cached = (token, time.monotonic() + expires_in)
        return token
