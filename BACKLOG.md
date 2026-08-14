# BACKLOG

The org's work queue. Read it at the start of every cycle; change it only at a
cycle's **Records** step.

**Bright line — what a cycle may do to this file:** change a row's **Status**,
append **notes**, and append **new rows**. A cycle may NEVER edit an item's
**Priority** or **Acceptance criteria**, and NEVER delete a row. Priority and
criteria are the human's lever — they are set and re-scoped by a person, not by an
agent. (See [CLAUDE.md](CLAUDE.md) → "How the org works".)

## ID taxonomy (mirrors the three standing goals)

| Prefix | Meaning |
|---|---|
| **S** | Stability / features — improve the app |
| **R** | Refactors — improve the app without changing behavior |
| **W** | Keep-current — ThoughtSpot REST v2 drift, dependency drift |
| **M** | Org / process — improve the agents, skills, and gates themselves |

**Priority:** P1 (urgent) → P4 (nice-to-have). **Status:** `open` · `in-progress`
· `in-review` · `done`. **Protected:** does the item likely touch a
[protected path](CLAUDE.md)? (If yes, its PR needs the `human-approved` label.)

## Rows

| ID | P | Item | Acceptance criteria | Status | Protected |
|----|---|------|---------------------|--------|-----------|
| S1 | P3 | Wire frontend `vitest` into the suite | `cd frontend && npm test` runs at least one real component test and passes; CI `frontend` job runs it; [docs/dev/TESTING.md](docs/dev/TESTING.md) updated to drop "not yet wired" | in-review | no |
| S2 | P3 | Add a `/health` smoke check to CI | CI (via TestClient or a booted app) asserts `GET /health` returns 200; check runs in the pipeline and is documented in TESTING.md | open | yes (`.github/workflows/*`) |
| W1 | P3 | Add `mypy ts_admin/` to CI | `mypy ts_admin/` runs in the CI `backend` job and is green (baseline existing errors if needed, with a tracking note) | open | yes (`.github/workflows/*`) |
| M1 | P4 | Single-source the protected-path list | The protected-path patterns live in ONE place; the `guard` job and CLAUDE.md reference/derive from it (or a check fails on drift), so the two can't silently diverge | open | yes (`.github/workflows/*`, `CLAUDE.md`) |
| S3 | P2 | Group Management (read-only v1): group grid + detail drawer, user audit drawer, and the ThoughtSpot `fetch-permissions` API-drift fix | `GET /api/v1/groups` and `/groups/{guid}` serve the Groups page from cache (cluster + org scoped); `/users/{guid}/access` returns live EFFECTIVE permissions; `principal_permissions` hits `security/principals/fetch-permissions` with the correct body key and parses the real nested response; full verification bar green | in-review | no |
| S4 | P2 | Group detail drawer disagreed with the grid: `get_group_detail` was not org-scoped | `get_group_detail` scopes the group row, member count, and member list to a single org; a regression test seeds one GUID in two orgs and asserts the drawer count equals the grid count for each | in-review | no |
| S5 | P3 | Nested group membership is missing: `_sync_groups` parses `sub_groups` but never persists them | Users who belong to a group only via a sub-group appear in that group's `member_count` and member list (or the UI states explicitly that counts are direct members only); a unit test covers a group whose sole member arrives through a sub-group | open | no |
| M2 | P2 | The PR #14 merge silently reverted PR #12's vitest wiring, leaving `npm test` red on `main` with no gate to catch it | `frontend/vitest.config.mts`, `vitest.setup.ts`, the Legend test, and the testing-library/jsdom devDeps are restored and `npm test` is green; a CI gate (or the S1 CI wiring) runs `npm test` so a future merge cannot silently delete the suite again | in-review | yes (`.github/workflows/*` for the CI half) |
| S6 | P2 | Lineage edges whose **target** is deleted outlive it — ghost nodes in the graph | A model deleted in TS while its consuming liveboard is unchanged leaves no `CachedDependency`/`CachedColumnUsage` row behind; a unit test deletes a target from `CachedMetadata`, rebuilds, and asserts the edge is gone. (Phase 1 owns `USES`/non-LIVEBOARD, `_persist_column_map` owns `CONNECTS` + LIVEBOARD `USES`; neither purges by target GUID, and `build_column_map`'s `if not table_guids and not lb_guids: return 0` early-return skips the purge entirely) | open | no |
| S7 | P2 | `has_lb_edges` self-heal makes `incremental=True` a permanent full TML crawl on any org that legitimately yields zero liveboard edges | The self-heal fires at most once per org (keyed off a persisted "liveboard tier last built" marker, not "are there any rows"), so an org whose liveboards are all TML-inaccessible (403 stubs) does not re-export every liveboard on every dependencies sync forever; a unit test covers two consecutive incremental builds that produce zero liveboard edges and asserts the second exports nothing | open | no |
| S8 | P3 | `principal_permissions` / `fetch_permissions` have no wire-shape coverage and fail silently to `[]`, which `execute_transfer_sharing` records as SUCCESS | A test feeds each parser a recorded v2 response payload and asserts the extracted rows; `execute_transfer_sharing` does not write a SUCCESS audit-log row when the fetch returns zero rows for a user whose preview reported N | open | no |
| S9 | P3 | `_is_admin` joins on `cluster_id` only, so membership synced for one org blocks transfers executed in another | `_is_admin` (and the `admin_count` snapshot) scope membership to the org the operation runs in; a unit test seeds an admin membership in org 0 and asserts a transfer in org 5 is not blocked by it | open | no |
| M3 | P3 | New cluster-scoped **detail** read endpoints can't be registered in `READ_ENDPOINTS` (its extractor assumes `body["items"]`), so `/groups/{guid}` and `/users/{guid}/access` are unguarded | `READ_ENDPOINTS` (or a sibling registry) accepts detail-shaped responses and the two endpoints above are registered, OR CLAUDE.md documents the live-passthrough exemption explicitly so the rule isn't stated unconditionally while having a silent carve-out | open | yes (`tests/integration/test_cluster_isolation.py`) |
| W2 | P2 | `ruff format` drift breaks the format gate: unbounded `ruff>=0.4.0` pin lets a newer local/CI ruff reformat 5 files inherited from PR #10 | `ruff format --check ts_admin/ tests/` is green both locally and in CI. Resolve by (a) `ruff format`-ing the 5 drifted files (`ts_admin/services/lineage_service.py`, `tests/unit/test_lineage_columns.py`, `tests/unit/test_lineage_models.py`, `tests/unit/test_lineage_service.py`, `tests/integration/test_relationships_api.py`) AND (b) bounding the ruff version in `pyproject.toml` `[dev]` (e.g. a `~=` pin) so formatter output is reproducible across runs. Confirm whether CI's resolved ruff actually reddens `main` before/after. | in-review | no |

> Seeded 2026-07-15 at bootstrap (BOOT) from Step 0 discovery. Run
> `/improve-cycle discover` to add more rows from a bug-hunt sweep.

## Cycle notes

- 2026-07-15 (S1): Delivered the non-protected core of S1 in PR (branch
  `improve/S1-wire-vitest`, commit `65d612d`): `frontend/vitest.config.mts` +
  `vitest.setup.ts` + a real `Legend` render test (iterates `NODE_STYLE_ORDER`,
  asserts every node-type label) + jsdom/testing-library devDeps + regenerated
  lockfile + `docs/dev/TESTING.md`. `cd frontend && npm test` passes (2/2).
  **Criterion "CI `frontend` job runs it" is intentionally NOT in this PR** — it
  requires editing `.github/workflows/ci.yml` (protected). The exact 3-line YAML
  step is provided in the PR body for a human to add under the `human-approved`
  label. Left `in-review`; the CI-wiring remainder is the only unmet sub-criterion.
- 2026-07-15 (S1): While running the QA bar, discovered the pre-existing
  `ruff format --check` failure on 5 PR-#10 files (unbounded ruff pin) → filed **W2**
  above. Not fixed in the S1 PR (finding and fixing kept in separate diffs).
- 2026-07-15 (W2): Fixed in branch `improve/W2-ruff-format-drift` (commit `4f4da28`):
  `ruff format`-ed the 5 files + bounded the pin to `ruff>=0.15.0,<0.16.0`. Bar
  green (`ruff format --check` = 82 files already formatted; pytest 134+100). Opened
  as **PR #11** (base `main`). At the human's direction, W2 was fixed FIRST so S1's
  CI is clean: **S1 (PR #12) is stacked on the W2 branch** — after #11 merges,
  retarget #12's base to `main`. Both PRs touch no protected paths.
- 2026-08-14 (S3/S4/M2): Review-first cycle on the uncommitted Group Management
  tree (branch `improve/S3-group-management`). Committed the feature work as-is,
  then fixed what two parallel `reviewer` lenses independently CONFIRMED:
  the empty-`not_in` group purge (data loss on any empty `groups/search` page),
  `get_group_detail` not being org-scoped (S4 — drawer disagreed with the grid),
  the stale `sync_log` row surviving a rebuildable-cache drop, the support bundle
  losing its traceback across a log rotation, and the `UserDetailDrawer` stale-
  response race. Each fix carries a regression test; the S4 tests were verified to
  fail without the fix. **M2 was found outside the diff:** PR #14 silently reverted
  merged PR #12's vitest wiring (config, setup, Legend test, testing-library +
  jsdom devDeps) and left `npm test` RED on `main` — restored here, but the CI half
  (a job that runs `npm test`) touches `.github/workflows/` and needs a human.
- 2026-08-14: Filed S6–S9 and M3 from the same review rather than fixing them —
  each needs a design decision (a target-keyed purge, a persisted build marker,
  recorded API fixtures, an org-scoping semantics call, a guard-registry change)
  rather than a mechanical patch, and one reviewer-suggested guard was already
  proven wrong by the existing suite mid-cycle.
