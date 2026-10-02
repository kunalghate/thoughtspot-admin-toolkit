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
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

GATE = re.compile(r"\b(ruff check|ruff format|pytest|npx tsc|npm run build|npm test|playwright|make test|mypy)\b")
TAIL_CHARS = 600


def _git(d: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True).stdout.strip()


def _effective_dir(payload: dict, command: str) -> Path:
    """The directory the gate actually ran in: payload cwd, or the target of a leading `cd X &&`."""
    base = Path(payload.get("cwd") or ".")
    # ponytail: only a leading `cd X &&|;` is parsed; git -C / pushd / `(cd x; ...)` fall back to the
    # payload cwd. Extend here if the evidence log shows gates landing under the wrong branch.
    try:
        tokens = shlex.split(command)
    except ValueError:
        return base
    if len(tokens) >= 3 and tokens[0] == "cd" and tokens[2] in ("&&", ";"):
        return base / tokens[1]
    return base


def _log_root(d: Path) -> Path:
    """The primary checkout: parent of the common .git dir, so worktree runs share its evidence dir."""
    common = _git(d, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common).parent if common else d


def _branch(d: Path) -> str:
    """Branch name for d; for a detached HEAD, the branch pointing at it (main wins), else detached-<sha>."""
    branch = _git(d, "rev-parse", "--abbrev-ref", "HEAD")
    if branch != "HEAD":
        return branch
    names = [
        n
        for n in _git(d, "branch", "--points-at", "HEAD", "--format=%(refname:short)").splitlines()
        if n and not n.startswith("(")
    ]
    if "main" in names:
        return "main"
    if len(names) == 1:
        return names[0]
    return f"detached-{_git(d, 'rev-parse', '--short', 'HEAD')}"


def main() -> None:
    payload = json.load(sys.stdin)
    if payload.get("tool_name") != "Bash":
        return
    command = payload.get("tool_input", {}).get("command", "")
    if not GATE.search(command):
        return

    run_dir = _effective_dir(payload, command)
    branch = _branch(run_dir)
    if not branch or branch == "main":
        return

    response = payload.get("tool_response") or {}
    if isinstance(response, str):
        output, status = response, ""
    else:
        output = str(response.get("stdout") or "") + str(response.get("stderr") or "")
        status = str(response.get("exit_code", response.get("interrupted", "")))

    log = _log_root(run_dir) / ".claude" / "evidence" / f"{branch.replace('/', '__')}.log"
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
