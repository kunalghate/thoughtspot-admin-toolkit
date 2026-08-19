"""
Unit tests for ts_admin.services.bulk_sharing_service.

The load-bearing invariant here: `preview_share` and `execute_share` operate on
the IDENTICAL resolved set. A GUID with no CachedMetadata row for
`(cluster_id, org_id)` is excluded from both and reported by both — it is never
shared under a guessed `object_type`, and never revoked (NO_ACCESS) behind a
preview that did not mention it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, create_engine, select

from ts_admin.models.audit_log import AuditLog
from ts_admin.models.cache.ts_group import CachedGroup
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cluster import Cluster
from ts_admin.models.job import Job
from ts_admin.models.sync_log import SyncLog
from ts_admin.ts_client.exceptions import (
    TSInvalidParametersError,
    TSObjectNotFoundError,
    TSServerError,
)

CLUSTER_ID = "c1"
ORG_ID = 0


# ── Fake TS client ─────────────────────────────────────────────────────────────


class _FakeClient:
    """Stand-in for ThoughtSpotClient; records every share_objects call.

    It also keeps a real ACL, because `security/metadata/share` answers 204 No
    Content and `execute_share` therefore READS THE ACL BACK to decide whether
    the share landed (S44). A fake whose share is a no-op is a fake of a cluster
    where every share silently fails — which is exactly the state three
    non-existent endpoints were in for the life of the project, and exactly what
    the verification pass now refuses to call SUCCESS.

    `apply_shares = False` reproduces that cluster on purpose;
    `share_should_fail` reproduces a share call that raises.
    """

    share_calls: list[tuple[list[str], list[str], str]] = []  # (object_ids, principal_ids, permission)
    share_kwargs: list[dict] = []  # (message, notify) per call
    permission_calls: list[tuple[str, str, str]] = []  # (ts_guid, object_type, permission_type)
    acl: dict[tuple[str, str], str] = {}  # (object_guid, principal_guid) → mode
    apply_shares: bool = True
    share_should_fail: Exception | None = None
    fail_object_ids: set[str] = set()  # empty = every share call fails

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def fetch_permissions(self, *, ts_guid, object_type, permission_type="DEFINED"):
        from ts_admin.ts_client.models import TSPermission

        _FakeClient.permission_calls.append((ts_guid, str(object_type), permission_type))
        return [
            TSPermission(
                principal_id=pid,
                principal_name=pid,
                principal_type="USER_GROUP",
                share_mode=mode,
            )
            for (guid, pid), mode in _FakeClient.acl.items()
            # The real client drops NO_ACCESS rows — a revoke shows up as the
            # principal being ABSENT, never as a NO_ACCESS entry.
            if guid == ts_guid and mode != "NO_ACCESS"
        ]

    async def share_objects(self, *, object_ids, principal_ids, permission, message="", notify=False):
        _FakeClient.share_calls.append((list(object_ids), list(principal_ids), str(permission)))
        _FakeClient.share_kwargs.append({"message": message, "notify": notify})
        if _FakeClient.share_should_fail is not None and (
            not _FakeClient.fail_object_ids or set(object_ids) & _FakeClient.fail_object_ids
        ):
            raise _FakeClient.share_should_fail
        if not _FakeClient.apply_shares:
            return  # 204 No Content, and nothing actually changed
        for oid in object_ids:
            for pid in principal_ids:
                _FakeClient.acl[(oid, pid)] = str(permission)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_fake():
    _FakeClient.share_calls = []
    _FakeClient.share_kwargs = []
    _FakeClient.permission_calls = []
    _FakeClient.acl = {}
    _FakeClient.apply_shares = True
    _FakeClient.share_should_fail = None
    _FakeClient.fail_object_ids = set()


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
def patched_env(monkeypatch):
    """Patch TS client + load_config so no live call is ever attempted."""
    from ts_admin.config import AppConfig, ClusterConfig
    from ts_admin.ts_client.models import AuthType

    cluster_cfg = ClusterConfig(
        id=CLUSTER_ID,
        name="Prod",
        url="https://prod.thoughtspot.cloud",
        username="admin",
        auth_type=AuthType.BASIC,
    )
    config = AppConfig(clusters={CLUSTER_ID: cluster_cfg}, active_cluster_id=CLUSTER_ID)
    monkeypatch.setattr("ts_admin.config.load_config", lambda: config)
    monkeypatch.setattr("ts_admin.config._load_secret", lambda cluster_id, field: "fake-secret")
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", _FakeClient)


@pytest.fixture
def seeded(in_memory_db):
    """lb-1 (LIVEBOARD) + ans-1 (ANSWER) cached; `ghost-1` deliberately is not."""
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
        # Certifies the metadata cache as complete — both entry points fail
        # closed without it.
        session.add(
            SyncLog(cluster_id=CLUSTER_ID, org_id=ORG_ID, entity_type="metadata", status="SUCCESS", record_count=2)
        )
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
        for guid, name, obj_type in [("lb-1", "Sales", "LIVEBOARD"), ("ans-1", "Revenue", "ANSWER")]:
            session.add(
                CachedMetadata(
                    cluster_id=CLUSTER_ID,
                    org_id=ORG_ID,
                    ts_guid=guid,
                    name=name,
                    object_type=obj_type,
                    owner_guid="u-alice",
                    owner_name="Alice",
                    tag_names=json.dumps([]),
                    synced_at=now,
                )
            )
        session.commit()


def _create_job(job_type: str) -> str:
    import uuid

    from ts_admin.database import get_session

    job_id = str(uuid.uuid4())
    job = Job(id=job_id, cluster_id=CLUSTER_ID, job_type=job_type, status="QUEUED")
    with get_session() as s:
        s.add(job)
        s.commit()
    return job_id


def _preview(object_guids: list[str], mode: str = "READ_ONLY") -> dict:
    from ts_admin.services.bulk_sharing_service import preview_share

    return asyncio.run(
        preview_share(
            cluster_id=CLUSTER_ID,
            org_id=ORG_ID,
            object_guids=object_guids,
            principal_guids=["g-finance"],
            mode=mode,
        )
    )


def _execute(job_id: str, object_guids: list[str], mode: str = "READ_ONLY") -> None:
    from ts_admin.services.bulk_sharing_service import execute_share

    asyncio.run(
        execute_share(
            job_id,
            CLUSTER_ID,
            ORG_ID,
            object_guids,
            ["g-finance"],
            mode,
        )
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestShareRequestReachesTheClientIntact:
    @pytest.mark.parametrize("notify", [True, False])
    def test_notify_reaches_the_client_instead_of_only_the_audit_log(self, in_memory_db, patched_env, seeded, notify):
        """`notify` used to be recorded in the audit-log row and then dropped —
        it never reached the client. `notify_on_share` defaults to TRUE on the
        wire, so "do not notify" would have emailed every recipient while the
        audit trail said `"notify": false`."""
        from ts_admin.services.bulk_sharing_service import SHARE_MESSAGE, execute_share

        job_id = _create_job("bulk_share")
        asyncio.run(
            execute_share(
                job_id,
                CLUSTER_ID,
                ORG_ID,
                ["lb-1"],
                ["g-finance"],
                "READ_ONLY",
                notify=notify,
            )
        )
        assert _FakeClient.share_kwargs == [{"message": SHARE_MESSAGE, "notify": notify}]

    def test_message_is_never_empty(self, in_memory_db, patched_env, seeded):
        """`message` is in the share schema's `required` list."""
        from ts_admin.services.bulk_sharing_service import SHARE_MESSAGE, execute_share

        job_id = _create_job("bulk_share")
        asyncio.run(execute_share(job_id, CLUSTER_ID, ORG_ID, ["lb-1"], ["g-finance"], "READ_ONLY"))
        assert SHARE_MESSAGE
        assert _FakeClient.share_kwargs[0]["message"] == SHARE_MESSAGE


