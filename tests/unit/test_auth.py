"""
Unit tests for auth strategies.

Verifies that org_id is correctly included/excluded in the login request body
for BasicAuth and TrustedAuth. This guards against a regression where the
org-scoped token fix (ADR-012) is silently broken — causing metadata syncs to
return org 0 content for all orgs.
"""

from __future__ import annotations

import pytest
import httpx
import respx

from ts_admin.ts_client.auth import BasicAuth, BearerTokenAuth, TrustedAuth


# ── Helpers ────────────────────────────────────────────────────────────────────

def _token_response(token: str = "test-token") -> dict:
    return {"token": token}


# ── BasicAuth ─────────────────────────────────────────────────────────────────

class TestBasicAuth:

    @respx.mock
    @pytest.mark.anyio
    async def test_login_without_org_id(self):
        """No org_id → org_id must NOT appear in the login body."""
        route = respx.post("https://ts.example.com/api/rest/2.0/auth/token/full").mock(
            return_value=httpx.Response(200, json=_token_response())
        )
        auth = BasicAuth(username="admin", password="secret")
        async with httpx.AsyncClient(base_url="https://ts.example.com") as http:
            await auth.get_headers(http)

        body = route.calls[0].request.content
        import json
        parsed = json.loads(body)
        assert "org_id" not in parsed
        assert parsed["username"] == "admin"
        assert parsed["password"] == "secret"

    @respx.mock
    @pytest.mark.anyio
    async def test_login_with_org_id(self):
        """org_id set → org_id must appear in the login body."""
        route = respx.post("https://ts.example.com/api/rest/2.0/auth/token/full").mock(
            return_value=httpx.Response(200, json=_token_response())
        )
        auth = BasicAuth(username="admin", password="secret", org_id=928000883)
        async with httpx.AsyncClient(base_url="https://ts.example.com") as http:
            await auth.get_headers(http)

        body = route.calls[0].request.content
        import json
        parsed = json.loads(body)
        assert parsed["org_id"] == 928000883

    @respx.mock
    @pytest.mark.anyio
    async def test_login_with_primary_org_id_zero(self):
        """org_id=0 (primary org) must be included — 0 is falsy in Python but valid."""
        route = respx.post("https://ts.example.com/api/rest/2.0/auth/token/full").mock(
            return_value=httpx.Response(200, json=_token_response())
        )
        auth = BasicAuth(username="admin", password="secret", org_id=0)
        async with httpx.AsyncClient(base_url="https://ts.example.com") as http:
            await auth.get_headers(http)

        body = route.calls[0].request.content
        import json
        parsed = json.loads(body)
        assert parsed["org_id"] == 0

    @respx.mock
    @pytest.mark.anyio
    async def test_token_cached_after_first_login(self):
        """Second call to get_headers must not make a second login request."""
        respx.post("https://ts.example.com/api/rest/2.0/auth/token/full").mock(
            return_value=httpx.Response(200, json=_token_response("cached-token"))
        )
        auth = BasicAuth(username="admin", password="secret")
        async with httpx.AsyncClient(base_url="https://ts.example.com") as http:
            h1 = await auth.get_headers(http)
            h2 = await auth.get_headers(http)

        assert h1 == h2
        assert respx.calls.call_count == 1

    @respx.mock
    @pytest.mark.anyio
    async def test_invalidate_clears_token(self):
        """invalidate() forces a fresh login on the next call."""
        respx.post("https://ts.example.com/api/rest/2.0/auth/token/full").mock(
            return_value=httpx.Response(200, json=_token_response())
        )
        auth = BasicAuth(username="admin", password="secret")
        async with httpx.AsyncClient(base_url="https://ts.example.com") as http:
            await auth.get_headers(http)
            auth.invalidate()
            await auth.get_headers(http)

        assert respx.calls.call_count == 2

    @respx.mock
    @pytest.mark.anyio
    async def test_raises_on_401(self):
        """401 from TS → TSAuthenticationError."""
        respx.post("https://ts.example.com/api/rest/2.0/auth/token/full").mock(
            return_value=httpx.Response(401)
        )
        from ts_admin.ts_client.exceptions import TSAuthenticationError
        auth = BasicAuth(username="admin", password="wrong")
        async with httpx.AsyncClient(base_url="https://ts.example.com") as http:
            with pytest.raises(TSAuthenticationError):
                await auth.get_headers(http)


# ── TrustedAuth ───────────────────────────────────────────────────────────────

class TestTrustedAuth:

    @respx.mock
    @pytest.mark.anyio
    async def test_login_without_org_id(self):
        """No org_id → org_id must NOT appear in the login body."""
        route = respx.post("https://ts.example.com/api/rest/2.0/auth/token/full").mock(
            return_value=httpx.Response(200, json=_token_response())
        )
        auth = TrustedAuth(username="admin", secret_key="key123")
        async with httpx.AsyncClient(base_url="https://ts.example.com") as http:
            await auth.get_headers(http)

        body = route.calls[0].request.content
        import json
        parsed = json.loads(body)
        assert "org_id" not in parsed
        assert parsed["secret_key"] == "key123"

    @respx.mock
    @pytest.mark.anyio
    async def test_login_with_org_id(self):
        """org_id set → org_id must appear in the login body."""
        route = respx.post("https://ts.example.com/api/rest/2.0/auth/token/full").mock(
            return_value=httpx.Response(200, json=_token_response())
        )
        auth = TrustedAuth(username="admin", secret_key="key123", org_id=42)
        async with httpx.AsyncClient(base_url="https://ts.example.com") as http:
            await auth.get_headers(http)

        body = route.calls[0].request.content
        import json
        parsed = json.loads(body)
        assert parsed["org_id"] == 42


# ── BearerTokenAuth ───────────────────────────────────────────────────────────

class TestBearerTokenAuth:

    @pytest.mark.anyio
    async def test_returns_bearer_header(self):
        auth = BearerTokenAuth(token="my-token")
        async with httpx.AsyncClient() as http:
            headers = await auth.get_headers(http)
        assert headers == {"Authorization": "Bearer my-token"}
