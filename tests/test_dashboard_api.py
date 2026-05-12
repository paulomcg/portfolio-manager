"""Tests for the dashboard's JSON endpoints against synthetic audit fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.dashboard import api

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("PM_STATE_DIR", str(state))
    monkeypatch.setenv("PM_AUDIT_PATH", str(state / "audit.jsonl"))
    monkeypatch.setenv("PM_SQLITE_PATH", str(state / "positions.sqlite"))
    monkeypatch.setenv("PM_ALERTS_LOG_PATH", str(state / "alerts.jsonl"))
    # Seed the audit log from the bundled fixture so all the readers have data.
    (state / "audit.jsonl").write_text((FIXTURES / "sample_audit.jsonl").read_text())
    yield state


class TestState:
    def test_returns_last_cycle(self):
        s = api.get_state()
        assert s["ok"] is True
        assert s["last_cycle"]["event"] == "watch.cycle"
        assert s["last_cycle"]["wallet"] == "test"

    def test_no_audit_returns_warning(self, tmp_path, monkeypatch):
        # Empty audit
        empty = tmp_path / "empty.jsonl"
        empty.write_text("")
        monkeypatch.setenv("PM_AUDIT_PATH", str(empty))
        s = api.get_state()
        assert s["last_cycle"] is None
        assert "warning" in s

    def test_wallet_filter(self):
        s = api.get_state(wallet="other")
        assert s["last_cycle"] is None
        s = api.get_state(wallet="test")
        assert s["last_cycle"] is not None


class TestAudit:
    def test_returns_rows_newest_first(self):
        a = api.get_audit(limit=10)
        ts = [r["ts_utc"] for r in a["rows"]]
        assert ts == sorted(ts, reverse=True)

    def test_limit(self):
        a = api.get_audit(limit=2)
        assert len(a["rows"]) == 2

    def test_event_filter(self):
        a = api.get_audit(event="watch.cycle", limit=100)
        assert all(r["event"] == "watch.cycle" for r in a["rows"])

    def test_wallet_filter(self):
        a = api.get_audit(wallet="other", limit=100)
        assert a["count"] == 0


class TestAlerts:
    def test_empty_when_no_alerts_db(self):
        out = api.get_alerts_pending()
        # Returns ok with empty list when sqlite is empty / not initialised.
        assert out["ok"] is True
        assert out["count"] == 0


class TestEquity:
    def test_returns_series(self):
        e = api.get_equity()
        assert e["ok"] is True
        assert e["count"] >= 1
        # Each point has the expected fields
        first = e["series"][0]
        assert "ts" in first and "equity_usd" in first and "drawdown_pct" in first

    def test_empty_when_no_data(self, tmp_path, monkeypatch):
        empty = tmp_path / "empty.jsonl"
        empty.write_text("")
        monkeypatch.setenv("PM_AUDIT_PATH", str(empty))
        e = api.get_equity()
        assert e["count"] == 0
        assert "warning" in e


class TestMetrics:
    def test_computes_metrics(self):
        m = api.get_metrics()
        assert m["ok"] is True
        assert m["metrics"]["bars"] >= 1


class TestSnapshot:
    def test_combined_payload(self):
        s = api.get_snapshot()
        assert s["ok"] is True
        assert s["state"]["ok"] is True
        assert s["audit"]["ok"] is True
        assert s["alerts_pending"]["ok"] is True
        assert s["metrics"]["ok"] is True
