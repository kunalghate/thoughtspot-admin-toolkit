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
import respx
from sqlmodel import create_engine

from ts_admin.services import connection_status
from ts_admin.services.connection_status import ConnectionState
from ts_admin.ts_client.exceptions import (
    TSAuthenticationError,
    TSConnectionError,
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
    # "c2" is the second cluster used by the cluster-routing tests below; a
    # successful run_sync marks it connected, and that must not leak between tests.
    for cid in (CLUSTER_ID, "c2"):
        connection_status.clear(cid)
    yield
    for cid in (CLUSTER_ID, "c2"):
        connection_status.clear(cid)


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

    async def _boom(*, org_id: int, job_id: str, target_cluster_id: str | None = None) -> None:
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
async def test_user_resync_updates_timestamps_on_existing_rows(monkeypatch, in_memory_db, patched_config):
    """Re-syncing must refresh created_at/modified_at on rows that already exist.
    Rows cached before timestamp parsing landed have NULLs — if the upsert's
    update branch skips these fields, they stay NULL forever no matter how many
    times the admin re-syncs."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    created = datetime(2025, 1, 1, tzinfo=timezone.utc)
    modified = datetime(2025, 2, 1, tzinfo=timezone.utc)

    from ts_admin.database import get_session
    from ts_admin.models.cache.ts_user import CachedUser

    # Pre-existing cached row with NULL timestamps (pre-fix state).
    with get_session() as session:
        session.add(
            CachedUser(
                cluster_id=CLUSTER_ID,
                ts_guid="guid-a",
                username="stale-name",
                display_name="Stale",
                email="a@example.io",
                status="ACTIVE",
                created_at=None,
                modified_at=None,
                synced_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    fresh = SimpleNamespace(
        id="guid-a",
        name="user-a",
        display_name="User A",
        email="a@example.io",
        status=SimpleNamespace(value="ACTIVE"),
        created=created,
        modified=modified,
    )

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def search_users(self, *, org_id):
            yield [fresh]

    monkeypatch.setattr(
        "ts_admin.config.ClusterConfig.build_auth_strategy",
        lambda self, org_id=None: None,
    )
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", FakeClient)

    from sqlmodel import select

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("users")
    await run_sync(entity_type="users", org_id=0, job_id=job_id)

    status, _ = _job_status(job_id)
    assert status == "COMPLETE"

    with get_session() as session:
        row = session.exec(
            select(CachedUser).where(CachedUser.cluster_id == CLUSTER_ID, CachedUser.ts_guid == "guid-a")
        ).one()
    assert row.username == "user-a"
    assert row.created_at == created.replace(tzinfo=None) or row.created_at == created
    assert row.modified_at == modified.replace(tzinfo=None) or row.modified_at == modified


@pytest.mark.anyio
async def test_group_sync_populates_memberships_and_purges_stale(monkeypatch, in_memory_db, patched_config):
    """Group sync must write user_group_memberships rows (they power is_admin
    and UserDetail.groups), rewrite them on re-sync, and purge groups that no
    longer exist upstream — otherwise deleted groups linger forever."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from sqlmodel import select

    from ts_admin.database import get_session
    from ts_admin.models.cache.ts_group import CachedGroup
    from ts_admin.models.cache.ts_user import UserGroupMembership

    now = datetime.now(timezone.utc)

    def _group(guid: str, members: list[str], author: str = "u-creator"):
        return SimpleNamespace(
            id=guid,
            name=f"group-{guid}",
            display_name=f"Group {guid}",
            description="",
            privileges=["ADMINISTRATION"],
            member_users=members,
            author_id=author,
            created=now,
            modified=now,
        )

    pages: list[list] = [[_group("g1", ["u1", "u2"]), _group("g2", ["u1"])]]

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def search_groups(self, *, org_id):
            for page in pages:
                yield page

    monkeypatch.setattr(
        "ts_admin.config.ClusterConfig.build_auth_strategy",
        lambda self, org_id=None: None,
    )
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", FakeClient)

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("groups")
    await run_sync(entity_type="groups", org_id=0, job_id=job_id)
    status, _ = _job_status(job_id)
    assert status == "COMPLETE"

    def _memberships() -> set[tuple[str, str]]:
        with get_session() as session:
            rows = session.exec(select(UserGroupMembership).where(UserGroupMembership.cluster_id == CLUSTER_ID)).all()
        return {(r.user_guid, r.group_guid) for r in rows}

    assert _memberships() == {("u1", "g1"), ("u2", "g1"), ("u1", "g2")}

    # The creator GUID (groups/search `author_id`) is persisted on insert — it
    # is the only source for the Groups grid's "Created by" column.
    with get_session() as session:
        authors = session.exec(
            select(CachedGroup.ts_guid, CachedGroup.author_guid).where(CachedGroup.cluster_id == CLUSTER_ID)
        ).all()
    assert dict(authors) == {"g1": "u-creator", "g2": "u-creator"}

    # Re-sync: u2 left g1, g2 was deleted upstream, and g1 changed hands.
    pages[:] = [[_group("g1", ["u1"], author="u-newowner")]]
    job_id = _make_job("groups")
    await run_sync(entity_type="groups", org_id=0, job_id=job_id)
    status, _ = _job_status(job_id)
    assert status == "COMPLETE"

    # Membership rewritten (no duplicates, no stale members), stale group purged.
    assert _memberships() == {("u1", "g1")}
    # ...and the update path refreshes author_guid, not just the insert path.
    with get_session() as session:
        author = session.exec(
            select(CachedGroup.author_guid).where(CachedGroup.cluster_id == CLUSTER_ID, CachedGroup.ts_guid == "g1")
        ).first()
    assert author == "u-newowner"
    with get_session() as session:
        guids = session.exec(select(CachedGroup.ts_guid).where(CachedGroup.cluster_id == CLUSTER_ID)).all()
    assert list(guids) == ["g1"]

    # A sweep that returns NOTHING must not purge. SQLAlchemy renders
    # `not_in(<empty>)` as `NOT IN (NULL) OR (1 = 1)` — always true — so an
    # unguarded purge here would delete every group and membership for the org
    # on any empty response (wrong org context, transient upstream blip).
    pages[:] = []
    job_id = _make_job("groups")
    await run_sync(entity_type="groups", org_id=0, job_id=job_id)
    status, _ = _job_status(job_id)
    assert status == "COMPLETE"

    assert _memberships() == {("u1", "g1")}
    with get_session() as session:
        guids = session.exec(select(CachedGroup.ts_guid).where(CachedGroup.cluster_id == CLUSTER_ID)).all()
    assert list(guids) == ["g1"]


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


# ── Metadata cache completeness (S23) ─────────────────────────────────────────
#
# `_sync_metadata` commits a DELETE-all for the org and then re-pages in the
# spec order LIVEBOARD, ANSWER, <five logical-table subtypes>, committing per
# page. An interruption therefore leaves the cache NON-EMPTY but TRUNCATED. The
# completeness signal has to be the sync_log row, written before the delete.


def _seed_metadata_success_marker(org_id: int = 0, *, record_count: int = 2) -> None:
    from datetime import datetime, timezone

    from ts_admin.database import get_session
    from ts_admin.models.sync_log import SyncLog

    with get_session() as session:
        session.add(
            SyncLog(
                cluster_id=CLUSTER_ID,
                org_id=org_id,
                entity_type="metadata",
                status="SUCCESS",
                record_count=record_count,
                synced_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            )
        )
        session.commit()


def _metadata_rows(org_id: int = 0) -> list:
    from sqlmodel import select

    from ts_admin.database import get_session
    from ts_admin.models.cache.ts_metadata import CachedMetadata

    with get_session() as session:
        return list(
            session.exec(
                select(CachedMetadata).where(
                    CachedMetadata.cluster_id == CLUSTER_ID,
                    CachedMetadata.org_id == org_id,
                )
            ).all()
        )


def _seed_cached_metadata(*objs: tuple[str, str], org_id: int = 0) -> None:
    from datetime import datetime, timezone

    from ts_admin.database import get_session
    from ts_admin.models.cache.ts_metadata import CachedMetadata

    with get_session() as session:
        for guid, object_type in objs:
            session.add(
                CachedMetadata(
                    cluster_id=CLUSTER_ID,
                    org_id=org_id,
                    ts_guid=guid,
                    name=f"obj-{guid}",
                    object_type=object_type,
                    owner_guid="owner-1",
                    owner_name="Alice",
                    tag_names="[]",
                    synced_at=datetime.now(timezone.utc),
                )
            )
        session.commit()


def _metadata_marker_status(org_id: int = 0) -> str | None:
    from sqlmodel import select

    from ts_admin.database import get_session
    from ts_admin.models.sync_log import SyncLog

    with get_session() as session:
        row = session.exec(
            select(SyncLog).where(
                SyncLog.cluster_id == CLUSTER_ID,
                SyncLog.org_id == org_id,
                SyncLog.entity_type == "metadata",
            )
        ).first()
    return row.status if row else None


def _meta_obj(guid: str, object_type: str):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=guid,
        name=f"obj-{guid}",
        type=SimpleNamespace(value=object_type),
        owner_id="owner-1",
        author_name="Alice",
        tags=[],
        created=now,
        modified=now,
        last_accessed=now,
        view_count=0,
    )


