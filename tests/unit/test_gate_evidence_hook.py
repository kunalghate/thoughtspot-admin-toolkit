"""M18: the gate-evidence hook must log gates run inside a detached worktree to the primary checkout's log."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "gate_evidence.py"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _commit(cwd: Path, name: str) -> None:
    (cwd / name).write_text(name)
    _git(cwd, "add", name)
    _git(cwd, "commit", "-m", name)


def run_hook(payload: dict | str) -> subprocess.CompletedProcess:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    proc = subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc


def _payload(cwd: Path, command: str) -> dict:
    return {
        "tool_name": "Bash",
        "cwd": str(cwd),
        "tool_input": {"command": command},
        "tool_response": {"stdout": "5 passed", "stderr": "", "exit_code": 0},
    }


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _commit(repo, "a")
    _git(repo, "branch", "improve/x")
    _git(repo, "checkout", "-q", "improve/x")
    _commit(repo, "b")
    _git(repo, "checkout", "-q", "main")
    return repo


def _evidence(repo: Path) -> Path:
    return repo / ".claude" / "evidence"


def test_detached_worktree_gate_lands_in_primary_log(repo: Path) -> None:
    wt = repo / "tmp" / "wt"
    _git(repo, "worktree", "add", "--detach", str(wt), "improve/x")
    log = _evidence(repo) / "improve__x.log"
    assert not log.exists()

    run_hook(_payload(wt, "pytest -q"))

    assert "$ pytest -q" in log.read_text()
    assert not list(repo.rglob("HEAD.log"))


def test_leading_cd_into_worktree_resolves_its_branch(repo: Path) -> None:
    wt = repo / "tmp" / "wt"
    _git(repo, "worktree", "add", "--detach", str(wt), "improve/x")

    run_hook(_payload(repo, f"cd {wt} && pytest"))

    assert f"$ cd {wt} && pytest" in (_evidence(repo) / "improve__x.log").read_text()


def test_leading_cd_semicolon_resolves_worktree_branch_not_payload_cwd(repo: Path) -> None:
    wt = repo / "tmp" / "wt"
    _git(repo, "worktree", "add", "--detach", str(wt), "improve/x")
    _git(repo, "checkout", "-q", "-b", "improve/z")

    run_hook(_payload(repo, f"cd {wt}; pytest"))

    assert f"$ cd {wt}; pytest" in (_evidence(repo) / "improve__x.log").read_text()
    assert not (_evidence(repo) / "improve__z.log").exists()


def test_main_wins_over_other_branch_at_same_tip(repo: Path) -> None:
    _git(repo, "branch", "improve/fresh", "main")
    wt = repo / "tmp" / "wt"
    _git(repo, "worktree", "add", "--detach", str(wt), "main")

    run_hook(_payload(wt, "pytest -q"))

    assert not _evidence(repo).exists()


def test_quoted_executable_path_is_a_gate(repo: Path) -> None:
    _git(repo, "checkout", "-q", "improve/x")
    command = '"/Users/a b/.venv/bin/ruff" check ts_admin/'

    run_hook(_payload(repo, command))

    assert f"$ {command}" in (_evidence(repo) / "improve__x.log").read_text()


def test_worktree_detached_at_main_is_skipped(repo: Path) -> None:
    wt = repo / "tmp" / "wt"
    _git(repo, "worktree", "add", "--detach", str(wt), "main")

    run_hook(_payload(wt, "pytest -q"))

    assert not _evidence(repo).exists()


def test_fail_open(repo: Path, tmp_path: Path) -> None:
    run_hook("not json")

    plain = tmp_path / "plain"
    plain.mkdir()
    run_hook(_payload(plain, "pytest -q"))
    assert not (plain / ".claude").exists()

    _git(repo, "checkout", "-q", "improve/x")
    run_hook(_payload(repo, "ls -la"))
    assert not _evidence(repo).exists()


def test_primary_checkout_on_branch_logs_same_file(repo: Path) -> None:
    _git(repo, "checkout", "-q", "improve/x")

    run_hook(_payload(repo, "ruff check ts_admin/"))

    assert "$ ruff check ts_admin/" in (_evidence(repo) / "improve__x.log").read_text()
