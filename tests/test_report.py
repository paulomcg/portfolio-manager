"""Tests for pm report — end-to-end against a fixture audit log."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from scripts import report

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("PM_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PM_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    yield tmp_path


class TestLoadCycles:
    def test_filters_to_watch_cycle_event(self, tmp_path):
        audit_path = FIXTURES / "sample_audit.jsonl"
        cycles = report.load_cycles(audit_path)
        assert len(cycles) == 6
        assert all(c["event"] == "watch.cycle" for c in cycles)

    def test_chronological_order(self, tmp_path):
        cycles = report.load_cycles(FIXTURES / "sample_audit.jsonl")
        ts = [c["ts_utc"] for c in cycles]
        assert ts == sorted(ts)

    def test_wallet_filter(self, tmp_path):
        cycles = report.load_cycles(FIXTURES / "sample_audit.jsonl", wallet="other")
        assert cycles == []
        cycles = report.load_cycles(FIXTURES / "sample_audit.jsonl", wallet="test")
        assert len(cycles) == 6

    def test_since_until(self, tmp_path):
        cycles = report.load_cycles(
            FIXTURES / "sample_audit.jsonl",
            since="2026-01-03T00:00:00+00:00",
            until="2026-01-05T00:00:00+00:00",
        )
        assert len(cycles) == 3


class TestBuildEquitySeries:
    def test_yields_one_per_cycle(self):
        cycles = report.load_cycles(FIXTURES / "sample_audit.jsonl")
        s = report.build_equity_series(cycles)
        assert len(s) == 6
        assert list(s.values) == [1000.0, 1010.0, 1020.0, 1015.0, 1030.0, 1040.0]


class TestCollectFills:
    def test_flattens_fills_across_cycles(self):
        cycles = report.load_cycles(FIXTURES / "sample_audit.jsonl")
        fills = report.collect_fills(cycles)
        assert len(fills) == 2
        assert {f["asset"] for f in fills} == {"WSOL", "JTO"}


class TestComputeMetrics:
    def test_metrics_against_fixture(self):
        cycles = report.load_cycles(FIXTURES / "sample_audit.jsonl")
        equity = report.build_equity_series(cycles)
        fills = report.collect_fills(cycles)
        m = report.compute_metrics(equity, fills)
        assert m["bars"] == 6
        assert m["total_return_pct"] == pytest.approx(4.0, abs=1e-6)
        # max drawdown: peak 1020 → trough 1015 = ~0.49%
        assert m["max_drawdown_pct"] == pytest.approx(0.4902, abs=0.01)
        # 1 winner ($10) + 1 loser (-$5) → win rate 50%, expectancy $2.50
        trades = m["trades"]
        assert trades["trades"] == 2
        assert trades["win_rate"] == 0.5
        assert trades["expectancy_usd"] == pytest.approx(2.5, abs=1e-4)
        # per-asset pnl
        assert m["per_asset_pnl_usd"] == {"WSOL": 10.0, "JTO": -5.0}


class TestRun:
    def test_writes_report_files(self, tmp_path):
        out_dir = tmp_path / "report"
        result = report.run(
            audit_path=FIXTURES / "sample_audit.jsonl",
            wallet="test",
            out=out_dir,
        )
        assert (out_dir / "report.json").exists()
        assert (out_dir / "report.md").exists()
        # Equity chart should also be written (6 bars > 1).
        assert (out_dir / "equity.png").exists()
        assert (out_dir / "equity.png").stat().st_size > 1000  # actual PNG bytes
        # Report dict reflects what was written.
        data = json.loads((out_dir / "report.json").read_text())
        assert data["wallet_filter"] == "test"
        assert data["cycle_count"] == 6
        assert data["metrics"]["bars"] == 6
        assert "equity_curve" in data
        assert len(data["equity_curve"]) == 6

    def test_no_cycles_still_writes_report(self, tmp_path):
        # Empty audit path → 0 cycles → report still written with warning.
        empty = tmp_path / "empty.jsonl"
        empty.write_text("")
        out_dir = tmp_path / "report"
        result = report.run(audit_path=empty, out=out_dir)
        assert (out_dir / "report.json").exists()
        assert result["cycle_count"] == 0
        # No equity.png when too few bars.
        assert not (out_dir / "equity.png").exists()
        # Warning surfaced
        assert "warning" in result["metrics"]


class TestDeterminism:
    def test_byte_identical_modulo_generation_timestamp(self, tmp_path):
        out_a = tmp_path / "a"
        out_b = tmp_path / "b"
        report.run(audit_path=FIXTURES / "sample_audit.jsonl", out=out_a)
        report.run(audit_path=FIXTURES / "sample_audit.jsonl", out=out_b)
        # report.json differs only in `generated_at_utc`. Strip it and compare.
        a = json.loads((out_a / "report.json").read_text())
        b = json.loads((out_b / "report.json").read_text())
        a.pop("generated_at_utc")
        b.pop("generated_at_utc")
        assert a == b
