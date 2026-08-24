# Findings ledger

**Findings that are real but not yet committed to.** A discovery run may file
only as many `BACKLOG.md` rows as the org has closed since the previous
discovery run (the Balance rules in
[.claude/skills/improve-cycle/SKILL.md](../../.claude/skills/improve-cycle/SKILL.md)).
Surplus CONFIRMED findings land here instead of in the queue.

Why the split: "we found this" and "we committed to fixing this" were the same
list, so the list could only grow — 5 hunter lenses file in parallel while a fix
cycle closes one row. This file absorbs the difference without losing anything.

## Rules

- **Nothing is lost.** A finding here is as real as a backlog row; it is just
  not queued.
- **Only a human promotes.** A cycle may append here and may mark an entry
  `promoted` / `stale`, but only a human moves an entry into `BACKLOG.md`.
  Promotion copies the entry verbatim and gives it an ID.
- **Same evidence bar as a row.** Every entry carries a failure scenario, the
  file:line evidence, and drafted acceptance criteria written against `main` —
  so promotion is a copy, never a re-investigation.
- **Re-verify before promoting.** Findings go stale; the code moves. State the
  commit the finding was measured against.
- **Duplicates die here, not in the queue.** Dedupe a new finding against this
  file *and* `BACKLOG.md` before appending.

## Entry format

```
### <short title>
`CONFIRMED` · measured against `<commit>` on <date> · lens: <correctness|security|…>
**Failure scenario:** …
**Evidence:** file:line …
**Drafted acceptance criteria:** …
**Status:** unpromoted | promoted (→ S99) | stale (<why>)
```

## Findings

_Empty. Seeded 2026-08-24 with the Balance rules; the next `discover` run is the
first that can write here._
