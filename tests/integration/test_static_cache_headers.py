"""
Cache-Control policy on the served frontend (F12).

The contract: index.html (and every non-hashed file) is `no-cache`, so the
browser revalidates on every load and a pip upgrade shows the new UI without a
hard refresh. Content-hashed /_next/static/* assets are immutable and cached
for a year. Conditional requests still 304, and the 304 carries the policy too.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

CHUNK_PATH = "/_next/static/chunks/app-abc123.js"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Serve a minimal fake Next.js export so the test needs no `make build`."""
    static = tmp_path / "static"
    (static / "_next" / "static" / "chunks").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>ts-admin</title>")
    (static / "_next" / "static" / "chunks" / "app-abc123.js").write_text("console.log(1)")

    import ts_admin.main as main

    monkeypatch.setattr(main, "STATIC_DIR", static)
    return TestClient(main.create_app())


def test_index_html_must_revalidate(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_hashed_next_assets_are_immutable(client):
    response = client.get(CHUNK_PATH)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_conditional_index_request_gets_304_with_no_cache(client):
    first = client.get("/")
    second = client.get("/", headers={"if-none-match": first.headers["etag"]})
    assert second.status_code == 304
    assert second.headers["cache-control"] == "no-cache"
