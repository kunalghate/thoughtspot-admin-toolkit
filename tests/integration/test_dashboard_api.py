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
        # `deleted_confirmed_at` is what makes the feed say "Deleted" — a
        # SUCCESS export alone only means the backup exists
        # (`ts_admin/models/archive_record.py:46-55`), and the owning Job row
        # supplies the intent for sessions that confirm nothing.
        session.add(Job(id="del-1", cluster_id="c1", job_type="bulk_delete", status="COMPLETE"))
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
                    deleted_confirmed_at=now,
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
                session.add(Job(id=f"solo-{n}", cluster_id="c1", job_type="bulk_delete", status="COMPLETE"))
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
                        deleted_confirmed_at=datetime.now(tz=timezone.utc),
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
            "connections": False,
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


class TestActivityStatusCanSayFailed:
    """
    M14: the activity feed's status was `"PARTIAL" if failed else "SUCCESS"` —
    a two-way expression that can never emit FAILED, so a delete session in
    which nothing was deleted rendered as a partial success on the dashboard.

    The status now derives from `deleted_confirmed_at` (the only field that
    means "gone from ThoughtSpot") plus the owning Job's intent, so every
    session here seeds a matching `Job` row as production does.

    These tests seed into org 7 so the feed under test contains only the
    session being asserted on — the `seeded` fixture's own del-1/share-1
    sessions live in org 0. The collapse behaviour they used to lean on is now
    covered directly by `TestCollapseMergesByWorstStatus`.
    """

    URL = "/api/v1/dashboard?cluster_id=c1&org_id=7"

    @staticmethod
    def _archive(job_id: str, guid: str, status: str, *, deleted: bool = False) -> ArchiveRecord:
        return ArchiveRecord(
            cluster_id="c1",
            job_id=job_id,
            ts_guid=guid,
            name=guid,
            object_type="LIVEBOARD",
            owner_guid="u1",
            owner_name="user-u1",
            org_id=7,
            tml_export_status=status,
            deleted_confirmed_at=datetime.now(tz=timezone.utc) if deleted else None,
        )

    @staticmethod
    def _delete_job(job_id: str, status: str = "COMPLETE") -> Job:
        return Job(id=job_id, cluster_id="c1", job_type="bulk_delete", status=status)

    @staticmethod
    def _share(job_id: str, guid: str, status: str) -> ShareRecord:
        return ShareRecord(
            cluster_id="c1",
            job_id=job_id,
            org_id=7,
            object_guid=guid,
            object_name=guid,
            object_type="LIVEBOARD",
            principal_guid="g1",
            principal_name="Admins",
            principal_type="USER_GROUP",
            new_mode="READ_ONLY",
            status=status,
        )

    def _entry(self, client, kind: str) -> dict:
        activity = client.get(self.URL).json()["recent_activity"]
        # S27 non-vacuity: an empty feed must never read as a green assertion.
        assert activity, "recent_activity is empty — the status assertion below would be vacuous"
        matching = [a for a in activity if a["kind"] == kind]
        assert len(matching) == 1, f"expected exactly one {kind} entry, got {activity}"
        return matching[0]

    def test_a_delete_session_where_every_tml_export_failed_is_failed(self, client, seeded, in_memory_db):
        with Session(in_memory_db) as session:
            session.add(self._delete_job("del-allfail", "FAILED"))
            session.add(self._archive("del-allfail", "x1", "FAILED"))
            session.add(self._archive("del-allfail", "x2", "FAILED"))
            session.commit()
        entry = self._entry(client, "delete")
        assert entry["status"] == "FAILED"
        assert entry["status"] != "PARTIAL"

    def test_a_partly_failed_delete_session_is_still_partial(self, client, seeded, in_memory_db):
        """Non-vacuity: the fix must not collapse PARTIAL into FAILED."""
        with Session(in_memory_db) as session:
            session.add(self._delete_job("del-mixed", "PARTIAL"))
            session.add(self._archive("del-mixed", "x1", "SUCCESS", deleted=True))
            session.add(self._archive("del-mixed", "x2", "FAILED"))
            session.commit()
        assert self._entry(client, "delete")["status"] == "PARTIAL"

    def test_a_clean_delete_session_is_success(self, client, seeded, in_memory_db):
        with Session(in_memory_db) as session:
            session.add(self._delete_job("del-clean"))
            session.add(self._archive("del-clean", "x1", "SUCCESS", deleted=True))
            session.add(self._archive("del-clean", "x2", "SUCCESS", deleted=True))
            session.commit()
        assert self._entry(client, "delete")["status"] == "SUCCESS"

    def test_a_share_session_where_every_row_failed_is_failed(self, client, seeded, in_memory_db):
        with Session(in_memory_db) as session:
            session.add(self._share("share-allfail", "x1", "FAILED"))
            session.add(self._share("share-allfail", "x2", "FAILED"))
            session.commit()
        entry = self._entry(client, "share")
        assert entry["status"] == "FAILED"
        assert entry["status"] != "PARTIAL"

    def test_a_partly_failed_share_session_is_still_partial(self, client, seeded, in_memory_db):
        """Non-vacuity: the fix must not collapse PARTIAL into FAILED."""
        with Session(in_memory_db) as session:
            session.add(self._share("share-mixed", "x1", "SUCCESS"))
            session.add(self._share("share-mixed", "x2", "FAILED"))
            session.commit()
        assert self._entry(client, "share")["status"] == "PARTIAL"