class TestPreviewExecuteAgree:
    def test_uncached_guid_is_excluded_from_both_and_reported_in_both(
        self,
        in_memory_db,
        patched_env,
        seeded,
    ):
        requested = ["lb-1", "ghost-1"]

        preview = _preview(requested)
        assert {r["object_guid"] for r in preview["items"]} == {"lb-1"}
        assert [s["object_guid"] for s in preview["skipped"]] == ["ghost-1"]
        assert preview["skipped_count"] == 1

        job_id = _create_job("bulk_share")
        _execute(job_id, requested)

        # The exact set the preview described is the exact set that was shared.
        shared_guids = {g for ids, _p, _m in _FakeClient.share_calls for g in ids}
        assert shared_guids == {r["object_guid"] for r in preview["items"]}
        assert "ghost-1" not in shared_guids

        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
        result = job.get_result() or {}
        assert job.status == "PARTIAL"  # something requested did not happen
        assert [x["object_guid"] for x in result["skipped"]] == ["ghost-1"]
        assert result["skipped_count"] == 1
        assert result["succeeded_pairs"] == 1
        assert result["requested_pairs"] == 2

    def test_no_access_revoke_never_exceeds_the_preview(self, in_memory_db, patched_env, seeded):
        # The dangerous direction: a divergence here REVOKES access the admin
        # was never shown.
        requested = ["lb-1", "ghost-1"]
        preview = _preview(requested, mode="NO_ACCESS")

        job_id = _create_job("bulk_share")
        _execute(job_id, requested, mode="NO_ACCESS")

        shared_guids = {g for ids, _p, _m in _FakeClient.share_calls for g in ids}
        assert shared_guids == {r["object_guid"] for r in preview["items"]} == {"lb-1"}

    def test_object_type_is_never_guessed(self, in_memory_db, patched_env, seeded):
        job_id = _create_job("bulk_share")
        _execute(job_id, ["lb-1", "ans-1", "ghost-1"])

        # One call per cached type, and the ANSWER never rides in a LIVEBOARD call.
        by_ids = {tuple(sorted(ids)) for ids, _p, _m in _FakeClient.share_calls}
        assert by_ids == {("lb-1",), ("ans-1",)}

    def test_all_guids_uncached_fails_the_job_without_sharing_anything(
        self,
        in_memory_db,
        patched_env,
        seeded,
    ):
        job_id = _create_job("bulk_share")
        _execute(job_id, ["ghost-1", "ghost-2"])

        assert _FakeClient.share_calls == []
        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
        assert job.status == "FAILED"
        assert "0 of 2 objects resolved" in (job.error or "")

    def test_fully_cached_request_still_completes(self, in_memory_db, patched_env, seeded):
        job_id = _create_job("bulk_share")
        _execute(job_id, ["lb-1", "ans-1"])

        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
        result = job.get_result() or {}
        assert job.status == "COMPLETE"
        assert result["skipped"] == []
        assert result["succeeded_pairs"] == result["total_pairs"] == result["requested_pairs"] == 2