def _install_metadata_client(
    monkeypatch,
    pages,
    *,
    raise_after: int | None = None,
    exc: BaseException | None = None,
    release_version: str | None = "26.8.0.cl",
    probe_exc: BaseException | None = None,
) -> dict:
    """Fake ThoughtSpotClient whose search_metadata yields `pages`, optionally
    raising `exc` after `raise_after` pages to simulate an interrupted crawl.

    `test_connection` is part of the fake because `_sync_metadata` probes the
    cluster's release version to decide which subtypes it may ask for; pass
    `probe_exc` to make that probe fail. Returns a dict the caller can read the
    recorded probe/crawl arguments back out of.
    """
    recorded: dict = {"release_version_passed": "<not called>", "probes": 0}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def test_connection(self):
            recorded["probes"] += 1
            if probe_exc is not None:
                raise probe_exc
            return {"release_version": release_version or "unknown", "url": "https://prod.thoughtspot.cloud"}

        async def search_metadata(self, *, release_version=None):
            recorded["release_version_passed"] = release_version
            for idx, page in enumerate(pages):
                if raise_after is not None and idx >= raise_after:
                    raise exc or TSConnectionError("upstream dropped the connection mid-crawl")
                yield page

    monkeypatch.setattr(
        "ts_admin.config.ClusterConfig.build_auth_strategy",
        lambda self, org_id=None: None,
    )
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", FakeClient)
    return recorded


# WHY THESE TESTS INTERRUPT WITH CancelledError, NOT TSConnectionError
# --------------------------------------------------------------------
# `run_sync`'s terminal `except Exception` handler already flips the marker to
# FAILED, so an interruption it CATCHES was never the hole — see
# `test_caught_failure_also_invalidates_the_marker` below, which passes with or
# without the write-ahead write. The hole is the interruption that never reaches
# that handler: process kill, SIGINT, server shutdown, task cancellation. The
# in-process stand-in is `asyncio.CancelledError` — a BaseException since 3.8,
# so `except Exception` does not catch it and no terminal marker write happens.
# Only the write-ahead marker can invalidate the cache in that case. Using a
# catchable exception here is exactly what makes an ordering test vacuous.


@pytest.mark.anyio
async def test_interrupted_metadata_sync_is_not_reported_as_synced(monkeypatch, in_memory_db, patched_config):
    """The S23 bug. A metadata sync killed after page 1 leaves liveboards and
    answers cached and every model/table missing, while the previous SUCCESS
    sync_log row survives untouched — so the whole app (dashboard, transfer
    preview, share preview) reads a truncated cache as authoritative and
    silently acts on a subset."""
    import asyncio

    from ts_admin.services.sync_status import metadata_is_authoritative

    _seed_metadata_success_marker()
    # Page 1 = the two archivable types; page 2 = the logical tables that never arrive.
    pages = [
        [_meta_obj("lb-1", "LIVEBOARD"), _meta_obj("ans-1", "ANSWER")],
        [_meta_obj("model-1", "WORKSHEET")],
    ]
    _install_metadata_client(monkeypatch, pages, raise_after=1, exc=asyncio.CancelledError())

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    with pytest.raises(asyncio.CancelledError):
        await run_sync(entity_type="metadata", org_id=0, job_id=job_id)

    # No terminal handler ran — the job is still RUNNING, exactly as it would be
    # after a `kill -9`. Nothing downstream can lean on the job row either.
    assert _job_status(job_id)[0] == "RUNNING"

    # ANTI-VACUITY: prove we reproduced the *truncated* shape, not a trivially
    # empty cache. If this is empty the test proves nothing about completeness.
    rows = _metadata_rows()
    assert rows, "expected a partially-populated cache — the truncation was not reproduced"
    assert {r.object_type for r in rows} == {"LIVEBOARD", "ANSWER"}
    assert not any(r.object_type == "WORKSHEET" for r in rows)

    # The crux: a non-empty cache must NOT read as authoritative.
    assert _metadata_marker_status() == "IN_PROGRESS"
    assert metadata_is_authoritative(cluster_id=CLUSTER_ID, org_id=0) is False


