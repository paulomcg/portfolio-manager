"""Tests for the alerts queue (sqlite-backed) + JSONL mirror."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import alerts, config, positions


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("PM_STATE_DIR", str(state))
    monkeypatch.setenv("PM_SQLITE_PATH", str(state / "positions.sqlite"))
    monkeypatch.setenv("PM_AUDIT_PATH", str(state / "audit.jsonl"))
    monkeypatch.setenv("PM_ALERTS_LOG_PATH", str(state / "alerts.jsonl"))
    yield state


def _decision(rule_id="r", severity="warn", asset="WSOL", action="trim", qty=1.0):
    return {
        "action": action,
        "asset": asset,
        "qty": qty,
        "quote_usd_est": 100.0,
        "reason": "test",
        "rule_id": rule_id,
        "severity": severity,
    }


class TestEmitAndPending:
    def test_emit_returns_id_and_appears_in_pending(self):
        aid = alerts.emit("w1", _decision(rule_id="halt-15"))
        assert isinstance(aid, str) and len(aid) > 0
        rows = alerts.pending("w1")
        assert len(rows) == 1
        assert rows[0]["alert_id"] == aid
        assert rows[0]["rule_id"] == "halt-15"
        assert rows[0]["acked"] is False
        assert rows[0]["decision"]["asset"] == "WSOL"

    def test_pending_filters_by_severity(self):
        alerts.emit("w1", _decision(rule_id="r1", severity="warn"))
        alerts.emit("w1", _decision(rule_id="r2", severity="critical"))
        crit = alerts.pending("w1", severity="critical")
        assert len(crit) == 1
        assert crit[0]["rule_id"] == "r2"

    def test_pending_filters_by_wallet(self):
        alerts.emit("w1", _decision(rule_id="r1"))
        alerts.emit("w2", _decision(rule_id="r2"))
        rows = alerts.pending("w1")
        assert {r["rule_id"] for r in rows} == {"r1"}

    def test_pending_no_filter_returns_all_wallets(self):
        alerts.emit("w1", _decision(rule_id="r1"))
        alerts.emit("w2", _decision(rule_id="r2"))
        rows = alerts.pending()
        assert len(rows) == 2

    def test_pending_sorted_newest_first(self):
        ids = []
        for i in range(3):
            ids.append(alerts.emit("w1", _decision(rule_id=f"r{i}")))
        rows = alerts.pending("w1")
        # Newest first: r2, r1, r0
        assert [r["rule_id"] for r in rows] == ["r2", "r1", "r0"]


class TestAck:
    def test_ack_marks_alerts_read(self):
        a1 = alerts.emit("w1", _decision(rule_id="r1"))
        a2 = alerts.emit("w1", _decision(rule_id="r2"))
        result = alerts.ack([a1, a2])
        assert result == {"acked": 2, "not_found": 0}
        assert alerts.pending("w1") == []

    def test_ack_idempotent_for_already_acked(self):
        a1 = alerts.emit("w1", _decision(rule_id="r1"))
        alerts.ack([a1])
        result = alerts.ack([a1])
        # Already acked → 0 new acks, 0 not_found (the id exists)
        assert result == {"acked": 0, "not_found": 0}

    def test_ack_unknown_id_reports_not_found(self):
        result = alerts.ack(["bogus-id-1234"])
        assert result == {"acked": 0, "not_found": 1}

    def test_ack_partial(self):
        a1 = alerts.emit("w1", _decision(rule_id="r1"))
        result = alerts.ack([a1, "bogus"])
        assert result == {"acked": 1, "not_found": 1}


class TestHistory:
    def test_history_includes_acked(self):
        a1 = alerts.emit("w1", _decision(rule_id="r1"))
        a2 = alerts.emit("w1", _decision(rule_id="r2"))
        alerts.ack([a1])
        rows = alerts.history("w1")
        assert len(rows) == 2
        acked = {r["rule_id"]: r["acked"] for r in rows}
        assert acked == {"r1": True, "r2": False}


class TestJsonlMirror:
    def test_emit_appends_to_alerts_log(self):
        alerts.emit("w1", _decision(rule_id="r1"))
        alerts.emit("w1", _decision(rule_id="r2"))
        log = Path(config.alerts_log_path())
        assert log.exists()
        lines = log.read_text().strip().splitlines()
        assert len(lines) == 2
        for ln in lines:
            payload = json.loads(ln)
            assert "alert_id" in payload
            assert payload["decision"]["asset"] == "WSOL"