class TestResolutionScoping:
    def test_resolution_is_scoped_to_cluster_and_org(self, in_memory_db, patched_env, seeded):
        # Same GUID cached under a different org — it must NOT resolve for org 0.
        now = datetime.now(tz=timezone.utc)
        with Session(in_memory_db) as s:
            s.add(
                CachedMetadata(
                    cluster_id=CLUSTER_ID,
                    org_id=5,
                    ts_guid="other-org-1",
                    name="Elsewhere",
                    object_type="LIVEBOARD",
                    owner_guid="u-alice",
                    owner_name="Alice",
                    tag_names=json.dumps([]),
                    synced_at=now,
                )
            )
            s.commit()

        preview = _preview(["lb-1", "other-org-1"])
        assert [x["object_guid"] for x in preview["skipped"]] == ["other-org-1"]

        job_id = _create_job("bulk_share")
        _execute(job_id, ["lb-1", "other-org-1"])
        shared_guids = {g for ids, _p, _m in _FakeClient.share_calls for g in ids}
        assert shared_guids == {"lb-1"}


class TestDryRunReportsSkipped:
    def test_dryrun_job_result_carries_the_skipped_list(self, in_memory_db, patched_env, seeded):
        from ts_admin.services.bulk_sharing_service import dryrun_share

        job_id = _create_job("bulk_share_dryrun")
        asyncio.run(
            dryrun_share(
                job_id=job_id,
                cluster_id=CLUSTER_ID,
                org_id=ORG_ID,
                object_guids=["lb-1", "ghost-1"],
                principal_guids=["g-finance"],
                mode="NO_ACCESS",
            )
        )

        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
        result = job.get_result() or {}
        assert job.status == "COMPLETE"
        assert result["skipped_count"] == 1
        assert [x["object_guid"] for x in result["skipped"]] == ["ghost-1"]


