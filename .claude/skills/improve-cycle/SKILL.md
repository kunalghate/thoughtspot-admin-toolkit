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

---

## Phase pipeline (single-item modes)

### 1. PICK
Read [BACKLOG.md](../../../BACKLOG.md). Reclaim any dead `in-progress` row (branch
gone / stale). Pick the target (top open row, or the given `<ID>`). **Re-verify the
item still applies against current code** — findings go stale. If it's already
satisfied, skip to a records-only PR marking it `done`.

### 2. RESEARCH
Spawn `researcher` (fallback: the `Explore` agent). For a large item, fan out one
researcher per area in a single message. Collect the touch map + reuse targets +
risk flags.

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
- Append a one-line micro-retro to `docs/org-memory/retros.md`.

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

## Discovery mode (`/improve-cycle discover [fix]`)

1. Pick a **hunting ground** the backlog doesn't already cover — skip grounds marked
   recently-audited-clean in `docs/org-memory/`.
2. Fan out `bug-hunter` agents in parallel, ONE per lens
   (correctness / security / regression / data-integrity / performance) in a single
   message.
3. Dedupe findings, re-verify survivors against current code.
4. File each **CONFIRMED** finding as a new `BACKLOG.md` row (with drafted
   acceptance criteria, S/R/W/M prefix, priority) via a **records-only PR** —
   finding and fixing never share a diff. Record "audited X, found clean" facts to
   `docs/org-memory/codebase.md`.
5. Plain `discover` **stops here** (filing only). `discover fix` waits for that PR
   to merge, pulls `main`, then runs phases 2–8 per finding.

---

## Invariants (never break)

- Nothing auto-merges — every cycle ends at an open PR for human review.
- Never touch a protected path without the human adding `human-approved`; agents
  never add that label. A red `guard` job means "hand to human."
- Never weaken a gate or a guard test to pass.
- A PR is ready for a human to merge only when: required checks green ∧ no CONFIRMED
  correctness bug ∧ `guard` green.
