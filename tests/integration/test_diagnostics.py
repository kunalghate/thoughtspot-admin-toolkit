"""
Integration tests for /api/v1/diagnostics — log tail and support bundle.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from ts_admin.models.cluster import Cluster
from ts_admin.models.job import Job


@pytest.fixture(autouse=True)
def isolated_log_dir(tmp_path, monkeypatch):
    """Redirect logging_config to a tmp dir so we don't touch the user's
    real ~/.ts-admin-toolkit/logs."""
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    return tmp_path


@pytest.fixture
def in_memory_db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import ts_admin.database as db_module

    monkeypatch.setattr(db_module, "get_engine", lambda: engine)
    db_module.init_db()
    return engine


@pytest.fixture
def client(in_memory_db, monkeypatch):
    # load_config() reads the user's ~/.config — stub it so the bundle's
    # app_info.json doesn't fail on a fresh test environment.
    from ts_admin.config import AppConfig, ClusterConfig
    from ts_admin.ts_client.models import AuthType

    cluster_cfg = ClusterConfig(
        id="c1",
        name="Prod",
        url="https://prod.thoughtspot.cloud",
        username="admin",
        auth_type=AuthType.BASIC,
    )
    config = AppConfig(clusters={"c1": cluster_cfg}, active_cluster_id="c1")
    monkeypatch.setattr("ts_admin.config.load_config", lambda: config)

    from ts_admin.main import create_app

    return TestClient(create_app())


def _seed_cluster(engine):
    with Session(engine) as session:
        session.add(
            Cluster(
                id="c1",
                name="Prod",
                url="https://prod.thoughtspot.cloud",
                username="admin",
                auth_type="basic",
            )
        )
        session.commit()


def _seed_failed_job(engine, *, job_id: str = "failed-1") -> None:
    with Session(engine) as session:
        job = Job(
            id=job_id,
            cluster_id="c1",
            job_type="bulk_delete",
            status="FAILED",
            error="ThoughtSpot login expired — Reconnect this cluster from Settings → Clusters.",
            error_type="TSAuthenticationError",
            error_traceback="Traceback (most recent call last):\n  ...stack...\nTSAuthenticationError: 401",
            created_at=datetime.now(tz=timezone.utc),
        )
        session.add(job)
        session.commit()


def test_logs_endpoint_returns_text(client):
    res = client.get("/api/v1/diagnostics/logs?lines=100")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/plain")
    # Either there's content or the placeholder message — either is valid.
    assert isinstance(res.text, str)


def test_logs_endpoint_caps_lines_param(client):
    # >5000 should reject (Pydantic validation).
    res = client.get("/api/v1/diagnostics/logs?lines=999999")
    assert res.status_code == 422


def test_bundle_returns_zip_with_expected_files(client, in_memory_db):
    _seed_cluster(in_memory_db)
    _seed_failed_job(in_memory_db)

    res = client.get("/api/v1/diagnostics/bundle")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert "attachment" in res.headers["content-disposition"]

    zf = zipfile.ZipFile(io.BytesIO(res.content))
    names = set(zf.namelist())
    assert "README.txt" in names
    assert "app_info.json" in names
    assert "failed_jobs.json" in names
    # app.log only appears if the file exists; the test env may or may not
    # have produced one yet — don't assert on it.

    info = json.loads(zf.read("app_info.json"))
    assert "version" in info
    assert "platform" in info
    assert info["active_cluster_id"] == "c1"
    assert any(c["id"] == "c1" for c in info["clusters"])

    failed = json.loads(zf.read("failed_jobs.json"))
    assert len(failed) == 1
    assert failed[0]["error_type"] == "TSAuthenticationError"
    assert "TSAuthenticationError" in failed[0]["error_traceback"]


def test_bundle_truncates_log_to_tail(client, tmp_path, monkeypatch):
    """The default bundle must stay small enough to email — a multi-MB log
    gets tailed, not shipped whole."""
    from ts_admin.api import diagnostics

    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"
    log_file.write_text("\n".join(f"line {i}" for i in range(10_000)), encoding="utf-8")
    (log_dir / "app.log.1").write_text("\n".join(f"old {i}" for i in range(10_000)), encoding="utf-8")
    (log_dir / "app.log.2").write_text("much older rotated log\n", encoding="utf-8")
    monkeypatch.setattr("ts_admin.logging_config.get_log_file", lambda: log_file)
    monkeypatch.setattr("ts_admin.logging_config.get_log_dir", lambda: log_dir)

    zf = zipfile.ZipFile(io.BytesIO(client.get("/api/v1/diagnostics/bundle").content))
    lines = zf.read("app.log").decode().splitlines()
    assert len(lines) == diagnostics._TAIL_LINES + 1  # + the truncation notice
    assert "truncated" in lines[0]
    assert lines[-1] == "line 9999"

    # The newest rotation rides along, also tailed: app.log rotates at 5 MB, so
    # a failure from minutes ago can already have moved out of app.log.
    prev = zf.read("app.log.1").decode().splitlines()
    assert len(prev) == diagnostics._TAIL_LINES + 1
    assert prev[-1] == "old 9999"

    # Everything older stays out, and the bundle says so rather than going quiet.
    assert "app.log.2" not in zf.namelist()
    assert "app.log.2" in zf.read("omitted_logs.txt").decode()


def test_bundle_full_includes_rotated_logs(client, tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"
    log_file.write_text("\n".join(f"line {i}" for i in range(10_000)), encoding="utf-8")
    (log_dir / "app.log.1").write_text("old rotated log\n", encoding="utf-8")
    monkeypatch.setattr("ts_admin.logging_config.get_log_file", lambda: log_file)
    monkeypatch.setattr("ts_admin.logging_config.get_log_dir", lambda: log_dir)

    zf = zipfile.ZipFile(io.BytesIO(client.get("/api/v1/diagnostics/bundle?full=true").content))
    assert "app.log.1" in zf.namelist()
    assert len(zf.read("app.log").decode().splitlines()) == 10_000


def test_bundle_with_job_id_includes_specific_job(client, in_memory_db):
    _seed_cluster(in_memory_db)
    _seed_failed_job(in_memory_db, job_id="abc123")

    res = client.get("/api/v1/diagnostics/bundle?job_id=abc123")
    assert res.status_code == 200

    zf = zipfile.ZipFile(io.BytesIO(res.content))
    assert "job_abc123.json" in zf.namelist()

    payload = json.loads(zf.read("job_abc123.json"))
    assert payload["job"]["id"] == "abc123"
    assert payload["job"]["error_type"] == "TSAuthenticationError"
    assert isinstance(payload["archive_records"], list)


def test_bundle_with_unknown_job_id_returns_404(client, in_memory_db):
    _seed_cluster(in_memory_db)
    res = client.get("/api/v1/diagnostics/bundle?job_id=nope")
    assert res.status_code == 404


def test_bundle_does_not_leak_secret_field_names(client, in_memory_db):
    """Defensive: even if a future code change accidentally serializes a
    ClusterConfig field named password/token/secret, the bundle must not
    contain those substrings."""
    _seed_cluster(in_memory_db)
    _seed_failed_job(in_memory_db)

    res = client.get("/api/v1/diagnostics/bundle")
    zf = zipfile.ZipFile(io.BytesIO(res.content))
    # Only assert on dynamically-generated JSON. README.txt is a static
    # template that purposefully names what's *excluded*.
    for name in zf.namelist():
        if not name.endswith(".json"):
            continue
        content = zf.read(name).decode("utf-8", errors="replace").lower()
        for forbidden in ("password", "secret_key", '"token"', "bearer "):
            assert forbidden not in content, f"forbidden token {forbidden!r} found in bundle file {name}"
