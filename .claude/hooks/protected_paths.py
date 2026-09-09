#!/usr/bin/env python3
"""PreToolUse hook: stop an agent editing a CLAUDE.md protected path without a human.

CI's `guard` job is the authority and stays the authority — this only moves the
failure from "one wasted cycle" to "one prompt". It asks rather than denies,
because a human sitting in the session is allowed to edit these files; a headless
subagent, which cannot answer the prompt, is not.
"""

import json
import re
import sys

PROTECTED = (
    "CLAUDE.md",
    ".github/workflows/",
    "ts_admin/main.py",
    "ts_admin/services/cluster_service.py",
    "ts_admin/cli.py",
    "ts_admin/config.py",
    "tests/integration/test_dryrun_safety.py",
    "tests/integration/test_cluster_isolation.py",
    "tests/integration/test_audit_log_writes.py",
    "tests/unit/test_cluster_service.py",
)
# Only a shell command that actually writes counts, and only when the write verb
# comes just before the path: `cat CLAUDE.md` is not an edit, and neither is
# `pytest tests/integration/test_dryrun_safety.py | tee log` (the tee is after it).
WRITES = re.compile(r"(>>?|sed -i\S*|\btee\b|\bmv\b|\brm\b|\bcp\b|\bpatch\b|\btruncate\b)")
LOOKBACK = 60


def written_by(command: str, path: str) -> bool:
    for m in re.finditer(re.escape(path), command):
        if WRITES.search(command[max(0, m.start() - LOOKBACK) : m.start()]):
            return True
    return False


def hit(payload: dict) -> str | None:
    tool = payload.get("tool_name", "")
    args = payload.get("tool_input", {})
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        target = str(args.get("file_path", ""))
    elif tool == "Bash":
        command = str(args.get("command", ""))
        return next((p for p in PROTECTED if written_by(command, p)), None)
    else:
        return None
    return next((p for p in PROTECTED if p in target), None)


def main() -> None:
    path = hit(json.load(sys.stdin))
    if not path:
        return
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": (
                        f"{path} is a protected path (CLAUDE.md). Agents may not change it "
                        "autonomously; a PR touching it needs a human to add the "
                        "`human-approved` label. Appending a genuinely new endpoint to "
                        "DRYRUN_ENDPOINTS/READ_ENDPOINTS is expected — confirm if that is "
                        "what this is."
                    ),
                }
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (json.JSONDecodeError, ValueError):
        pass
    sys.exit(0)
