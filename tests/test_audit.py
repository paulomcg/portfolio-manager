"""Tests for the append-only JSONL audit log."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import audit, config


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("PM_STATE_DIR", str(state))
    monkeypatch.setenv("PM_AUDIT_PATH", str(state / "audit.jsonl"))
    yield state


class TestAppendAndRead:
    def test_append_writes_one_line(self):
        audit.append({"event": "position.add", "asset": "WSOL"})
        text = Path(config.audit_path()).read_text()
        assert text.count("\n") == 1
        row = json.loads(text.strip())
        assert row["event"] == "position.add"
        assert "ts_utc" in row

    def test_read_returns_newest_first(self):
        for i in range(3):
            audit.append({"event": f"e{i}"})
        rows = audit.read()
        assert [r["event"] for r in rows] == ["e2", "e1", "e0"]

    def test_read_limit_caps_results(self):
        for i in range(10):
            audit.append({"event": f"e{i}"})
        rows = audit.read(limit=3)
        assert len(rows) == 3

    def test_read_since_filters_by_timestamp(self):
        audit.append({"event": "old", "ts_utc": "2025-01-01T00:00:00Z"})
        audit.append({"event": "new", "ts_utc": "2026-12-31T00:00:00Z"})
        rows = audit.read(since="2026-01-01T00:00:00Z")
        assert len(rows) == 1
        assert rows[0]["event"] == "new"

    def test_read_no_file_returns_empty(self):
        # Fresh isolated state; nothing written yet.
        assert audit.read() == []

    def test_corrupt_line_skipped(self):
        p = Path(config.audit_path())
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"event":"good"}\nnot-json\n{"event":"good2"}\n')
        rows = audit.read()
        events = [r["event"] for r in rows]
        assert events == ["good2", "good"]

    def test_ts_utc_auto_filled_when_missing(self):
        audit.append({"event": "x"})
        row = json.loads(Path(config.audit_path()).read_text().strip())
        # Just check it's a non-empty string
        assert isinstance(row["ts_utc"], str) and len(row["ts_utc"]) > 0
