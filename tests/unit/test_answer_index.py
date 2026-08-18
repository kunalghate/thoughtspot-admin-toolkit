"""
Deep answer-index (Phase 3) regression tests.

`build_answer_index` derives its incremental watermark from
`max(CachedColumnUsage.synced_at)` over the ANSWER usage rows — the very rows
the LAZY per-answer writer `index_answer` also writes. Stamping `synced_at`
there poisoned the watermark: `RelationshipsView` fires `index_answer` on every
first open of an answer, so ONE ordinary click set the watermark to "now", every
answer then read as unchanged, and the opt-in "Build deep column index" issued
ZERO TML exports while reporting COMPLETE with `record_count: 0` — for the life
of the install.

The fix is two-part and BOTH halves are load-bearing (see the section note above
`_usage_rows_from_answer` in `lineage_service`):

  1. lazy rows are written with `synced_at = None`; SQL MAX ignores NULLs, so a
     third-party writer can no longer move the watermark;
  2. an answer with no CERTIFIED (stamped) row counts as changed regardless of
     the watermark — which is what heals a database that already carries a
     pre-fix stamp, and what lets a never-crawled answer in.

Each half has its own falsifiable test below, plus the incremental behaviour
they must NOT destroy (a second pass with nothing changed still skips).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from tests.unit.test_lineage_columns import (
    CLUSTER_ID,
    _make_job,
    _seed,
    in_memory_db,  # noqa: F401 — pytest fixture, bound by argument name
    patched_config,  # noqa: F401 — pytest fixture, bound by argument name
)

# `_seed` already writes answer-1; these are its siblings, so a poisoned
# watermark has a population to hide (a single answer cannot show the bug).
EXTRA_ANSWERS = ("answer-2", "answer-3", "answer-4", "answer-5")
ALL_ANSWERS = ("answer-1", *EXTRA_ANSWERS)


def _answer_edoc(guid: str) -> dict:
    """One answer on `model-1` using one model column → exactly one usage row."""
    return {
        "guid": guid,
        "answer": {
            "name": f"Answer {guid}",
            "tables": [{"name": "Sales Model", "fqn": "model-1"}],
            "search_query": "[Total Revenue]",
            "answer_columns": [{"name": "Total Revenue"}],
            "table": {"table_columns": [{"column_id": "Total Revenue"}]},
        },
    }


class _AnswerTMLClient:
    """TML export stub that knows every answer in `ALL_ANSWERS`."""

    def __init__(self):
        self.exported: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def tml_export(self, *, object_ids, edoc_format=None):
        self.exported.extend(object_ids)
        out = []
        for guid in object_ids:
            if guid in ALL_ANSWERS:
                out.append({"info": {"id": guid, "name": guid}, "edoc": _answer_edoc(guid)})
        return out


def _seed_answers(engine, *, modified_at: datetime | None = None) -> None:
    """Add EXTRA_ANSWERS to the metadata cache `_seed` already populated."""
    from ts_admin.models.cache.ts_metadata import CachedMetadata

    now = modified_at or datetime.now(tz=timezone.utc)
    with Session(engine) as session:
        for guid in EXTRA_ANSWERS:
            session.add(
                CachedMetadata(
                    cluster_id=CLUSTER_ID,
                    org_id=0,
                    ts_guid=guid,
                    name=f"Answer {guid}",
                    object_type="ANSWER",
                    owner_name="Alice",
                    modified_at=now,
                    synced_at=now,
                )
            )
        session.commit()


def _fake_client(monkeypatch) -> _AnswerTMLClient:
    fake = _AnswerTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)
    return fake


def _usage_rows(engine) -> list:
    from ts_admin.models.cache.ts_column_usage import CachedColumnUsage

    with Session(engine) as session:
        return session.exec(select(CachedColumnUsage).where(CachedColumnUsage.consumer_type == "ANSWER")).all()


def _job_result(engine, job_id: str) -> dict:
    from ts_admin.models.job import Job

    with Session(engine) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status == "COMPLETE"
        return job.get_result() or {}


# ── Half 1: a lazy write must not certify anything ───────────────────────────────


async def test_lazy_index_leaves_the_deep_index_certification_stamp_unset(
    monkeypatch,
    in_memory_db,  # noqa: F811
    patched_config,  # noqa: F811
):
    """`synced_at` on an ANSWER usage row means "a deep pass certified this"."""
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    _fake_client(monkeypatch)

    assert await lineage_service.index_answer(cluster_id=CLUSTER_ID, org_id=0, guid="answer-1") == 1

    rows = _usage_rows(in_memory_db)
    assert rows, "fixture vacuous: the lazy index wrote nothing to inspect"
    assert all(r.synced_at is None for r in rows)


async def test_deep_index_after_a_lazy_index_still_crawls_every_other_answer(
    monkeypatch,
    in_memory_db,  # noqa: F811
    patched_config,  # noqa: F811
):
    """
    The headline bug: browsing ONE answer used to kill the deep index outright.

    Kills the mutation that restores `row.synced_at = now` in `index_answer` —
    with it, `last_built` is "now", every answer's `modified_at` is older, the
    changed-set is empty and `record_count` is 0 with zero exports.
    """
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    _seed_answers(in_memory_db)
    fake = _fake_client(monkeypatch)

    # Ordinary browsing: the user opens one answer in the Relationships view.
    assert await lineage_service.index_answer(cluster_id=CLUSTER_ID, org_id=0, guid="answer-1") == 1
    fake.exported.clear()

    job_id = _make_job()
    written = await lineage_service.build_answer_index(cluster_id=CLUSTER_ID, org_id=0, job_id=job_id)

    assert set(fake.exported) >= set(EXTRA_ANSWERS)
    assert written == len(ALL_ANSWERS)  # 1 usage row per answer
    result = _job_result(in_memory_db, job_id)
    assert result["record_count"] == len(ALL_ANSWERS)
    assert result["answers_total"] == len(ALL_ANSWERS)
    assert result["answers_crawled"] == len(ALL_ANSWERS)
    # Everything the pass crawled is now certified.
    rows = _usage_rows(in_memory_db)
    assert {r.consumer_guid for r in rows} == set(ALL_ANSWERS)
    assert all(r.synced_at is not None for r in rows)


# ── Half 2: an uncertified answer is changed, whatever the watermark says ────────


async def test_deep_index_heals_a_watermark_poisoned_before_the_fix(
    monkeypatch,
    in_memory_db,  # noqa: F811
    patched_config,  # noqa: F811
):
    """
    Installs in the field already hold a lazy row WITH a stamp — half 1 cannot
    unwrite it, so the tier would stay dead on exactly the databases that hit
    the bug. The "no certified row ⇒ changed" disjunct is what recovers them.

    Kills the mutation that drops `guid not in certified` from `_changed`.
    """
    from ts_admin.models.cache.ts_column_usage import CachedColumnUsage
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    _seed_answers(in_memory_db)

    # Exactly what the pre-fix `index_answer` left behind: one answer's rows,
    # stamped LATER than every answer's modified_at.
    poison = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    with Session(in_memory_db) as session:
        session.add(
            CachedColumnUsage(
                cluster_id=CLUSTER_ID,
                org_id=0,
                model_guid="model-1",
                model_column_name="Total Revenue",
                consumer_guid="answer-1",
                consumer_type="ANSWER",
                consumer_name="Rev answer",
                synced_at=poison,
            )
        )
        session.commit()

    fake = _fake_client(monkeypatch)
    job_id = _make_job()
    written = await lineage_service.build_answer_index(cluster_id=CLUSTER_ID, org_id=0, job_id=job_id)

    assert set(fake.exported) == set(EXTRA_ANSWERS)
    assert written == len(EXTRA_ANSWERS)
    result = _job_result(in_memory_db, job_id)
    assert result["record_count"] == len(EXTRA_ANSWERS)
    assert result["answers_crawled"] == len(EXTRA_ANSWERS)
    # The poisoned answer is certified and unmodified, so it is legitimately
    # skipped — its existing row survives rather than being deleted and lost.
    assert result["answers_skipped_unchanged"] == 1
    assert {r.consumer_guid for r in _usage_rows(in_memory_db)} == set(ALL_ANSWERS)


# ── The incrementality the fix must NOT destroy ─────────────────────────────────


async def test_second_deep_index_with_nothing_changed_skips_and_says_why(
    monkeypatch,
    in_memory_db,  # noqa: F811
    patched_config,  # noqa: F811
):
    """
    A full pass certifies every answer, so the next pass must export nothing —
    otherwise the "deep index" re-crawls the largest TML set on every run.

    Also pins the reporting fix: a run that exported nothing because everything
    was current must be distinguishable in the job result from one that had no
    answers at all. `record_count: 0` alone made the poisoned watermark look
    healthy.
    """
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    _seed_answers(in_memory_db)
    fake = _fake_client(monkeypatch)

    assert await lineage_service.build_answer_index(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job()) > 0
    fake.exported.clear()

    job_id = _make_job()
    assert await lineage_service.build_answer_index(cluster_id=CLUSTER_ID, org_id=0, job_id=job_id) == 0
    assert fake.exported == []

    result = _job_result(in_memory_db, job_id)
    assert result["record_count"] == 0
    assert result["reason"] == "all_answers_up_to_date"
    assert result["answers_total"] == len(ALL_ANSWERS)
    assert result["answers_crawled"] == 0


async def test_deep_index_with_no_cached_answers_reports_a_different_reason(
    monkeypatch,
    in_memory_db,  # noqa: F811
    patched_config,  # noqa: F811
):
    """ "Nothing to crawl" and "everything current" are different states."""
    from ts_admin.models.cache.ts_metadata import CachedMetadata
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    with Session(in_memory_db) as session:
        for row in session.exec(select(CachedMetadata).where(CachedMetadata.object_type == "ANSWER")).all():
            session.delete(row)
        session.commit()
    fake = _fake_client(monkeypatch)

    job_id = _make_job()
    assert await lineage_service.build_answer_index(cluster_id=CLUSTER_ID, org_id=0, job_id=job_id) == 0
    assert fake.exported == []

    result = _job_result(in_memory_db, job_id)
    assert result["reason"] == "no_answers_cached"
    assert result["answers_total"] == 0


async def test_a_modified_answer_is_recrawled_after_a_full_pass(
    monkeypatch,
    in_memory_db,  # noqa: F811
    patched_config,  # noqa: F811
):
    """
    The timestamp half of `_changed` must still work once everything is
    certified — kills a mutation that returns `guid not in certified` alone.
    """
    from ts_admin.models.cache.ts_metadata import CachedMetadata
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    _seed_answers(in_memory_db)
    fake = _fake_client(monkeypatch)

    await lineage_service.build_answer_index(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    fake.exported.clear()

    # Edited in ThoughtSpot after the pass (a FUTURE stamp — the build's own
    # `synced_at` is later than the seeded `modified_at`).
    with Session(in_memory_db) as session:
        row = session.exec(
            select(CachedMetadata).where(
                CachedMetadata.cluster_id == CLUSTER_ID,
                CachedMetadata.ts_guid == "answer-3",
            )
        ).one()
        row.modified_at = datetime.now(tz=timezone.utc) + timedelta(hours=1)
        session.add(row)
        session.commit()

    written = await lineage_service.build_answer_index(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    assert fake.exported == ["answer-3"]
    assert written == 1
