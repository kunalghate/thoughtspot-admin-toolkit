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
