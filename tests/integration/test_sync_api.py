"""
Integration tests for the sync API's entity allowlist.

`POST /api/v1/sync/{entity}` used to accept "permissions" — it returned 200 with
a job id and the job was already FAILED before the response rendered, because
`run_sync` has no handler for it. The endpoint contract is what these tests pin:
an entity the service cannot sync must be refused with a 400 that names the
options, not accepted and quietly failed on a background task.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from ts_admin.api.sync import VALID_ENTITIES
from ts_admin.models.cluster import Cluster
from ts_admin.models.sync_log import SyncLog


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
    with Session(in_memory_db) as session:
        session.add(
            Cluster(
                id="c1",
                name="c1",
                url="https://c1.thoughtspot.cloud",
                username="admin",
                auth_type="basic",
            )
        )
        session.add(
            SyncLog(
                cluster_id="c1",
                org_id=0,
                entity_type="users",
                status="SUCCESS",
                record_count=7,
                synced_at=datetime.now(tz=timezone.utc),
            )
        )
        session.commit()
    return in_memory_db


# An entity is only rejected AFTER the allowlist check, which runs before any
# config or cluster resolution — so these never touch a cluster.
@pytest.mark.parametrize("entity", ["permissions", "not_an_entity"])
def test_an_entity_with_no_handler_is_refused_with_the_valid_options(client, entity):
    r = client.post(f"/api/v1/sync/{entity}?cluster_id=c1&org_id=0")

    assert r.status_code == 400
    detail = r.json()["detail"]
    assert entity in detail  # names what was rejected
    for valid in sorted(VALID_ENTITIES):
        assert valid in detail  # ...and what to send instead
    assert "permissions" not in sorted(VALID_ENTITIES)


def test_sync_status_never_renders_an_entity_nothing_can_sync(client, seeded):
    """
    A row for an unsyncable entity is stuck at NOT_SYNCED forever, which reads
    as "you have not synced this yet" rather than "this does not exist".
    """
    r = client.get("/api/v1/sync?cluster_id=c1&org_id=0")

    assert r.status_code == 200
    rendered = {row["entity_type"] for row in r.json()}
    assert rendered == VALID_ENTITIES
    assert "permissions" not in rendered
    users = next(row for row in r.json() if row["entity_type"] == "users")
    assert (users["status"], users["record_count"]) == ("SUCCESS", 7)


# ── Concurrency guard (S24/S34) ────────────────────────────────────────────────
#
# POST /sync/{entity} used to create a job unconditionally, so repeated Sync
# clicks started duplicate concurrent syncs of the same type — observed live as
# three simultaneous TML crawls tripling each other's wall-clock. A sync for an
# entity that already has a QUEUED/RUNNING job must be refused with a 409 that
# names the in-flight job, and "sync all" must attach to in-flight work instead
# of duplicating it.


def _seed_job(engine, *, job_id: str, entity: str, status: str, org_id: int = 0) -> None:
    from ts_admin.models.job import Job

    with Session(engine) as session:
        job = Job(id=job_id, cluster_id="c1", job_type=f"sync:{entity}", status=status)
        job.set_parameters({"entity_type": entity, "org_id": org_id, "cluster_id": "c1"})
        session.add(job)
        session.commit()


@pytest.fixture
def no_real_sync(monkeypatch):
    """Triggers that ARE allowed must not hit the network from a test."""
    import ts_admin.services.sync_service as sync_service

    def _noop(**kwargs):
        return None

    monkeypatch.setattr(sync_service, "run_sync", _noop)


# "dependencies" is S34's observed case (concurrent TML crawls); "metadata" is
# the S24 shape (a plain entity sync).
@pytest.mark.parametrize("entity", ["metadata", "dependencies"])
@pytest.mark.parametrize("status", ["QUEUED", "RUNNING"])
def test_a_second_sync_of_an_in_flight_entity_is_refused(client, seeded, entity, status):
    _seed_job(seeded, job_id="j-inflight", entity=entity, status=status)

    r = client.post(f"/api/v1/sync/{entity}?cluster_id=c1&org_id=0")

    assert r.status_code == 409
    detail = r.json()["detail"]
    assert entity in detail and "j-inflight" in detail  # actionable: names the job

    from sqlmodel import select

    from ts_admin.models.job import Job

    with Session(seeded) as session:
        jobs = session.exec(select(Job).where(Job.job_type == f"sync:{entity}")).all()
    assert [j.id for j in jobs] == ["j-inflight"]  # no second job was created


def test_the_same_entity_may_sync_concurrently_in_another_org(client, seeded, no_real_sync):
    _seed_job(seeded, job_id="j-org0", entity="metadata", status="RUNNING", org_id=0)

    r = client.post("/api/v1/sync/metadata?cluster_id=c1&org_id=5")

    assert r.status_code == 200
    assert r.json()["job_id"] != "j-org0"


def test_a_finished_job_does_not_block_a_new_sync(client, seeded, no_real_sync):
    _seed_job(seeded, job_id="j-done", entity="metadata", status="COMPLETE")

    r = client.post("/api/v1/sync/metadata?cluster_id=c1&org_id=0")

    assert r.status_code == 200
    assert r.json()["job_id"] != "j-done"


def test_sync_all_attaches_to_in_flight_work_instead_of_duplicating_it(client, seeded, no_real_sync):
    _seed_job(seeded, job_id="j-users", entity="users", status="RUNNING")

    r = client.post("/api/v1/sync/all?cluster_id=c1&org_id=0")

    assert r.status_code == 200
    by_entity = {row["entity_type"]: row["job_id"] for row in r.json()}
    assert by_entity["users"] == "j-users"  # reused, not duplicated

    from sqlmodel import select

    from ts_admin.models.job import Job

    with Session(seeded) as session:
        user_jobs = session.exec(select(Job).where(Job.job_type == "sync:users")).all()
    assert [j.id for j in user_jobs] == ["j-users"]