@pytest.mark.anyio
async def test_write_ahead_marker_preserves_the_last_completed_sync(monkeypatch, in_memory_db, patched_config):
    """The write-ahead marker says "a sync is running", NOT "a sync just finished
    with 0 rows". `_write_sync_log` upserts the single (cluster, org, entity)
    row, so writing synced_at=now / record_count=0 there would destroy the only
    record of when the cache was last known complete — and the Topbar, which
    renders off exactly those two fields, would report a healthy in-flight sync
    as a zero-row sync that landed a moment ago.

    Drop `preserve_progress=True` from the `_sync_metadata` call and this goes
    red on both assertions.
    """
    import asyncio
    from datetime import datetime, timezone

    from sqlmodel import select

    from ts_admin.database import get_session
    from ts_admin.models.sync_log import SyncLog

    seeded_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    _seed_metadata_success_marker(record_count=42)
    _install_metadata_client(
        monkeypatch, [[_meta_obj("lb-1", "LIVEBOARD")]], raise_after=0, exc=asyncio.CancelledError()
    )

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    with pytest.raises(asyncio.CancelledError):
        await run_sync(entity_type="metadata", org_id=0, job_id=job_id)

    with get_session() as session:
        row = session.exec(select(SyncLog).where(SyncLog.entity_type == "metadata")).one()

    assert row.status == "IN_PROGRESS"  # anti-vacuity: the write-ahead write DID happen
    assert row.record_count == 42, "the last completed sync's record_count was clobbered"
    assert row.synced_at.replace(tzinfo=timezone.utc) == seeded_at, (
        "the last completed sync's timestamp was clobbered — the app now has no "
        "record of when the cache was last known complete"
    )


@pytest.mark.anyio
async def test_marker_invalidated_before_any_row_is_deleted(monkeypatch, in_memory_db, patched_config):
    """Ordering test — pins the marker AHEAD of the delete, not merely somewhere
    before the crawl.

    The window this closes is the gap between the delete's commit and the marker
    write. They are two separate transactions by design (a single one would hold
    the SQLite write lock across the whole crawl — see the comment in
    `_sync_metadata`), so an interruption can land between them. Write-ahead
    makes that window contain "marker invalidated, rows still present", which is
    merely pessimistic. The reverse order makes it contain "rows all gone, marker
    still SUCCESS" — an empty cache certified as complete, which reads as *this
    org has no content* rather than *we don't know*.

    Interrupt exactly at the marker write to sit inside that window: move the
    `_write_sync_log(... IN_PROGRESS)` call below the delete block and this
    goes red.
    """
    import asyncio

    import ts_admin.services.sync_service as sync_module
    from ts_admin.services.sync_status import metadata_is_authoritative

    _seed_metadata_success_marker()
    _seed_cached_metadata(("lb-old", "LIVEBOARD"), ("ws-old", "WORKSHEET"))
    assert len(_metadata_rows()) == 2  # anti-vacuity: there IS something to lose

    real_write = sync_module._write_sync_log

    def _die_on_the_in_progress_write(entity_type, org_id, *, status, **kwargs):
        if entity_type == "metadata" and status == "IN_PROGRESS":
            raise asyncio.CancelledError()
        return real_write(entity_type, org_id, status=status, **kwargs)

    monkeypatch.setattr(sync_module, "_write_sync_log", _die_on_the_in_progress_write)
    _install_metadata_client(monkeypatch, [[_meta_obj("lb-1", "LIVEBOARD")]])

    job_id = _make_job("metadata")
    with pytest.raises(asyncio.CancelledError):
        await sync_module.run_sync(entity_type="metadata", org_id=0, job_id=job_id)

    # THE INVARIANT: never "certified complete AND emptied". Either the marker
    # went first (rows intact, still certified — pessimistically fine) or the
    # marker was invalidated. Both are honest; the conjunction is the lie.
    rows = _metadata_rows()
    certified = metadata_is_authoritative(cluster_id=CLUSTER_ID, org_id=0)
    assert not (certified and rows == []), (
        "the org's whole metadata cache was deleted while its sync_log row still says SUCCESS — "
        "an empty cache certified as complete. Write the IN_PROGRESS marker BEFORE the delete."
    )
    # Concretely, on the correct ordering the delete never ran at all.
    assert len(rows) == 2


@pytest.mark.anyio
async def test_caught_failure_also_invalidates_the_marker(monkeypatch, in_memory_db, patched_config):
    """The pre-existing half of the guarantee, pinned so a refactor of
    `run_sync`'s terminal handler can't quietly drop it. This one passes with or
    without the write-ahead write — that is the point of keeping it separate
    from the two ordering tests above."""
    from ts_admin.services.sync_status import metadata_is_authoritative

    _seed_metadata_success_marker()
    pages = [[_meta_obj("lb-1", "LIVEBOARD")], [_meta_obj("model-1", "WORKSHEET")]]
    _install_metadata_client(monkeypatch, pages, raise_after=1)

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    await run_sync(entity_type="metadata", org_id=0, job_id=job_id)

    assert _job_status(job_id)[0] == "FAILED"
    assert _metadata_rows()  # truncated, not empty
    assert _metadata_marker_status() == "FAILED"
    assert metadata_is_authoritative(cluster_id=CLUSTER_ID, org_id=0) is False


