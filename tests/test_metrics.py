"""Unit tests for metrics.py — hand-computed truth against synthetic equity curves."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from scripts import metrics


@pytest.fixture
def equity_climb() -> pd.Series:
    """Monotonically climbing equity — no drawdown."""
    return pd.Series(
        [1000.0, 1010.0, 1020.0, 1030.0, 1050.0, 1080.0],
        index=pd.date_range("2026-01-01", periods=6, freq="D"),
        name="equity_usd",
    )


@pytest.fixture
def equity_with_drawdown() -> pd.Series:
    """A clear 10% drawdown from 1100 → 990."""
    return pd.Series(
        [1000.0, 1050.0, 1100.0, 1050.0, 990.0, 1020.0],
        index=pd.date_range("2026-01-01", periods=6, freq="D"),
        name="equity_usd",
    )


class TestTotalReturn:
    def test_simple(self, equity_climb):
        # 1080/1000 - 1 = 0.08 → 8%
        assert metrics.total_return_pct(equity_climb) == pytest.approx(8.0)

    def test_too_few_points(self):
        s = pd.Series([1000.0])
        assert metrics.total_return_pct(s) == 0.0

    def test_zero_initial(self):
        s = pd.Series([0.0, 100.0, 200.0])
        assert metrics.total_return_pct(s) == 0.0


class TestCagr:
    def test_one_year_climb(self):
        # 1000 → 1100 over 365 days → 10% CAGR
        s = pd.Series([1000.0, 1100.0],
                      index=pd.date_range("2026-01-01", periods=2, freq="D"))
        # 1 day over 365 periods/year = 1/365 year → (1.1)^365 enormous
        # So use periods_per_year=1 to mean "the whole span is 1 year"
        result = metrics.cagr_pct(s, periods_per_year=1)
        assert result == pytest.approx(10.0, abs=1e-6)

    def test_zero_periods(self):
        s = pd.Series([1000.0])
        assert metrics.cagr_pct(s) == 0.0


class TestMaxDrawdown:
    def test_climb_no_drawdown(self, equity_climb):
        dd = metrics.max_drawdown(equity_climb)
        assert dd["pct"] == 0.0

    def test_drawdown_correctly_identified(self, equity_with_drawdown):
        dd = metrics.max_drawdown(equity_with_drawdown)
        # Peak 1100 → trough 990 → -110/1100 = -10%
        assert dd["pct"] == pytest.approx(10.0, abs=1e-6)
        assert dd["peak_value"] == 1100.0
        assert dd["trough_value"] == 990.0

    def test_empty_series(self):
        dd = metrics.max_drawdown(pd.Series([], dtype=float))
        assert dd["pct"] == 0.0


class TestSharpe:
    def test_zero_variance_returns_zero(self):
        # All zero returns → std=0 → return 0
        s = pd.Series([1000.0, 1000.0, 1000.0, 1000.0])
        assert metrics.sharpe(s) == 0.0

    def test_hand_computed_value(self):
        # Returns: [0.01, 0.01, 0.01] → mean=0.01, std=0 → sharpe=0 (no variance)
        # Returns: [0.01, 0.02, 0.005, 0.015] → mean ≈ 0.0125, std ≈ 0.0064
        s = pd.Series([1000.0, 1010.0, 1030.2, 1035.351, 1050.881])
        # Test against numpy-computed truth so the test is robust.
        rets = s.pct_change().dropna()
        expected = (rets.mean() / rets.std(ddof=1)) * math.sqrt(365)
        assert metrics.sharpe(s, periods_per_year=365) == pytest.approx(expected, abs=1e-9)

    def test_too_few_points(self):
        assert metrics.sharpe(pd.Series([1000.0])) == 0.0


class TestSortino:
    def test_no_negatives_returns_zero(self):
        # All positive returns → no downside → return 0
        s = pd.Series([1000.0, 1010.0, 1020.0, 1030.0, 1040.0])
        assert metrics.sortino(s) == 0.0

    def test_with_negatives(self):
        # Mix of pos/neg returns; just sanity-check it's positive for upward bias.
        s = pd.Series([1000.0, 1050.0, 1020.0, 1080.0, 1040.0, 1100.0])
        # Returns include both positive and negative; with overall up-bias, sortino > 0.
        assert metrics.sortino(s) > 0.0


class TestCalmar:
    def test_zero_drawdown(self, equity_climb):
        assert metrics.calmar(equity_climb) == 0.0  # no DD → 0 by convention

    def test_with_drawdown(self, equity_with_drawdown):
        # CAGR for this short series exists; calmar = cagr / max_dd
        c = metrics.calmar(equity_with_drawdown)
        assert isinstance(c, float)


class TestTradeStats:
    def test_no_fills(self):
        s = metrics.trade_stats([])
        assert s["trades"] == 0

    def test_only_buys_not_counted(self):
        s = metrics.trade_stats([
            {"action": "buy", "asset": "WSOL", "realized_pnl_usd": 0},
        ])
        assert s["trades"] == 0

    def test_winners_and_losers(self):
        s = metrics.trade_stats([
            {"action": "sell", "asset": "A", "realized_pnl_usd": 10.0},
            {"action": "sell", "asset": "B", "realized_pnl_usd": -5.0},
            {"action": "exit", "asset": "C", "realized_pnl_usd": 20.0},
            {"action": "trim", "asset": "D", "realized_pnl_usd": -2.0},
        ])
        assert s["trades"] == 4
        assert s["winners"] == 2
        assert s["losers"] == 2
        assert s["win_rate"] == 0.5
        assert s["expectancy_usd"] == pytest.approx(5.75, abs=1e-4)
        assert s["total_pnl_usd"] == pytest.approx(23.0)


class TestPerAssetPnl:
    def test_aggregates(self):
        out = metrics.per_asset_pnl([
            {"action": "sell", "asset": "WSOL", "realized_pnl_usd": 10.0},
            {"action": "sell", "asset": "WSOL", "realized_pnl_usd": -3.0},
            {"action": "sell", "asset": "JTO", "realized_pnl_usd": 7.5},
            {"action": "buy", "asset": "WSOL", "realized_pnl_usd": 0.0},  # excluded
        ])
        assert out == {"WSOL": 7.0, "JTO": 7.5}


class TestInferPeriodsPerYear:
    def test_daily_bars(self):
        idx = pd.date_range("2026-01-01", periods=10, freq="D")
        assert metrics.infer_periods_per_year(idx) == 365

    def test_hourly_bars(self):
        idx = pd.date_range("2026-01-01", periods=10, freq="h")
        # 365 * 24 = 8760
        assert metrics.infer_periods_per_year(idx) == 8760

    def test_empty_falls_back_to_default(self):
        assert metrics.infer_periods_per_year(pd.DatetimeIndex([])) == 365