class TestTagResolutionMatchesTheDeleter:
    """
    `bulk_sharing_service.resolve_tag_to_guids` and `deleter_service.resolve_tag`
    are two intake paths over the same cache and the same tag name. They used to
    disagree — only the Deleter excluded System-User-owned content — so `finance`
    meant one set in Bulk Sharing and a different set in the Bulk Deleter. That
    divergence is worst in `mode=NO_ACCESS`, which would revoke access on
    ThoughtSpot's built-in content the admin never picked out by hand.
    """

    @pytest.fixture
    def tagged(self, in_memory_db, seeded):
        now = datetime.now(tz=timezone.utc)
        with Session(in_memory_db) as s:
            for guid, owner in [("lb-user", "Alice"), ("lb-system", "System User")]:
                s.add(
                    CachedMetadata(
                        cluster_id=CLUSTER_ID,
                        org_id=ORG_ID,
                        ts_guid=guid,
                        name=guid,
                        object_type="LIVEBOARD",
                        owner_guid="u-alice",
                        owner_name=owner,
                        tag_names=json.dumps(["finance"]),
                        synced_at=now,
                    )
                )
            # Same tag, another org — must never leak across the org boundary.
            s.add(
                CachedMetadata(
                    cluster_id=CLUSTER_ID,
                    org_id=99,
                    ts_guid="lb-other-org",
                    name="lb-other-org",
                    object_type="LIVEBOARD",
                    owner_guid="u-alice",
                    owner_name="Alice",
                    tag_names=json.dumps(["finance"]),
                    synced_at=now,
                )
            )
            s.commit()

    def test_both_features_resolve_the_same_tag_to_the_same_set(self, tagged):
        from ts_admin.services.bulk_sharing_service import resolve_tag_to_guids
        from ts_admin.services.deleter_service import resolve_tag

        share_set = set(resolve_tag_to_guids(cluster_id=CLUSTER_ID, org_id=ORG_ID, tag_name="finance"))
        delete_set = {
            r["ts_guid"] for r in resolve_tag(tag_name="finance", cluster_id=CLUSTER_ID, org_id=ORG_ID)["items"]
        }

        assert share_set == delete_set == {"lb-user"}
        assert "lb-system" not in share_set, "System-User-owned content must not be reachable by tag intake"
        assert "lb-other-org" not in share_set

    def test_a_tag_that_is_a_substring_of_another_does_not_over_match(self, tagged, in_memory_db):
        """The LIKE narrowing is a prefilter — the Python check is what decides."""
        now = datetime.now(tz=timezone.utc)
        with Session(in_memory_db) as s:
            s.add(
                CachedMetadata(
                    cluster_id=CLUSTER_ID,
                    org_id=ORG_ID,
                    ts_guid="lb-super",
                    name="lb-super",
                    object_type="LIVEBOARD",
                    owner_guid="u-alice",
                    owner_name="Alice",
                    tag_names=json.dumps(["finance-archive"]),
                    synced_at=now,
                )
            )
            s.commit()

        from ts_admin.services.bulk_sharing_service import resolve_tag_to_guids

        assert set(resolve_tag_to_guids(cluster_id=CLUSTER_ID, org_id=ORG_ID, tag_name="finance")) == {"lb-user"}


