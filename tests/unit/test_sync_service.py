"""
Unit tests for sync_service.run_sync error handling.

The contract under test: a sync that fails because the connected account can't
reach an org (or lacks a privilege) must NOT mark the whole cluster's session as
expired. Only a genuine credential/session failure (TSAuthenticationError) does
that. Conflating the two told users to "reconnect this cluster" when reconnecting
could never help — the credentials were always valid.
"""

from __future__ import annotations

import pytest
from sqlmodel import create_engine

from ts_admin.services import connection_status
from ts_admin.services.connection_status import ConnectionState
from ts_admin.ts_client.exceptions import (
    TSAuthenticationError,
    TSInsufficientPrivilegesError,
)

CLUSTER_ID = "c1"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def in_memory_db(monkeypatch):
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import ts_admin.database as db_module

    monkeypatch.setattr(db_module, "get_engine", lambda: engine)
    db_module.init_db()
    return engine


@pytest.fixture
def patched_config(monkeypatch):
    """Minimal active-cluster config so run_sync's logging/log-write paths work."""
    from ts_admin.config import AppConfig, ClusterConfig
    from ts_admin.ts_client.models import AuthType

    cluster_cfg = ClusterConfig(
        id=CLUSTER_ID,
        name="Prod",
        url="https://prod.thoughtspot.cloud",
        username="admin",
        auth_type=AuthType.TRUSTED,
    )
    config = AppConfig(clusters={CLUSTER_ID: cluster_cfg}, active_cluster_id=CLUSTER_ID)
    monkeypatch.setattr("ts_admin.config.load_config", lambda: config)
    return config


@pytest.fixture(autouse=True)
def clear_health():
    connection_status.clear(CLUSTER_ID)
    yield
    connection_status.clear(CLUSTER_ID)


def _make_job(entity: str) -> str:
    from ts_admin.services.job_service import create_job

    return create_job(
        job_type=f"sync:{entity}",
        parameters={"entity_type": entity, "org_id": 349890686},
        cluster_id=CLUSTER_ID,
    )


def _job_status(job_id: str) -> tuple[str, str | None]:
    from ts_admin.database import get_session
    from ts_admin.models.job import Job

    with get_session() as session:
        job = session.get(Job, job_id)
        return job.status, job.error_type


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_org_access_denied_does_not_expire_cluster(monkeypatch, in_memory_db, patched_config):
    """TSInsufficientPrivilegesError → job FAILED, but cluster session stays non-expired."""

    async def _boom(*, org_id: int, job_id: str) -> None:
        raise TSInsufficientPrivilegesError(f"The connected account can't access org {org_id}.")

    monkeypatch.setattr("ts_admin.services.sync_service._sync_metadata", _boom)

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    await run_sync(entity_type="metadata", org_id=349890686, job_id=job_id)

    status, error_type = _job_status(job_id)
    assert status == "FAILED"
    assert error_type == "TSInsufficientPrivilegesError"
    # The crux: the cluster must NOT be flagged expired (no pointless reconnect loop).
    assert connection_status.get(CLUSTER_ID).state is not ConnectionState.EXPIRED


@pytest.mark.anyio
async def test_user_sync_reports_progress(monkeypatch, in_memory_db, patched_config):
    """A successful paginated sync must climb job.progress as pages arrive and
    finish COMPLETE with progress == record count. Without this the UI can only
    ever show a generic spinner — never a live count or a populated bar."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    now = datetime.now(timezone.utc)

    def _user(guid: str):
        return SimpleNamespace(
            id=guid,
            name=f"user-{guid}",
            display_name=f"User {guid}",
            email=f"{guid}@example.io",
            status=SimpleNamespace(value="ACTIVE"),
            created=now,
            modified=now,
        )

    pages = [[_user("a"), _user("b")], [_user("c")]]
    seen_progress: list[int] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def search_users(self, *, org_id):
            for page in pages:
                yield page

    # Avoid the keychain — the fake client ignores auth anyway.
    monkeypatch.setattr(
        "ts_admin.config.ClusterConfig.build_auth_strategy",
        lambda self, org_id=None: None,
    )
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", FakeClient)

    # Capture every progress update so we can prove it climbed, not just landed.
    import ts_admin.services.sync_service as sync_module

    real_update = sync_module.update_progress

    def _spy(job_id: str, progress: int) -> None:
        seen_progress.append(progress)
        real_update(job_id, progress)

    monkeypatch.setattr(sync_module, "update_progress", _spy)

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("users")
    await run_sync(entity_type="users", org_id=0, job_id=job_id)

    status, _ = _job_status(job_id)
    assert status == "COMPLETE"

    from ts_admin.database import get_session
    from ts_admin.models.job import Job

    with get_session() as session:
        job = session.get(Job, job_id)
    assert job.progress == 3  # all three users counted
    # Progress was reported per page (climbing), not only at the end.
    assert seen_progress == [2, 3]


@pytest.mark.anyio
async def test_dependencies_dispatches_to_lineage_build(monkeypatch, in_memory_db, patched_config):
    """run_sync('dependencies') must route to lineage_service.build_object_graph."""
    seen: dict = {}

    async def _fake_build(*, cluster_id, org_id, job_id, finalize=True):
        seen["cluster_id"] = cluster_id
        seen["org_id"] = org_id
        seen["finalize"] = finalize
        # Mirror the real build: finalize marks the job COMPLETE.
        if finalize:
            from ts_admin.services.job_service import mark_complete

            mark_complete(job_id, {"entity_type": "dependencies", "record_count": 7})
        return 7

    import ts_admin.services.lineage_service as lineage_module

    monkeypatch.setattr(lineage_module, "build_object_graph", _fake_build)
    # No Phase 2 column pass wired in this test → object tier finalizes the job.
    monkeypatch.delattr(lineage_module, "build_column_map", raising=False)

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("dependencies")
    await run_sync(entity_type="dependencies", org_id=0, job_id=job_id)

    assert seen == {"cluster_id": CLUSTER_ID, "org_id": 0, "finalize": True}
    status, _ = _job_status(job_id)
    assert status == "COMPLETE"


@pytest.mark.anyio
async def test_genuine_auth_failure_still_expires_cluster(monkeypatch, in_memory_db, patched_config):
    """TSAuthenticationError → job FAILED AND cluster flipped to EXPIRED (guards the distinction)."""

    async def _boom(*, org_id: int, job_id: str) -> None:
        raise TSAuthenticationError("session expired")

    monkeypatch.setattr("ts_admin.services.sync_service._sync_metadata", _boom)

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    await run_sync(entity_type="metadata", org_id=349890686, job_id=job_id)

    status, _ = _job_status(job_id)
    assert status == "FAILED"
    assert connection_status.get(CLUSTER_ID).state is ConnectionState.EXPIRED
