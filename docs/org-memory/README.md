# Org memory

The org keeps three durable stores. Each answers a different question and is
changed by a different process. Do not blur them.

| Store | Holds | Changed by |
|---|---|---|
| [CLAUDE.md](../../CLAUDE.md) | **Rules** (the constitution) | human-approved PRs only — it is a protected path |
| [BACKLOG.md](../../BACKLOG.md) | **Tasks** (the work queue) | every cycle — Status / notes / new rows only |
| `docs/org-memory/` | **Facts** (discoveries about the code) | every cycle, at the **Records** step |

## The two facts files

- [codebase.md](codebase.md) — verified facts about the code, in topical `##`
  sections. This is the file every agent reads *before* starting work.
- [retros.md](retros.md) — one line per cycle on process friction.

## Conventions

- **Read before work.** Every agent reads `codebase.md` (and, for discovery,
  `retros.md`) at boot, before touching anything.
- **Write at Records time.** Facts are folded in during phase 6 of a cycle, not
  mid-task.
- **One dated bullet per fact**, with `file:line` evidence and the originating
  cycle/PR: `- YYYY-MM-DD (ID/PR): claim with file:line evidence`.
- **Delete when falsified.** A fact that no longer holds is removed, not left to
  rot. Recalled facts reflect what was true when written — verify a named
  file/function still exists before relying on it.
- **Promote hardened facts.** A fact that has proven load-bearing and stable can be
  promoted up into `CLAUDE.md` (via a human-approved PR).
- **Keep it pruned** (~120 lines). Merge, don't accrete. Negative facts ("audited X,
  found clean") are valuable — they let future cycles skip audited ground.
