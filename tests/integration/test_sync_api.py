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