class TestDeleteFeedCountsConfirmedDeletes:
    """
    The delete feed's verdict comes from `deleted_confirmed_at`, never from
    `tml_export_status`.

    `_execute_delete` runs Phase A (export EVERY object) to completion before
    Phase B deletes any, so a crash between the phases leaves a session where
    every row is SUCCESS-exported and every object is still live in
    ThoughtSpot. Counting exports there told the admin "Deleted 2 objects
    (TML backed up)" in green about content that was never touched — the exact
    inversion `ts_admin/models/archive_record.py:46-55` warns about.

    Archive rows alone cannot tell that crashed delete apart from a deliberate
    export-only run (F9): both are all-SUCCESS-exported with nothing confirmed.
    The owning `Job` supplies the intent, which is why every session here seeds
    one. Seeded into org 7 so the `seeded` fixture's org-0 sessions cannot
    collapse into these by label.
    """

    URL = "/api/v1/dashboard?cluster_id=c1&org_id=7"

    @staticmethod
    def _rec(job_id: str, guid: str, tml: str, *, deleted: bool = False, at=None) -> ArchiveRecord:
        rec = ArchiveRecord(
            cluster_id="c1",
            job_id=job_id,
            ts_guid=guid,
            name=guid,
            object_type="LIVEBOARD",
            owner_guid="u1",
            owner_name="user-u1",
            org_id=7,
            tml_export_status=tml,
            deleted_confirmed_at=datetime.now(tz=timezone.utc) if deleted else None,
        )
        if at is not None:
            rec.archived_at = at
        return rec

    def _delete_entries(self, client) -> list[dict]:
        activity = client.get(self.URL).json()["recent_activity"]
        assert activity, "recent_activity is empty — every assertion below would be vacuous"
        return [a for a in activity if a["kind"] == "delete"]

    def _entry(self, client) -> dict:
        entries = self._delete_entries(client)
        assert len(entries) == 1, f"expected exactly one delete entry, got {entries}"
        return entries[0]

    def test_a_crashed_delete_with_every_tml_exported_is_not_reported_as_deleted(self, client, seeded, in_memory_db):
        """F2: SUCCESS exports + zero confirmations is a failed delete, not a success."""
        with Session(in_memory_db) as session:
            session.add(Job(id="del-crash", cluster_id="c1", job_type="bulk_delete", status="FAILED"))
            session.add(self._rec("del-crash", "x1", "SUCCESS"))
            session.add(self._rec("del-crash", "x2", "SUCCESS"))
            session.commit()
        entry = self._entry(client)
        assert entry["status"] == "FAILED"
        assert "Deleted" not in entry["label"], entry["label"]

    def test_an_export_only_run_says_exported_not_deleted(self, client, seeded, in_memory_db):
        """F5/F9: the export-without-delete feature's own success state."""
        with Session(in_memory_db) as session:
            session.add(
                Job(
                    id="exp-1",
                    cluster_id="c1",
                    job_type="archive",
                    status="COMPLETE",
                    parameters=json.dumps({"action": "export", "object_ids": ["x1", "x2"]}),
                )
            )
            session.add(self._rec("exp-1", "x1", "SUCCESS"))
            session.add(self._rec("exp-1", "x2", "SUCCESS"))
            session.commit()
        entry = self._entry(client)
        assert entry["status"] == "SUCCESS"
        assert "Exported" in entry["label"], entry["label"]
        assert "Deleted" not in entry["label"], entry["label"]

    def test_a_delete_session_with_no_terminal_export_at_all_is_failed_not_partial(self, client, seeded, in_memory_db):
        """F3: a stranded PENDING row is not evidence of a partial success."""
        with Session(in_memory_db) as session:
            session.add(Job(id="del-stuck", cluster_id="c1", job_type="bulk_delete", status="FAILED"))
            session.add(self._rec("del-stuck", "x1", "FAILED"))
            session.add(self._rec("del-stuck", "x2", "PENDING"))
            session.commit()
        entry = self._entry(client)
        assert entry["status"] == "FAILED"
        assert entry["status"] != "PARTIAL"
        assert entry["label"] == "Delete failed — 0 of 2 objects deleted"

    def test_a_session_larger_than_the_old_row_window_reports_exact_counts(self, client, seeded, in_memory_db):
        """F4: 400 rows in ONE session — the counts must be the session's, not the window's."""
        at = datetime.now(tz=timezone.utc) - timedelta(minutes=5)
        with Session(in_memory_db) as session:
            session.add(Job(id="del-big", cluster_id="c1", job_type="bulk_delete", status="PARTIAL"))
            for n in range(300):
                session.add(self._rec("del-big", f"fail-{n}", "FAILED", at=at))
            for n in range(100):
                session.add(self._rec("del-big", f"ok-{n}", "SUCCESS", deleted=True, at=at))
            session.commit()
        entry = self._entry(client)
        assert entry["label"] == "Deleted 100 of 400 objects (TML backed up)"
        assert entry["status"] == "PARTIAL"

    def test_a_job_still_running_reads_as_pending(self, client, seeded, in_memory_db):
        with Session(in_memory_db) as session:
            session.add(Job(id="del-live", cluster_id="c1", job_type="bulk_delete", status="RUNNING"))
            session.add(self._rec("del-live", "x1", "PENDING"))
            session.add(self._rec("del-live", "x2", "PENDING"))
            session.commit()
        entry = self._entry(client)
        assert entry["status"] == "PENDING"
        assert entry["label"].endswith("…"), entry["label"]

    def test_an_export_only_run_that_exported_nothing_is_failed_not_success(self, client, seeded, in_memory_db):
        """
        Round-3 F1: the export limb keyed success on `failed == 0`, but
        `tml_export_status` is TRI-valued. The per-chunk reconcile loop that
        turns an unaccounted GUID into FAILED (`deletion_service.py:490-499`)
        is skipped whole when a non-`(TSAdminError, httpx.HTTPError)` exception
        escapes to the blanket handler at `deletion_service.py:715` — an
        OSError from `tml_path.write_text()` on a full or read-only TML
        directory does exactly that — so a run that wrote ZERO files lands at
        0 SUCCESS / 0 FAILED / N PENDING and used to render a green
        "Exported 2 objects to TML".
        """
        with Session(in_memory_db) as session:
            session.add(
                Job(
                    id="exp-nothing",
                    cluster_id="c1",
                    job_type="archive",
                    status="FAILED",
                    parameters=json.dumps({"action": "export", "object_ids": ["x1", "x2"]}),
                )
            )
            session.add(self._rec("exp-nothing", "x1", "PENDING"))
            session.add(self._rec("exp-nothing", "x2", "PENDING"))
            session.commit()
        entry = self._entry(client)
        assert entry["status"] == "FAILED"
        assert "Exported" not in entry["label"], entry["label"]
        assert entry["label"] == "TML export failed for 2 objects"

    def test_a_running_delete_with_some_confirmed_keeps_the_counts_but_not_the_terminal_status(
        self, client, seeded, in_memory_db
    ):
        """
        Round-3 F5: `deleted > 0` was evaluated before the in-flight check, so a
        bulk_delete at chunk 2 of 8 showed an amber PARTIAL — this repo's "some
        worked, retry the rest" — about a job that will most likely finish. The
        counts are real and stay; only the pill goes neutral.
        """
        with Session(in_memory_db) as session:
            session.add(Job(id="del-midway", cluster_id="c1", job_type="bulk_delete", status="RUNNING"))
            session.add(self._rec("del-midway", "x1", "SUCCESS", deleted=True))
            session.add(self._rec("del-midway", "x2", "SUCCESS", deleted=True))
            session.add(self._rec("del-midway", "x3", "PENDING"))
            session.add(self._rec("del-midway", "x4", "PENDING"))
            session.commit()
        entry = self._entry(client)
        assert entry["status"] == "PENDING"
        assert entry["status"] != "PARTIAL"
        assert "2 of 4 objects" in entry["label"], entry["label"]

    def test_a_session_whose_job_row_is_gone_still_renders_neutrally(self, client, seeded, in_memory_db):
        """The join is OUTER: purged job history must not delete the admin's audit feed."""
        with Session(in_memory_db) as session:
            session.add(self._rec("del-orphan", "x1", "SUCCESS"))
            session.add(self._rec("del-orphan", "x2", "FAILED"))
            session.commit()
        entry = self._entry(client)
        assert entry["status"] == "PENDING"
        assert entry["label"] == "Archived 2 objects — none deleted, 1 backed up"
        assert "Deleted" not in entry["label"]

    def test_the_feed_is_scoped_by_both_cluster_and_org(self, client, seeded, in_memory_db):
        """
        Two shadow scopes, per the S27 diagonal rule: a single diagonal shadow is
        excluded by either predicate alone, so dropping just `org_id` — or just
        `cluster_id` — would survive. Both shadows reuse the job id so a leak
        merges into the group under test and changes its counts.
        """
        with Session(in_memory_db) as session:
            session.add(Job(id="scope-1", cluster_id="c1", job_type="bulk_delete", status="COMPLETE"))
            session.add(self._rec("scope-1", "x1", "SUCCESS", deleted=True))
            session.add(self._rec("scope-1", "x2", "SUCCESS", deleted=True))
            # Shadow A: right cluster, wrong org.
            for guid in ("s1", "s2"):
                shadow = self._rec("scope-1", guid, "SUCCESS")
                shadow.org_id = 0
                session.add(shadow)
            # Shadow B: wrong cluster, right org.
            for guid in ("s3", "s4"):
                shadow = self._rec("scope-1", guid, "SUCCESS")
                shadow.cluster_id = "c2"
                session.add(shadow)
            session.commit()
        entries = self._delete_entries(client)
        assert [(e["label"], e["status"]) for e in entries] == [("Deleted 2 objects (TML backed up)", "SUCCESS")]

    def test_the_feed_timestamp_is_a_real_datetime(self, client, seeded, in_memory_db):
        """A bare string here would silently mis-sort the merged feed."""
        with Session(in_memory_db) as session:
            session.add(Job(id="del-ts", cluster_id="c1", job_type="bulk_delete", status="COMPLETE"))
            session.add(self._rec("del-ts", "x1", "SUCCESS", deleted=True))
            session.commit()
        entry = self._entry(client)
        assert isinstance(datetime.fromisoformat(entry["timestamp"]), datetime)


