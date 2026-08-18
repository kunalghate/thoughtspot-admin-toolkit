"""
GET /api/v1/update — the endpoint behind the in-app update pill.

The contract the UI depends on: this endpoint always answers 200. An admin on a
locked-down network with no route to github.com must still get a usable app, so
an unreachable GitHub is `checked=false`, never a 5xx.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

from ts_admin.services.update_service import LATEST_RELEASE_API, reset_cache


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
def _clear_cache():
    reset_cache()
    yield
    reset_cache()


@pytest.fixture
def client():
    from ts_admin.main import create_app

    return TestClient(create_app())


@respx.mock
def test_reports_an_available_update(client, monkeypatch):
    monkeypatch.setattr("ts_admin.__version__", "0.1.0")
    respx.get(LATEST_RELEASE_API).mock(
        return_value=httpx.Response(
            200,
            json={
                "tag_name": "v0.4.0",
                "html_url": "https://github.com/o/r/releases/tag/v0.4.0",
                "assets": [{"browser_download_url": "https://github.com/o/r/x-0.4.0-py3-none-any.whl"}],
            },
        )
    )

    body = client.get("/api/v1/update").json()

    assert body["update_available"] is True
    assert body["latest"] == "0.4.0"
    # The UI shows this verbatim — it must be the command, not a description.
    assert body["command"] == "ts-admin-toolkit update"


@respx.mock
def test_unreachable_github_is_200_and_quiet(client, monkeypatch):
    monkeypatch.setattr("ts_admin.__version__", "0.1.0")
    respx.get(LATEST_RELEASE_API).mock(side_effect=httpx.ConnectError("no route to host"))

    response = client.get("/api/v1/update")

    assert response.status_code == 200
    assert response.json()["checked"] is False
    assert response.json()["update_available"] is False
