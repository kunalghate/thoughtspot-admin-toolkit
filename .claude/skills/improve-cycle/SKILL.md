---
name: improve-cycle
description: >-
  Run ONE improvement cycle as the CEO orchestrator. Modes: `/improve-cycle`
  (work the top open backlog item), `/improve-cycle <ID>` (a specific item),
  `/improve-cycle N` (run N cycles; parallel git worktrees when the items' product
  files are disjoint), `/improve-cycle discover` (hunt & FILE new bugs, no fix),
  `/improve-cycle discover fix` (hunt, file, then fix). Every cycle ends at an OPEN
  PR for human review — nothing auto-merges to main.
---

# improve-cycle — the CEO orchestrator

**You are the CEO — an orchestrator, not a department.** Your context is the org's
scarcest resource; spend it on decisions, not department work. Dispatch the agents
in `.claude/agents/`; do not do their work yourself.

**Parallel dispatch.** Agents with no data dependency launch in ONE message so they
run concurrently: review lenses, discovery hunters, research fan-out, and the
Review Board ∥ QA. Dependent stages stay sequential: research → design → build →
review.

**Serialize the gates.** pytest integration runs in-memory (TestClient), but
`make dev` / Playwright / uvicorn bind fixed ports (8000/3000). Only ONE agent runs
server-bound gates at a time. Parallel worktree implementers run only the cheap
lint/typecheck gate; the port-bound gates run serially at the end.

**Read first, always:** [CLAUDE.md](../../../CLAUDE.md) (constitution, verification
bar, protected paths) and `docs/org-memory/codebase.md` (verified facts).

**The CEO writes no product code.** A diff you authored yourself — including one
written before the cycle started — is the riskiest diff in the cycle, because no
researcher or architect ever looked at it. Route it through the same pipeline as
an implementer's work (S33 lesson).

---

## ThoughtSpot ground truth (SpotterCode MCP)

Never assert a REST v2 fact from training data or by reading our own client back
to itself — three invented request keys shipped that way, silently no-op'd by the
API. Two tiers, by auth state:

- **Docs tools — always open, no auth:** `get-rest-api-reference` (endpoint
  specs, request/response schemas, version tags; pass `apiName` for an exact
  endpoint) and `get-developer-docs-reference` (SDK/embed docs). Mandatory for
  every W-row, drift check, and any claim about an endpoint's contract.
  Department agents have no MCP tools — they drive the same docs endpoint over
  plain `curl` (JSON-RPC recipe in `docs/org-memory/codebase.md`) or hand the
  question back to the CEO.
- **`execute-thoughtspot-code` — auth flip-flops; probe, don't assume.** Open
  each cycle with one cheap read (`GET auth/session/user`) and note the state
  and the session's `current_org`. Authenticated: use it for live verification —
  **reads only; never set `confirm_write_operations` inside a cycle** (live
  writes are the human's lever, and the app's own dry-run rules apply in
  spirit). Unauthenticated: fall back to the repo's `ThoughtSpotClient` against
  a configured cluster — via the `patched_config` fixture, never a hand-patched
  engine (an agent has accidentally hit prod that way).

Record what you establish in `docs/org-memory/codebase.md`'s REST v2 section,
dated, marked "measured live" vs "from the reference".

---

## Balance rules (filing vs fixing)

The queue diverged for ~20 cycles because filing is unbounded fan-out (5 hunter
lenses, every CONFIRMED finding filed) while fixing is one row per cycle. These
four rules are the brake. They bind; they are not advisory.

**B1 - Reconcile before you pick.** Every cycle starts by walking the
`In review` section: for each row, check whether its PR merged
(`gh pr view <n>` / `git log --grep=<ID> main`). If merged, **re-verify the
acceptance criteria against current `main`** and then either move the row to
`BACKLOG_COMPLETED.md` (all criteria met) or return it to `open` with a note
naming the unmet criterion. A row may not sit in `in-review` with no open PR.
The merge happens outside the cycle, so nothing else ever closes that loop -
the first reconcile found 6 finished rows and 2 rows whose CI half never
shipped, all reading as "in flight".

**B2 - Discovery is gated on queue depth.** `discover` **refuses to run** while
open P1+P2 rows exceed **15**. Report the count and the top unqueued grounds,
then stop. Hunting for new bugs while the known-bug queue is deeper than the
burn rate is not discovery, it is avoidance. A human can override by naming the
hunting ground explicitly.