class TestCollapseMergesByWorstStatus:
    """
    F1: `_collapse` only overwrote a SUCCESS, so the merged status depended on
    which entry happened to be newest. A FAILED run followed by a PARTIAL one
    kept FAILED; the same pair in the other order reported PARTIAL about a run
    containing a total failure. The rank makes it worst-wins in both directions.

    Driven through `user_action` rows: their status comes straight off the
    record (so it can be any value) and two deletes of the same username
    produce a byte-identical label, which is what makes them collapse.
    """

    URL = "/api/v1/dashboard?cluster_id=c1&org_id=7"

    @staticmethod
    def _action(job_id: str, status: str, at) -> UserActionRecord:
        return UserActionRecord(
            cluster_id="c1",
            job_id=job_id,
            org_id=7,
            action_type="delete",
            from_username="ghost",
            status=status,
            executed_at=at,
        )

    def _entry(self, client) -> dict:
        activity = client.get(self.URL).json()["recent_activity"]
        matching = [a for a in activity if a["label"] == "Deleted user ghost"]
        assert len(matching) == 1, f"expected one collapsed entry, got {activity}"
        return matching[0]

    def _seed(self, in_memory_db, newest: str, oldest: str) -> None:
        now = datetime.now(tz=timezone.utc)
        with Session(in_memory_db) as session:
            session.add(self._action("ua-new", newest, now))
            session.add(self._action("ua-old", oldest, now - timedelta(minutes=5)))
            session.commit()

    def test_failed_then_partial_collapses_to_failed(self, client, seeded, in_memory_db):
        self._seed(in_memory_db, newest="FAILED", oldest="PARTIAL")
        entry = self._entry(client)
        assert entry["count"] == 2
        assert entry["status"] == "FAILED"

    def test_partial_then_failed_also_collapses_to_failed(self, client, seeded, in_memory_db):
        """The other direction — the pre-rank code reported PARTIAL here."""
        self._seed(in_memory_db, newest="PARTIAL", oldest="FAILED")
        entry = self._entry(client)
        assert entry["count"] == 2
        assert entry["status"] == "FAILED"

    def test_two_successes_stay_success(self, client, seeded, in_memory_db):
        """Non-vacuity: the rank must not promote a run that never failed."""
        self._seed(in_memory_db, newest="SUCCESS", oldest="SUCCESS")
        entry = self._entry(client)
        assert entry["count"] == 2
        assert entry["status"] == "SUCCESS"