@pytest.mark.anyio
async def test_completed_metadata_sync_recertifies_the_cache(monkeypatch, in_memory_db, patched_config):
    """The recovery half: after a full re-sync the cache is authoritative again,
    so the refusal guards clear on their own without any manual intervention."""
    from ts_admin.services.sync_status import metadata_is_authoritative

    pages = [
        [_meta_obj("lb-1", "LIVEBOARD")],
        [_meta_obj("model-1", "WORKSHEET")],
    ]
    _install_metadata_client(monkeypatch, pages, raise_after=1)

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    await run_sync(entity_type="metadata", org_id=0, job_id=job_id)
    assert metadata_is_authoritative(cluster_id=CLUSTER_ID, org_id=0) is False

    # Now the same crawl, uninterrupted.
    _install_metadata_client(monkeypatch, pages)
    job_id = _make_job("metadata")
    await run_sync(entity_type="metadata", org_id=0, job_id=job_id)

    assert _job_status(job_id)[0] == "COMPLETE"
    assert {r.object_type for r in _metadata_rows()} == {"LIVEBOARD", "WORKSHEET"}
    assert _metadata_marker_status() == "SUCCESS"
    assert metadata_is_authoritative(cluster_id=CLUSTER_ID, org_id=0) is True


@pytest.mark.anyio
async def test_genuine_auth_failure_still_expires_cluster(monkeypatch, in_memory_db, patched_config):
    """TSAuthenticationError → job FAILED AND cluster flipped to EXPIRED (guards the distinction)."""

    async def _boom(*, org_id: int, job_id: str, target_cluster_id: str | None = None) -> None:
        raise TSAuthenticationError("session expired")

    monkeypatch.setattr("ts_admin.services.sync_service._sync_metadata", _boom)

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    await run_sync(entity_type="metadata", org_id=349890686, job_id=job_id)

    status, _ = _job_status(job_id)
    assert status == "FAILED"
    assert connection_status.get(CLUSTER_ID).state is ConnectionState.EXPIRED


@pytest.mark.anyio
async def test_tag_sync_scopes_its_auth_token_to_the_requested_org(monkeypatch, in_memory_db, patched_config):
    """`tags/search` has no org filter parameter — the token's org context is the
    only thing that scopes it.

    Verified live against PS-internal Prod (26.8.0.cl): a token scoped to the
    Secondary org returns 0 tags, an unscoped token returns Primary's 114. Syncing
    with an unscoped token therefore wrote Primary's tags into the cache stamped
    with whichever org the user asked for.

    Users and groups are deliberately NOT covered here, but for different reasons.
    `groups/search` takes `org_identifiers`, and on the same cluster the body
    filter returns an org's full membership (133 users for Secondary) where a
    token scoped to that org returns only what that session can see (4).
    `users/search` no longer sends `org_identifiers` at all — it scopes
    client-side off each record's own `orgs` list, because the server-side filter
    404s a whole page when any one result has an unresolvable membership. See
    tests/unit/test_client_org_scoping.py.
    """
    seen_org_ids: list[int | None] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def search_tags(self):
            return []

    def _capture(self, org_id=None):
        seen_org_ids.append(org_id)
        return None

    monkeypatch.setattr("ts_admin.config.ClusterConfig.build_auth_strategy", _capture)
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", FakeClient)

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("tags")
    await run_sync(entity_type="tags", org_id=349890686, job_id=job_id)

    assert _job_status(job_id)[0] == "COMPLETE"
    assert seen_org_ids == [349890686]


# ── Cluster routing ───────────────────────────────────────────────────────────
#
# The shell can be displaying one cluster while a different one is marked active
# in config (adding a cluster and activating it does not repoint an already-open
# page). Before run_sync took a cluster_id, every sync ran against the *active*
# cluster regardless of what the caller asked for — so the grid showed cluster A
# while Sync fetched cluster B, and B's org ids were used against A.


@pytest.fixture
def two_cluster_config(monkeypatch):
    """Two clusters where the ACTIVE one is deliberately not the one we ask for."""
    from ts_admin.config import AppConfig, ClusterConfig
    from ts_admin.ts_client.models import AuthType

    def _cluster(cid: str, name: str) -> ClusterConfig:
        return ClusterConfig(
            id=cid,
            name=name,
            url=f"https://{cid}.thoughtspot.cloud",
            username="admin",
            auth_type=AuthType.TRUSTED,
        )

    config = AppConfig(
        clusters={CLUSTER_ID: _cluster(CLUSTER_ID, "Prod"), "c2": _cluster("c2", "Demo")},
        active_cluster_id="c2",
    )
    monkeypatch.setattr("ts_admin.config.load_config", lambda: config)
    return config


@pytest.mark.anyio
async def test_run_sync_targets_the_requested_cluster_not_the_active_one(monkeypatch, in_memory_db, two_cluster_config):
    seen: list[str | None] = []

    async def _capture(*, org_id: int, job_id: str, target_cluster_id: str | None = None) -> None:
        seen.append(target_cluster_id)

    monkeypatch.setattr("ts_admin.services.sync_service._sync_metadata", _capture)

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    await run_sync(entity_type="metadata", org_id=0, job_id=job_id, cluster_id=CLUSTER_ID)

    assert seen == [CLUSTER_ID], "sync must run against the caller's cluster, not active_cluster_id"


@pytest.mark.anyio
async def test_run_sync_falls_back_to_the_active_cluster_when_none_is_named(
    monkeypatch, in_memory_db, two_cluster_config
):
    seen: list[str | None] = []

    async def _capture(*, org_id: int, job_id: str, target_cluster_id: str | None = None) -> None:
        seen.append(target_cluster_id)

    monkeypatch.setattr("ts_admin.services.sync_service._sync_metadata", _capture)

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    await run_sync(entity_type="metadata", org_id=0, job_id=job_id)

    assert seen == ["c2"]


@pytest.mark.anyio
async def test_failed_sync_log_is_written_against_the_requested_cluster(monkeypatch, in_memory_db, two_cluster_config):
    """A failure syncing cluster A must not stamp cluster B's sync_log row."""

    async def _boom(*, org_id: int, job_id: str, target_cluster_id: str | None = None) -> None:
        raise TSConnectionError("unreachable")

    monkeypatch.setattr("ts_admin.services.sync_service._sync_metadata", _boom)

    from sqlmodel import select

    from ts_admin.database import get_session
    from ts_admin.models.sync_log import SyncLog
    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    await run_sync(entity_type="metadata", org_id=0, job_id=job_id, cluster_id=CLUSTER_ID)

    with get_session() as session:
        rows = session.exec(select(SyncLog).where(SyncLog.entity_type == "metadata")).all()

    assert [r.cluster_id for r in rows] == [CLUSTER_ID]


