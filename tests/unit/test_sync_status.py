"""
Unit tests for services/sync_status — the single cache-completeness helper.

The contract: "is the metadata cache authoritative for THIS (cluster, org)?"
answered from the newest `sync_log` row, never from a row count. Three
properties have to hold or the five refusal sites built on top are wrong:

  1. Newest-first — `sync_log` has no unique constraint, so a stale SUCCESS
     sitting next to a fresh IN_PROGRESS must not win.
  2. Only SUCCESS certifies — FAILED and IN_PROGRESS both mean "not complete".
  3. Scoped by cluster_id AND org_id — a sibling scope's SUCCESS must never
     certify this one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, create_engine

from ts_admin.models.sync_log import SyncLog
from ts_admin.services.sync_status import (
    last_successful_sync,
    metadata_is_authoritative,
    require_authoritative_metadata,
)
from ts_admin.ts_client.exceptions import StaleCacheError

CLUSTER = "c1"
ORG = 0

# Two shadow scopes, not one. A single diagonal ("other cluster", "other org")
# row is excluded by EITHER predicate alone — so dropping just the cluster_id
# filter, or just the org_id filter, would still pass. Covering (c2, ORG) and
# (CLUSTER, org 1) means each predicate is the sole thing excluding one row.
SHADOW_SCOPES = (("c2", ORG), (CLUSTER, 1))


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


def _log(engine, *, cluster_id=CLUSTER, org_id=ORG, status="SUCCESS", entity_type="metadata", minutes_ago=0, count=0):
    with Session(engine) as s:
        s.add(
            SyncLog(
                cluster_id=cluster_id,
                org_id=org_id,
                entity_type=entity_type,
                status=status,
                record_count=count,
                synced_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
            )
        )
        s.commit()


# ── last_successful_sync ──────────────────────────────────────────────────────


class TestLastSuccessfulSync:
    def test_returns_the_newest_of_two_success_rows(self, in_memory_db):
        _log(in_memory_db, minutes_ago=60, count=10)
        _log(in_memory_db, minutes_ago=1, count=99)
        with Session(in_memory_db) as s:
            row = last_successful_sync(s, cluster_id=CLUSTER, org_id=ORG, entity_type="metadata")
        assert row is not None
        # Without the ORDER BY, `.first()` returns insertion order — the stale one.
        assert row.record_count == 99

    def test_ignores_failed_and_in_progress(self, in_memory_db):
        _log(in_memory_db, status="FAILED", minutes_ago=5)
        _log(in_memory_db, status="IN_PROGRESS", minutes_ago=1)
        with Session(in_memory_db) as s:
            assert last_successful_sync(s, cluster_id=CLUSTER, org_id=ORG, entity_type="metadata") is None

    def test_ignores_other_entity_types(self, in_memory_db):
        _log(in_memory_db, entity_type="users")
        with Session(in_memory_db) as s:
            assert last_successful_sync(s, cluster_id=CLUSTER, org_id=ORG, entity_type="metadata") is None

    @pytest.mark.parametrize(("shadow_cluster", "shadow_org"), SHADOW_SCOPES)
    def test_a_sibling_scopes_success_never_certifies_this_one(self, in_memory_db, shadow_cluster, shadow_org):
        _log(in_memory_db, cluster_id=shadow_cluster, org_id=shadow_org, status="SUCCESS")
        with Session(in_memory_db) as s:
            assert last_successful_sync(s, cluster_id=CLUSTER, org_id=ORG, entity_type="metadata") is None
        # ...and the shadow row really is there (anti-vacuity: prove the seed took).
        with Session(in_memory_db) as s:
            assert (
                last_successful_sync(s, cluster_id=shadow_cluster, org_id=shadow_org, entity_type="metadata")
                is not None
            )


# ── metadata_is_authoritative ─────────────────────────────────────────────────


class TestMetadataIsAuthoritative:
    def test_false_when_never_synced(self, in_memory_db):
        assert metadata_is_authoritative(cluster_id=CLUSTER, org_id=ORG) is False

    def test_true_after_a_success(self, in_memory_db):
        _log(in_memory_db, status="SUCCESS")
        assert metadata_is_authoritative(cluster_id=CLUSTER, org_id=ORG) is True

    def test_false_when_the_only_marker_is_in_progress(self, in_memory_db):
        # This is the shape the real writer produces: `_write_sync_log` UPSERTS
        # on (cluster, org, entity_type), so the write-ahead IN_PROGRESS replaces
        # the previous SUCCESS rather than sitting beside it. That is what makes
        # "any SUCCESS row exists" a sound reading of completeness here.
        _log(in_memory_db, status="IN_PROGRESS")
        assert metadata_is_authoritative(cluster_id=CLUSTER, org_id=ORG) is False


# ── require_authoritative_metadata ────────────────────────────────────────────


class TestRequireAuthoritativeMetadata:
    def test_passes_silently_with_a_success_marker(self, in_memory_db):
        _log(in_memory_db, status="SUCCESS")
        require_authoritative_metadata(cluster_id=CLUSTER, org_id=ORG)  # must not raise

    @pytest.mark.parametrize(
        ("seeded_status", "expected"),
        [(None, "NOT_SYNCED"), ("IN_PROGRESS", "IN_PROGRESS"), ("FAILED", "FAILED")],
    )
    def test_message_carries_the_observed_status(self, in_memory_db, seeded_status, expected):
        if seeded_status:
            _log(in_memory_db, status=seeded_status)
        with pytest.raises(StaleCacheError) as excinfo:
            require_authoritative_metadata(cluster_id=CLUSTER, org_id=ORG)
        assert excinfo.value.status == expected
        assert excinfo.value.entity_type == "metadata"
        # The user-facing string names the real state, not a generic "stale".
        assert expected in str(excinfo.value)

    @pytest.mark.parametrize(("shadow_cluster", "shadow_org"), SHADOW_SCOPES)
    def test_raises_even_when_a_sibling_scope_is_synced(self, in_memory_db, shadow_cluster, shadow_org):
        _log(in_memory_db, cluster_id=shadow_cluster, org_id=shadow_org, status="SUCCESS")
        with pytest.raises(StaleCacheError):
            require_authoritative_metadata(cluster_id=CLUSTER, org_id=ORG)
        # The sibling itself is fine — proves the seed landed and the helper
        # isn't just refusing everything.
        require_authoritative_metadata(cluster_id=shadow_cluster, org_id=shadow_org)
