"""
The fail-closed contract for a truncated metadata cache (S23).

`_sync_metadata` deletes every row for the org and re-pages in spec order
(LIVEBOARD, ANSWER, then the five logical-table subtypes), committing per page.
An interrupted sync therefore leaves the cache non-empty but TRUNCATED — and
five operations derive their *input set* from that cache, so they would silently
act on a subset: transfer would leave objects behind on a departing user, a
NO_ACCESS revoke would leave access live, and downstream-delete would report "no
dependents" for a root that has plenty.

The three read/preview sites RAISE. The two `execute_*` sites are different:
they only ever run as Starlette background tasks, AFTER the 202 has been sent,
so a raise there can never become a response and would strand the Job row in
QUEUED forever. Their real refusal lives in the routers (`api/sharing.py::
execute`, `api/users.py::transfer_execute`) — see
`tests/integration/test_stale_cache_endpoints.py`, which is the ONLY place that
class of bug is visible. The service-level guard is kept as defense in depth and
marks the job FAILED instead of raising.

Read paths must NOT refuse — browsing a partial cache is still useful, it just
gets flagged.

Deliberately NOT covered: `archiver_service`. NOTE: the reason is NOT that its
input is superset-correct. `search_metadata` paginates WITHIN each spec
(`ts_client/client.py:~352-375`) and `_sync_metadata` commits per page, so an
interruption can leave a strict SUBSET of the org's liveboards, not a superset.
The exemption holds for a different reason: truncation only ever NARROWS the set
of objects the archiver offers, so it under-archives. Missing a candidate is
recoverable (re-sync and run again); acting on a set the user believes is
complete is not. The archiver fails safe by construction; the five sites above
fail dangerous.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, create_engine, select

from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cache.ts_user import CachedUser
from ts_admin.models.sync_log import SyncLog
from ts_admin.ts_client.exceptions import StaleCacheError

CLUSTER_ID = "c1"
ORG_ID = 0


# ── Fixtures ──────────────────────────────────────────────────────────────────


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


@pytest.fixture(autouse=True)
def patched_config(monkeypatch):
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
    monkeypatch.setattr(
        "ts_admin.config.ClusterConfig.build_auth_strategy",
        lambda self, org_id=None: None,
    )
    return config


@pytest.fixture(autouse=True)
def fake_ts_client(monkeypatch):
    """No live calls. Every method a guarded site can reach is a no-op, so any
    exception that does escape is ours, not the network's."""

    class FakeClient:
        # `share_objects` really applies the share, so the post-execute
        # verification read-back in `execute_share` sees what it asked for. A
        # no-op share plus an always-empty `fetch_permissions` would make every
        # share job here FAILED for a reason that has nothing to do with the
        # stale-cache guard these tests are about.
        acl: dict[tuple[str, str], str] = {}

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def fetch_dependents(self, *, objects):
            return {}

        async def fetch_permissions(self, *, ts_guid, object_type, permission_type="DEFINED"):
            from ts_admin.ts_client.models import TSPermission

            return [
                TSPermission(
                    principal_id=pid,
                    principal_name=pid,
                    principal_type="USER_GROUP",
                    share_mode=mode,
                )
                for (guid, pid), mode in FakeClient.acl.items()
                # The real endpoint never returns a NO_ACCESS row.
                if guid == ts_guid and mode != "NO_ACCESS"
            ]

        async def assign_metadata_owner(self, *, object_ids, new_owner_identifier):
            return None

        async def share_objects(self, *, object_ids, principal_ids, permission, message="", notify=False):
            for oid in object_ids:
                for pid in principal_ids:
                    FakeClient.acl[(oid, pid)] = str(permission)

    FakeClient.acl = {}

    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", FakeClient)
    return FakeClient


