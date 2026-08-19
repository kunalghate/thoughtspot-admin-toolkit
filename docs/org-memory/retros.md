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
- 2026-08-14 S6: **A fully green verification bar certified a permanent-data-loss
  change.** ruff, 181 unit + 129 integration, tsc, build, vitest all passed, and
  the acceptance-criteria test provably failed without the fix — yet two reviewer
  lenses independently reproduced unrecoverable deletion. The gates prove "does
  what the criteria say"; only the adversarial lenses asked "should the criteria
  say that". Keep the Review Board non-optional even on a green bar. Second
  lesson: **a backlog row's acceptance criteria can itself be the bug.** S6's
  criteria mandated deleting an edge whose source liveboard is unchanged — by
  definition a row the run cannot rebuild — so no safe implementation exists. The
  cycle correctly stopped at a records-only PR rather than shipping to criteria.
  Friction: two agents acted on a stale `codebase.md` "KNOWN RED ruff format"
  bullet that W2 had fixed in PR #11 — Records must **prune** facts, not only
  append (bullet deleted this cycle). Also: parallel agents wrote scratch
  `tests/unit/test_zz_*.py` into the shared tree and reddened another agent's
  `ruff check`; QA's fix — run the bar in a detached worktree of the branch HEAD —
  should become the default. Third lesson: **don't discard a slow lens's report
  once the decision is made.** The performance lens landed after S6 was already
  rejected and looked moot, but two of its findings (no composite index on
  `ts_metadata`; sync tasks blocking the event loop) are latent on `main`,
  independent of the rejected diff — and one of them *blocks the replacement*
  this cycle recommended. Read late reports; file what outlives the diff.

- 2026-08-15 (S7): **Rejected at review — the second consecutive cycle to build,
  fully verify, and then reject a lineage change.** The pattern is now clear
  enough to name: *the criteria prescribed a mechanism, and nobody checked what
  that mechanism would remove.* S7's criteria say "keyed off a persisted
  'liveboard tier last built' marker"; the CEO designed straight to that wording,
  the researcher mapped the code faithfully, the architect planned it cleanly, and
  all three missed that `has_lb_edges` — the thing the criteria told us to delete —
  was **not** the real self-heal. The actual recovery was an accident of
  `_persist_column_map` rebuilding `CachedColumnLineage` every run, which no test
  named and no reader noticed. The correctness lens found it only by mutation.
  Filed as **M9**: research must answer "what does the current code do that this
  mechanism would remove, and what load-bearing behaviour has no test?" *before*
  design. Second lesson, and the sharper one: **when a suite cannot detect the
  class of error you keep making, harden the suite before changing the behaviour
  again.** 6 of 7 mutations left all 16 lineage tests green, including "never
  re-crawl anything, ever." A third design attempt against that suite would have
  been guessing with extra steps, so the cycle shipped the two missing pinning
  tests instead and filed **S27** (P1) to block any S7 re-attempt until the rest
  land. Third: **I authorised the vacuity myself.** My plan told the implementer
  to rewrite an existing regression test's setup so it would pass — and that added
  line deleted exactly the state two blockers land in. M4 warned about this one
  cycle ago and I re-committed it at the design step; "the plan said to" is not a
  defence. Cheap rule that would have caught it: check out the pre-change test
  file and run it against the branch. Friction, repeating from S6 and now filed as
  **M8** rather than just noted: review agents again wrote scratch repros into
  `tests/unit/` of the *shared* checkout and flipped QA's suite red mid-run —
  serialization has to cover file writes, not only ports. What went right: the
  parallel Review Board earned its cost outright (four lenses, three independent
  CONFIRMED blockers, each reproduced as a measured `main`-vs-branch delta), and
  the security and performance lenses came back clean with the intended win
  quantified — so the reject is about the mechanism, not the goal.

