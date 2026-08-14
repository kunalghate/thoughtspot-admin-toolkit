# Retros

One line per cycle on process friction. Format:

`- YYYY-MM-DD <ID>: <friction observed> → <action or M-row filed>`

- 2026-07-15 BOOT: Org scaffold stood up. Discovery found the existing `CLAUDE.md`
  already covered Stack/Architecture/Critical-rules, so the constitution was
  appended (not rewritten). Protected-path list is duplicated between `ci.yml` and
  `CLAUDE.md` → filed M1 to single-source it.
- 2026-07-15 S1: Friction — S1 was flagged `Protected: no`, but its own criterion
  "CI `frontend` job runs it" requires editing `.github/workflows/ci.yml` (a
  protected path). The row conflates a non-protected core (config+test+docs) with a
  protected CI-wiring step. Delivered the core in a guard-green PR and handed the
  ci.yml one-liner to the human. Suggest a human split S1 into core vs CI-wiring, or
  re-flag it Protected. → No M-row filed (one-row scoping issue, human's lever).
- 2026-07-15 S1: The org bootstrap (BACKLOG/org-memory/.claude + protected
  ci.yml/CLAUDE.md edits) is uncommitted, so the working tree intermingled cycle
  work with scaffold. Committed S1's 6 product files as an atomic commit for a clean
  review target; kept records in the working tree to land with the human's
  foundational commit. `/code-review` skipped in favour of scoped reviewer agents
  (dirty tree would have made the diff-based skill noisy). Non-recurring (bootstrap).
- 2026-07-15 W2: The uncommitted bootstrap (modified tracked ci.yml/CLAUDE.md)
  blocked `git rebase` ("unstaged changes"). Resolved with `git stash -u` → rebase
  S1 onto W2 → push → open PRs → `git stash pop` on `main` to restore records.
  Human chose W2-first, so S1 (PR #12) is STACKED on the W2 branch (PR #11) to keep
  S1's CI green pre-merge; base retargets to `main` when #11 merges. Stacked-PR flow
  worked cleanly since S1 (frontend/docs) and W2 (backend) touch disjoint files.
- 2026-08-14 S3/S4/M2: Review-first cycle on an uncommitted 36-file feature tree.
  Two `reviewer` lenses (correctness, regression) run in parallel **independently
  converged** on the same three CONFIRMED findings (empty-`not_in` purge, stale
  `sync_log` after a cache drop, support bundle losing the traceback across a
  rotation) — duplicate lenses were not wasted; the agreement is what made them
  safe to fix without a live cluster. Friction: one reviewer-suggested guard
  (`if all_lb_guids:`) was **wrong** and an existing test caught it immediately —
  a reminder that CONFIRMED-by-agent still has to clear the suite before it lands.
  Process gap found outside the diff: PR #14 silently reverted PR #12's merged
  vitest wiring and left `npm test` red on `main` with no gate to notice → M2.
  Merged work is not durable while the suite that proves it isn't in CI.