class TestShareFeedStatus:
    """The share feed's three-way status, and its distinct object/principal counts."""

    URL = "/api/v1/dashboard?cluster_id=c1&org_id=7"

    @staticmethod
    def _share(job_id: str, obj: str, principal: str, status: str) -> ShareRecord:
        return ShareRecord(
            cluster_id="c1",
            job_id=job_id,
            org_id=7,
            object_guid=obj,
            object_name=obj,
            object_type="LIVEBOARD",
            principal_guid=principal,
            principal_name=principal,
            principal_type="USER_GROUP",
            new_mode="READ_ONLY",
            status=status,
        )

    def _entry(self, client) -> dict:
        activity = client.get(self.URL).json()["recent_activity"]
        assert activity, "recent_activity is empty — the assertion below would be vacuous"
        matching = [a for a in activity if a["kind"] == "share"]
        assert len(matching) == 1, f"expected exactly one share entry, got {activity}"
        return matching[0]

    def test_a_running_share_job_is_pending_even_when_every_landed_row_succeeded(self, client, seeded, in_memory_db):
        """
        Round-3 F2. The predecessor of this test hand-seeded
        `ShareRecord(status="PENDING")`, a value NO writer can produce —
        `bulk_sharing_service.py:891` writes only
        `"FAILED" if chunk_error else "SUCCESS"` — so it exercised a dead limb.

        The REACHABLE in-flight case is this one: that writer commits per
        (object_type, 50-GUID chunk) INSIDE its loop
        (`bulk_sharing_service.py:843-895`), so a poll between chunks sees a
        set of rows that all succeeded belonging to a job that is still
        running and may still end FAILED. Row status alone cannot tell that
        apart from "finished"; the `Job` row can.
        """
        with Session(in_memory_db) as session:
            session.add(Job(id="share-live", cluster_id="c1", job_type="bulk_share", status="RUNNING"))
            session.add(self._share("share-live", "x1", "g1", "SUCCESS"))
            session.add(self._share("share-live", "x2", "g1", "SUCCESS"))
            session.commit()
        entry = self._entry(client)
        assert entry["status"] == "PENDING"
        assert entry["status"] != "SUCCESS"
        assert "in progress" in entry["label"], entry["label"]

    def test_a_completed_share_job_is_still_a_plain_success(self, client, seeded, in_memory_db):
        """Non-vacuity for the test above: the Job join must not make everything PENDING."""
        with Session(in_memory_db) as session:
            session.add(Job(id="share-done", cluster_id="c1", job_type="bulk_share", status="COMPLETE"))
            session.add(self._share("share-done", "x1", "g1", "SUCCESS"))
            session.add(self._share("share-done", "x2", "g1", "SUCCESS"))
            session.commit()
        entry = self._entry(client)
        assert entry["status"] == "SUCCESS"
        assert entry["label"] == "Updated sharing on 2 objects for 1 principal"

    def test_a_failed_share_job_is_failed_even_when_every_row_says_success(self, client, seeded, in_memory_db):
        """
        Round-5 FIX 1. This assertion was `== "PARTIAL"` in round 4; it is
        CORRECTED to `== "FAILED"` here — the right answer, not a relaxed one.
        M14's acceptance criteria say verbatim that a job in which zero items
        succeeded reports FAILED, never PARTIAL, and round 4 let `Job.status`
        veto SUCCESS without letting it assert FAILED, so this session fell
        straight through to the PARTIAL limb — which emits the SAME
        "Updated sharing on N objects" f-string as SUCCESS.

        `ShareRecord.status` is optimistic: the single writer at
        `bulk_sharing_service.py:891` commits `"FAILED" if chunk_error else
        "SUCCESS"` per chunk BEFORE verification, and `_verify_share`
        (`bulk_sharing_service.py:926-927`) adjusts only the JOB when
        ThoughtSpot's read-back says the grant did not land. So this exact
        shape — terminal FAILED job, 100% SUCCESS rows, `failed == 0` — is
        reachable, and the label must not affirm an update that never happened.

        Round-6 FIX 1 adds the OTHER half of that: the label must not affirm
        the failure either. This fixture is byte-identical to the
        `_recover_stuck_jobs` shape below, so the same rendered sentence has to
        be honest under both provenances — hence the NON-AFFIRMING wording.
        """
        with Session(in_memory_db) as session:
            session.add(Job(id="share-unverified", cluster_id="c1", job_type="bulk_share", status="FAILED"))
            session.add(self._share("share-unverified", "x1", "g1", "SUCCESS"))
            session.add(self._share("share-unverified", "x2", "g1", "SUCCESS"))
            session.commit()
        entry = self._entry(client)
        assert entry["status"] != "SUCCESS"
        assert entry["status"] == "FAILED"
        assert "Updated sharing on" not in entry["label"], entry["label"]
        assert "did not complete" in entry["label"], entry["label"]
        assert "failed" not in entry["label"].lower(), entry["label"]

    def test_a_share_job_failed_by_restart_recovery_does_not_claim_the_update_failed(
        self, client, seeded, in_memory_db
    ):
        """
        Round-6 FIX 1, the provenance the FAILED limb cannot see. The writer
        here is `_recover_stuck_jobs` (`ts_admin/main.py:163`), which sets
        `job.status = "FAILED"` on every RUNNING job at startup after a server
        restart — routine on this stack, because the backend is commonly
        hand-launched without `--reload`.

        Those SUCCESS `ShareRecord` rows were committed per 50-GUID chunk
        INSIDE the loop at `bulk_sharing_service.py:891`, each after a 204 from
        `security/metadata/share`. THE GRANTS THEY DESCRIBE MAY BE LIVE IN
        THOUGHTSPOT RIGHT NOW — only the chunks the restart cut off did not
        happen. `(succeeded, failed, Job.status)` is identical to the
        `_verify_share` shape above where nothing landed, so the status stays
        FAILED (conservative; a re-share is idempotent) but the label must NOT
        assert that the update failed for these objects.

        `frontend/pages/dashboard.tsx` renders `label` verbatim next to a
        colour dot with no status word of its own, so this sentence is the
        entire message the admin reads.
        """
        with Session(in_memory_db) as session:
            session.add(
                Job(
                    id="share-restarted",
                    cluster_id="c1",
                    job_type="bulk_share",
                    status="FAILED",
                    error="Server restarted while job was running",
                )
            )
            session.add(self._share("share-restarted", "x1", "g1", "SUCCESS"))
            session.add(self._share("share-restarted", "x2", "g1", "SUCCESS"))
            session.commit()
        entry = self._entry(client)
        assert entry["status"] == "FAILED"
        # Not an affirming failure claim about grants that may be live...
        assert "failed" not in entry["label"].lower(), entry["label"]
        # ...and not an affirming success claim either.
        assert "Updated sharing on" not in entry["label"], entry["label"]
        assert "did not complete" in entry["label"], entry["label"]

    def test_a_failed_share_job_with_some_failed_rows_is_failed_not_partial(self, client, seeded, in_memory_db):
        """
        The second reachable instance of the same defect: some chunks errored
        (so `failed > 0`) AND the read-back landed nothing, so
        `bulk_sharing_service.py:926-927` overrides the optimistic PARTIAL to
        FAILED. `succeeded > 0 and failed > 0` misses the SUCCESS limb on the
        row counts alone, so only the explicit `Job.status == "FAILED"` limb
        keeps this out of PARTIAL.
        """
        with Session(in_memory_db) as session:
            session.add(Job(id="share-mixed", cluster_id="c1", job_type="bulk_share", status="FAILED"))
            session.add(self._share("share-mixed", "x1", "g1", "SUCCESS"))
            session.add(self._share("share-mixed", "x2", "g1", "FAILED"))
            session.commit()
        entry = self._entry(client)
        assert entry["status"] == "FAILED"
        assert "Updated sharing on" not in entry["label"], entry["label"]

    def test_a_partial_share_job_is_not_success_even_when_every_row_says_success(self, client, seeded, in_memory_db):
        """
        The skipped-GUID / user-cancel shape: `bulk_sharing_service.py:919-920`
        marks the job PARTIAL for GUIDs that were never attempted, and those
        GUIDs get NO `ShareRecord` row at all — so the rows are unanimously
        SUCCESS while the request was only partly served.
        """
        with Session(in_memory_db) as session:
            session.add(Job(id="share-skipped", cluster_id="c1", job_type="bulk_share", status="PARTIAL"))
            session.add(self._share("share-skipped", "x1", "g1", "SUCCESS"))
            session.add(self._share("share-skipped", "x2", "g1", "SUCCESS"))
            session.commit()
        entry = self._entry(client)
        assert entry["status"] != "SUCCESS"
        assert entry["status"] == "PARTIAL"

    def test_a_partial_share_job_with_failed_rows_does_not_reuse_the_success_label(self, client, seeded, in_memory_db):
        """
        Round-6 FIX 2. One chunk raised `TSAdminError`, so `failed_records` is
        non-empty and `bulk_sharing_service.py:919-920` marks the job PARTIAL
        while that chunk's GUIDs get FAILED `ShareRecord` rows.

        `n` in the feed is `count(distinct object_guid)` over ALL rows of the
        session, SUCCESS and FAILED alike, so the PARTIAL limb emitting the
        SUCCESS limb's `f"Updated sharing on {n} objects…"` claimed 2 objects
        were updated when only 1 was. The two limbs must be distinguishable in
        the rendered sentence, and the PARTIAL one must not assert a count it
        does not have.
        """
        with Session(in_memory_db) as session:
            session.add(Job(id="share-halfway", cluster_id="c1", job_type="bulk_share", status="PARTIAL"))
            session.add(self._share("share-halfway", "x1", "g1", "SUCCESS"))
            session.add(self._share("share-halfway", "x2", "g1", "FAILED"))
            session.commit()
        entry = self._entry(client)
        assert entry["status"] == "PARTIAL"
        # `n` is 2 (both rows), but only x1 was updated — never the SUCCESS f-string.
        assert entry["label"] != "Updated sharing on 2 objects for 1 principal"
        assert not entry["label"].startswith("Updated sharing on"), entry["label"]
        assert "Partially updated sharing" in entry["label"], entry["label"]
        # ...and distinguishable from the FAILED limb, which shares no wording.
        assert "did not complete" not in entry["label"], entry["label"]

    def test_a_share_session_with_no_job_row_at_all_is_still_success(self, client, seeded, in_memory_db):
        """
        This pins FORWARD-INSURANCE, not a reachable production state, and it
        is NOT the non-vacuity guard for the FAILED limb (the COMPLETE-job test
        above is). NO writer produces a missing `Job` row today: nothing in
        `ts_admin/` deletes one, `api/jobs.py`'s only mutating route sets
        `is_cancelled`, `database._REBUILDABLE_SENTINELS` does not list `jobs`,
        and `config.delete_cluster` drops only the `Cluster` row. The LEFT
        OUTER JOIN can still legitimately yield NULL the day a job-retention or
        pruning feature exists, and the `None` disjunct fails safe there — with
        the `Job` row absent, `row.status` is NULL and an all-SUCCESS session
        must stay SUCCESS. Narrowing the guard to `("COMPLETE",)` turns this
        red, which is what keeps the disjunct load-bearing.
        """
        with Session(in_memory_db) as session:
            session.add(self._share("share-orphan", "x1", "g1", "SUCCESS"))
            session.add(self._share("share-orphan", "x2", "g1", "SUCCESS"))
            session.commit()
        entry = self._entry(client)
        assert entry["status"] == "SUCCESS"
        assert entry["label"] == "Updated sharing on 2 objects for 1 principal"

    def test_the_share_feed_is_scoped_by_both_cluster_and_org(self, client, seeded, in_memory_db):
        """
        S27 diagonal, re-confirmed after the `Job` join was added: a single
        diagonal shadow is excluded by EITHER predicate alone, so both shadows
        are needed for `cluster_id` and `org_id` each to be independently
        load-bearing. Both reuse the job id so a leak merges into the group
        under test and changes its distinct counts.
        """
        with Session(in_memory_db) as session:
            session.add(Job(id="share-scope", cluster_id="c1", job_type="bulk_share", status="COMPLETE"))
            session.add(self._share("share-scope", "x1", "g1", "SUCCESS"))
            session.add(self._share("share-scope", "x2", "g1", "SUCCESS"))
            # Shadow A: right cluster, wrong org.
            for obj in ("s1", "s2"):
                shadow = self._share("share-scope", obj, "g9", "FAILED")
                shadow.org_id = 0
                session.add(shadow)
            # Shadow B: wrong cluster, right org.
            for obj in ("s3", "s4"):
                shadow = self._share("share-scope", obj, "g9", "FAILED")
                shadow.cluster_id = "c2"
                session.add(shadow)
            session.commit()
        entry = self._entry(client)
        assert (entry["label"], entry["status"]) == ("Updated sharing on 2 objects for 1 principal", "SUCCESS")

    def test_a_wholly_failed_share_session_says_so_in_the_label(self, client, seeded, in_memory_db):
        with Session(in_memory_db) as session:
            session.add(self._share("share-dead", "x1", "g1", "FAILED"))
            session.add(self._share("share-dead", "x2", "g1", "FAILED"))
            session.commit()
        entry = self._entry(client)
        assert entry["status"] == "FAILED"
        assert "failed" in entry["label"], entry["label"]

    def test_a_large_share_session_reports_exact_distinct_counts(self, client, seeded, in_memory_db):
        """100 objects × 4 principals = 400 rows, well past the old 300-row window."""
        with Session(in_memory_db) as session:
            for obj in range(100):
                for principal in range(4):
                    status = "SUCCESS" if obj >= 75 else "FAILED"
                    session.add(self._share("share-big", f"obj-{obj}", f"g-{principal}", status))
            session.commit()
        entry = self._entry(client)
        assert "100 objects for 4 principals" in entry["label"], entry["label"]
        assert entry["status"] == "PARTIAL"