- 2026-08-15 (S27): **Shipped — the first cycle in three to end at a green PR on
  this code path, and it did so by changing tests instead of behaviour.** The
  sequencing lesson from the S7 reject held up: when a suite cannot detect the
  class of error you keep making, harden the suite first. Concretely, the pre-S27
  suite killed 9 of 48 mutations and was blind to *every* `cluster_id`/`org_id`
  predicate in `build_column_map` — on an app whose constitution names
  multi-cluster a v1 pillar. Nobody knew that until it was measured; three cycles
  had reviewed this file without anyone counting.
  What worked, and is worth repeating: **the acceptance evidence was a kill, not a
  green bar.** Requiring every new test to be demonstrated red-then-green against
  the specific mutation it targets caught two weak fixtures during development
  (the diagonal shadow scope, the shared liveboard GUID) that would have shipped as
  confident-looking dead weight. Also worth repeating: telling the reviewer *not*
  to trust the implementer's own kill table. It re-ran 27 mutations independently
  and agreed on all 27 including the documented survivors — which is what makes the
  published table usable by the next cycle instead of merely reassuring.
  The sharpest finding was self-referential: **the anti-vacuity tests were
  fixture-vacuous.** Stub the shadow seeder and all 7 stayed green, because "X is
  unchanged" is trivially true when X is empty — in the two tests that are the sole
  killer of 12 scoping mutations. M4 says a guard test can be vacuous; S27 adds
  that its *fixture* can be, one level below where anyone looks. New rule in
  org-memory: any "X is unchanged" assertion must first assert X is non-empty.
  Two process notes. **I skipped the architect stage deliberately** — test-only,
  zero production change, so there was no mechanism to vet and the S6/S7 failure
  mode was structurally absent. That was the right call here and should not become
  a habit. And **M9 bit me inside the row filed to prevent it**: S27's criteria
  enumerate a mutation ("reordering the post-persist write") that exists only on
  the rejected S7 branch, because I wrote the row with that design still in hand.
  Criteria written at the end of a failed cycle inherit its assumptions — write
  them against `main`, or mark them provisional.
  Friction: none from M8 this time. Both review agents used worktrees and the
  scratchpad as instructed and left the tree clean, so QA's gate run was
  trustworthy — the fix worked before the M8 row is even merged.
- 2026-08-15 (S23): The M9 research question earned its place — asking "what would
  the criteria's prescribed mechanism REMOVE?" before design is what caught that
  option (a) would have shipped a defect the entire bar renders invisible. But M9
  only covers the *criteria's* mechanism, not the *design's*: the write-ahead marker
  was my own choice and its two CONFIRMED defects (a guard that cannot report itself,
  and a marker that makes healthy syncs look broken) were found by the review board,
  not by design. Both were invisible to a fully green bar — the third consecutive
  cycle where that is true, which is M5's whole argument.
  Friction: my own "do not touch `dashboard_service.py`" rule, written to avoid a
  conflict with an in-flight PR of mine, would have shipped a real regression if the
  regression lens had not caught it. Conflict-avoidance is not a safety constraint
  and must yield to one; I lifted it mid-cycle. No process row filed — the rule was
  mine for this cycle, not a standing one.

- **S33 — Topbar sync-label coverage (2026-08-18).** The agent briefs caused no
  friction; the department pipeline worked. Two things are worth carrying forward.
  **(1) M8 is environmental, not hypothetical.** A concurrent writer modified two
  unrelated files in the shared checkout mid-cycle AND ran `npm run build` in it,
  taking both dev servers down and making any gate run in the shared tree
  untrustworthy. Isolating QA and both review lenses into `--detach` worktrees is
  what kept the evidence honest; that should be the DEFAULT recipe in the agent
  briefs, not a per-cycle improvisation I remembered to specify.
  **(2) M5 landed on the CEO's own work.** The cycle's product change was a UI fix
  I wrote before dispatching, on a wrong initial diagnosis (I read a literal `…` in
  the label as CSS truncation). A fully green five-gate bar — ruff, 313+152 pytest,
  tsc, build, 30 vitest — said nothing about a header that renders two overlapping
  unreadable strings. Only the review board caught it, and only because one lens
  built a CSS replica and MEASURED rather than reasoned. Two lessons: a visual
  change needs a visual verification artifact, and the CEO writing product code
  before the pipeline runs means the riskiest diff in the cycle is the one no
  researcher or architect ever looked at.
  **(3) A spec can be confidently wrong.** I told the implementer to add
  `overflow: hidden` as "belt-and-braces"; it measured that this zeroes
  `min-width: auto` (Flexbox §4.5) and reproduces the exact bug, and pushed back
  with numbers. The deviation was right. Briefs should ask for the invariant and
  the measurement, not prescribe the CSS property.

- 2026-08-18 (skill hardening): Human-directed pass folding S6/S7/S27/S33 retro
  lessons into `improve-cycle` + agent briefs (M6 REJECT path, M9 gate, M8
  worktree default, M7 pruning, SpotterCode ground-truth tiers). Friction that
  prompted it: every one of these rules had already been paid for in a cycle but
  lived only in retros, so each cycle re-improvised them. Lesson: when a retro
  lesson recurs twice, it belongs in the skill/brief, not the retro.
