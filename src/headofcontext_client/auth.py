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


class ClientCredentials:
    """OAuth 2.0 client credentials against any token endpoint; caches the token until near expiry.

    ``token_url`` is the endpoint itself (``https://cloud.headofcontext.com/oauth/token``,
    ``https://keycloak.example.com/realms/acme/protocol/openid-connect/token``). For a Keycloak
    realm, :class:`KeycloakClientCredentials` derives it from the issuer.
    """

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10.0,
        refresh_margin_seconds: float = 30.0,
    ) -> None:
        if not token_url.startswith(("https://", "http://")):
            raise ValueError("token_url must be an absolute http(s) URL")
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._transport = transport
        self._timeout = timeout_seconds
        self._margin = refresh_margin_seconds
        self._cached: tuple[str, float] | None = None

    @property
    def token_url(self) -> str:
        return self._token_url

    async def token(self) -> str:
        if self._cached and self._cached[1] - self._margin > time.monotonic():
            return self._cached[0]
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as http:
            try:
                response = await http.post(
                    self._token_url,
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


class KeycloakClientCredentials(ClientCredentials):
    """Client credentials against a Keycloak realm, given its issuer URL."""

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
        super().__init__(
            token_url=f"{self._issuer}/protocol/openid-connect/token",
            client_id=client_id,
            client_secret=client_secret,
            transport=transport,
            timeout_seconds=timeout_seconds,
            refresh_margin_seconds=refresh_margin_seconds,
        )
