"""ClientCredentials posts to the endpoint it is given; the Keycloak flavour derives it."""

from __future__ import annotations

import httpx
import pytest

from headofcontext_client import ClientCredentials, KeycloakClientCredentials, Unauthorized


def _issuer(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        form = dict(pair.split("=") for pair in request.content.decode().split("&"))
        if form.get("client_secret") != "s3cret":
            return httpx.Response(401, json={"error": "invalid_client"})
        return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})

    return httpx.MockTransport(handle)


@pytest.mark.asyncio
async def test_client_credentials_posts_to_the_given_endpoint() -> None:
    seen: list[httpx.Request] = []
    provider = ClientCredentials(
        token_url="https://cloud.example/oauth/token",
        client_id="agent-1",
        client_secret="s3cret",
        transport=_issuer(seen),
    )
    assert await provider.token() == "tok-1"
    assert str(seen[0].url) == "https://cloud.example/oauth/token"
    assert b"grant_type=client_credentials" in seen[0].content
    # Cached until near expiry: no second request.
    assert await provider.token() == "tok-1"
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_keycloak_flavour_derives_the_endpoint_from_the_issuer() -> None:
    seen: list[httpx.Request] = []
    provider = KeycloakClientCredentials(
        issuer="https://kc.example/realms/acme/",
        client_id="agent-1",
        client_secret="s3cret",
        transport=_issuer(seen),
    )
    assert provider.token_url == "https://kc.example/realms/acme/protocol/openid-connect/token"
    await provider.token()
    assert str(seen[0].url) == provider.token_url


@pytest.mark.asyncio
async def test_bad_secret_is_unauthorized() -> None:
    provider = ClientCredentials(
        token_url="https://cloud.example/oauth/token",
        client_id="agent-1",
        client_secret="wrong",
        transport=_issuer([]),
    )
    with pytest.raises(Unauthorized):
        await provider.token()


def test_token_url_must_be_absolute() -> None:
    with pytest.raises(ValueError):
        ClientCredentials(token_url="/oauth/token", client_id="a", client_secret="b")