# ── M14: zero successes is FAILED, never PARTIAL ──────────────────────────────


def _job_row(job_id: str) -> Job:
    from ts_admin.database import get_session

    with get_session() as s:
        return s.get(Job, job_id)


def _audit_rows() -> list[AuditLog]:
    from ts_admin.database import get_session

    with get_session() as s:
        return list(s.exec(select(AuditLog).where(AuditLog.action_type == "bulk_share")).all())


class TestZeroSuccessesIsFailedNeverPartial:
    """
    A job in which NOTHING succeeded must not present as one that achieved
    something. `status = "PARTIAL" if (failed or cancelled or skipped) else
    "SUCCESS"` used to be evaluated BEFORE any `succeeded == 0` branch, so a
    share against an endpoint that 404'd for the life of the project reported
    PARTIAL with zero pairs affected — which reads as "some of it worked, retry
    the rest" and, attached to the 404's canned "run a sync and retry" message,
    sent admins re-syncing forever. Reverting the branch order fails these.
    """

    def test_every_share_call_failing_ends_the_job_failed(self, in_memory_db, patched_env, seeded):
        _FakeClient.share_should_fail = TSObjectNotFoundError(
            object_type="resource",
            identifier="/api/rest/2.0/security/share",
            detail="Not Found",
        )

        job_id = _create_job("bulk_share")
        _execute(job_id, ["lb-1", "ans-1"])

        job = _job_row(job_id)
        assert job.status == "FAILED"
        assert job.status != "PARTIAL"

    def test_the_failure_message_names_the_cause_not_just_a_count(self, in_memory_db, patched_env, seeded):
        _FakeClient.share_should_fail = TSObjectNotFoundError(
            object_type="resource",
            identifier="/api/rest/2.0/security/share",
            detail="Not Found",
        )

        job_id = _create_job("bulk_share")
        _execute(job_id, ["lb-1"])

        error = _job_row(job_id).error or ""
        assert "0 share operations succeeded" in error
        # The endpoint path and ThoughtSpot's own words — the only thing that
        # tells an admin the endpoint does not exist rather than the cache
        # being stale.
        assert "/api/rest/2.0/security/share" in error
        assert "Not Found" in error

    def test_the_audit_row_says_failed_too(self, in_memory_db, patched_env, seeded):
        _FakeClient.share_should_fail = TSInvalidParametersError("bad request")

        job_id = _create_job("bulk_share")
        _execute(job_id, ["lb-1", "ans-1"])

        rows = _audit_rows()
        assert len(rows) == 1
        assert rows[0].status == "FAILED"
        assert rows[0].items_affected == 0
        assert "bad request" in rows[0].get_parameters()["error"]

    def test_a_genuinely_partial_run_is_still_partial(self, in_memory_db, patched_env, seeded):
        """Non-vacuity: the fix must not collapse PARTIAL into FAILED."""
        _FakeClient.share_should_fail = TSInvalidParametersError("bad request")
        _FakeClient.fail_object_ids = {"ans-1"}

        job_id = _create_job("bulk_share")
        _execute(job_id, ["lb-1", "ans-1"])

        job = _job_row(job_id)
        assert job.status == "PARTIAL"
        assert (job.get_result() or {})["succeeded_pairs"] == 1
        assert _audit_rows()[0].status == "PARTIAL"

    def test_a_bug_in_our_own_code_fails_the_job_instead_of_becoming_a_partial(
        self,
        in_memory_db,
        patched_env,
        seeded,
    ):
        """The per-chunk catch is narrowed to (TSAdminError, httpx.HTTPError).

        A blanket `except Exception` there turned a live TypeError in our own
        code into "this chunk failed upstream" and shipped it as a PARTIAL job
        with a ThoughtSpot-flavoured error message it had nothing to do with.
        """
        _FakeClient.share_should_fail = TypeError("share_objects() got an unexpected keyword argument")

        job_id = _create_job("bulk_share")
        _execute(job_id, ["lb-1"])

        job = _job_row(job_id)
        assert job.status == "FAILED"
        assert job.error_type == "TypeError"
        # It never reached the per-chunk bucket, so no audit row claims a
        # partially-completed share.
        assert _audit_rows() == []


