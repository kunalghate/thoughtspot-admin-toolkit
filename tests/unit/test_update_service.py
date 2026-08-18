"""
Update check: version comparison, caching, and failing soft.

The update banner is the only way a user learns a newer release exists, so the
two failure modes that matter are opposite: claiming an update when there is
none (sends the user to reinstall for nothing) and breaking the app when GitHub
is unreachable (an offline admin must still be able to work).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from ts_admin.services import update_service
from ts_admin.services.update_service import LATEST_RELEASE_API, check_for_update, reset_cache

WHEEL = "https://github.com/o/r/releases/download/v9.9.9/ts_admin_toolkit-9.9.9-py3-none-any.whl"


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_cache()
    yield
    reset_cache()


def _release(tag: str, *, wheel: bool = True) -> dict:
    assets = [{"browser_download_url": WHEEL}] if wheel else []
    # GitHub attaches the auto-generated source archives too; they must not be
    # mistaken for the installable wheel.
    assets.append({"browser_download_url": "https://github.com/o/r/archive/refs/tags/v1.tar.gz"})
    return {"tag_name": tag, "assets": assets, "html_url": "https://github.com/o/r/releases/tag/" + tag}


@pytest.mark.parametrize(
    ("current", "latest", "expected"),
    [
        ("0.1.0", "0.2.0", True),
        ("0.1.0", "0.1.1", True),
        ("0.9.0", "0.10.0", True),  # numeric, not lexicographic
        ("0.2.0", "0.2.0", False),
        ("0.2.0", "0.1.0", False),  # never offer a downgrade
        ("0.2.0", "0.2", False),  # 0.2 == 0.2.0
        ("0.0.0+dev", "0.2.0", False),  # source checkout: unknown, stay quiet
        ("0.1.0", "nightly", False),  # unparseable tag: stay quiet
    ],
)
@respx.mock
async def test_version_comparison(monkeypatch, current, latest, expected):
    monkeypatch.setattr("ts_admin.__version__", current)
    respx.get(LATEST_RELEASE_API).mock(return_value=httpx.Response(200, json=_release(latest)))

    result = await check_for_update()

    assert result.current == current
    assert result.update_available is expected


@respx.mock
async def test_no_wheel_asset_means_no_update_offered(monkeypatch):
    """A release with no .whl cannot be installed, so offering it would dead-end."""
    monkeypatch.setattr("ts_admin.__version__", "0.1.0")
    respx.get(LATEST_RELEASE_API).mock(return_value=httpx.Response(200, json=_release("0.2.0", wheel=False)))

    result = await check_for_update()

    assert result.latest == "0.2.0"
    assert result.wheel_url is None
    assert result.update_available is False


@respx.mock
async def test_wheel_url_is_the_wheel_not_the_source_archive(monkeypatch):
    monkeypatch.setattr("ts_admin.__version__", "0.1.0")
    respx.get(LATEST_RELEASE_API).mock(return_value=httpx.Response(200, json=_release("0.2.0")))

    result = await check_for_update()

    assert result.wheel_url == WHEEL
    assert result.update_available is True


@respx.mock
async def test_network_failure_reports_unchecked_not_an_error(monkeypatch):
    monkeypatch.setattr("ts_admin.__version__", "0.1.0")
    respx.get(LATEST_RELEASE_API).mock(side_effect=httpx.ConnectError("offline"))

    result = await check_for_update()

    assert result.checked is False
    assert result.update_available is False
    assert result.current == "0.1.0"


@respx.mock
async def test_github_error_status_reports_unchecked(monkeypatch):
    """A repo with no releases yet returns 404 — not a crash."""
    monkeypatch.setattr("ts_admin.__version__", "0.1.0")
    respx.get(LATEST_RELEASE_API).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))

    result = await check_for_update()

    assert result.checked is False
    assert result.update_available is False


@respx.mock
async def test_result_is_cached(monkeypatch):
    monkeypatch.setattr("ts_admin.__version__", "0.1.0")
    route = respx.get(LATEST_RELEASE_API).mock(return_value=httpx.Response(200, json=_release("0.2.0")))

    await check_for_update()
    await check_for_update()

    assert route.call_count == 1


@respx.mock
async def test_force_bypasses_the_cache(monkeypatch):
    """`ts-admin-toolkit update` must not install from a stale cached check."""
    monkeypatch.setattr("ts_admin.__version__", "0.1.0")
    route = respx.get(LATEST_RELEASE_API).mock(return_value=httpx.Response(200, json=_release("0.2.0")))

    await check_for_update()
    await check_for_update(force=True)

    assert route.call_count == 2


@respx.mock
async def test_failed_check_is_not_cached(monkeypatch):
    """An offline blip must not suppress the banner for the next 6 hours."""
    monkeypatch.setattr("ts_admin.__version__", "0.1.0")
    respx.get(LATEST_RELEASE_API).mock(side_effect=httpx.ConnectError("offline"))
    assert (await check_for_update()).checked is False

    respx.get(LATEST_RELEASE_API).mock(return_value=httpx.Response(200, json=_release("0.2.0")))
    assert (await check_for_update()).update_available is True


def test_version_is_read_from_package_metadata():
    """
    `make release-github` bumps pyproject.toml only. A hardcoded literal in
    __init__.py would drift and make every version report a lie.
    """
    import ts_admin

    assert ts_admin.__version__ != "0.0.0+dev", "package metadata not readable — is it installed?"
    assert update_service.UPDATE_COMMAND == "ts-admin-toolkit update"