@pytest.mark.anyio
async def test_run_sync_rejects_an_unknown_cluster_id(monkeypatch, in_memory_db, two_cluster_config):
    called = False

    async def _never(*, org_id: int, job_id: str, target_cluster_id: str | None = None) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("ts_admin.services.sync_service._sync_metadata", _never)

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    await run_sync(entity_type="metadata", org_id=0, job_id=job_id, cluster_id="does-not-exist")

    assert called is False
    status, _ = _job_status(job_id)
    assert status == "FAILED"


def test_sync_log_keeps_no_history_so_there_is_no_record_count_trend(in_memory_db, patched_config):
    """
    `sync_log` is a CURRENT-STATE table, not a time series.

    Every writer upserts the single (cluster_id, org_id, entity_type) row and
    none append, so "the two most recent successful syncs" is never two rows.
    `dashboard_service` used to diff exactly that and therefore reported a delta
    of 0 unconditionally — the Dashboard's trend indicator never rendered once.
    The dead field is gone; this test is why it cannot come back as a second
    query. Restoring it needs a stored previous count, not more SELECTs.

    (The bound also matters on its own: `sync_log` has only single-column
    indexes, which is acceptable only because it stays clusters x orgs x
    entities in size.)
    """
    from sqlmodel import select

    from ts_admin.database import get_session
    from ts_admin.models.sync_log import SyncLog
    from ts_admin.services.sync_service import _write_sync_log

    for count in (356, 360, 411):
        _write_sync_log("users", 0, status="SUCCESS", record_count=count, cluster_id=CLUSTER_ID)

    with get_session() as session:
        rows = session.exec(select(SyncLog).where(SyncLog.entity_type == "users")).all()

    assert len(rows) == 1, "a writer started appending — sync_log is no longer bounded"
    assert rows[0].record_count == 411  # anti-vacuity: the writes DID happen


# ── Cache purge: rows deleted upstream (P2) ───────────────────────────────────
#
# Every sync was upsert-only except `_sync_groups`. A user deprovisioned in
# ThoughtSpot by any route other than this toolkit (IdP/SCIM, the TS admin UI)
# therefore stayed in the grid, in the inactive-user count and in the sharing
# principal picker forever — and `dashboard_service`'s "orphaned content" signal,
# defined as "owner not in ts_users", could never fire, so the one card built for
# exactly this case read 0 and showed an all-clear.


def _fake_user(guid: str, *, status: str = "ACTIVE"):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=guid,
        name=f"user-{guid}",
        display_name=f"User {guid}",
        email=f"{guid}@example.io",
        status=SimpleNamespace(value=status),
        created=now,
        modified=now,
    )


def _users_client(pages: list[list], monkeypatch):
    """Install a fake ThoughtSpotClient whose search_users yields `pages`.

    `pages` is captured by reference so a test can mutate it between syncs — the
    purge is only observable across two sweeps.
    """

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

    monkeypatch.setattr(
        "ts_admin.config.ClusterConfig.build_auth_strategy",
        lambda self, org_id=None: None,
    )
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", FakeClient)


def _cached_user_guids() -> set[str]:
    from sqlmodel import select

    from ts_admin.database import get_session
    from ts_admin.models.cache.ts_user import CachedUser

    with get_session() as session:
        return set(session.exec(select(CachedUser.ts_guid).where(CachedUser.cluster_id == CLUSTER_ID)).all())


def _org_memberships() -> set[tuple[str, int]]:
    from sqlmodel import select

    from ts_admin.database import get_session
    from ts_admin.models.cache.ts_user import UserOrgMembership

    with get_session() as session:
        rows = session.exec(select(UserOrgMembership).where(UserOrgMembership.cluster_id == CLUSTER_ID)).all()
    return {(r.ts_guid, r.org_id) for r in rows}


@pytest.mark.anyio
async def test_user_sync_purges_principals_absent_from_the_sweep(monkeypatch, in_memory_db, patched_config):
    """A user gone upstream must leave BOTH ts_users and user_org_memberships."""
    pages: list[list] = [[_fake_user("u1"), _fake_user("u2")]]
    _users_client(pages, monkeypatch)

    from ts_admin.services.sync_service import run_sync

    await run_sync(entity_type="users", org_id=0, job_id=_make_job("users"))
    assert _cached_user_guids() == {"u1", "u2"}
    assert _org_memberships() == {("u1", 0), ("u2", 0)}

    # u2 was deprovisioned in ThoughtSpot between syncs.
    pages[:] = [[_fake_user("u1")]]
    job_id = _make_job("users")
    await run_sync(entity_type="users", org_id=0, job_id=job_id)
    assert _job_status(job_id)[0] == "COMPLETE"

    assert _cached_user_guids() == {"u1"}
    assert _org_memberships() == {("u1", 0)}


@pytest.mark.anyio
async def test_user_sync_empty_sweep_deletes_nothing(monkeypatch, in_memory_db, patched_config):
    """The `_sync_groups:280` guard, applied to users.

    SQLAlchemy renders `not_in(<empty>)` as `NOT IN (NULL) OR (1 = 1)` — always
    true — so an unguarded purge would empty the whole cache on one blank page
    (wrong org context, transient upstream blip). A zero-result response is
    indistinguishable from "the org really has no users", and guessing wrong here
    is unrecoverable without a full re-sync.
    """
    pages: list[list] = [[_fake_user("u1"), _fake_user("u2")]]
    _users_client(pages, monkeypatch)

    from ts_admin.services.sync_service import run_sync

    await run_sync(entity_type="users", org_id=0, job_id=_make_job("users"))
    assert _cached_user_guids() == {"u1", "u2"}

    pages[:] = []
    job_id = _make_job("users")
    await run_sync(entity_type="users", org_id=0, job_id=job_id)
    assert _job_status(job_id)[0] == "COMPLETE"

    assert _cached_user_guids() == {"u1", "u2"}
    assert _org_memberships() == {("u1", 0), ("u2", 0)}


