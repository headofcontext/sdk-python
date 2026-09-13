import httpx
import pytest

from headofcontext_client import KeycloakClientCredentials, Unauthorized


async def test_client_credentials_cached() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/realms/acme/protocol/openid-connect/token"
        body = request.content.decode()
        assert "grant_type=client_credentials" in body and "client_id=assistant" in body
        return httpx.Response(200, json={"access_token": f"tok-{calls}", "expires_in": 300})

    provider = KeycloakClientCredentials(
        issuer="http://kc/realms/acme",
        client_id="assistant",
        client_secret="s",
        transport=httpx.MockTransport(handler),
    )
    assert await provider.token() == "tok-1"
    assert await provider.token() == "tok-1"
    assert calls == 1


async def test_client_credentials_failure() -> None:
    provider = KeycloakClientCredentials(
        issuer="http://kc/realms/acme",
        client_id="a",
        client_secret="s",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(401, json={"error": "invalid_client"})
        ),
    )
    with pytest.raises(Unauthorized):
        await provider.token()
