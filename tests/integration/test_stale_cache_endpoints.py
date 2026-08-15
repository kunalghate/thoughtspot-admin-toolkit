"""
The stale-cache refusal, asserted THROUGH THE ENDPOINTS (S23 follow-up).

WHY THIS FILE EXISTS — A SERVICE-LEVEL UNIT TEST CANNOT DETECT THIS CLASS OF BUG.
The first cut of S23 put `require_authoritative_metadata` inside
`bulk_sharing_service.execute_share` and `user_management_service.execute_transfer`,
and `tests/unit/test_stale_cache_guard.py` called those functions DIRECTLY, saw a
`StaleCacheError`, and went green. But neither function is ever called directly in
production: both run only as Starlette background tasks (`api/sharing.py::execute`,
`api/users.py::transfer_execute`), i.e. AFTER the 202 + job_id has already been
written to the wire. So in the real request path the guard was fail-SILENT:

  * the `(StaleCacheError, 409)` row in `_STATUS_BY_TYPE` could never apply —
    Starlette raises "Caught handled exception, but response already started";
  * the caller got 202 "accepted" and an actionable hint they never saw;
  * the `Job` row sat at QUEUED / error=None forever, reaped only by a restart.

Calling through TestClient is the only thing that exercises the ordering of
"guard vs. create_job vs. add_task". Every test below therefore drives HTTP.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select

from ts_admin.models.cache.ts_group import CachedGroup
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cache.ts_user import CachedUser, UserOrgMembership
from ts_admin.models.cluster import Cluster
from ts_admin.models.job import Job
from ts_admin.models.sync_log import SyncLog

CLUSTER_ID = "c1"
ORG_ID = 0


@pytest.fixture(autouse=True)
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
def client(in_memory_db):
    from ts_admin.main import create_app

    return TestClient(create_app())


@pytest.fixture
def seeded(in_memory_db):
    """A TRUNCATED cache: rows present, but NO metadata SUCCESS marker.

    This is the shape an interrupted sync leaves behind, and it is the whole
    point — a row count cannot tell it apart from a healthy cache, so every
    assertion below is about the marker, not about emptiness.
    """
    now = datetime.now(tz=timezone.utc)
    with Session(in_memory_db) as session:
        session.add(
            Cluster(
                id=CLUSTER_ID,
                name="Prod",
                url="https://prod.thoughtspot.cloud",
                username="admin",
                auth_type="basic",
            )
        )
        for guid, name in [("u-alice", "alice"), ("u-bob", "bob")]:
            session.add(
                CachedUser(
                    cluster_id=CLUSTER_ID,
                    ts_guid=guid,
                    username=name,
                    display_name=name.title(),
                    email=f"{name}@co.com",
                    status="ACTIVE",
                    synced_at=now,
                )
            )
            session.add(UserOrgMembership(cluster_id=CLUSTER_ID, ts_guid=guid, org_id=ORG_ID, synced_at=now))
        session.add(
            CachedGroup(
                cluster_id=CLUSTER_ID,
                org_id=ORG_ID,
                ts_guid="g-finance",
                name="Finance",
                display_name="Finance",
                synced_at=now,
            )
        )
        session.add(
            CachedMetadata(
                cluster_id=CLUSTER_ID,
                org_id=ORG_ID,
                ts_guid="lb-1",
                name="Sales Liveboard",
                object_type="LIVEBOARD",
                owner_guid="u-alice",
                owner_name="Alice",
                tag_names=json.dumps(["finance"]),
                synced_at=now,
            )
        )
        session.commit()


def _certify(engine, *, status: str = "SUCCESS") -> None:
    with Session(engine) as s:
        s.add(
            SyncLog(
                cluster_id=CLUSTER_ID,
                org_id=ORG_ID,
                entity_type="metadata",
                status=status,
                record_count=1,
            )
        )
        s.commit()


def _jobs(engine) -> list[tuple[str, str, str | None]]:
    with Session(engine) as s:
        return [(j.job_type, j.status, j.error) for j in s.exec(select(Job)).all()]


SHARE_EXECUTE = (
    "/api/v1/sharing/execute",
    {
        "cluster_id": CLUSTER_ID,
        "org_id": ORG_ID,
        "object_guids": ["lb-1"],
        "principal_guids": ["g-finance"],
        "mode": "READ_ONLY",
    },
)

TRANSFER_EXECUTE = (
    "/api/v1/users/transfer/execute",
    {
        "cluster_id": CLUSTER_ID,
        "org_id": ORG_ID,
        "from_user_guid": "u-alice",
        "to_user_identifier": "bob",
        "object_ids": ["lb-1"],
    },
)

EXECUTE_ENDPOINTS = [
    pytest.param(*SHARE_EXECUTE, id="sharing.execute"),
    pytest.param(*TRANSFER_EXECUTE, id="users.transfer_execute"),
]


class TestExecuteEndpointsRefuseOnAnUncertifiedCache:
    @pytest.mark.parametrize(("path", "body"), EXECUTE_ENDPOINTS)
    def test_409_with_an_actionable_error_type(self, client, seeded, path, body):
        r = client.post(path, json=body)
        assert r.status_code == 409, r.text
        payload = r.json()
        assert payload["error_type"] == "StaleCacheError"
        assert payload["hint"]

    @pytest.mark.parametrize(("path", "body"), EXECUTE_ENDPOINTS)
    def test_no_job_row_is_created(self, client, seeded, in_memory_db, path, body):
        """THE regression. With the guard in the service instead of the router,
        this list came back as e.g. [('bulk_share', 'QUEUED', None)] — a job the
        UI polls forever for work that never started and never will."""
        assert _jobs(in_memory_db) == []  # anti-vacuity: none before, either
        client.post(path, json=body)
        assert _jobs(in_memory_db) == []

    @pytest.mark.parametrize(("path", "body"), EXECUTE_ENDPOINTS)
    def test_an_in_progress_marker_also_refuses(self, client, seeded, in_memory_db, path, body):
        """A sync running RIGHT NOW is mid-delete-and-repage, so the cache is at
        its most truncated. SUCCESS is the only certification."""
        _certify(in_memory_db, status="IN_PROGRESS")
        r = client.post(path, json=body)
        assert r.status_code == 409, r.text
        assert _jobs(in_memory_db) == []

    @pytest.mark.parametrize(("path", "body"), EXECUTE_ENDPOINTS)
    def test_202_and_a_job_once_certified(self, client, seeded, in_memory_db, monkeypatch, path, body):
        """Anti-vacuity for all three above: the same request against the same
        seeded rows succeeds once the SUCCESS marker exists, so the refusal is
        the marker's doing and not a broken fixture."""
        from ts_admin.services import bulk_sharing_service, user_management_service

        async def _noop(*args, **kwargs):
            return None

        monkeypatch.setattr(bulk_sharing_service, "execute_share", _noop)
        monkeypatch.setattr(user_management_service, "execute_transfer", _noop)

        _certify(in_memory_db)
        r = client.post(path, json=body)
        assert r.status_code == 202, r.text
        assert r.json()["job_id"]
        assert len(_jobs(in_memory_db)) == 1