@pytest.mark.anyio
async def test_user_sync_keeps_members_of_other_orgs(monkeypatch, in_memory_db, patched_config):
    """The purge is org-aware, and this is the part that could break a live cluster.

    `ts_users` is cluster-scoped while the sweep is org-scoped (`search_users`
    filters client-side off each record's own org list), so a naive
    `not_in(seen_guids)` on ts_users would delete every user who happens to
    belong only to a DIFFERENT org — silent, cross-org data loss on any
    multi-org cluster. Deleting only users with no membership left anywhere on
    the cluster is what makes that impossible.
    """
    pages: list[list] = [[_fake_user("u1"), _fake_user("u2")]]
    _users_client(pages, monkeypatch)

    from ts_admin.services.sync_service import run_sync

    # u1 + u2 are in org 0; then org 7 is synced and has u2 + u3.
    await run_sync(entity_type="users", org_id=0, job_id=_make_job("users"))
    pages[:] = [[_fake_user("u2"), _fake_user("u3")]]
    await run_sync(entity_type="users", org_id=7, job_id=_make_job("users"))
    assert _cached_user_guids() == {"u1", "u2", "u3"}

    # Re-sync org 0 with u2 removed from THAT org (still on the cluster, in org 7).
    pages[:] = [[_fake_user("u1")]]
    job_id = _make_job("users")
    await run_sync(entity_type="users", org_id=0, job_id=job_id)
    assert _job_status(job_id)[0] == "COMPLETE"

    # u2 lost only its org-0 membership; u3 was never in the org-0 sweep at all.
    assert _org_memberships() == {("u1", 0), ("u2", 7), ("u3", 7)}
    assert _cached_user_guids() == {"u1", "u2", "u3"}


