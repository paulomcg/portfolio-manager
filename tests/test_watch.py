"""Tests for the watch loop (monitor mode)."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from scripts import alerts, audit, positions, watch
from scripts.wallet_source import SyntheticWalletSource, WalletSourceError

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    s = tmp_path / "state"
    s.mkdir()
    monkeypatch.setenv("PM_STATE_DIR", str(s))
    monkeypatch.setenv("PM_SQLITE_PATH", str(s / "positions.sqlite"))
    monkeypatch.setenv("PM_AUDIT_PATH", str(s / "audit.jsonl"))
    monkeypatch.setenv("PM_ALERTS_LOG_PATH", str(s / "alerts.jsonl"))
    yield s


def _rules() -> dict:
    return {
        "name": "test",
        "rules": [
            {
                "id": "cap-40",
                "type": "max_position_pct",
                "threshold_pct": 40,
                "action": {"type": "trim_to", "target_pct": 40},
            },
            {
                "id": "halt-15",
                "type": "halt_on_drawdown",
                "threshold_pct": 15,
                "action": {"type": "liquidate_all"},
            },
        ],
    }


def _sleeps_none(seconds):
    """Sleep stub used to keep tests deterministic."""
    return None


class TestMonitorLoop:
    def test_three_iterations_emit_three_cycles(self):
        src = SyntheticWalletSource(
            wallet_path=FIXTURES / "wallet_snapshot.json",
            pnl_path=FIXTURES / "pnl_snapshot.json",
        )
        sink = io.StringIO()
        summary = watch.run_monitor(
            rules_config=_rules(),
            wallet_source=src,
            wallet_address="w1",
            interval_seconds=0,
            iterations=3,
            sink=sink,
            sleep_fn=_sleeps_none,
        )
        assert summary["ok"] is True
        assert summary["iterations"] == 3
        lines = [ln for ln in sink.getvalue().splitlines() if ln.strip()]
        assert len(lines) == 3
        # Each line is a valid cycle record
        records = [json.loads(ln) for ln in lines]
        assert {r["cycle_index"] for r in records} == {0, 1, 2}
        for r in records:
            assert r["mode"] == "monitor"
            assert r["wallet"] == "w1"
            assert r["errors"] == []

    def test_rule_firing_emits_alerts(self):
        # WSOL is 72% of equity in the synthetic fixture → cap-40 fires every cycle.
        src = SyntheticWalletSource(
            wallet_path=FIXTURES / "wallet_snapshot.json",
            pnl_path=FIXTURES / "pnl_snapshot.json",
        )
        sink = io.StringIO()
        summary = watch.run_monitor(
            rules_config=_rules(),
            wallet_source=src,
            wallet_address="w1",
            interval_seconds=0,
            iterations=2,
            sink=sink,
            sleep_fn=_sleeps_none,
        )
        # 2 cycles × 1 firing each = 2 alerts
        assert summary["alerts_emitted"] == 2
        pending = alerts.pending("w1")
        assert len(pending) == 2
        assert all(p["rule_id"] == "cap-40" for p in pending)

    def test_audit_rows_recorded_per_cycle(self):
        src = SyntheticWalletSource(
            wallet_path=FIXTURES / "wallet_snapshot.json",
            pnl_path=FIXTURES / "pnl_snapshot.json",
        )
        watch.run_monitor(
            rules_config=_rules(),
            wallet_source=src,
            wallet_address="w1",
            interval_seconds=0,
            iterations=4,
            sink=io.StringIO(),
            sleep_fn=_sleeps_none,
        )
        rows = audit.read()
        # Newest first: 4 watch.cycle rows
        watch_rows = [r for r in rows if r.get("event") == "watch.cycle"]
        assert len(watch_rows) == 4

    def test_hwm_persisted_across_cycles(self):
        src = SyntheticWalletSource(
            wallet_path=FIXTURES / "wallet_snapshot.json",
            pnl_path=FIXTURES / "pnl_snapshot.json",
        )
        watch.run_monitor(
            rules_config=_rules(),
            wallet_source=src,
            wallet_address="w1",
            interval_seconds=0,
            iterations=2,
            sink=io.StringIO(),
            sleep_fn=_sleeps_none,
        )
        hwms = positions.load_hwm_state("w1")
        assert "WSOL" in hwms
        assert "<PORTFOLIO>" in hwms
        assert hwms["WSOL"] == 3900.0  # value from fixture
        assert hwms["<PORTFOLIO>"] == 5400.0

    def test_wallet_source_error_recorded_as_cycle_error(self, tmp_path):
        src = SyntheticWalletSource(wallet_path=tmp_path / "missing.json")
        sink = io.StringIO()
        summary = watch.run_monitor(
            rules_config=_rules(),
            wallet_source=src,
            wallet_address="w1",
            interval_seconds=0,
            iterations=2,
            sink=sink,
            sleep_fn=_sleeps_none,
        )
        # Loop kept running; recorded errors per cycle.
        assert summary["iterations"] == 2
        records = [json.loads(l) for l in sink.getvalue().splitlines() if l.strip()]
        for r in records:
            assert len(r["errors"]) == 1
            assert r["errors"][0]["kind"] == "wallet_source_error"


class TestSyntheticWalletSource:
    def test_loads_wallet_and_pnl(self):
        src = SyntheticWalletSource(
            wallet_path=FIXTURES / "wallet_snapshot.json",
            pnl_path=FIXTURES / "pnl_snapshot.json",
        )
        wallet, pnl = src.fetch()
        assert wallet["wallet_address"] == "test_wallet"
        # ts_utc is refreshed on each fetch
        assert "T" in wallet["ts_utc"]
        # pnl keys are chain:address
        assert any(k.startswith("solana:") for k in pnl)

    def test_missing_wallet_raises(self, tmp_path):
        src = SyntheticWalletSource(wallet_path=tmp_path / "nope.json")
        with pytest.raises(WalletSourceError):
            src.fetch()

    def test_invalid_json_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json")
        src = SyntheticWalletSource(wallet_path=p)
        with pytest.raises(WalletSourceError):
            src.fetch()
