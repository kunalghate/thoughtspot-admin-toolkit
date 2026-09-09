"""
Unit tests for the Dashboard activity feed's pure renderers.

`_delete_entry` is the whole decision table for "what did this archive session
actually do?" — it is module-level and pure precisely so every branch can be
driven here, with no HTTP round trip and no DB. The integration suite
(`tests/integration/test_dashboard_api.py`) then proves the SQL aggregate feeds
it the right numbers.

Every case asserts the label and the status AS A PAIR: the defect this replaces
was a feed that said "Deleted 2 objects" next to a green SUCCESS pill about two
objects that were still live in ThoughtSpot. Label and status agreeing is the
property, not either one alone.
"""

from __future__ import annotations

import pytest

from ts_admin.services.dashboard_service import _delete_entry, _plural, _severity


def _case(name, *, total, deleted=0, exported=0, failed=0, job_type=None, job_status=None, action=None, expect):
    return pytest.param(
        {
            "total": total,
            "deleted": deleted,
            "exported": exported,
            "failed": failed,
            "job_type": job_type,
            "job_status": job_status,
            "action": action,
        },
        expect,
        id=name,
    )


# (label, status) pairs, one per branch of the rule table.
DELETE_CASES = [
    # (a) every row confirmed deleted.
    _case(
        "a-all-deleted",
        total=2,
        deleted=2,
        exported=2,
        job_type="bulk_delete",
        job_status="COMPLETE",
        expect=("Deleted 2 objects (TML backed up)", "SUCCESS"),
    ),
    _case(
        "a-all-deleted-singular",
        total=1,
        deleted=1,
        exported=1,
        job_type="bulk_delete",
        job_status="COMPLETE",
        expect=("Deleted 1 object (TML backed up)", "SUCCESS"),
    ),
    # (b) some confirmed deletes — a real partial, whatever the job claims.
    _case(
        "b-some-deleted",
        total=400,
        deleted=100,
        exported=100,
        failed=300,
        job_type="bulk_delete",
        job_status="PARTIAL",
        expect=("Deleted 100 of 400 objects (TML backed up)", "PARTIAL"),
    ),
    # (a)/(b) outrank job metadata: a confirmed delete can never be hidden by a
    # job that calls itself an export, or by a job still marked RUNNING.
    _case(
        "b-confirmed-delete-outranks-export-intent",
        total=2,
        deleted=1,
        exported=2,
        job_type="archive",
        job_status="COMPLETE",
        action="export",
        expect=("Deleted 1 of 2 objects (TML backed up)", "PARTIAL"),
    ),
    _case(
        "a-confirmed-delete-outranks-in-flight",
        total=2,
        deleted=2,
        exported=2,
        job_type="bulk_delete",
        job_status="RUNNING",
        expect=("Deleted 2 objects (TML backed up)", "SUCCESS"),
    ),
    # (b) in flight WITH some already deleted: the counts are real and stay,
    # but PARTIAL ("some worked, retry the rest") is a terminal claim about a
    # job at chunk 2 of 8 — the status must be the neutral PENDING.
    _case(
        "b-in-flight-with-some-deleted",
        total=8,
        deleted=2,
        exported=8,
        job_type="bulk_delete",
        job_status="RUNNING",
        expect=("Deleted 2 of 8 objects so far (TML backed up)…", "PENDING"),
    ),
    # (c) in flight — nothing confirmed yet, and the job is still going.
    _case(
        "c-in-flight-running",
        total=3,
        exported=1,
        job_type="bulk_delete",
        job_status="RUNNING",
        expect=("Exporting/Deleting 3 objects…", "PENDING"),
    ),
    _case(
        "c-in-flight-queued",
        total=1,
        job_type="archive",
        job_status="QUEUED",
        action="delete",
        expect=("Exporting/Deleting 1 object…", "PENDING"),
    ),
    # `Job.status` is never "PENDING" (`job_service.py:36,79,99,138,152`), so
    # it is NOT an in-flight status — an export session carrying it is read on
    # its rows like any other finished one.
    _case(
        "c-pending-is-not-a-job-status",
        total=5,
        job_type="archive",
        job_status="PENDING",
        action="export",
        expect=("TML export failed for 5 objects", "FAILED"),
    ),
    # (d) export-only run that finished cleanly — NOT a delete.
    _case(
        "d-export-only-clean",
        total=2,
        exported=2,
        job_type="archive",
        job_status="COMPLETE",
        action="export",
        expect=("Exported 2 objects to TML (not deleted)", "SUCCESS"),
    ),
    # (d2) export-only, some rows failed to export.
    _case(
        "d2-export-partial",
        total=3,
        exported=2,
        failed=1,
        job_type="archive",
        job_status="PARTIAL",
        action="export",
        expect=("Exported 2 of 3 objects to TML (not deleted)", "PARTIAL"),
    ),
    # (d1) export-only where the reconcile loop never ran: `failed == 0` while
    # every row is still PENDING. `deletion_service.py:490-499` is what turns an
    # unaccounted GUID into FAILED, and it is skipped whole when an exception
    # escapes to the blanket handler at `deletion_service.py:715`, so this is
    # the shape of a run that wrote ZERO TML files. The old `failed == 0` test
    # rendered it green: "Exported 200 objects to TML" / SUCCESS.
    _case(
        "d-export-none-exported-none-failed",
        total=200,
        exported=0,
        failed=0,
        deleted=0,
        job_type="archive",
        job_status="FAILED",
        action="export",
        expect=("TML export failed for 200 objects", "FAILED"),
    ),
    # (d2b) same hole one step along: a partial export whose unwritten rows are
    # PENDING rather than FAILED.
    _case(
        "d2-export-partial-none-failed",
        total=200,
        exported=50,
        failed=0,
        job_type="archive",
        job_status="FAILED",
        action="export",
        expect=("Exported 50 of 200 objects to TML (not deleted)", "PARTIAL"),
    ),
    # (d3) export-only, nothing exported at all.
    _case(
        "d3-export-total-failure",
        total=3,
        failed=3,
        job_type="archive",
        job_status="FAILED",
        action="export",
        expect=("TML export failed for 3 objects", "FAILED"),
    ),
    # (e) delete intent, zero confirmed deletes, job over — a real failure,
    # including the case where every TML export SUCCEEDED and Phase B never ran.
    _case(
        "e-delete-nothing-confirmed",
        total=2,
        exported=2,
        job_type="bulk_delete",
        job_status="FAILED",
        expect=("Delete failed — 0 of 2 objects deleted", "FAILED"),
    ),
    _case(
        "e-delete-every-export-failed",
        total=2,
        failed=2,
        job_type="bulk_delete",
        job_status="FAILED",
        expect=("Delete failed — 0 of 2 objects deleted", "FAILED"),
    ),
    _case(
        "e-archive-action-delete",
        total=1,
        exported=1,
        job_type="archive",
        job_status="COMPLETE",
        action="delete",
        expect=("Delete failed — 0 of 1 object deleted", "FAILED"),
    ),
    # (f) no jobs row at all — state neutrally, claim nothing.
    _case(
        "f-no-job-row",
        total=4,
        exported=3,
        failed=1,
        expect=("Archived 4 objects — none deleted, 3 backed up", "PENDING"),
    ),
    _case(
        "f-no-job-row-singular",
        total=1,
        exported=1,
        expect=("Archived 1 object — none deleted, 1 backed up", "PENDING"),
    ),
]


def test_the_case_table_is_not_empty():
    """S27/M4: a parametrize over an empty list reads green while testing nothing."""
    assert len(DELETE_CASES) == 19
    assert {c.id for c in DELETE_CASES} >= {"a-all-deleted", "d-export-only-clean", "e-delete-nothing-confirmed"}


@pytest.mark.parametrize(("kwargs", "expected"), DELETE_CASES)
def test_delete_entry_label_and_status_agree(kwargs, expected):
    assert _delete_entry(**kwargs) == expected


class TestPlural:
    def test_singular_and_plural(self):
        assert _plural(1, "object") == "1 object"
        assert _plural(2, "object") == "2 objects"
        assert _plural(0, "principal") == "0 principals"


class TestSeverityRank:
    def test_failed_outranks_partial_outranks_pending_outranks_success(self):
        assert _severity("SUCCESS") < _severity("PENDING") < _severity("PARTIAL") < _severity("FAILED")

    def test_an_unknown_status_is_never_swallowed_by_success_nor_promoted_to_terminal(self):
        unknown = _severity("SOMETHING_NEW")
        assert unknown > _severity("SUCCESS")
        assert unknown < _severity("PARTIAL")
        assert _severity(None) == unknown