**B3 - One-in, one-out on filing.** A `discover` run may file at most **as many
new `BACKLOG.md` rows as have been moved to `done` since the previous discovery
run** (read the Flow ledger at the top of `BACKLOG.md`; floor of 3 if there is
no previous entry). Rank the survivors by blast radius and file the top N.
Every remaining CONFIRMED finding is appended to
`docs/org-memory/findings.md` - full evidence and drafted criteria, so
promotion is a copy - and **only a human** promotes an entry from there into
the queue. Nothing is lost; the queue just stops absorbing everything.
A non-discovery cycle that trips over an unrelated bug files **one** row for it
(the blocker, if any) and puts the rest in the findings ledger.

**B4 - Batch the small rows.** A single cycle MAY close 2-4 `P3`/`P4` rows in one
PR when they touch **disjoint files**, need no shared design decision, and are
covered by one test slice. Research and review still run per row; only the
branch, the gates, and the PR are shared, and the PR body carries a separate
evidence section per row. Do NOT batch anything `P1`/`P2`, anything touching a
protected path, or two rows that edit the same file - a mixed diff makes a
CONFIRMED finding in one row block the others.

---

## Phase pipeline (single-item modes)

### 1. PICK
Read [BACKLOG.md](../../../BACKLOG.md). **Run the B1 reconcile first** - walk every
`in review` row, close the ones whose PR merged and whose criteria re-verify, return
the rest to `open` with the unmet criterion named. Then reclaim any dead
`in-progress` row (branch gone / stale). Pick the target (top open row, or the given `<ID>`). **Re-verify the
item still applies against current code** — findings go stale. If it's already
satisfied, skip to a records-only PR marking it `done`.

### 2. RESEARCH
Spawn `researcher` (fallback: the `Explore` agent). For a large item, fan out one
researcher per area in a single message. Collect the touch map + reuse targets +
risk flags.

**M9 gate (mandatory, before design):** the brief must answer *"what does the
current code do that the criteria's — and later the design's — mechanism would
REMOVE, and what load-bearing behaviour there has NO test naming it?"* An
accidental recovery path counts (S7's real self-heal was an accident no test
named). A mechanism that fails this check goes to the REJECT outcome below, not
to design — this exact miss caused the S6 and S7 rejects. If the suite cannot
detect the class of error being changed (mutation-check it), harden the suite
first in its own row/PR, then re-attempt the behaviour change (the S27 sequencing).

### 3. DESIGN & BUILD
`architect` drafts the plan → **you** check it against the acceptance criteria and
the "what NOT to touch" list. Then create branch `improve/<ID>-<slug>`, mark the
row `in-progress` **on the branch**, and dispatch `implementer` to build. For N
independent items, use worktree-isolated implementers (one each).

### 4. REVIEW ∥ QA
In ONE message, launch the **Review Board** (`reviewer` per lens —
correctness / security / regression / performance, each told to REFUTE) **and**
`qa-verifier` together. Also run `/code-review` and `/security-review` on the diff.
Fix every **CONFIRMED** finding (re-dispatch `implementer`), then **re-run QA** — a
diff that changed after verification is unverified.

