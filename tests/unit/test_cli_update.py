"""
`ts-admin-toolkit update` — the command the in-app pill and the README tell
users to type.

What matters here is that it never leaves the user stranded: every exit path
either installs, says "already current", or names the fallback. A non-zero exit
with no explanation is the failure mode this guards.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from ts_admin.cli import main
from ts_admin.services.update_service import UpdateCheck


def _available() -> UpdateCheck:
    return UpdateCheck(
        current="0.1.0",
        latest="0.4.0",
        update_available=True,
        wheel_url="https://github.com/o/r/releases/download/v0.4.0/x-0.4.0-py3-none-any.whl",
        release_url="https://github.com/o/r/releases/tag/v0.4.0",
    )


def _patch_check(result: UpdateCheck):
    async def _fake(force: bool = False) -> UpdateCheck:
        return result

    return patch("ts_admin.services.update_service.check_for_update", _fake)


class _Completed:
    def __init__(self, returncode: int, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr


def test_up_to_date_says_so_and_exits_clean():
    with _patch_check(UpdateCheck(current="0.4.0", latest="0.4.0", update_available=False)):
        result = CliRunner().invoke(main, ["update"])

    assert result.exit_code == 0
    assert "latest version" in result.output
    assert "v0.4.0" in result.output


def test_check_flag_reports_but_does_not_install():
    with _patch_check(_available()), patch("subprocess.run") as run:
        result = CliRunner().invoke(main, ["update", "--check"])

    assert result.exit_code == 0
    assert "0.1.0 → 0.4.0" in result.output.replace("v", "")
    run.assert_not_called()


def test_installs_the_wheel_from_the_latest_release():
    with (
        _patch_check(_available()),
        patch("ts_admin.cli._resolve_uv", lambda: "/fake/uv"),
        patch("subprocess.run", return_value=_Completed(0)) as run,
    ):
        result = CliRunner().invoke(main, ["update"])

    assert result.exit_code == 0
    argv = run.call_args[0][0]
    assert argv[:4] == ["/fake/uv", "tool", "install", "--force"]
    assert argv[-1] == _available().wheel_url
    # The user is left knowing how to get back into the app.
    assert "ts-admin-toolkit serve" in result.output


def test_missing_uv_names_the_fallback_instead_of_failing_silently():
    with _patch_check(_available()), patch("ts_admin.cli._resolve_uv", lambda: None):
        result = CliRunner().invoke(main, ["update"])

    assert result.exit_code == 1
    assert "install command" in result.output


def test_failed_install_surfaces_the_reason():
    with (
        _patch_check(_available()),
        patch("ts_admin.cli._resolve_uv", lambda: "/fake/uv"),
        patch("subprocess.run", return_value=_Completed(1, "error: no space left on device")),
    ):
        result = CliRunner().invoke(main, ["update"])

    assert result.exit_code == 1
    assert "no space left on device" in result.output


def test_unreachable_github_fails_with_an_explanation():
    with _patch_check(UpdateCheck(current="0.1.0", checked=False, error="Could not reach GitHub.")):
        result = CliRunner().invoke(main, ["update"])

    assert result.exit_code == 1
    assert "Could not reach GitHub." in result.output


@pytest.mark.parametrize("flag", ["--version", "-v"])
def test_version_flag_prints_the_installed_version(flag):
    result = CliRunner().invoke(main, [flag])

    assert result.exit_code == 0
    assert "ts-admin-toolkit" in result.output
