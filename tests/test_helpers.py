"""Tests for the strategy helpers module."""

from __future__ import annotations

import pandas as pd
import pytest

from scripts import helpers


class TestEveryNBars:
    def test_fires_on_multiples(self):
        assert helpers.every_n_bars(0, 7)
        assert helpers.every_n_bars(7, 7)
        assert helpers.every_n_bars(14, 7)

    def test_does_not_fire_off_multiples(self):
        assert not helpers.every_n_bars(1, 7)
        assert not helpers.every_n_bars(13, 7)

    def test_rejects_non_positive_n(self):
        with pytest.raises(ValueError):
            helpers.every_n_bars(5, 0)
        with pytest.raises(ValueError):
            helpers.every_n_bars(5, -1)


class TestCalendarAligned:
    def test_iso_z_form(self):
        # 2026-05-11 is a Monday.
        assert helpers.calendar_aligned("2026-05-11T00:00:00Z", "mon")

    def test_iso_offset_form(self):
        assert helpers.calendar_aligned("2026-05-11T00:00:00+00:00", "monday")

    def test_does_not_fire_on_wrong_day(self):
        assert not helpers.calendar_aligned("2026-05-11T00:00:00Z", "fri")

    def test_rejects_unknown_weekday(self):
        with pytest.raises(ValueError):
            helpers.calendar_aligned("2026-05-11T00:00:00Z", "blursday")


class TestRollingReturn:
    def test_dataframe_input(self):
        df = pd.DataFrame({"c": [100, 110, 120, 121]})
        # lookback=3 → first=100, last=121 → 0.21
        assert helpers.rolling_return(df, 3) == pytest.approx(0.21, abs=1e-9)

    def test_list_of_dicts_input(self):
        bars = [{"c": 100}, {"c": 110}, {"c": 120}, {"c": 121}]
        assert helpers.rolling_return(bars, 3) == pytest.approx(0.21, abs=1e-9)

    def test_insufficient_history_returns_none(self):
        df = pd.DataFrame({"c": [100, 110]})
        assert helpers.rolling_return(df, 3) is None

    def test_zero_first_close_returns_none(self):
        df = pd.DataFrame({"c": [0, 1, 2, 3]})
        assert helpers.rolling_return(df, 3) is None


class TestPositionsHelpers:
    def _state(self) -> dict:
        return {
            "total_equity_usd": 5400.0,
            "cash_usd": 200.0,
            "positions": [
                {"asset": "WSOL", "qty": 30, "value_usd": 3900},
                {"asset": "JTO", "qty": 400, "value_usd": 1300},
            ],
        }

    def test_has_position_true(self):
        assert helpers.has_position(self._state(), "WSOL")

    def test_has_position_false_when_zero_qty(self):
        s = self._state()
        s["positions"][0]["qty"] = 0
        assert not helpers.has_position(s, "WSOL")

    def test_has_position_false_unknown_asset(self):
        assert not helpers.has_position(self._state(), "BONK")

    def test_position_pct_of_equity(self):
        s = self._state()
        # WSOL: 3900 / 5400 = 72.222%
        assert helpers.position_pct_of_equity(s, "WSOL") == pytest.approx(72.222, abs=0.01)
        # JTO: 1300 / 5400 = 24.07%
        assert helpers.position_pct_of_equity(s, "JTO") == pytest.approx(24.074, abs=0.01)
        # absent → 0
        assert helpers.position_pct_of_equity(s, "BONK") == 0.0

    def test_position_pct_of_equity_zero_equity(self):
        assert helpers.position_pct_of_equity({"total_equity_usd": 0, "positions": []}, "X") == 0.0

    def test_cash_pct_of_equity(self):
        s = self._state()
        # 200 / 5400 = 3.703%
        assert helpers.cash_pct_of_equity(s) == pytest.approx(3.703, abs=0.01)

    def test_get_position(self):
        s = self._state()
        assert helpers.get_position(s, "WSOL")["qty"] == 30
        assert helpers.get_position(s, "BONK") is None