@pytest.fixture(autouse=True)
def seeded(in_memory_db):
    """A TRUNCATED cache: liveboards + answers present, models/tables absent.
    Row counts alone therefore look perfectly healthy."""
    now = datetime.now(tz=timezone.utc)
    with Session(in_memory_db) as s:
        for guid, name in [("u-alice", "alice"), ("u-bob", "bob")]:
            s.add(
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
        s.add(
            CachedMetadata(
                cluster_id=CLUSTER_ID,
                org_id=ORG_ID,
                ts_guid="lb-1",
                name="Sales",
                object_type="LIVEBOARD",
                owner_guid="u-alice",
                owner_name="Alice",
                tag_names=json.dumps([]),
                synced_at=now,
            )
        )
        s.commit()


def _certify(engine) -> None:
    """Write the SUCCESS marker a completed metadata sync leaves behind."""
    with Session(engine) as s:
        s.add(
            SyncLog(
                cluster_id=CLUSTER_ID,
                org_id=ORG_ID,
                entity_type="metadata",
                status="SUCCESS",
                record_count=1,
            )
        )
        s.commit()


def _job(job_type: str) -> str:
    from ts_admin.services.job_service import create_job

    return create_job(job_type=job_type, parameters={}, cluster_id=CLUSTER_ID)


# ── The five refusal sites ────────────────────────────────────────────────────


async def _call_resolve_downstream() -> None:
    from ts_admin.services import deleter_service

    await deleter_service.resolve_downstream(
        root_guid="ws-1", root_type="LOGICAL_TABLE", cluster_id=CLUSTER_ID, org_id=ORG_ID
    )


async def _call_preview_transfer() -> None:
    from ts_admin.services import user_management_service as svc

    svc.preview_transfer(cluster_id=CLUSTER_ID, org_id=ORG_ID, from_user_guid="u-alice")


async def _call_execute_transfer(job_id: str) -> None:
    from ts_admin.services import user_management_service as svc

    await svc.execute_transfer(
        job_id,
        CLUSTER_ID,
        ORG_ID,
        "u-alice",
        "bob",
        ["lb-1"],
    )


async def _call_preview_share() -> None:
    from ts_admin.services import bulk_sharing_service as svc

    await svc.preview_share(
        cluster_id=CLUSTER_ID,
        org_id=ORG_ID,
        object_guids=["lb-1"],
        principal_guids=["u-bob"],
        mode="READ_ONLY",
    )


async def _call_execute_share(job_id: str) -> None:
    from ts_admin.services import bulk_sharing_service as svc

    await svc.execute_share(
        job_id,
        CLUSTER_ID,
        ORG_ID,
        ["lb-1"],
        ["u-bob"],
        "READ_ONLY",
    )


# Sites reached synchronously from a request, where raising IS the response.
REFUSAL_SITES = [
    pytest.param(_call_resolve_downstream, id="deleter.resolve_downstream"),
    pytest.param(_call_preview_transfer, id="user_management.preview_transfer"),
    pytest.param(_call_preview_share, id="bulk_sharing.preview_share"),
]

# Sites that only ever run as background tasks. `job_type` matters: the record
# written just past the guard differs per site (ShareRecord vs UserActionRecord).
JOB_SITES = [
    pytest.param(_call_execute_share, "bulk_share", id="bulk_sharing.execute_share"),
    pytest.param(_call_execute_transfer, "user_transfer_ownership", id="user_management.execute_transfer"),
]


def _fail_marker(engine) -> None:
    with Session(engine) as s:
        s.add(
            SyncLog(
                cluster_id=CLUSTER_ID,
                org_id=ORG_ID,
                entity_type="metadata",
                status="FAILED",
                error="upstream dropped the connection mid-crawl",
            )
        )
        s.commit()


@pytest.mark.parametrize("call", REFUSAL_SITES)
@pytest.mark.anyio
async def test_refuses_without_a_success_marker(call):
    """Fail-closed. A raise, never a logged warning — a warning nobody reads is
    indistinguishable from the silent data loss this exists to prevent."""
    with pytest.raises(StaleCacheError) as excinfo:
        await call()
    assert excinfo.value.entity_type == "metadata"
    assert excinfo.value.status == "NOT_SYNCED"


@pytest.mark.parametrize("call", REFUSAL_SITES)
@pytest.mark.anyio
async def test_proceeds_past_the_guard_once_certified(call, in_memory_db):
    """Anti-vacuity for the test above: with the marker present the very same
    call runs through. Without this, deleting the marker check entirely would
    still leave the suite green in a suspicious 'everything raises' way."""
    _certify(in_memory_db)
    await call()  # must not raise


@pytest.mark.parametrize("call", REFUSAL_SITES)
@pytest.mark.anyio
async def test_a_failed_marker_still_refuses(call, in_memory_db):
    """SUCCESS is the only certification. A FAILED row must not be read as one."""
    _fail_marker(in_memory_db)
    with pytest.raises(StaleCacheError) as excinfo:
        await call()
    assert excinfo.value.status == "FAILED"


# ── The two background-task sites ─────────────────────────────────────────────
#
# These CANNOT raise: Starlette runs them after the 202 response has been sent,
# so an escaping exception becomes "Caught handled exception, but response
# already started" and leaves the Job stuck in QUEUED with error=None until the
# next server restart reaps it. The guard therefore fails the job instead — and
# the actual refusal, the one the user sees, is asserted at the endpoint in
# tests/integration/test_stale_cache_endpoints.py.


def _job_row(job_id: str):
    from ts_admin.database import get_session
    from ts_admin.models.job import Job

    with get_session() as session:
        return session.get(Job, job_id)


@pytest.mark.parametrize(("call", "job_type"), JOB_SITES)
@pytest.mark.anyio
async def test_background_site_fails_the_job_instead_of_stranding_it(call, job_type):
    """Never QUEUED-with-no-error. A stranded job is worse than a failed one:
    the UI polls it forever and the admin has no idea the work never started."""
    job_id = _job(job_type)
    await call(job_id)  # must NOT raise — nothing is listening

    job = _job_row(job_id)
    assert job.status == "FAILED"
    assert job.error_type == "StaleCacheError"
    assert job.error  # an actionable message, not None


@pytest.mark.parametrize(("call", "job_type"), JOB_SITES)
@pytest.mark.anyio
async def test_background_site_guard_runs_before_any_work(call, job_type):
    """GUARD PLACEMENT, pinned. Moving `require_authoritative_metadata` below
    `mark_running` (or below the record write) leaves every other assertion in
    this file green — a reviewer did exactly that and the suite stayed at 368
    passed. These two assertions are what make placement observable:

      * `total == 0` and `started_at is None` — `mark_running` never ran.
      * no ShareRecord / UserActionRecord — the audit-trail row that the very
        next lines write was never created for an operation that never happened.
    """
    from sqlmodel import select

    from ts_admin.database import get_session
    from ts_admin.models.share_record import ShareRecord
    from ts_admin.models.user_action_record import UserActionRecord

    job_id = _job(job_type)
    await call(job_id)

    job = _job_row(job_id)
    assert job.total == 0, "mark_running() ran — the guard is placed after it"
    assert job.started_at is None, "mark_running() ran — the guard is placed after it"

    with get_session() as session:
        assert session.exec(select(ShareRecord)).all() == []
        assert session.exec(select(UserActionRecord)).all() == []


@pytest.mark.parametrize(("call", "job_type"), JOB_SITES)
@pytest.mark.anyio
async def test_background_site_proceeds_once_certified(call, job_type, in_memory_db):
    """Anti-vacuity: with the SUCCESS marker the same call runs to completion,
    so the two tests above are not passing because everything always fails."""
    _certify(in_memory_db)
    job_id = _job(job_type)
    await call(job_id)

    job = _job_row(job_id)
    assert job.status != "FAILED"
    assert job.error_type != "StaleCacheError"


@pytest.mark.parametrize(("call", "job_type"), JOB_SITES)
@pytest.mark.anyio
async def test_background_site_fails_the_job_on_a_failed_marker(call, job_type, in_memory_db):
    """SUCCESS is the only certification — a FAILED row must not read as one."""
    _fail_marker(in_memory_db)
    job_id = _job(job_type)
    await call(job_id)
    assert _job_row(job_id).error_type == "StaleCacheError"


# ── The read path must NOT refuse ─────────────────────────────────────────────


class TestReadPathIsFlagOnly:
    def test_search_still_returns_rows_without_a_marker(self):
        from ts_admin.services.metadata_service import MetadataService

        items, total = MetadataService.search(cluster_id=CLUSTER_ID, org_id=ORG_ID)
        assert total == 1
        assert items[0].ts_guid == "lb-1"

    def test_search_signature_is_unchanged(self):
        """`search` returns tuple[list, int] — the flag rides on the API response
        model, not on this signature. Changing it would break every caller."""
        from ts_admin.services.metadata_service import MetadataService

        result = MetadataService.search(cluster_id=CLUSTER_ID, org_id=ORG_ID)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_stats_flags_the_truncation_without_raising(self, in_memory_db):
        from ts_admin.services.metadata_service import MetadataService

        stats = MetadataService.stats(cluster_id=CLUSTER_ID, org_id=ORG_ID)
        assert stats["total"] == 1  # still usable
        assert stats["cache_authoritative"] is False

        _certify(in_memory_db)
        assert MetadataService.stats(cluster_id=CLUSTER_ID, org_id=ORG_ID)["cache_authoritative"] is True

    # ── S31: the owned-object count is flagged, never refused ─────────────────

    def test_preview_delete_flags_instead_of_refusing(self):
        """`preview_delete` feeds a WARNING, not an input set — it must stay
        usable on a truncated cache and say so, rather than 409."""
        from ts_admin.services import user_management_service as svc

        result = svc.preview_delete(cluster_id=CLUSTER_ID, user_guids=["u-alice", "u-bob"], org_id=ORG_ID)

        assert result["total"] == 2
        assert result["metadata_cache_authoritative"] is False

    def test_the_acceptance_case_a_zero_count_is_never_bare(self, in_memory_db):
        """S31 proper: the SAME user, genuinely owning an object, reads as 0
        because the metadata cache was truncated under them — and that 0 must
        ship with the flag False.

        Two reads over one seed, one user (alice, who owns ``lb-1``):

        1. certified — the count is NON-ZERO. This is what makes read 2 a real
           observation: the counter works, the fixture really does give this
           user an owned object, and the query is not always-empty. (The old
           version of this test read alice here and *bob* below; bob owns
           nothing under any conditions, so its "zero" was a genuine zero and
           S31's actual claim was never exercised.)
        2. the truncation the bug describes — alice's ``CachedMetadata`` rows
           are deleted and the SUCCESS marker is replaced by the write-ahead
           IN_PROGRESS one, which is precisely ``_sync_metadata``'s window:
           DELETE-all committed, repopulation not yet done. Same user, same
           call: the count is now a bare 0 and the flag says so.
        """
        from ts_admin.services import user_management_service as svc

        _certify(in_memory_db)
        certified = svc.preview_delete(cluster_id=CLUSTER_ID, user_guids=["u-alice"], org_id=ORG_ID)
        assert certified["metadata_cache_authoritative"] is True
        assert certified["items"][0]["owned_object_count"] > 0

        with Session(in_memory_db) as s:
            for row in s.exec(select(CachedMetadata).where(CachedMetadata.owner_guid == "u-alice")).all():
                s.delete(row)
            for row in s.exec(select(SyncLog).where(SyncLog.entity_type == "metadata")).all():
                s.delete(row)
            s.add(
                SyncLog(
                    cluster_id=CLUSTER_ID,
                    org_id=ORG_ID,
                    entity_type="metadata",
                    status="IN_PROGRESS",
                    record_count=0,
                )
            )
            s.commit()

        mid_sync = svc.preview_delete(cluster_id=CLUSTER_ID, user_guids=["u-alice"], org_id=ORG_ID)
        assert mid_sync["items"][0]["owned_object_count"] == 0
        assert mid_sync["metadata_cache_authoritative"] is False

    # ── The count loop and the sync marker race in BOTH directions ────────────

    def test_a_sync_that_finishes_during_the_count_does_not_certify_it(self, in_memory_db, monkeypatch):
        """The fail-OPEN direction, and the reason the marker is read twice.

        ``preview_delete`` issues one COUNT per user (~3.4s for 500 users on the
        performance lens's measurement), so a metadata sync can *complete* while
        the loop is running. The counts were then read from the mid-sync
        truncated cache — post-DELETE-all, pre-repopulate, genuine bare zeros —
        and only afterwards did the marker flip to SUCCESS. A read-after-only
        implementation ships those bare zeros as CERTIFIED: exactly the S31
        shape the whole feature exists to prevent.

        Here the marker flips to SUCCESS from inside the loop (``_is_admin`` is
        called once per user, in the loop, on the same connection). Certified
        before? No. So the flag must be False however the after-read comes out.

        Non-vacuity: the sibling test below writes through the same hook in the
        opposite direction and can only pass if the AFTER-read observes a
        mid-call commit — that is what establishes the hook is effective and
        that intra-call visibility is real.
        """
        from ts_admin.services import user_management_service as svc

        real_is_admin = svc._is_admin

        def _certify_mid_loop(session, cluster_id, ts_guid):
            _certify(in_memory_db)  # a metadata sync completes right here
            return real_is_admin(session, cluster_id, ts_guid)

        monkeypatch.setattr(svc, "_is_admin", _certify_mid_loop)
        result = svc.preview_delete(cluster_id=CLUSTER_ID, user_guids=["u-alice"], org_id=ORG_ID)

        assert result["metadata_cache_authoritative"] is False

    def test_a_sync_that_starts_during_the_count_does_not_certify_it(self, in_memory_db, monkeypatch):
        """The conservative direction — and the non-vacuity anchor for the test
        above. The marker is SUCCESS when the call begins and is invalidated
        (write-ahead IN_PROGRESS, which ``_sync_metadata`` writes *before* its
        DELETE-all) from inside the loop. This can only come out False if the
        AFTER-read really does observe a commit made during the call.
        """
        from ts_admin.services import user_management_service as svc

        _certify(in_memory_db)
        real_is_admin = svc._is_admin

        def _invalidate_mid_loop(session, cluster_id, ts_guid):
            with Session(in_memory_db) as s:
                for row in s.exec(select(SyncLog).where(SyncLog.entity_type == "metadata")).all():
                    row.status = "IN_PROGRESS"
                    s.add(row)
                s.commit()
            return real_is_admin(session, cluster_id, ts_guid)

        monkeypatch.setattr(svc, "_is_admin", _invalidate_mid_loop)
        result = svc.preview_delete(cluster_id=CLUSTER_ID, user_guids=["u-alice"], org_id=ORG_ID)

        assert result["metadata_cache_authoritative"] is False

    def test_a_whole_sync_cycle_inside_the_count_window_does_not_certify_it(self, in_memory_db, monkeypatch):
        """KILL TEST for presence-vs-identity. The two reads must compare WHICH
        marker they saw, not merely that *a* marker was there.

        The gap a presence check leaves open: an entire sync cycle
        (SUCCESS N → IN_PROGRESS → DELETE-all → repopulate → SUCCESS N+1) fits
        inside the count loop. Marker N certifies the reads taken before the
        DELETE-all, marker N+1 certifies the reads taken after it, and the
        counts read from the truncated cache *in between* are certified by
        neither — yet ``x is not None`` before AND after is True at both
        instants, so a bare 0 ships as CERTIFIED. That is verbatim the S31
        shape the feature exists to prevent. Comparing ``(id, synced_at)``
        closes it: ``sync_service._write_sync_log`` upserts the row in place
        and refreshes ``synced_at`` on every non-``preserve_progress`` write,
        so "same identity before and after" is exactly "no sync completed in
        the window".

        Two OWNING users are required. The module fixture seeds ``u-alice``
        (owns ``lb-1``) and ``u-bob`` (owns nothing), so a race fired on the
        first user is read only by a user whose count is zero under all
        conditions — vacuous. ``u-carol`` is seeded here owning ``lb-2`` and
        ordered after alice, so the interesting count is genuinely read from
        the truncated cache. The non-vacuity assertions below pin that.
        """
        from ts_admin.services import user_management_service as svc

        now = datetime.now(tz=timezone.utc)
        with Session(in_memory_db) as s:
            s.add(
                CachedUser(
                    cluster_id=CLUSTER_ID,
                    ts_guid="u-carol",
                    username="carol",
                    display_name="Carol",
                    email="carol@co.com",
                    status="ACTIVE",
                    synced_at=now,
                )
            )
            s.add(
                CachedMetadata(
                    cluster_id=CLUSTER_ID,
                    org_id=ORG_ID,
                    ts_guid="lb-2",
                    name="Ops",
                    object_type="LIVEBOARD",
                    owner_guid="u-carol",
                    owner_name="Carol",
                    tag_names=json.dumps([]),
                    synced_at=now,
                )
            )
            # Marker N — written the way `_write_sync_log` writes it: ONE row,
            # upserted in place. A fresh row per sync would give the after-read
            # a different primary key and let a weaker identity check pass.
            s.add(
                SyncLog(
                    cluster_id=CLUSTER_ID,
                    org_id=ORG_ID,
                    entity_type="metadata",
                    status="SUCCESS",
                    record_count=2,
                    synced_at=now - timedelta(minutes=5),
                )
            )
            s.commit()

        real_is_admin = svc._is_admin
        fired: list[str] = []

        def _run_a_whole_sync_cycle(session, cluster_id, ts_guid):
            # `_is_admin` runs once per user, AFTER that user's count, on the
            # call's own session — so hooking it lets the cycle straddle the
            # two counts the way a real 3.4s loop does.
            fired.append(ts_guid)
            with Session(in_memory_db) as s:
                marker = s.exec(select(SyncLog).where(SyncLog.entity_type == "metadata")).one()
                if len(fired) == 1:
                    # alice's count (=1) is already taken. The sync now starts:
                    # write-ahead invalidation, then the DELETE-all commits.
                    marker.status = "IN_PROGRESS"
                    s.add(marker)
                    s.commit()
                    for row in s.exec(select(CachedMetadata)).all():
                        s.delete(row)
                    s.commit()
                else:
                    # carol's count (=0, a bare zero off the truncated cache) is
                    # already taken. Only NOW does the sync repopulate and flip
                    # the marker back to SUCCESS — same row, new `synced_at`.
                    for guid, owner in [("lb-1", "u-alice"), ("lb-2", "u-carol")]:
                        s.add(
                            CachedMetadata(
                                cluster_id=CLUSTER_ID,
                                org_id=ORG_ID,
                                ts_guid=guid,
                                name=guid,
                                object_type="LIVEBOARD",
                                owner_guid=owner,
                                owner_name=owner,
                                tag_names=json.dumps([]),
                                synced_at=datetime.now(tz=timezone.utc),
                            )
                        )
                    marker.status = "SUCCESS"
                    marker.synced_at = datetime.now(tz=timezone.utc)
                    s.add(marker)
                    s.commit()
            return real_is_admin(session, cluster_id, ts_guid)

        monkeypatch.setattr(svc, "_is_admin", _run_a_whole_sync_cycle)
        result = svc.preview_delete(cluster_id=CLUSTER_ID, user_guids=["u-alice", "u-carol"], org_id=ORG_ID)

        counts = {i["ts_guid"]: i["owned_object_count"] for i in result["items"]}
        # Non-vacuity: the hook fired for both users, alice was counted BEFORE
        # the cycle (so the counter demonstrably works), and carol's count is
        # the bare zero read from the emptied cache. Without these, a green
        # assertion below could just mean "nothing interesting happened".
        assert fired == ["u-alice", "u-carol"], fired
        assert counts["u-alice"] == 1, counts
        assert counts["u-carol"] == 0, counts
        # A SUCCESS marker existed at BOTH reads — a presence check says True.
        assert result["metadata_cache_authoritative"] is False, "FAIL-OPEN: bare 0 shipped as CERTIFIED"

    def test_certification_flips_the_flag(self, in_memory_db):
        from ts_admin.services import user_management_service as svc

        assert (
            svc.preview_delete(cluster_id=CLUSTER_ID, user_guids=["u-alice"], org_id=ORG_ID)[
                "metadata_cache_authoritative"
            ]
            is False
        )

        _certify(in_memory_db)
        assert (
            svc.preview_delete(cluster_id=CLUSTER_ID, user_guids=["u-alice"], org_id=ORG_ID)[
                "metadata_cache_authoritative"
            ]
            is True
        )
