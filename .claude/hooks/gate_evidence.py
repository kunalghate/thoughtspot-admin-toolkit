#!/usr/bin/env python3
"""PostToolUse hook: append every verification-bar command to a per-branch evidence log.

The org's PR evidence section is currently written from an agent's recollection of
its own gate runs, which is exactly the thing the review board is told not to trust.
This records the command, exit code and tail of output as the gate actually ran, so
the PR body can be checked against a machine-collected trail.

Never blocks: any failure here exits 0 and is invisible to the session.
"""

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

GATE = re.compile(r"\b(ruff check|ruff format|pytest|npx tsc|npm run build|npm test|playwright|make test|mypy)\b")
TAIL_CHARS = 600


def main() -> None:
    payload = json.load(sys.stdin)
    if payload.get("tool_name") != "Bash":
        return
    command = payload.get("tool_input", {}).get("command", "")
    if not GATE.search(command):
        return

    project = Path(payload.get("cwd") or ".")
    branch = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not branch or branch == "main":
        return

    response = payload.get("tool_response") or {}
    if isinstance(response, str):
        output, status = response, ""
    else:
        output = str(response.get("stdout") or "") + str(response.get("stderr") or "")
        status = str(response.get("exit_code", response.get("interrupted", "")))

    log = project / ".claude" / "evidence" / f"{branch.replace('/', '__')}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as fh:
        fh.write(f"\n=== {datetime.now(timezone.utc).isoformat(timespec='seconds')} exit={status}\n")
        fh.write(f"$ {command}\n")
        fh.write(output[-TAIL_CHARS:].rstrip() + "\n")


if __name__ == "__main__":
    try:
        main()
    except (json.JSONDecodeError, OSError, ValueError):
        pass
    sys.exit(0)
