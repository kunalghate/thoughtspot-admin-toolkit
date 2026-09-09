"""
Integration tests for the archiver's export-without-delete flow.

Two surfaces:
  POST /api/v1/archiver/execute            with action="export"
  GET  /api/v1/archiver/export/{job}/download

The export exists so an admin can hold the TML backup before trusting the
export/delete round trip, so the tests here are mostly about the negative
guarantee (nothing is deleted) and about not handing one instance's TML to
another.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from ts_admin.models.archive_record import ArchiveRecord
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cluster import Cluster
from ts_admin.models.job import Job


@pytest.fixture(autouse=True)
def in_memory_db(monkeypatch):
    from sqlalchemy.pool import StaticPool

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
def client(in_memory_db):
    from ts_admin.main import create_app

    return TestClient(create_app())


@pytest.fixture
def seeded(in_memory_db):
    with Session(in_memory_db) as session:
        for cid, name in [("c1", "Prod"), ("c2", "Dev")]:
            session.add(
                Cluster(
                    id=cid,
                    name=name,
                    url=f"https://{cid}.thoughtspot.cloud",
                    username="admin",
                    auth_type="basic",
                )
            )
        session.add(
            CachedMetadata(
                cluster_id="c1",
                org_id=0,
                ts_guid="lb-1",
                name="Sales",
                object_type="LIVEBOARD",
                owner_guid="u1",
                owner_name="Alice",
                tag_names=json.dumps([]),
                synced_at=datetime.now(tz=timezone.utc),
            )
        )
        session.commit()


def _seed_export(engine, tmp_path, *, job_id: str, cluster_id: str, name: str = "Sales") -> None:
    """An export that already happened: one record with a TML file on disk."""
    path = tmp_path / f"{job_id}-{name}.tml"
    path.write_text(f"liveboard:\n  name: {name}\n", encoding="utf-8")
    with Session(engine) as session:
        session.add(
            ArchiveRecord(
                cluster_id=cluster_id,
                job_id=job_id,
                ts_guid=f"guid-{name}",
                name=name,
                object_type="LIVEBOARD",
                owner_guid="u1",
                owner_name="Alice",
                org_id=0,
                days_unused=0,
                tags="[]",
                tml_export_status="SUCCESS",
                tml_path=str(path),
                archived_at=datetime.now(tz=timezone.utc),
                # Exported, never deleted — the state this whole feature creates.
                deleted_confirmed_at=None,
            )
        )
        session.commit()


class TestExecuteAcceptsExport:
    def test_export_is_a_valid_action(self, client, seeded, monkeypatch):
        """202 + a job id, without touching a live cluster."""
        import ts_admin.services.archiver_service as svc

        called: dict = {}

        async def _fake_execute(**kwargs):
            called.update(kwargs)

        monkeypatch.setattr(svc, "execute", _fake_execute)

        r = client.post(
            "/api/v1/archiver/execute",
            json={"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1"], "action": "export"},
        )
        assert r.status_code == 202, r.text
        assert r.json()["action"] == "export"
        assert called["action"] == "export"

    def test_rejects_an_unknown_action(self, client, seeded):
        r = client.post(
            "/api/v1/archiver/execute",
            json={"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1"], "action": "obliterate"},
        )
        assert r.status_code == 422

    def test_rejects_an_empty_selection(self, client, seeded):
        r = client.post(
            "/api/v1/archiver/execute",
            json={"cluster_id": "c1", "org_id": 0, "object_ids": [], "action": "export"},
        )
        assert r.status_code == 422


class TestDownloadBundle:
    def test_zips_every_exported_file(self, client, in_memory_db, seeded, tmp_path):
        _seed_export(in_memory_db, tmp_path, job_id="job-1", cluster_id="c1", name="Sales")
        _seed_export(in_memory_db, tmp_path, job_id="job-1", cluster_id="c1", name="Revenue")

        r = client.get("/api/v1/archiver/export/job-1/download?cluster_id=c1")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/zip"
        assert "tml-export-job-1.zip" in r.headers["content-disposition"]

        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = sorted(zf.namelist())
            assert names == ["Revenue-guid-Revenue.tml", "Sales-guid-Sales.tml"]
            assert "name: Sales" in zf.read("Sales-guid-Sales.tml").decode()

    def test_404_for_an_unknown_job(self, client, seeded):
        r = client.get("/api/v1/archiver/export/nope/download?cluster_id=c1")
        assert r.status_code == 404

    def test_never_serves_another_clusters_tml(self, client, in_memory_db, seeded, tmp_path):
        """A job id from another instance must not hand over its TML."""
        _seed_export(in_memory_db, tmp_path, job_id="job-2", cluster_id="c2", name="Secret")

        r = client.get("/api/v1/archiver/export/job-2/download?cluster_id=c1")
        assert r.status_code == 404, "c1 must not be able to download c2's export"

        # ...and the owning cluster still can.
        assert client.get("/api/v1/archiver/export/job-2/download?cluster_id=c2").status_code == 200

    def test_404_when_the_files_are_gone_from_disk(self, client, in_memory_db, seeded, tmp_path):
        """A record pointing at a deleted file must not yield an empty zip."""
        _seed_export(in_memory_db, tmp_path, job_id="job-3", cluster_id="c1", name="Gone")
        (tmp_path / "job-3-Gone.tml").unlink()

        r = client.get("/api/v1/archiver/export/job-3/download?cluster_id=c1")
        assert r.status_code == 404

    def test_skips_failed_records(self, client, in_memory_db, seeded, tmp_path):
        _seed_export(in_memory_db, tmp_path, job_id="job-4", cluster_id="c1", name="Good")
        with Session(in_memory_db) as session:
            session.add(
                ArchiveRecord(
                    cluster_id="c1",
                    job_id="job-4",
                    ts_guid="guid-Bad",
                    name="Bad",
                    object_type="LIVEBOARD",
                    owner_guid="u1",
                    owner_name="Alice",
                    org_id=0,
                    days_unused=0,
                    tags="[]",
                    tml_export_status="FAILED",
                    tml_export_error="nope",
                    archived_at=datetime.now(tz=timezone.utc),
                )
            )
            session.commit()

        r = client.get("/api/v1/archiver/export/job-4/download?cluster_id=c1")
        assert r.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            assert zf.namelist() == ["Good-guid-Good.tml"]

    def test_a_failed_jobs_tml_bundle_is_still_downloadable(self, client, in_memory_db, seeded, tmp_path):
        """M14: a job whose terminal status is FAILED still exported real files.

        The bundle is built from ArchiveRecord rows, never from
        `job.get_result()` — a FAILED job stores no result at all
        (`job_service.mark_failed`), so gating the download on the job status
        would strand the very backups an admin needs after a failed run.
        """
        _seed_export(in_memory_db, tmp_path, job_id="job-5", cluster_id="c1", name="Alpha")
        _seed_export(in_memory_db, tmp_path, job_id="job-5", cluster_id="c1", name="Beta")
        with Session(in_memory_db) as session:
            job = Job(id="job-5", cluster_id="c1", job_type="archive", status="FAILED", error="boom")
            session.add(job)
            session.commit()
            assert session.get(Job, "job-5").get_result() is None

        r = client.get("/api/v1/archiver/export/job-5/download?cluster_id=c1")
        assert r.status_code == 200, r.text
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            assert sorted(zf.namelist()) == ["Alpha-guid-Alpha.tml", "Beta-guid-Beta.tml"]
