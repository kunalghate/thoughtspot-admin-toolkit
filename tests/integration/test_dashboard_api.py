"""
Integration tests for the Dashboard API.

The dashboard is one aggregate read over the SQLite cache: entity counts,
recent jobs, and a merged audit-activity feed. These tests seed two clusters
to prove counts are scoped, and seed each audit source to prove the feed
groups bulk rows into per-session entries.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select

from ts_admin.models.archive_record import ArchiveRecord
from ts_admin.models.cache.ts_group import CachedGroup
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cache.ts_tag import CachedTag
from ts_admin.models.cache.ts_user import CachedUser, UserOrgMembership
from ts_admin.models.cluster import Cluster
from ts_admin.models.job import Job
from ts_admin.models.share_record import ShareRecord
from ts_admin.models.sync_log import SyncLog
from ts_admin.models.user_action_record import UserActionRecord


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
    now = datetime.now(tz=timezone.utc)
    with Session(in_memory_db) as session:
        for cid in ("c1", "c2"):
            session.add(
                Cluster(
                    id=cid,
                    name=cid,
                    url=f"https://{cid}.thoughtspot.cloud",
                    username="admin",
                    auth_type="basic",
                )
            )

        # Two users in c1/org0, one in c2 — counts must not bleed across.
        for cid, guid in [("c1", "u1"), ("c1", "u2"), ("c2", "u9")]:
            session.add(
                CachedUser(
                    cluster_id=cid,
                    ts_guid=guid,
                    username=f"user-{guid}",
                    display_name=guid,
                    email=f"{guid}@x.io",
                    status="ACTIVE",
                    synced_at=now,
                )
            )
            session.add(UserOrgMembership(cluster_id=cid, ts_guid=guid, org_id=0, synced_at=now))

        session.add(
            CachedGroup(
                cluster_id="c1",
                org_id=0,
                ts_guid="g1",
                name="admins",
                display_name="Admins",
                description="",
                privileges="[]",
                synced_at=now,
            )
        )
        session.add(CachedTag(cluster_id="c1", org_id=0, ts_guid="t1", name="Stale"))
        session.add(
            CachedMetadata(
                cluster_id="c1",
                org_id=0,
                ts_guid="m1",
                name="LB",
                object_type="LIVEBOARD",
                owner_guid="u1",
                owner_name="user-u1",
                tag_names=json.dumps([]),
                last_accessed_at=now,
                modified_at=now,
                synced_at=now,
            )
        )

        # Jobs: one recent failure, one old failure (outside the 7d window).
        session.add(Job(id="j-new", cluster_id="c1", job_type="sync:users", status="FAILED", error="boom"))
        old = Job(id="j-old", cluster_id="c1", job_type="sync:users", status="FAILED")
        old.created_at = now - timedelta(days=30)
        session.add(old)

        # Audit rows: a 2-object deletion session, a share session, a transfer.
        for guid in ("m1", "m2"):
            session.add(
                ArchiveRecord(
                    cluster_id="c1",
                    job_id="del-1",
                    ts_guid=guid,
                    name=guid,
                    object_type="LIVEBOARD",
                    owner_guid="u1",
                    owner_name="user-u1",
                    org_id=0,
                    tml_export_status="SUCCESS",
                )
            )
        session.add(
            ShareRecord(
                cluster_id="c1",
                job_id="share-1",
                org_id=0,
                object_guid="m1",
                object_name="LB",
                object_type="LIVEBOARD",
                principal_guid="g1",
                principal_name="Admins",
                principal_type="USER_GROUP",
                new_mode="READ_ONLY",
            )
        )
        session.add(
            UserActionRecord(
                cluster_id="c1",
                job_id="xfer-1",
                org_id=0,
                action_type="transfer",
                from_username="alice",
                to_username="bob",
                status="SUCCESS",
            )
        )
        session.commit()


class TestDashboard:
    def test_counts_are_cluster_scoped(self, client, seeded):
        r = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0")
        assert r.status_code == 200, r.text
        counts = r.json()["counts"]
        assert counts["users"] == 2
        assert counts["groups"] == 1
        assert counts["tags"] == 1
        assert counts["objects_total"] == 1
        assert counts["objects_by_type"] == {"LIVEBOARD": 1}

        r2 = client.get("/api/v1/dashboard?cluster_id=c2&org_id=0")
        assert r2.json()["counts"]["users"] == 1
        assert r2.json()["counts"]["groups"] == 0

    def test_failed_jobs_window_and_recent_jobs(self, client, seeded):
        body = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()
        # Only the recent failure counts toward the 7-day window.
        assert body["failed_jobs_7d"] == 1
        ids = [j["id"] for j in body["recent_jobs"]]
        assert "j-new" in ids

    def test_activity_groups_bulk_rows_per_session(self, client, seeded):
        body = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()
        labels = [a["label"] for a in body["recent_activity"]]
        # 2 ArchiveRecord rows → ONE grouped deletion entry.
        assert "Deleted 2 objects (TML backed up)" in labels
        assert "Updated sharing on 1 object for 1 principal" in labels
        assert "Transferred ownership: alice → bob" in labels
        assert len(body["recent_activity"]) == 3

    def test_empty_cluster_returns_zeroes(self, client, seeded):
        body = client.get("/api/v1/dashboard?cluster_id=c2&org_id=99").json()
        assert body["counts"]["objects_total"] == 0
        assert body["recent_activity"] == []

    def test_activity_older_than_the_window_is_hidden(self, client, seeded, in_memory_db):
        """A 'recent' feed showing months-old rows reads as current activity."""
        old = datetime.now(tz=timezone.utc) - timedelta(days=120)
        with Session(in_memory_db) as session:
            session.add(
                UserActionRecord(
                    cluster_id="c1",
                    job_id="xfer-ancient",
                    org_id=0,
                    action_type="delete",
                    from_username="ghost",
                    status="SUCCESS",
                    executed_at=old,
                )
            )
            session.commit()
        labels = [a["label"] for a in client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()["recent_activity"]]
        assert "Deleted user ghost" not in labels

    def test_identical_activity_entries_collapse_with_a_count(self, client, seeded, in_memory_db):
        """Four single-object deletes are one event to an admin, not four rows."""
        with Session(in_memory_db) as session:
            for n in range(4):
                session.add(
                    ArchiveRecord(
                        cluster_id="c1",
                        job_id=f"solo-{n}",
                        ts_guid=f"solo-{n}",
                        name=f"solo-{n}",
                        object_type="LIVEBOARD",
                        owner_guid="u1",
                        owner_name="user-u1",
                        org_id=0,
                        tml_export_status="SUCCESS",
                    )
                )
            session.commit()
        activity = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()["recent_activity"]
        solo = [a for a in activity if a["label"] == "Deleted 1 object (TML backed up)"]
        assert len(solo) == 1
        assert solo[0]["count"] == 4

    def test_failed_job_count_survives_a_busy_cluster(self, client, seeded, in_memory_db):
        """The window was counted from the newest 200 jobs, so failures fell out of it."""
        with Session(in_memory_db) as session:
            for n in range(250):
                session.add(Job(id=f"noise-{n}", cluster_id="c1", job_type="sync:metadata", status="COMPLETE"))
            session.commit()
        body = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()
        assert body["failed_jobs_7d"] == 1  # j-new, still counted behind 250 newer jobs

    def test_recent_jobs_carry_the_failure_reason(self, client, seeded):
        jobs = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()["recent_jobs"]
        failed = next(j for j in jobs if j["id"] == "j-new")
        assert failed["error"] == "boom"
        assert "error_type" in failed

    def test_running_jobs_are_reported(self, client, seeded, in_memory_db):
        with Session(in_memory_db) as session:
            session.add(
                Job(id="j-run", cluster_id="c1", job_type="sync:metadata", status="RUNNING", progress=3, total=10)
            )
            session.commit()
        running = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()["running_jobs"]
        assert [(j["id"], j["progress"], j["total"]) for j in running] == [("j-run", 3, 10)]


class TestNeverSyncedVersusZero:
    def test_unsynced_entity_is_flagged_rather_than_reported_as_zero(self, client, seeded):
        """`tags: 0` on a cluster that never ran a tag sync is a lie, not a count."""
        body = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()
        assert body["synced"]["tags"] is False
        assert body["synced"]["users"] is False

    def test_synced_flag_flips_once_a_successful_sync_is_logged(self, client, seeded, in_memory_db):
        with Session(in_memory_db) as session:
            session.add(SyncLog(cluster_id="c1", org_id=0, entity_type="tags", record_count=1, status="SUCCESS"))
            session.commit()
        body = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()
        assert body["synced"]["tags"] is True

    def test_no_record_count_trend_is_published(self, client, seeded, in_memory_db):
        """
        The payload carried a `deltas` map that was structurally always 0.

        It diffed "the two most recent successful syncs" of an entity, but no
        writer appends to `sync_log` — `sync_service._write_sync_log` and
        `lineage_service._write_dependencies_sync_log` both upsert the single
        (cluster, org, entity) row — so the query could never return two rows and
        the Dashboard's trend indicator never rendered once. The test that used
        to live here hand-inserted two rows, a state production cannot reach,
        which is precisely why the dead field survived review.

        Pinned as an ABSENCE: an always-zero field is worse than no field, and
        re-adding it needs a stored previous count (see
        `test_sync_log_keeps_no_history_so_there_is_no_record_count_trend`).
        """
        now = datetime.now(tz=timezone.utc)
        with Session(in_memory_db) as session:
            session.add(
                SyncLog(
                    cluster_id="c1",
                    org_id=0,
                    entity_type="users",
                    record_count=360,
                    status="SUCCESS",
                    synced_at=now,
                )
            )
            session.commit()

        body = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()

        assert "deltas" not in body
        # ...and the rest of the sync state the same query feeds is unaffected.
        assert body["synced"]["users"] is True
        assert body["synced_at"]["users"] is not None


class TestCacheFreshness:
    def test_never_synced_entity_reports_no_timestamp(self, client, seeded):
        body = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()
        assert body["synced_at"]["tags"] is None
        assert body["synced_at"]["dependencies"] is None

    def test_reports_the_most_recent_successful_sync_per_entity(self, client, seeded, in_memory_db):
        now = datetime.now(tz=timezone.utc)
        older, latest = now - timedelta(hours=5), now - timedelta(minutes=12)
        with Session(in_memory_db) as session:
            for synced_at in (older, latest):
                session.add(
                    SyncLog(
                        cluster_id="c1",
                        org_id=0,
                        entity_type="users",
                        record_count=360,
                        status="SUCCESS",
                        synced_at=synced_at,
                    )
                )
            # A different entity keeps its own clock — syncs are independent.
            session.add(
                SyncLog(
                    cluster_id="c1",
                    org_id=0,
                    entity_type="groups",
                    record_count=234,
                    status="SUCCESS",
                    synced_at=older,
                )
            )
            session.commit()
        body = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()
        assert body["synced_at"]["users"].startswith(latest.replace(tzinfo=None).isoformat()[:16])
        assert body["synced_at"]["groups"].startswith(older.replace(tzinfo=None).isoformat()[:16])

    def test_a_failed_sync_does_not_advance_the_freshness_clock(self, client, seeded, in_memory_db):
        """A failed attempt leaves the cache exactly as old as it already was."""
        now = datetime.now(tz=timezone.utc)
        success, failure = now - timedelta(hours=3), now
        with Session(in_memory_db) as session:
            session.add(
                SyncLog(
                    cluster_id="c1",
                    org_id=0,
                    entity_type="metadata",
                    record_count=2650,
                    status="SUCCESS",
                    synced_at=success,
                )
            )
            session.add(
                SyncLog(
                    cluster_id="c1",
                    org_id=0,
                    entity_type="metadata",
                    record_count=0,
                    status="FAILED",
                    synced_at=failure,
                )
            )
            session.commit()
        body = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()
        assert body["synced_at"]["metadata"].startswith(success.replace(tzinfo=None).isoformat()[:16])

    def test_freshness_is_scoped_to_the_cluster(self, client, seeded, in_memory_db):
        with Session(in_memory_db) as session:
            session.add(SyncLog(cluster_id="c2", org_id=0, entity_type="tags", record_count=9, status="SUCCESS"))
            session.commit()
        body = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()
        assert body["synced_at"]["tags"] is None


class TestAttentionSignals:
    @pytest.fixture
    def synced_users_and_groups(self, in_memory_db):
        with Session(in_memory_db) as session:
            for entity in ("users", "groups", "metadata"):
                session.add(SyncLog(cluster_id="c1", org_id=0, entity_type=entity, record_count=1, status="SUCCESS"))
            session.commit()

    def test_counts_inactive_users_empty_groups_and_ungrouped_users(
        self, client, seeded, in_memory_db, synced_users_and_groups
    ):
        with Session(in_memory_db) as session:
            user = session.exec(select(CachedUser).where(CachedUser.ts_guid == "u1")).one()
            user.status = "INACTIVE"
            session.add(user)
            session.commit()
        attention = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()["attention"]
        assert attention["inactive_users"] == 1
        assert attention["empty_groups"] == 1  # g1 has no members
        assert attention["users_without_group"] == 2  # u1, u2

    def test_orphaned_content_is_content_whose_owner_is_gone(
        self, client, seeded, in_memory_db, synced_users_and_groups
    ):
        with Session(in_memory_db) as session:
            session.add(
                CachedMetadata(
                    cluster_id="c1",
                    org_id=0,
                    ts_guid="m-orphan",
                    name="Orphan",
                    object_type="LIVEBOARD",
                    owner_guid="deleted-user",
                    owner_name="Departed",
                    tag_names=json.dumps([]),
                )
            )
            session.commit()
        attention = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()["attention"]
        assert attention["orphaned_content"] == 1  # m1 (owner u1) is not counted

    def test_signals_stay_silent_until_their_prerequisites_have_synced(self, client, seeded, in_memory_db):
        """Without a user sync every object looks orphaned — do not raise a false alarm."""
        with Session(in_memory_db) as session:
            session.add(
                CachedMetadata(
                    cluster_id="c1",
                    org_id=0,
                    ts_guid="m-orphan",
                    name="Orphan",
                    object_type="LIVEBOARD",
                    owner_guid="deleted-user",
                    owner_name="Departed",
                    tag_names=json.dumps([]),
                )
            )
            session.commit()
        attention = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()["attention"]
        assert attention == {
            "inactive_users": 0,
            "users_without_group": 0,
            "empty_groups": 0,
            "orphaned_content": 0,
        }


class TestSyncInFlightFlag:
    """`synced[entity]` is False for the whole duration of a HEALTHY sync.

    `_sync_metadata` writes an IN_PROGRESS marker before it deletes the cache,
    and `_write_sync_log` UPSERTS the single (cluster, org, entity) row — so the
    previous SUCCESS row is gone until the sync finishes. Reading `synced` alone,
    the dashboard told the admin their content was "Never synced — sync now"
    while a perfectly normal multi-minute sync was running, and invited a second
    concurrent one. `syncing[entity]` is what tells the two states apart.

    Showing "—" for the COUNT during this window is correct and stays: the cache
    genuinely is mid-delete. It was the label that lied.
    """

    def test_syncing_is_false_for_everything_by_default(self, client, seeded):
        assert client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()["syncing"] == {
            "users": False,
            "groups": False,
            "metadata": False,
            "tags": False,
            "dependencies": False,
        }

    def test_an_in_progress_marker_reports_syncing_not_never_synced(self, client, seeded, in_memory_db):
        with Session(in_memory_db) as session:
            session.add(
                SyncLog(cluster_id="c1", org_id=0, entity_type="metadata", status="IN_PROGRESS", record_count=0)
            )
            session.commit()
        body = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()
        assert body["syncing"]["metadata"] is True
        # Still not certified — the count really is unknown right now.
        assert body["synced"]["metadata"] is False
        # Anti-vacuity: a sibling entity with no marker at all is NOT in flight.
        assert body["syncing"]["users"] is False

    def test_a_completed_sync_is_synced_and_not_syncing(self, client, seeded, in_memory_db):
        with Session(in_memory_db) as session:
            session.add(SyncLog(cluster_id="c1", org_id=0, entity_type="metadata", status="SUCCESS", record_count=3))
            session.commit()
        body = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()
        assert body["synced"]["metadata"] is True
        assert body["syncing"]["metadata"] is False
