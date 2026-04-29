"""
Smoke tests for ts_admin.logging_config — file handler attaches and writes.
"""

from __future__ import annotations

import logging

import ts_admin.logging_config as lc


def test_setup_logging_writes_to_log_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    # Reset the idempotency flag and detach prior handlers so this test is
    # hermetic regardless of what the rest of the suite did.
    monkeypatch.setattr(lc, "_already_configured", False)
    root = logging.getLogger()
    prior = list(root.handlers)
    for h in prior:
        root.removeHandler(h)
    try:
        lc.setup_logging()

        log_file = lc.get_log_file()
        assert log_file.parent == tmp_path
        assert log_file.exists()

        logging.getLogger("test.logging").info("hello-from-test-marker")
        # Force flush so the line is on disk.
        for h in root.handlers:
            h.flush()

        content = log_file.read_text(encoding="utf-8")
        assert "hello-from-test-marker" in content
    finally:
        # Restore prior handlers
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in prior:
            root.addHandler(h)


def test_setup_logging_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.setattr(lc, "_already_configured", False)
    root = logging.getLogger()
    prior = list(root.handlers)
    for h in prior:
        root.removeHandler(h)
    try:
        lc.setup_logging()
        n1 = len(root.handlers)
        lc.setup_logging()
        n2 = len(root.handlers)
        assert n1 == n2
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in prior:
            root.addHandler(h)