@pytest.mark.anyio
async def test_purged_owner_makes_orphaned_content_visible(monkeypatch, in_memory_db, patched_config):
    """The end-to-end reason the purge matters.

    `dashboard_service._attention` defines orphaned_content as "owner not in
    ts_users". With no purge that predicate was unsatisfiable, so the Needs
    Attention card showed an all-clear precisely when an owner had been
    deprovisioned — the one situation it exists to catch.
    """
    from datetime import datetime, timezone

    from sqlmodel import Session

    from ts_admin.models.cache.ts_metadata import CachedMetadata
    from ts_admin.services.dashboard_service import DashboardService

    pages: list[list] = [[_fake_user("u1"), _fake_user("u2")]]
    _users_client(pages, monkeypatch)

    from ts_admin.services.sync_service import run_sync

    await run_sync(entity_type="users", org_id=0, job_id=_make_job("users"))

    # A liveboard owned by u2.
    with Session(in_memory_db) as session:
        session.add(
            CachedMetadata(
                cluster_id=CLUSTER_ID,
                org_id=0,
                ts_guid="lb-1",
                name="Q3 Revenue",
                object_type="LIVEBOARD",
                owner_guid="u2",
                owner_name="User u2",
                synced_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    def _orphaned() -> int:
        with Session(in_memory_db) as session:
            return DashboardService._attention(
                session,
                cluster_id=CLUSTER_ID,
                org_id=0,
                synced={"users": True, "groups": False, "metadata": True},
            )["orphaned_content"]

    assert _orphaned() == 0  # owner still known — anti-vacuity for the assert below

    pages[:] = [[_fake_user("u1")]]
    await run_sync(entity_type="users", org_id=0, job_id=_make_job("users"))

    assert _orphaned() == 1


@pytest.mark.anyio
async def test_tag_sync_purges_deleted_tags_and_guards_the_empty_sweep(monkeypatch, in_memory_db, patched_config):
    """Tags get the same treatment as groups: purge on a real sweep, never on an
    empty one. `search_tags()` is a single complete org-scoped list and CachedTag
    is (cluster, org)-keyed, so the sweep and the delete scope line up exactly."""
    from types import SimpleNamespace

    from sqlmodel import select

    from ts_admin.database import get_session
    from ts_admin.models.cache.ts_tag import CachedTag

    tags: list = [
        SimpleNamespace(id="t1", name="Certified", color="#0f0"),
        SimpleNamespace(id="t2", name="Deprecated", color="#f00"),
    ]

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def search_tags(self):
            return list(tags)

    monkeypatch.setattr(
        "ts_admin.config.ClusterConfig.build_auth_strategy",
        lambda self, org_id=None: None,
    )
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", FakeClient)

    def _tag_guids(org_id: int = 0) -> set[str]:
        with get_session() as session:
            return set(
                session.exec(
                    select(CachedTag.ts_guid).where(CachedTag.cluster_id == CLUSTER_ID, CachedTag.org_id == org_id)
                ).all()
            )

    from ts_admin.services.sync_service import run_sync

    await run_sync(entity_type="tags", org_id=0, job_id=_make_job("tags"))
    assert _tag_guids() == {"t1", "t2"}

    # The same tags exist in another org — the purge must not reach across.
    await run_sync(entity_type="tags", org_id=7, job_id=_make_job("tags"))

    tags[:] = [SimpleNamespace(id="t1", name="Certified", color="#0f0")]
    job_id = _make_job("tags")
    await run_sync(entity_type="tags", org_id=0, job_id=job_id)
    assert _job_status(job_id)[0] == "COMPLETE"
    assert _tag_guids() == {"t1"}
    assert _tag_guids(org_id=7) == {"t1", "t2"}

    # Empty sweep: keep the cache.
    tags[:] = []
    job_id = _make_job("tags")
    await run_sync(entity_type="tags", org_id=0, job_id=job_id)
    assert _job_status(job_id)[0] == "COMPLETE"
    assert _tag_guids() == {"t1"}


# ── Cancellation is real, not a 204 that cancels nothing (P3) ─────────────────
#
# DELETE /jobs/{id}/cancel set is_cancelled and returned 204, but nothing in
# sync_service or lineage_service ever read the flag. An admin who cancelled a
# 30-minute crawl on the wrong org got a 204 while it ran to completion hammering
# the cluster, then reported COMPLETE.


def _cancel(job_id: str) -> None:
    from ts_admin.database import get_session
    from ts_admin.models.job import Job

    with get_session() as session:
        job = session.get(Job, job_id)
        job.is_cancelled = True
        session.add(job)
        session.commit()


def _sync_log_status(entity_type: str, org_id: int = 0) -> str | None:
    from sqlmodel import select

    from ts_admin.database import get_session
    from ts_admin.models.sync_log import SyncLog

    with get_session() as session:
        row = session.exec(
            select(SyncLog).where(
                SyncLog.cluster_id == CLUSTER_ID,
                SyncLog.org_id == org_id,
                SyncLog.entity_type == entity_type,
            )
        ).first()
    return row.status if row else None


@pytest.mark.anyio
async def test_cancelled_metadata_sync_does_not_end_complete(monkeypatch, in_memory_db, patched_config):
    """Cancel a running metadata sync at a page boundary.

    Two things have to hold. The job must NOT read COMPLETE — it processed a
    prefix of the pages, and COMPLETE claims it processed all of them. And the
    metadata sync_log must stay non-SUCCESS: `_sync_metadata` deletes the org's
    rows before it re-pages, so a cancel leaves a genuinely TRUNCATED cache that
    `require_authoritative_metadata` has to keep refusing.
    """
    from types import SimpleNamespace

    from ts_admin.ts_client.exceptions import StaleCacheError

    holder: dict = {}
    pages_fetched = 0

    def _obj(guid: str):
        return SimpleNamespace(
            id=guid,
            name=f"obj-{guid}",
            type=SimpleNamespace(value="LIVEBOARD"),
            owner_id="u1",
            author_name="Alice",
            tags=[],
            created=None,
            modified=None,
            last_accessed=None,
            view_count=0,
        )

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def test_connection(self):
            return {"release_version": "26.8.0.cl", "url": "https://prod.thoughtspot.cloud"}

        async def search_metadata(self, *, release_version=None):
            nonlocal pages_fetched
            for guid in ("a", "b", "c"):
                pages_fetched += 1
                yield [_obj(guid)]
                # The admin hits cancel while page 1 is being written.
                _cancel(holder["job_id"])

    monkeypatch.setattr(
        "ts_admin.config.ClusterConfig.build_auth_strategy",
        lambda self, org_id=None: None,
    )
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", FakeClient)

    from ts_admin.services.sync_service import run_sync

    holder["job_id"] = _make_job("metadata")
    await run_sync(entity_type="metadata", org_id=0, job_id=holder["job_id"])

    status, _ = _job_status(holder["job_id"])
    assert status == "PARTIAL", "a cancelled sync must not report COMPLETE"
    assert status != "COMPLETE"
    # It actually stopped early rather than draining all three pages.
    assert pages_fetched == 2

    assert _sync_log_status("metadata") == "FAILED"
    with pytest.raises(StaleCacheError):
        from ts_admin.services.sync_status import require_authoritative_metadata

        require_authoritative_metadata(cluster_id=CLUSTER_ID, org_id=0)


@pytest.mark.anyio
async def test_cancelled_group_sync_skips_the_purge(monkeypatch, in_memory_db, patched_config):
    """A cancelled sweep must never purge.

    `seen_guids` then holds only the pages the crawl reached, so purging would
    delete every group it never got to — turning "stop early" into data loss.
    """
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from sqlmodel import select

    from ts_admin.database import get_session
    from ts_admin.models.cache.ts_group import CachedGroup

    now = datetime.now(timezone.utc)
    holder: dict = {}

    def _group(guid: str):
        return SimpleNamespace(
            id=guid,
            name=f"group-{guid}",
            display_name=f"Group {guid}",
            description="",
            privileges=[],
            member_users=[],
            author_id="u-creator",
            created=now,
            modified=now,
        )

    cancel_after_first_page = False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def search_groups(self, *, org_id):
            yield [_group("g1")]
            if cancel_after_first_page:
                _cancel(holder["job_id"])
            yield [_group("g2")]

    monkeypatch.setattr(
        "ts_admin.config.ClusterConfig.build_auth_strategy",
        lambda self, org_id=None: None,
    )
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", FakeClient)

    def _group_guids() -> set[str]:
        with get_session() as session:
            return set(session.exec(select(CachedGroup.ts_guid).where(CachedGroup.cluster_id == CLUSTER_ID)).all())

    from ts_admin.services.sync_service import run_sync

    holder["job_id"] = _make_job("groups")
    await run_sync(entity_type="groups", org_id=0, job_id=holder["job_id"])
    assert _group_guids() == {"g1", "g2"}

    cancel_after_first_page = True
    holder["job_id"] = _make_job("groups")
    await run_sync(entity_type="groups", org_id=0, job_id=holder["job_id"])

    assert _job_status(holder["job_id"])[0] == "PARTIAL"
    # g2 was never reached by the cancelled sweep — it must still be cached.
    assert _group_guids() == {"g1", "g2"}


# ── One metadata spec's failure must not destroy the whole sync (W3) ──────────
#
# `search_metadata` issues seven independent `metadata/search` requests. A
# failure in one used to propagate out of the generator and take the whole sync
# with it — discarding the specs that had already succeeded, so an admin could
# end with NO metadata cache rather than a partial one. The reachable trigger is
# the `SQL_VIEW` subtype, which the v2 reference tags "Version: 10.11.0.cl or
# later" while we query it unconditionally.
#
# These tests drive the REAL ThoughtSpotClient over respx rather than the fake
# above: the spec loop under test lives in the client, and a fake client would
# assert the fix into existence instead of exercising it.

PROD_URL = "https://prod.thoughtspot.cloud"


def _wire_obj(guid: str) -> dict:
    """A metadata/search record, in the shape the live endpoint returns."""
    return {
        "metadata_id": guid,
        "metadata_name": f"obj-{guid}",
        "metadata_header": {"id": guid, "name": f"obj-{guid}", "author": "u1", "authorDisplayName": "Alice"},
    }


def _install_live_metadata_cluster(
    monkeypatch,
    *,
    release_version: str = "26.8.0.cl",
    rows: dict[str, list] | None = None,
    fail_on: str | None = None,
) -> list[str]:
    """Route /system and /metadata/search at respx. Returns the list that records
    every spec reaching the wire, so a *skipped* spec is observable."""
    import json as _json

    import httpx

    from ts_admin.ts_client.auth import BearerTokenAuth

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        metadata_filter = _json.loads(request.content)["metadata"][0]
        subtypes = metadata_filter.get("subtypes")
        spec = subtypes[0] if subtypes else metadata_filter["type"]
        seen.append(spec)
        if spec == fail_on:
            return httpx.Response(400, json={"error": {"message": "Invalid parameter values: subtypes"}})
        return httpx.Response(200, json=(rows or {}).get(spec, []))

    respx.get(f"{PROD_URL}/api/rest/2.0/system").mock(
        return_value=httpx.Response(200, json={"release_version": release_version, "name": "prod"})
    )
    respx.post(f"{PROD_URL}/api/rest/2.0/metadata/search").mock(side_effect=handler)
    monkeypatch.setattr(
        "ts_admin.config.ClusterConfig.build_auth_strategy",
        lambda self, org_id=None: BearerTokenAuth(token="t"),
    )
    return seen


def _job_result(job_id: str) -> dict | None:
    from ts_admin.database import get_session
    from ts_admin.models.job import Job

    with get_session() as session:
        job = session.get(Job, job_id)
        return job.get_result()


@respx.mock
@pytest.mark.anyio
async def test_a_failing_metadata_spec_keeps_what_the_other_specs_fetched(monkeypatch, in_memory_db, patched_config):
    """The W3 bug end to end: SQL_VIEW 400s, everything else still lands."""
    from ts_admin.services.sync_status import metadata_is_authoritative

    seen = _install_live_metadata_cluster(
        monkeypatch,
        rows={"LIVEBOARD": [_wire_obj("lb-1")], "ANSWER": [_wire_obj("a-1")], "WORKSHEET": [_wire_obj("w-1")]},
        fail_on="SQL_VIEW",
    )

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    await run_sync(entity_type="metadata", org_id=0, job_id=job_id)

    # The specs that worked are cached — the whole point of the change.
    assert {r.ts_guid for r in _metadata_rows()} == {"lb-1", "a-1", "w-1"}
    # USER_DEFINED is issued AFTER SQL_VIEW: the crawl continued past the failure.
    assert seen[-1] == "USER_DEFINED"

    # …and the cache is not certified. A spec that never ran means a whole class
    # of object is missing from a cache `_sync_metadata` had already emptied.
    status, _ = _job_status(job_id)
    assert status == "PARTIAL"
    assert status != "COMPLETE"
    result = _job_result(job_id)
    assert result["record_count"] == 3
    assert [f for f in result["failed_specs"] if f.startswith("SQL_VIEW")] == result["failed_specs"]
    assert _metadata_marker_status() == "FAILED"
    assert metadata_is_authoritative(cluster_id=CLUSTER_ID, org_id=0) is False


@respx.mock
@pytest.mark.anyio
async def test_a_clean_metadata_sync_is_still_certified(monkeypatch, in_memory_db, patched_config):
    """Anti-vacuity for the test above: PARTIAL is conditional, not the new normal."""
    from ts_admin.services.sync_status import metadata_is_authoritative

    _install_live_metadata_cluster(monkeypatch, rows={"LIVEBOARD": [_wire_obj("lb-1")]})

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    await run_sync(entity_type="metadata", org_id=0, job_id=job_id)

    assert _job_status(job_id)[0] == "COMPLETE"
    assert _metadata_marker_status() == "SUCCESS"
    assert metadata_is_authoritative(cluster_id=CLUSTER_ID, org_id=0) is True


@respx.mock
@pytest.mark.anyio
async def test_an_old_cluster_never_sends_the_sql_view_subtype(monkeypatch, in_memory_db, patched_config):
    """The version gate, driven from the cluster's own /system response: below
    10.11 the out-of-enum value must not reach the wire at all."""
    seen = _install_live_metadata_cluster(
        monkeypatch,
        release_version="10.10.0.cl",
        rows={"LIVEBOARD": [_wire_obj("lb-1")]},
    )

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    await run_sync(entity_type="metadata", org_id=0, job_id=job_id)

    assert "SQL_VIEW" not in seen
    assert "USER_DEFINED" in seen  # the specs either side of it still ran
    assert {r.ts_guid for r in _metadata_rows()} == {"lb-1"}
    # A skipped spec is an unfetched spec, so this crawl is not a complete one.
    assert _job_status(job_id)[0] == "PARTIAL"
    assert _job_result(job_id)["failed_specs"] == ["SQL_VIEW: not supported before release 10.11.0"]


@respx.mock
@pytest.mark.anyio
async def test_a_current_cluster_still_gets_its_sql_views(monkeypatch, in_memory_db, patched_config):
    """Both live clusters are 26.8 — the gate must be invisible to them."""
    seen = _install_live_metadata_cluster(
        monkeypatch,
        release_version="26.8.0.cl",
        rows={"SQL_VIEW": [_wire_obj("sv-1")]},
    )

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    await run_sync(entity_type="metadata", org_id=0, job_id=job_id)

    assert "SQL_VIEW" in seen
    assert {r.ts_guid for r in _metadata_rows()} == {"sv-1"}
    assert _job_status(job_id)[0] == "COMPLETE"


@pytest.mark.anyio
async def test_the_cluster_release_version_reaches_the_spec_gate(monkeypatch, in_memory_db, patched_config):
    """Plumbing: whatever /system reports is what decides the specs."""
    recorded = _install_metadata_client(monkeypatch, [[_meta_obj("lb-1", "LIVEBOARD")]], release_version="10.11.0.sw")

    from ts_admin.services.sync_service import run_sync

    await run_sync(entity_type="metadata", org_id=0, job_id=_make_job("metadata"))

    assert recorded["probes"] == 1
    assert recorded["release_version_passed"] == "10.11.0.sw"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"probe_exc": TSConnectionError("cluster unreachable")}, "the probe request failed"),
        ({"release_version": None}, "the cluster reported no version at all"),
    ],
)
async def test_an_unknown_release_version_does_not_disable_the_spec(
    monkeypatch, in_memory_db, patched_config, kwargs, reason
):
    """Fail-open, and fail-open all the way down: an unreadable version must not
    cost a current cluster its SQL views, and must not fail the sync either."""
    recorded = _install_metadata_client(monkeypatch, [[_meta_obj("lb-1", "LIVEBOARD")]], **kwargs)

    from ts_admin.services.sync_service import run_sync

    job_id = _make_job("metadata")
    await run_sync(entity_type="metadata", org_id=0, job_id=job_id)

    # None is what `search_metadata` reads as "assume current" — see
    # `_supports_subtype`, which issues every spec for it.
    assert recorded["release_version_passed"] is None, reason
    assert _job_status(job_id)[0] == "COMPLETE"
    assert _metadata_marker_status() == "SUCCESS"