- **Isolation is the default, not an improvisation (M8):** every review lens and
  QA run in their own detached worktree — `git worktree add --detach <path>
  <branch>` (plain `add` fails while the branch is checked out in the shared
  tree; symlink `frontend/node_modules`). Scratch repros live in the worktree or
  scratchpad, NEVER in the shared checkout's `tests/`; never `npm run build` in
  the shared tree (it clobbers a live dev server's `.next`).
- **Kill evidence for guard tests:** a new guard test ships demonstrated
  red-then-green against the specific mutation/revert it exists to catch. Any
  "X is unchanged" assertion must first assert X is non-empty — fixtures can be
  vacuous one level below the assertion (S27). Tell the reviewer NOT to trust
  the implementer's own kill table; re-run it.
- **Visual changes need a visual artifact:** the whole bar is blind to layout
  (jsdom doesn't lay out; `tsc`/build check types). Measure in a real browser —
  a headless replica or Playwright — and show the human the change running
  headful against the live cluster. Briefs state the invariant to hold and ask
  for measurement; they do not prescribe the CSS property (S33).
- **Read every lens's report, even after the decision is made.** Late findings
  that outlive the diff get filed as rows — two of S6's late performance
  findings blocked the replacement design.

### 5. QA BAR
Confirm the full bar is green, in order: `ruff check` + `ruff format --check` →
`pytest tests/unit` + `pytest tests/integration` → `cd frontend && npx tsc
--noEmit` → `cd frontend && npm run build` → the feature-specific check (via
`/test`). **Do not open a PR on red.**

### 6. RECORDS (before shipping, on the same branch)
- Update the `BACKLOG.md` row Status and commit it.
- **If the item is now `done` (completed/resolved): MOVE its index line and its
  detail entry — verbatim, nothing rewritten — from `BACKLOG.md` to
  `BACKLOG_COMPLETED.md` (index table + Completed items section).** BACKLOG.md
  holds only open / in-progress / in-review work.
- Fold every agent's **Memory-worthy** facts into `docs/org-memory/codebase.md`.
- **Prune, don't only append (M7):** re-verify every org-memory fact this cycle's
  work touched and DELETE or amend the ones that no longer hold — a stale
  "known red" bullet has misled two agents in a single cycle.
- New backlog rows are written **against `main`**, not against the branch in
  hand — criteria drafted with a failed design still in mind inherit its
  assumptions (S27's own criteria named a mutation that exists only on the
  rejected S7 branch). If that's unavoidable, mark them provisional.
- Append a one-line micro-retro to `docs/org-memory/retros.md`.
- **Append a row to the Flow ledger** at the top of `BACKLOG.md`: cycle,
  closed, filed, open-after (`N open / N in-review / N feedback`). B3's cap is
  read from this table, so a cycle that skips it breaks the next discovery run's
  budget.

**Bright line:** a cycle may ONLY change a row's Status, append notes, and append
new rows — never edit Priority/criteria or delete rows. Those are the human's lever.
Moving a `done` item's entry verbatim to `BACKLOG_COMPLETED.md` is the one
sanctioned removal from `BACKLOG.md`; it is a move, never a deletion.

### 7. SHIP
Commit + push the branch + open a PR whose body has an **evidence section** (gate
output, review outcome, guard status). **STOP HERE. Do not merge.** Set the row
`in-review` and report to the human. Never add the `human-approved` label, never
`gh pr merge --admin`.

### 8. MICRO-RETRO (mandatory)
One line: "did any agent/skill/rule mislead or slow this cycle?" Fix it trivially
in the same PR, or file a process (**M**) backlog row. "No friction" is a valid
answer.

---

## REJECT outcome (when the row itself is wrong — M6)

Research (the M9 gate) or review can show that a row's acceptance criteria — or
the mechanism they prescribe — cannot be satisfied safely. That is a defined
outcome, not a failure to improvise around. It has happened twice (S6, S7); both
implementations passed the entire verification bar and were still wrong.

1. **Stop before shipping.** Push the branch for inspection with a clear name;
   do NOT open a PR for the rejected diff.
2. Leave the row `open` — a cycle may not edit criteria — and append a note
   with the evidence and the branch@commit.
3. File the evidence + a proposed re-scope as new row(s), written against
   `main` (see Records).
4. Ship what outlives the reject as a records-only or tests-only PR: pinning
   tests for the load-bearing behaviour that was discovered, org-memory facts,
   the new rows.
5. Report to the human with a recommendation (re-scope, close in favour of a
   replacement row, or human-edit the criteria).

---

## Discovery mode (`/improve-cycle discover [fix]`)

0. **Check the B2 gate.** Count open P1+P2 rows in `BACKLOG.md`. Over 15 -> report
   the count and STOP; the org fixes before it hunts. Then read the Flow ledger to
   compute the B3 filing budget (rows closed since the previous discovery run,
   floor 3).
1. Pick a **hunting ground** the backlog doesn't already cover — skip grounds marked
   recently-audited-clean in `docs/org-memory/`.
2. Fan out `bug-hunter` agents in parallel, ONE per lens
   (correctness / security / regression / data-integrity / performance) in a single
   message.
3. Dedupe findings, re-verify survivors against current code.
4. Dedupe survivors against BOTH `BACKLOG.md` and
   `docs/org-memory/findings.md`, then rank by blast radius. File the **top N**
   (N = the B3 budget) as new `BACKLOG.md` rows with drafted acceptance criteria,
   S/R/W/M prefix, and priority; append **every** remaining CONFIRMED finding to
   `docs/org-memory/findings.md` in that file's entry format. Both go in one
   **records-only PR** — finding and fixing never share a diff. Record
   "audited X, found clean" facts to `docs/org-memory/codebase.md`, and say
   explicitly in the PR body how many findings were ledgered rather than filed.
5. Plain `discover` **stops here** (filing only). `discover fix` waits for that PR
   to merge, pulls `main`, then runs phases 2–8 per finding.

---

## Invariants (never break)

- Nothing auto-merges — every cycle ends at an open PR for human review.
- Never file more rows than the B3 budget allows, and never skip the B1 reconcile
  or the Flow-ledger line — the queue's honesty depends on both.
- Never touch a protected path without the human adding `human-approved`; agents
  never add that label. A red `guard` job means "hand to human."
- Never weaken a gate or a guard test to pass.
- A PR is ready for a human to merge only when: required checks green ∧ no CONFIRMED
  correctness bug ∧ `guard` green.
