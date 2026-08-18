"""
Unit tests for the packaging configuration in pyproject.toml.

These guard the end-user install path. The pre-built frontend lives in
ts_admin/static/, which is gitignored (it is a build output, committed only at
release time). hatchling excludes VCS-ignored files from builds by default, so
without an explicit `artifacts` entry the published wheel ships the FastAPI
backend with no UI — the app starts, serves the API, and renders nothing.

That failure is silent: the build succeeds and the package installs cleanly.
Only a user discovers it. Hence a test.
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

PYPROJECT = Path(__file__).parent.parent.parent / "pyproject.toml"


def _pyproject() -> dict:
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)


class TestStaticAssetsArePackaged:
    def test_artifacts_includes_the_static_directory(self) -> None:
        """The built frontend must be force-included despite being gitignored."""
        artifacts = _pyproject()["tool"]["hatch"]["build"]["artifacts"]
        assert any("ts_admin/static" in pattern for pattern in artifacts), (
            "pyproject.toml [tool.hatch.build] artifacts must include ts_admin/static/**, "
            "otherwise the published package contains no web UI."
        )

    def test_static_pattern_is_recursive(self) -> None:
        """Next.js emits nested directories (_next/static/...) — a flat glob misses them."""
        artifacts = _pyproject()["tool"]["hatch"]["build"]["artifacts"]
        static_patterns = [p for p in artifacts if "ts_admin/static" in p]
        assert any(p.endswith("**") for p in static_patterns), (
            f"static artifact pattern must be recursive (end in **), got {static_patterns}"
        )


class TestEntryPoint:
    def test_console_script_is_declared(self) -> None:
        """The install docs tell users to run `ts-admin-toolkit serve`."""
        scripts = _pyproject()["project"]["scripts"]
        assert scripts["ts-admin-toolkit"] == "ts_admin.cli:main"

    def test_requires_python_matches_installer(self) -> None:
        """install.sh pins a managed Python; it must satisfy requires-python."""
        requires = _pyproject()["project"]["requires-python"]
        assert requires == ">=3.10", (
            f"requires-python changed to {requires!r} — check the PYTHON_VERSION "
            "pinned in install.sh and install.ps1 still satisfies it."
        )