# ── S44: a bulk share is verified by reading it back ──────────────────────────


class TestPostExecuteVerification:
    """
    `security/metadata/share` returns a bare 204 No Content, so "the share
    succeeded" was an assumption and the audit row recording it was a guess.
    Every shared object is now re-read through `fetch-permissions` and compared
    against what was requested.
    """

    def test_a_share_that_never_lands_is_not_reported_as_success(self, in_memory_db, patched_env, seeded):
        # The 204 comes back, and nothing changed — the exact state a
        # non-existent endpoint leaves the cluster in.
        _FakeClient.apply_shares = False

        job_id = _create_job("bulk_share")
        _execute(job_id, ["lb-1", "ans-1"])

        job = _job_row(job_id)
        assert job.status == "FAILED"
        error = job.error or ""
        assert "2 of 2 re-read object(s) do not carry the requested access" in error
        assert "did not take effect" in error

        params = _audit_rows()[0].get_parameters()
        assert params["verified_failed"] == 2
        assert params["verified_ok"] == 0
        assert _audit_rows()[0].status == "FAILED"

    def test_a_share_that_lands_verifies_clean(self, in_memory_db, patched_env, seeded):
        job_id = _create_job("bulk_share")
        _execute(job_id, ["lb-1", "ans-1"])

        job = _job_row(job_id)
        assert job.status == "COMPLETE"
        result = job.get_result() or {}
        assert result["verified_ok"] == 2
        assert result["verified_failed"] == 0
        assert result["verified_errors"] == 0
        assert result["verification_scope"] == "full"

    def test_a_partly_applied_share_is_partial_not_complete(self, in_memory_db, patched_env, seeded):
        """One object's ACL is left unchanged behind a 204 — the other lands."""
        from ts_admin.services import bulk_sharing_service as svc

        real_share = _FakeClient.share_objects

        async def _selective(self, *, object_ids, principal_ids, permission, message="", notify=False):
            if "ans-1" in object_ids:
                _FakeClient.share_calls.append((list(object_ids), list(principal_ids), str(permission)))
                _FakeClient.share_kwargs.append({"message": message, "notify": notify})
                return  # 204, no ACL change
            await real_share(
                self,
                object_ids=object_ids,
                principal_ids=principal_ids,
                permission=permission,
                message=message,
                notify=notify,
            )

        _FakeClient.share_objects = _selective
        try:
            job_id = _create_job("bulk_share")
            _execute(job_id, ["lb-1", "ans-1"])
        finally:
            _FakeClient.share_objects = real_share

        job = _job_row(job_id)
        assert job.status == "PARTIAL"
        result = job.get_result() or {}
        assert result["verified_ok"] == 1
        assert result["verified_failed"] == 1
        assert [x["object_guid"] for x in result["verified_failed_guids"]] == ["ans-1"]
        assert svc.VERIFY_SAMPLE_LIMIT >= 2  # non-vacuity: both objects were in the sample

    def test_a_revoke_is_verified_by_absence_not_by_a_no_access_row(self, in_memory_db, patched_env, seeded):
        """`fetch_permissions` never returns a NO_ACCESS row, so an applied
        revoke shows up as the principal being GONE from the defined ACL."""
        _FakeClient.acl[("lb-1", "g-finance")] = "READ_ONLY"

        job_id = _create_job("bulk_share")
        _execute(job_id, ["lb-1"], mode="NO_ACCESS")

        result = _job_row(job_id).get_result() or {}
        assert result["verified_ok"] == 1
        assert result["verified_failed"] == 0

    def test_a_revoke_that_did_not_land_is_caught(self, in_memory_db, patched_env, seeded):
        _FakeClient.acl[("lb-1", "g-finance")] = "READ_ONLY"
        _FakeClient.apply_shares = False

        job_id = _create_job("bulk_share")
        _execute(job_id, ["lb-1"], mode="NO_ACCESS")

        job = _job_row(job_id)
        assert job.status == "FAILED"

    def test_verification_reads_the_defined_lens(self, in_memory_db, patched_env, seeded):
        """A share creates a DIRECT (DEFINED) share, and the preview diffed
        through the same lens — EFFECTIVE would report group-inherited access
        as proof that an explicit share landed."""
        job_id = _create_job("bulk_share")
        _execute(job_id, ["lb-1"])

        assert _FakeClient.permission_calls, "verification made no read-back call at all"
        assert {lens for _g, _t, lens in _FakeClient.permission_calls} == {"DEFINED"}

    def test_verification_is_capped_and_the_result_says_so(self, in_memory_db, patched_env, seeded):
        """Verification is N extra live calls on a job that may cover thousands
        of objects, so it is bounded — and a bounded verification reported as a
        full one would be the same class of lie this exists to fix."""
        from ts_admin.services import bulk_sharing_service as svc

        now = datetime.now(tz=timezone.utc)
        guids = [f"lb-bulk-{i}" for i in range(svc.VERIFY_SAMPLE_LIMIT + 10)]
        with Session(in_memory_db) as s:
            for guid in guids:
                s.add(
                    CachedMetadata(
                        cluster_id=CLUSTER_ID,
                        org_id=ORG_ID,
                        ts_guid=guid,
                        name=guid,
                        object_type="LIVEBOARD",
                        owner_guid="u-alice",
                        owner_name="Alice",
                        tag_names=json.dumps([]),
                        synced_at=now,
                    )
                )
            s.commit()

        job_id = _create_job("bulk_share")
        _execute(job_id, guids)

        result = _job_row(job_id).get_result() or {}
        assert result["verified_candidates"] == len(guids)
        assert result["verified_sampled"] == svc.VERIFY_SAMPLE_LIMIT
        assert result["verification_scope"] == "sample"
        assert "NOT verified" in result["verification_note"]
        assert f"{svc.VERIFY_SAMPLE_LIMIT} of {len(guids)}" in result["verification_note"]

    def test_a_read_back_that_itself_fails_is_not_counted_as_a_failed_share(
        self,
        in_memory_db,
        patched_env,
        seeded,
    ):
        """An unreachable verification read proves nothing either way — it must
        not be laundered into "the share failed"."""
        real_fetch = _FakeClient.fetch_permissions

        async def _boom(self, *, ts_guid, object_type, permission_type="DEFINED"):
            if _FakeClient.share_calls:  # only the post-execute read-back
                raise TSServerError(status_code=503, body="verification unavailable")
            return await real_fetch(self, ts_guid=ts_guid, object_type=object_type, permission_type=permission_type)

        _FakeClient.fetch_permissions = _boom
        try:
            job_id = _create_job("bulk_share")
            _execute(job_id, ["lb-1"])
        finally:
            _FakeClient.fetch_permissions = real_fetch

        job = _job_row(job_id)
        result = job.get_result() or {}
        assert result["verified_errors"] == 1
        assert result["verified_failed"] == 0
        assert job.status == "COMPLETE"
        assert "could not be re-read" in result["verification_note"]
