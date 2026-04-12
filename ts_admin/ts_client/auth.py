"""
Authentication strategies for ThoughtSpot REST API v2.

Three strategies are supported, each implementing the same interface:
  - BasicAuth:       username + password
  - TrustedAuth:     username + secret key (login as any user)
  - BearerTokenAuth: pre-obtained bearer token

Usage:
    strategy = BasicAuth(username="admin@co.com", password="secret")
    headers = await strategy.get_headers(client)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx


class AuthStrategy(ABC):
    """Base interface all auth strategies must implement."""

    @abstractmethod
    async def get_headers(self, http_client: httpx.AsyncClient) -> dict[str, str]:
        """
        Return HTTP headers needed to authenticate this request.
        May perform a login API call if a session token is needed.
        """

    @abstractmethod
    def invalidate(self) -> None:
        """Clear any cached session token so the next call re-authenticates."""


@dataclass
class BasicAuth(AuthStrategy):
    """
    Standard username + password authentication.
    Calls /api/rest/2.0/auth/token/full on first use and caches the token.

    Pass org_id to scope the token to a specific org — required for multi-org
    sync since TS returns org-scoped content based on the token's org context.
    """

    username: str
    password: str
    org_id: int | None = None
    _session_token: str | None = None

    async def get_headers(self, http_client: httpx.AsyncClient) -> dict[str, str]:
        if not self._session_token:
            await self._login(http_client)
        return {"Authorization": f"Bearer {self._session_token}"}

    async def _login(self, http_client: httpx.AsyncClient) -> None:
        from ts_admin.ts_client.exceptions import TSAuthenticationError

        body: dict = {
            "username": self.username,
            "password": self.password,
            "validity_time_in_sec": 86400,
        }
        if self.org_id is not None:
            body["org_id"] = self.org_id

        response = await http_client.post(
            "/api/rest/2.0/auth/token/full",
            json=body,
        )
        if response.status_code == 401:
            raise TSAuthenticationError("Invalid username or password")
        if response.status_code == 403:
            raise TSAuthenticationError("Access denied — check that this user has ADMINISTRATION privilege")
        if not response.is_success:
            raise TSAuthenticationError(
                f"Login failed ({response.status_code}): {response.text[:200]}"
            )
        self._session_token = response.json()["token"]

    def invalidate(self) -> None:
        self._session_token = None


@dataclass
class TrustedAuth(AuthStrategy):
    """
    Trusted authentication using the ThoughtSpot secret key.
    Allows login as any user without knowing their password.
    The secret key is found in: Developer tab → Security Settings.

    Pass org_id to scope the token to a specific org — required for multi-org
    sync since TS returns org-scoped content based on the token's org context.
    """

    username: str
    secret_key: str
    org_id: int | None = None
    _session_token: str | None = None

    async def get_headers(self, http_client: httpx.AsyncClient) -> dict[str, str]:
        if not self._session_token:
            await self._login(http_client)
        return {"Authorization": f"Bearer {self._session_token}"}

    async def _login(self, http_client: httpx.AsyncClient) -> None:
        from ts_admin.ts_client.exceptions import TSAuthenticationError

        body: dict = {
            "username": self.username,
            "secret_key": self.secret_key,
            "validity_time_in_sec": 86400,
        }
        if self.org_id is not None:
            body["org_id"] = self.org_id

        response = await http_client.post(
            "/api/rest/2.0/auth/token/full",
            json=body,
        )
        if response.status_code == 401:
            raise TSAuthenticationError("Invalid username or secret key")
        if response.status_code == 403:
            raise TSAuthenticationError("Access denied — check that Trusted Authentication is enabled in ThoughtSpot Developer settings")
        if not response.is_success:
            raise TSAuthenticationError(
                f"Login failed ({response.status_code}): {response.text[:200]}"
            )
        self._session_token = response.json()["token"]

    def invalidate(self) -> None:
        self._session_token = None


@dataclass
class BearerTokenAuth(AuthStrategy):
    """
    Pre-obtained bearer token authentication.
    The caller is responsible for token freshness.
    """

    token: str

    async def get_headers(self, http_client: httpx.AsyncClient) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def invalidate(self) -> None:
        # Nothing to invalidate — token is managed externally
        pass
