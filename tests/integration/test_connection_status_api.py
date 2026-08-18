"""
Integration tests for live connection-status tracking and the global
exception handlers.

Covers the bug these were built for: the cluster list must report a session as
"expired" the moment a call is rejected — instead of showing a stale
"Connected" until the user notices a failed job.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine


@pytest.fixture(autouse=True)
def in_memory_db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import ts_admin.database as db_module

    monkeypatch.setattr(db_module, "get_engine", lambda: engine)
    db_module.init_db()
    return engine


@pytest.fixture(autouse=True)
def clean_registry():
    """Connection status is process-global state — reset around each test."""
    from ts_admin.services import connection_status

    connection_status._status.clear()
    yield
    connection_status._status.clear()


@pytest.fixture
def live_client(monkeypatch):
    from ts_admin.config import AppConfig, ClusterConfig
    from ts_admin.ts_client.models import AuthType

    cluster_cfg = ClusterConfig(
        id="c1",
        name="Prod",
        url="https://prod.thoughtspot.cloud",
        username="admin",
        auth_type=AuthType.BASIC,
    )
    config = AppConfig(clusters={"c1": cluster_cfg}, active_cluster_id="c1")
    monkeypatch.setattr("ts_admin.config.load_config", lambda: config)
    monkeypatch.setattr("ts_admin.config._load_secret", lambda cluster_id, field: "fake-secret")

    from ts_admin.main import create_app

    return TestClient(create_app())


# ── test_cluster updates live health ───────────────────────────────────────────


class TestConnectionTestUpdatesStatus:
    @respx.mock
    def test_success_marks_connected_and_surfaces_version(self, live_client):
        respx.post("https://prod.thoughtspot.cloud/api/rest/2.0/auth/token/full").mock(
            return_value=httpx.Response(200, json={"token": "tok"})
        )
        respx.get("https://prod.thoughtspot.cloud/api/rest/2.0/system").mock(
            return_value=httpx.Response(200, json={"release_version": "10.5.0"})
        )

        r = live_client.post("/api/v1/clusters/c1/test")
        assert r.status_code == 200
        assert r.json()["success"] is True

        listed = live_client.get("/api/v1/clusters").json()
        c1 = next(c for c in listed if c["id"] == "c1")
        assert c1["connection_status"] == "connected"
        assert c1["connection_checked_at"] is not None

    @respx.mock
    def test_auth_rejection_marks_expired(self, live_client):
        # Login succeeds (valid creds) but the API call is rejected — the classic
        # "session expired" shape.
        respx.post("https://prod.thoughtspot.cloud/api/rest/2.0/auth/token/full").mock(
            return_value=httpx.Response(200, json={"token": "tok"})
        )
        respx.get("https://prod.thoughtspot.cloud/api/rest/2.0/system").mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )

        r = live_client.post("/api/v1/clusters/c1/test")
        assert r.status_code == 200
        assert r.json()["success"] is False

        listed = live_client.get("/api/v1/clusters").json()
        c1 = next(c for c in listed if c["id"] == "c1")
        assert c1["connection_status"] == "expired"

    @respx.mock
    def test_unreachable_marks_unreachable(self, live_client):
        respx.post("https://prod.thoughtspot.cloud/api/rest/2.0/auth/token/full").mock(
            side_effect=httpx.ConnectError("refused")
        )

        r = live_client.post("/api/v1/clusters/c1/test")
        assert r.status_code == 200
        assert r.json()["success"] is False

        listed = live_client.get("/api/v1/clusters").json()
        c1 = next(c for c in listed if c["id"] == "c1")
        assert c1["connection_status"] == "unreachable"


# ── Global exception handlers ──────────────────────────────────────────────────


def _app_with_probes(monkeypatch, tmp_path):
    """Build a real app (handlers registered) plus throwaway routes that raise."""
    from ts_admin.config import AppConfig, ClusterConfig
    from ts_admin.main import create_app
    from ts_admin.ts_client.exceptions import TSAuthenticationError
    from ts_admin.ts_client.models import AuthType

    config = AppConfig(
        clusters={
            "c1": ClusterConfig(
                id="c1", name="Prod", url="https://prod.thoughtspot.cloud", username="admin", auth_type=AuthType.BASIC
            )
        },
        active_cluster_id="c1",
    )
    monkeypatch.setattr("ts_admin.config.load_config", lambda: config)

    # These probe routes are attached after create_app(), so they land *after*
    # any StaticFiles mount at "/" and would be shadowed by it — the probes
    # would 404 instead of raising. Point STATIC_DIR at an empty directory so
    # no mount is created, and the suite behaves the same whether or not the
    # developer has run `make build`. (Real API routers register before the
    # mount inside create_app(), so production routing is unaffected.)
    monkeypatch.setattr("ts_admin.main.STATIC_DIR", tmp_path)

    app = create_app()

    @app.get("/api/v1/_probe/auth")
    async def _probe_auth():
        raise TSAuthenticationError("ThoughtSpot rejected credentials — session may have expired")

    @app.get("/api/v1/_probe/boom")
    async def _probe_boom():
        raise RuntimeError("unexpected internal explosion")

    return app


class TestGlobalExceptionHandlers:
    def test_ts_admin_error_returns_consistent_shape(self, monkeypatch, tmp_path):
        app = _app_with_probes(monkeypatch, tmp_path)
        client = TestClient(app, raise_server_exceptions=False)

        r = client.get("/api/v1/_probe/auth")
        assert r.status_code == 401
        body = r.json()
        assert body["detail"] == "ThoughtSpot login expired"
        assert "Reconnect" in body["hint"]
        assert body["error_type"] == "TSAuthenticationError"

    def test_auth_error_flips_active_cluster_to_expired(self, monkeypatch, tmp_path):
        from ts_admin.services import connection_status

        app = _app_with_probes(monkeypatch, tmp_path)
        client = TestClient(app, raise_server_exceptions=False)

        client.get("/api/v1/_probe/auth")
        assert connection_status.get("c1").state.value == "expired"

    def test_unexpected_error_is_generic_500_no_leak(self, monkeypatch, tmp_path):
        app = _app_with_probes(monkeypatch, tmp_path)
        client = TestClient(app, raise_server_exceptions=False)

        r = client.get("/api/v1/_probe/boom")
        assert r.status_code == 500
        body = r.json()
        assert body["detail"] == "Something went wrong"
        # raw exception message must never reach the client
        assert "explosion" not in str(body)
        assert body["error_type"] == "RuntimeError"
