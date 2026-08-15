"""
Mutation-driven pinning tests for the incremental lineage builders.

`tests/unit/test_lineage_columns.py` was measured mutation-vacuous (S7): six of
seven mutations to `build_column_map` left the whole file green. This file adds
the four clusters of behaviour that nothing else names:

1. cross-scope isolation  — a second cluster/org must not be read, written, or
   deleted by a build for the first one;
2. idempotence            — a second build with nothing changed must produce a
   byte-for-byte equivalent row population (no growth, no loss);
3. cross-kind survival    — a column build must not eat ANSWER usage rows or the
   object-tier USES edges (the existing coverage runs the two builders in the
   OPPOSITE order, which is why these mutations survived);
4. the self-heal / early-return trio — the NULL watermark in isolation, a NULL
   `modified_at`, and the empty-universe early return.

Every test here pins CURRENT behaviour. See `docs/dev/TESTING.md` §"Mutation
testing the lineage builder" for the harness recipe and the kill table: each
test below names the mutation(s) it was demonstrated to kill.

Fixtures/helpers are reused from `test_lineage_columns` deliberately — the canned
TML there is the shared fixture surface for the whole lineage suite.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from tests.unit.test_lineage_columns import (
    CLUSTER_ID,
    _FakeTMLClient,
    _make_job,
    _seed,
    in_memory_db,  # noqa: F401 — pytest fixture, bound by argument name
    patched_config,  # noqa: F401 — pytest fixture, bound by argument name
)

OTHER_CLUSTER_ID = "c2"
OTHER_ORG_ID = 1
# The two neighbouring scopes a build for (c1, org 0) must not touch. TWO shadows
# are required, not one: a single (c2, org 1) shadow is excluded by EITHER
# predicate on its own, so dropping just `cluster_id` — or just `org_id` — still
# leaves it invisible and the mutation survives (measured, 2026-08-15).
SHADOW_SCOPES = (
    (CLUSTER_ID, OTHER_ORG_ID),  # same cluster, sibling org  → kills an org_id drop
    (OTHER_CLUSTER_ID, 0),  # sibling cluster, same org  → kills a cluster_id drop
)
OLD = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _seed_shadow(engine, *, cluster_id: str, org_id: int, synced_at: datetime) -> None:
    """
    Seed a full shadow of everything `_seed` writes for c1/org-0, in a neighbouring scope.

    Same GUIDs on purpose: a build that loses its cluster/org scoping then reads
    each object twice (detectable as a duplicated export) rather than merely
    reading an unrelated object. `synced_at` is deliberately LATER than the c1
    build's, so an unscoped `max(synced_at)` watermark reads as "the future" and
    silently stops re-crawling c1's changed liveboards.

    PLUS one liveboard whose GUID exists ONLY in this scope. The shared GUIDs
    cannot detect an unscoped `not_in(all_lb_guids)` purge — they are in
    `all_lb_guids` and so protected by the very predicate under test (measured,
    2026-08-15). The scope-unique liveboard is not, so it is the row that dies.
    """
    from ts_admin.models.cache.ts_column_lineage import CachedColumnLineage
    from ts_admin.models.cache.ts_column_usage import CachedColumnUsage
    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.models.cache.ts_metadata import CachedMetadata
    from ts_admin.models.cluster import Cluster

    scope = {"cluster_id": cluster_id, "org_id": org_id}
    local_lb = f"lb-only-{cluster_id}-{org_id}"
    with Session(engine) as session:
        if cluster_id != CLUSTER_ID:
            session.add(Cluster(id=cluster_id, name="Other", url="https://o", username="a", auth_type="trusted"))
        for guid, name, otype in (
            ("table-1", "SALES", "ONE_TO_ONE_LOGICAL"),
            ("model-1", "Sales Model", "WORKSHEET"),
            ("lb-1", "Sales LB", "LIVEBOARD"),
            ("answer-1", "Rev answer", "ANSWER"),
            (local_lb, "Scope-local LB", "LIVEBOARD"),
        ):
            session.add(
                CachedMetadata(
                    **scope,
                    ts_guid=guid,
                    name=name,
                    object_type=otype,
                    owner_name="Bob",
                    modified_at=OLD,
                    synced_at=synced_at,
                )
            )
        session.add(
            CachedColumnLineage(
                **scope,
                model_guid="model-1",
                model_column_name="Total Revenue",
                table_guid="table-1",
                table_column_name="Revenue",
                db_table="FACT_SALES",
                db_column_name="REVENUE",
                connection_name="Snowflake Prod",
                synced_at=synced_at,
            )
        )
        for edge in (
            ("lb-1", "LIVEBOARD", "model-1", "MODEL", "USES"),
            (local_lb, "LIVEBOARD", "model-1", "MODEL", "USES"),
            ("model-1", "MODEL", "table-1", "DB_TABLE", "USES"),
            ("table-1", "DB_TABLE", "conn-1", "CONNECTION", "CONNECTS"),
        ):
            source_guid, source_type, target_guid, target_type, relation = edge
            session.add(
                CachedDependency(
                    **scope,
                    source_guid=source_guid,
                    source_type=source_type,
                    target_guid=target_guid,
                    target_type=target_type,
                    relation=relation,
                    synced_at=synced_at,
                )
            )
        for consumer_guid, consumer_type in (
            ("lb-1", "LIVEBOARD"),
            (local_lb, "LIVEBOARD"),
            ("answer-1", "ANSWER"),
        ):
            session.add(
                CachedColumnUsage(
                    **scope,
                    model_guid="model-1",
                    model_column_name="Total Revenue",
                    consumer_guid=consumer_guid,
                    consumer_type=consumer_type,
                    synced_at=synced_at,
                )
            )
        session.commit()


def _shadow_snapshot(engine, *, cluster_id: str, org_id: int) -> dict[str, list[tuple]]:
    """Every row in one shadow scope, as comparable tuples (identity + the synced_at watermark)."""
    from ts_admin.models.cache.ts_column_lineage import CachedColumnLineage
    from ts_admin.models.cache.ts_column_usage import CachedColumnUsage
    from ts_admin.models.cache.ts_dependency import CachedDependency

    with Session(engine) as session:
        lineage = session.exec(
            select(CachedColumnLineage).where(
                CachedColumnLineage.cluster_id == cluster_id,
                CachedColumnLineage.org_id == org_id,
            )
        ).all()
        edges = session.exec(
            select(CachedDependency).where(
                CachedDependency.cluster_id == cluster_id,
                CachedDependency.org_id == org_id,
            )
        ).all()
        usage = session.exec(
            select(CachedColumnUsage).where(
                CachedColumnUsage.cluster_id == cluster_id,
                CachedColumnUsage.org_id == org_id,
            )
        ).all()
    return {
        "lineage": sorted((r.model_guid, r.model_column_name, r.synced_at) for r in lineage),
        "edges": sorted((e.source_guid, e.target_guid, e.relation, e.synced_at) for e in edges),
        "usage": sorted((u.consumer_guid, u.consumer_type, u.model_column_name, u.synced_at) for u in usage),
    }


def _counts(engine, cluster_id: str = CLUSTER_ID, org_id: int = 0) -> dict[str, int]:
    from ts_admin.models.cache.ts_column_lineage import CachedColumnLineage
    from ts_admin.models.cache.ts_column_usage import CachedColumnUsage
    from ts_admin.models.cache.ts_dependency import CachedDependency

    with Session(engine) as session:
        lineage = session.exec(
            select(CachedColumnLineage).where(
                CachedColumnLineage.cluster_id == cluster_id,
                CachedColumnLineage.org_id == org_id,
            )
        ).all()
        edges = session.exec(
            select(CachedDependency).where(
                CachedDependency.cluster_id == cluster_id,
                CachedDependency.org_id == org_id,
            )
        ).all()
        usage = session.exec(
            select(CachedColumnUsage).where(
                CachedColumnUsage.cluster_id == cluster_id,
                CachedColumnUsage.org_id == org_id,
            )
        ).all()
    return {
        "lineage": len(lineage),
        "connects": len([e for e in edges if e.relation == "CONNECTS"]),
        "lb_uses": len([e for e in edges if e.relation == "USES" and e.source_type == "LIVEBOARD"]),
        "usage": len(usage),
    }


# ── Cluster 1: cross-scope isolation ─────────────────────────────────────────────


async def test_build_column_map_is_scoped_to_one_cluster_and_org(monkeypatch, in_memory_db, patched_config):  # noqa: F811
    """
    A build for c1/org-0 must neither read, export, nor delete a row belonging to
    either neighbouring scope (see `SHADOW_SCOPES` — one sibling org, one sibling
    cluster; a single diagonal neighbour kills neither predicate on its own).

    Kills every mutation that drops a `cluster_id`/`org_id` predicate from the
    metadata read, the `last_built` watermark, or any of `_persist_column_map`'s
    four delete statements. Three independent observations, because the delete
    scoping and the read scoping fail differently:

    * each c1 object is exported exactly ONCE (an unscoped metadata read sees a
      shadow's identical GUIDs and crawls the object twice);
    * c1's *changed* liveboard is still re-exported even though the shadows carry
      a LATER `synced_at` (an unscoped `max(synced_at)` watermark reads as the
      future and skips everything);
    * every shadow row is identical before and after.

    TZ NOTE: `last_built` comes back NAIVE from SQLite while the seeds are
    tz-aware, so the future timestamps below are written aware (SQLite stores
    them, reads them back naive, and the builder compares naive-to-naive).
    """
    from ts_admin.models.cache.ts_metadata import CachedMetadata
    from ts_admin.services import lineage_service

    _seed(in_memory_db, lb_modified=OLD)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)

    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    c1_after_first = _counts(in_memory_db)

    now = datetime.now(tz=timezone.utc)
    for shadow_cluster, shadow_org in SHADOW_SCOPES:
        _seed_shadow(in_memory_db, cluster_id=shadow_cluster, org_id=shadow_org, synced_at=now + timedelta(days=400))
    before = {s: _shadow_snapshot(in_memory_db, cluster_id=s[0], org_id=s[1]) for s in SHADOW_SCOPES}
    # Anti-vacuity: with an empty `_seed_shadow` every assertion below holds
    # trivially (before == after == all-empty), so the fixture must be proven
    # non-empty BEFORE the build runs. See docs/dev/TESTING.md.
    assert all(any(before[s].values()) for s in SHADOW_SCOPES), (
        "shadow fixture wrote nothing — this test guards nothing"
    )

    # c1's liveboard genuinely changed — but strictly BEFORE the shadow's watermark.
    with Session(in_memory_db) as session:
        row = session.exec(
            select(CachedMetadata).where(
                CachedMetadata.cluster_id == CLUSTER_ID,
                CachedMetadata.org_id == 0,
                CachedMetadata.ts_guid == "lb-1",
            )
        ).one()
        row.modified_at = now + timedelta(days=365)
        session.add(row)
        session.commit()

    fake.exported.clear()
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    assert fake.exported.count("lb-1") == 1
    assert fake.exported.count("table-1") == 1
    assert fake.exported.count("model-1") == 1
    after = {s: _shadow_snapshot(in_memory_db, cluster_id=s[0], org_id=s[1]) for s in SHADOW_SCOPES}
    assert after == before
    assert _counts(in_memory_db) == c1_after_first


async def test_self_heal_probe_ignores_other_scopes(monkeypatch, in_memory_db, patched_config):  # noqa: F811
    """
    The `has_lb_edges` self-heal probe is scope-local: a neighbouring scope's
    liveboard edges must not satisfy c1/org-0's probe.

    Kills a mutation dropping `cluster_id` or `org_id` from the `has_lb_edges`
    query. The lineage rows are deliberately LEFT IN PLACE so `last_built` is
    non-NULL and the probe is the only thing that can force the re-export.
    """
    from sqlmodel import delete as sql_delete

    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.services import lineage_service

    _seed(in_memory_db, lb_modified=OLD)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)

    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    now = datetime.now(tz=timezone.utc)
    for shadow_cluster, shadow_org in SHADOW_SCOPES:
        _seed_shadow(in_memory_db, cluster_id=shadow_cluster, org_id=shadow_org, synced_at=now)
    before = {s: _shadow_snapshot(in_memory_db, cluster_id=s[0], org_id=s[1]) for s in SHADOW_SCOPES}
    # Anti-vacuity: an empty `_seed_shadow` makes this test assert only that a
    # scope with no edges at all fails to mask c1's loss — which is trivially
    # true. The shadows must really hold liveboard edges for the probe to be
    # under test at all.
    assert all(any(before[s].values()) for s in SHADOW_SCOPES), (
        "shadow fixture wrote nothing — this test guards nothing"
    )
    assert all(any(e[:3] == ("lb-1", "model-1", "USES") for e in before[s]["edges"]) for s in SHADOW_SCOPES), (
        "shadow fixture seeded no liveboard USES edges — the probe is not under test"
    )

    # Wipe c1/org-0's liveboard edges ONLY — every shadow keeps its own.
    with Session(in_memory_db) as session:
        session.exec(
            sql_delete(CachedDependency).where(
                CachedDependency.cluster_id == CLUSTER_ID,
                CachedDependency.org_id == 0,
                CachedDependency.source_type == "LIVEBOARD",
            )
        )
        session.commit()

    fake.exported.clear()
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    assert "lb-1" in fake.exported  # a shadow's surviving edges must not mask c1's loss


# ── Cluster 2: idempotence ───────────────────────────────────────────────────────


async def test_second_build_with_nothing_changed_is_idempotent(monkeypatch, in_memory_db, patched_config):  # noqa: F811
    """
    Two builds with nothing changed must leave EXACTLY the same row population.

    This is the load-bearing test for the delete phase: every delete is paired
    with an insert that must restore what it removed, and every insert is paired
    with a delete that must prevent a duplicate. Both directions are asserted as
    exact counts, plus the specific row a "purge every unchanged liveboard's
    edges on every build" defect would drop.
    """
    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.services import lineage_service

    _seed(in_memory_db, lb_modified=OLD)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)

    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    first = _counts(in_memory_db)
    assert first["lineage"] and first["connects"] and first["lb_uses"] and first["usage"]

    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    assert _counts(in_memory_db) == first

    with Session(in_memory_db) as session:
        lb_uses = session.exec(
            select(CachedDependency).where(
                CachedDependency.cluster_id == CLUSTER_ID,
                CachedDependency.org_id == 0,
                CachedDependency.relation == "USES",
                CachedDependency.source_type == "LIVEBOARD",
            )
        ).all()
    assert [(e.source_guid, e.target_guid) for e in lb_uses] == [("lb-1", "model-1")]


# ── Cluster 3: cross-kind survival (column build runs LAST) ──────────────────────


async def test_column_build_preserves_answer_usage_and_object_edges(monkeypatch, in_memory_db, patched_config):  # noqa: F811
    """
    Run the three builders in the order the app actually hits on a cold cache
    with an answer already opened: index_answer → build_object_graph →
    build_column_map. `test_object_graph_rebuild_preserves_phase2_edges` runs the
    last two in the OPPOSITE order, so nothing pinned the column build's deletes
    against the rows the other two phases own.

    Kills mutations that widen `_persist_column_map`'s liveboard purges — e.g.
    dropping `consumer_type == "LIVEBOARD"` from the usage purge (eats the ANSWER
    rows) or `source_type == "LIVEBOARD"` from the edge purge (eats the
    object-tier MODEL→DB_TABLE edge, whose source is never in `all_lb_guids`).
    """
    from ts_admin.models.cache.ts_column_usage import CachedColumnUsage
    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)

    assert await lineage_service.index_answer(cluster_id=CLUSTER_ID, org_id=0, guid="answer-1") == 1
    await lineage_service.build_object_graph(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    with Session(in_memory_db) as session:
        answer_usage = session.exec(select(CachedColumnUsage).where(CachedColumnUsage.consumer_type == "ANSWER")).all()
        edges = session.exec(select(CachedDependency)).all()

    assert [(u.consumer_guid, u.model_guid) for u in answer_usage] == [("answer-1", "model-1")]
    object_tier = [
        e for e in edges if e.relation == "USES" and e.source_guid == "model-1" and e.target_guid == "table-1"
    ]
    assert object_tier and object_tier[0].source_type == "MODEL"
    # ...and Phase 2's own edges are still there alongside them.
    assert any(e.relation == "USES" and e.source_type == "LIVEBOARD" for e in edges)
    assert any(e.relation == "CONNECTS" for e in edges)


# ── Cluster 4: the self-heal / early-return trio ─────────────────────────────────


async def test_null_watermark_alone_forces_a_full_liveboard_recrawl(monkeypatch, in_memory_db, patched_config):  # noqa: F811
    """
    Mechanism 1 of the liveboard self-heal, IN ISOLATION.

    `test_total_liveboard_edge_loss_recovers_on_next_build` deletes the lineage
    rows AND the liveboard edges, so the NULL watermark and the `has_lb_edges`
    probe mask each other and the test only goes red when both are removed. Here
    the liveboard edges are deliberately KEPT (`has_lb_edges` stays True), so the
    only thing that can re-export the unchanged `lb-1` is `last_built is None`.

    Kills: `last_built is None` → `False` at the `_changed` disjunction in
    `build_column_map` (NOT the byte-identical line in `build_answer_index` —
    the two are the same bytes at two line numbers, so mutate by LINE).
    Measured 2026-08-15: under that mutation the whole rest of the lineage suite
    (36 tests, including `test_total_liveboard_edge_loss_recovers_on_next_build`)
    stays GREEN and only this test goes red — which is the evidence that the two
    self-heal mechanisms really do mask each other everywhere else.

    Like the NULL-`modified_at` test below, the kill is VIA CRASH: with the None
    check gone the next disjunct evaluates `datetime > None` and raises
    `TypeError`. Expect a red `TypeError`, not an assertion failure — pytest
    reports it as FAILED (it is raised in the call phase); `ERROR` would mean the
    fixture itself broke, i.e. the harness misfired.
    """
    from sqlmodel import delete as sql_delete

    from ts_admin.models.cache.ts_column_lineage import CachedColumnLineage
    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.services import lineage_service

    _seed(in_memory_db, lb_modified=OLD)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)

    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    with Session(in_memory_db) as session:
        session.exec(
            sql_delete(CachedColumnLineage).where(
                CachedColumnLineage.cluster_id == CLUSTER_ID,
                CachedColumnLineage.org_id == 0,
            )
        )
        session.commit()
        # Precondition: the probe cannot be what heals this — the edges survive.
        surviving = session.exec(select(CachedDependency).where(CachedDependency.source_type == "LIVEBOARD")).all()
    assert surviving

    fake.exported.clear()
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    assert "lb-1" in fake.exported


async def test_liveboard_with_null_modified_at_is_always_recrawled(monkeypatch, in_memory_db, patched_config):  # noqa: F811
    """
    A liveboard whose cached `modified_at` is NULL is treated as changed on every
    build (current behaviour — pinned, not endorsed: it means such a liveboard is
    re-exported forever).

    Kills: removing the `modified_at is None` disjunct from `_changed`. Note the
    kill is VIA CRASH, not via a wrong skip — without that guard the next
    disjunct evaluates `None > datetime(...)` and raises
    `TypeError: '>' not supported between instances of 'NoneType' and 'datetime.datetime'`.
    A future reader should expect a red `TypeError`, not an assertion failure —
    pytest reports it as FAILED (it is raised in the call phase); `ERROR` would
    mean the fixture itself broke, i.e. the harness misfired.
    """
    from ts_admin.models.cache.ts_metadata import CachedMetadata
    from ts_admin.services import lineage_service

    _seed(in_memory_db, lb_modified=OLD)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)

    with Session(in_memory_db) as session:
        row = session.exec(select(CachedMetadata).where(CachedMetadata.ts_guid == "lb-1")).one()
        row.modified_at = None
        session.add(row)
        session.commit()

    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    assert "lb-1" in fake.exported

    fake.exported.clear()
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    assert "lb-1" in fake.exported  # NULL modified_at never reads as "unchanged"


async def test_empty_metadata_universe_returns_early_and_touches_nothing(monkeypatch, in_memory_db, patched_config):  # noqa: F811
    """
    With a `Cluster` row but ZERO `CachedMetadata`, `build_column_map` must return
    0 without contacting ThoughtSpot and without deleting anything.

    This early return is the sole precondition that makes `_persist_column_map`'s
    `not_in(all_lb_guids)` purge safe (org-memory 2026-08-14/2026-08-15): reaching
    the purge with an empty `all_lb_guids` means "no liveboards exist", which is
    only true because an unsynced metadata cache cannot get past here. Deleting
    the early return therefore deletes every cached liveboard edge on any build
    run against an unsynced cache.

    Kills: `if not table_guids and not lb_guids: return 0` → `if False:` (the
    client factory below raises, and the pre-seeded edges are gone).
    """
    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.models.cluster import Cluster
    from ts_admin.services import lineage_service

    now = datetime.now(tz=timezone.utc)
    with Session(in_memory_db) as session:
        session.add(Cluster(id=CLUSTER_ID, name="Prod", url="https://p", username="a", auth_type="trusted"))
        session.add(
            CachedDependency(
                cluster_id=CLUSTER_ID,
                org_id=0,
                source_guid="lb-1",
                source_type="LIVEBOARD",
                source_name="Sales LB",
                target_guid="model-1",
                target_type="MODEL",
                target_name="Sales Model",
                relation="USES",
                synced_at=now,
            )
        )
        session.commit()

    def _explode(*args, **kwargs):
        raise AssertionError("build_column_map must not contact ThoughtSpot with an empty metadata universe")

    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", _explode)

    written = await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    assert written == 0

    with Session(in_memory_db) as session:
        edges = session.exec(select(CachedDependency)).all()
    assert [(e.source_guid, e.target_guid) for e in edges] == [("lb-1", "model-1")]
