"""
Integration tests for DELETE /api/v1/jobs/{id}/cancel and the cancel-pending
field it drives.

Cancellation is cooperative: the endpoint only sets a flag, and the background
task acts on it at its next page/chunk boundary. That gap is invisible unless the
flag itself is readable — a job that has been asked to stop but has not stopped
yet is indistinguishable from a job that ignored the request, which is exactly
what the sync and lineage crawls used to do (204, then run to completion, then
report COMPLETE). `JobResponse.is_cancelled` is what lets the UI say
"cancelling…" instead of showing a job that looks stuck.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from ts_admin.models.cluster import Cluster
from ts_admin.models.job import Job


@pytest.fixture
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


@pytest.fixture
def client(in_memory_db, monkeypatch):
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

    with Session(in_memory_db) as session:
        session.add(
            Cluster(
                id="c1",
                name="Prod",
                url="https://prod.thoughtspot.cloud",
                username="admin",
                auth_type="basic",
            )
        )
        session.commit()

    from ts_admin.main import create_app

    return TestClient(create_app())


def _seed_job(engine, *, job_id: str, status: str) -> None:
    with Session(engine) as session:
        session.add(Job(id=job_id, cluster_id="c1", job_type="sync:dependencies", status=status))
        session.commit()


def test_cancel_marks_a_running_sync_job_cancel_pending(client, in_memory_db):
    """204, and the flag is then visible on both the detail and list reads."""
    _seed_job(in_memory_db, job_id="j1", status="RUNNING")

    assert client.get("/api/v1/jobs/j1").json()["is_cancelled"] is False

    assert client.delete("/api/v1/jobs/j1/cancel").status_code == 204

    detail = client.get("/api/v1/jobs/j1").json()
    assert detail["is_cancelled"] is True
    # Still RUNNING: the endpoint requests a stop, the crawl performs it. The UI
    # needs this exact state (cancel-pending) to be distinguishable.
    assert detail["status"] == "RUNNING"

    listed = client.get("/api/v1/jobs", params={"cluster_id": "c1"}).json()["items"]
    assert [j["is_cancelled"] for j in listed if j["id"] == "j1"] == [True]


@pytest.mark.parametrize("status", ["COMPLETE", "FAILED", "PARTIAL"])
def test_cancel_rejects_a_finished_job(client, in_memory_db, status):
    """A terminal job cannot be cancelled — 409, and the flag stays false."""
    _seed_job(in_memory_db, job_id="j2", status=status)

    assert client.delete("/api/v1/jobs/j2/cancel").status_code == 409
    assert client.get("/api/v1/jobs/j2").json()["is_cancelled"] is False


def test_cancel_unknown_job_is_404(client, in_memory_db):
    assert client.delete("/api/v1/jobs/nope/cancel").status_code == 404


def test_job_responses_carry_the_org_from_parameters(client, in_memory_db):
    """
    Jobs have no org column — org lives in the parameters JSON. The UI needs it
    to match a job to the org it is viewing (the Topbar adopts an in-flight
    `sync:{entity}` job for its cluster+org instead of asserting the last
    FINISHED sync's outcome while a new one runs). A job recorded before the
    field existed serializes org_id as null, never as a guess.
    """
    with Session(in_memory_db) as session:
        with_org = Job(id="j-org", cluster_id="c1", job_type="sync:dependencies", status="RUNNING")
        with_org.set_parameters({"entity_type": "dependencies", "org_id": 928000883})
        without_org = Job(id="j-none", cluster_id="c1", job_type="archive", status="COMPLETE")
        session.add(with_org)
        session.add(without_org)
        session.commit()

    listed = {j["id"]: j for j in client.get("/api/v1/jobs?cluster_id=c1").json()["items"]}
    assert listed["j-org"]["org_id"] == 928000883
    assert listed["j-none"]["org_id"] is None

    assert client.get("/api/v1/jobs/j-org").json()["org_id"] == 928000883
