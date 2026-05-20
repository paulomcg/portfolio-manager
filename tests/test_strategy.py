"""Tests for the strategy loader + invocation wrapper."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from scripts import strategy
from scripts.strategy import StrategyError


@pytest.fixture
def strat_dir(tmp_path: Path) -> Path:
    return tmp_path


def _write(path: Path, body: str) -> Path:
    path.write_text(textwrap.dedent(body))
    return path


# ---------------------------------------------------------------------------
# load() — structural validation
# ---------------------------------------------------------------------------


class TestLoad:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(StrategyError, match="file not found"):
            strategy.load(tmp_path / "nope.py")

    def test_non_py_extension_raises(self, tmp_path):
        p = tmp_path / "x.txt"
        p.write_text("def decide(s, m): return []")
        with pytest.raises(StrategyError, match="must be a .py file"):
            strategy.load(p)

    def test_no_decide_function_raises(self, tmp_path):
        p = _write(tmp_path / "no_decide.py", """
            def something_else(s, m):
                return []
        """)
        with pytest.raises(StrategyError, match="no callable 'decide'"):
            strategy.load(p)

    def test_decide_with_three_required_args_rejected(self, tmp_path):
        p = _write(tmp_path / "too_many.py", """
            def decide(s, m, extra):
                return []
        """)
        with pytest.raises(StrategyError, match="3 required params"):
            strategy.load(p)

    def test_valid_strategy_loads(self, tmp_path):
        p = _write(tmp_path / "ok.py", """
            def decide(state, market_data):
                return [{'action': 'hold'}]
        """)
        invoc = strategy.load(p)
        assert invoc.module_name.startswith("_pm_strategy_")
        assert invoc.path == p.resolve()

    def test_kwargs_in_signature_tolerated(self, tmp_path):
        # **kwargs shouldn't count toward required param budget.
        p = _write(tmp_path / "kw.py", """
            def decide(state, market_data, **kwargs):
                return []
        """)
        strategy.load(p)  # should not raise

    def test_import_error_surfaces(self, tmp_path):
        p = _write(tmp_path / "boom.py", """
            raise RuntimeError("explode at import")
            def decide(s, m): return []
        """)
        with pytest.raises(StrategyError, match="import error"):
            strategy.load(p)


# ---------------------------------------------------------------------------
# invoke() — runtime validation
# ---------------------------------------------------------------------------


class TestInvoke:
    def _invocation(self, tmp_path, body: str):
        p = _write(tmp_path / "s.py", body)
        return strategy.load(p)

    def test_clean_buy_action(self, tmp_path):
        invoc = self._invocation(tmp_path, """
            def decide(state, market_data):
                return [{'action': 'buy', 'asset': 'WSOL', 'amount_usd': 100.0}]
        """)
        actions, warnings = invoc.invoke({}, {})
        assert len(actions) == 1
        assert actions[0]["asset"] == "WSOL"
        assert warnings == []

    def test_hold_action_passes(self, tmp_path):
        invoc = self._invocation(tmp_path, """
            def decide(state, market_data):
                return [{'action': 'hold'}]
        """)
        actions, warnings = invoc.invoke({}, {})
        assert actions == [{"action": "hold"}]
        assert warnings == []

    def test_none_return_treated_as_empty(self, tmp_path):
        invoc = self._invocation(tmp_path, """
            def decide(state, market_data):
                return None
        """)
        actions, warnings = invoc.invoke({}, {})
        assert actions == []
        assert warnings == []

    def test_non_list_return_is_warning(self, tmp_path):
        invoc = self._invocation(tmp_path, """
            def decide(state, market_data):
                return "buy WSOL"
        """)
        actions, warnings = invoc.invoke({}, {})
        assert actions == []
        assert len(warnings) == 1
        assert warnings[0]["kind"] == "strategy_bad_return"

    def test_exception_captured_as_warning(self, tmp_path):
        invoc = self._invocation(tmp_path, """
            def decide(state, market_data):
                raise ValueError("oops")
        """)
        actions, warnings = invoc.invoke({}, {})
        assert actions == []
        assert len(warnings) == 1
        assert warnings[0]["kind"] == "strategy_exception"
        assert "ValueError: oops" in warnings[0]["detail"]
        assert "traceback" in warnings[0]

    def test_unknown_action_filtered(self, tmp_path):
        invoc = self._invocation(tmp_path, """
            def decide(state, market_data):
                return [
                    {'action': 'buy', 'asset': 'WSOL', 'amount_usd': 100},
                    {'action': 'lunar_eclipse'},
                ]
        """)
        actions, warnings = invoc.invoke({}, {})
        assert len(actions) == 1
        assert actions[0]["asset"] == "WSOL"
        assert len(warnings) == 1
        assert warnings[0]["kind"] == "strategy_bad_action"

    def test_buy_without_amount_filtered(self, tmp_path):
        invoc = self._invocation(tmp_path, """
            def decide(state, market_data):
                return [{'action': 'buy', 'asset': 'WSOL'}]
        """)
        actions, warnings = invoc.invoke({}, {})
        assert actions == []
        assert len(warnings) == 1
        assert "exactly one of" in warnings[0]["detail"]

    def test_buy_with_both_qty_and_amount_filtered(self, tmp_path):
        invoc = self._invocation(tmp_path, """
            def decide(state, market_data):
                return [{'action': 'buy', 'asset': 'WSOL', 'qty': 1.0, 'amount_usd': 100.0}]
        """)
        actions, warnings = invoc.invoke({}, {})
        assert actions == []
        assert warnings[0]["kind"] == "strategy_bad_action"

    def test_sell_requires_qty_or_sell_all(self, tmp_path):
        invoc = self._invocation(tmp_path, """
            def decide(state, market_data):
                return [
                    {'action': 'sell', 'asset': 'WSOL'},
                    {'action': 'sell', 'asset': 'JTO', 'sell_all': True},
                    {'action': 'sell', 'asset': 'BONK', 'qty': 100.0},
                ]
        """)
        actions, warnings = invoc.invoke({}, {})
        # The first one is rejected; the other two pass.
        assert len(actions) == 2
        assert {a["asset"] for a in actions} == {"JTO", "BONK"}
        assert len(warnings) == 1

    def test_action_not_dict_filtered(self, tmp_path):
        invoc = self._invocation(tmp_path, """
            def decide(state, market_data):
                return [42, {'action': 'hold'}]
        """)
        actions, warnings = invoc.invoke({}, {})
        assert actions == [{"action": "hold"}]
        assert warnings[0]["kind"] == "strategy_bad_action"


# ---------------------------------------------------------------------------
# Smoke-test the bundled example strategies
# ---------------------------------------------------------------------------


class TestExampleStrategies:
    EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "strategies"

    def test_buy_and_hold_loads_and_acts_on_cycle_0(self):
        invoc = strategy.load(self.EXAMPLES / "buy_and_hold.py")
        actions, warnings = invoc.invoke(
            {"cycle_index": 0, "cash_usd": 1000.0, "positions": []},
            {},
        )
        assert warnings == []
        assert len(actions) == 1
        assert actions[0] == {
            "action": "buy", "asset": "WSOL", "amount_usd": 1000.0
        }

    def test_buy_and_hold_no_op_on_cycle_1(self):
        invoc = strategy.load(self.EXAMPLES / "buy_and_hold.py")
        actions, _ = invoc.invoke(
            {"cycle_index": 1, "cash_usd": 1000.0, "positions": []}, {}
        )
        assert actions == []

    def test_buy_and_hold_no_op_when_already_holding(self):
        invoc = strategy.load(self.EXAMPLES / "buy_and_hold.py")
        actions, _ = invoc.invoke(
            {
                "cycle_index": 0,
                "cash_usd": 0,
                "positions": [{"asset": "WSOL", "qty": 5.0, "value_usd": 650}],
            },
            {},
        )
        assert actions == []

    def test_weekly_dca_fires_on_multiples(self):
        invoc = strategy.load(self.EXAMPLES / "weekly_dca.py")
        # cycle 0, 7, 14 → fire; cycle 1..6 → no fire
        a0, _ = invoc.invoke(
            {"cycle_index": 0, "cash_usd": 1000.0, "positions": []}, {}
        )
        a3, _ = invoc.invoke(
            {"cycle_index": 3, "cash_usd": 1000.0, "positions": []}, {}
        )
        a7, _ = invoc.invoke(
            {"cycle_index": 7, "cash_usd": 1000.0, "positions": []}, {}
        )
        assert len(a0) == 1 and a0[0]["asset"] == "WSOL"
        assert a3 == []
        assert len(a7) == 1

    def test_momentum_threshold_cold_start_no_op(self):
        invoc = strategy.load(self.EXAMPLES / "momentum_threshold.py")
        actions, warnings = invoc.invoke(
            {"cycle_index": 0, "cash_usd": 1000.0, "positions": []},
            {"WSOL": {}},  # no history yet
        )
        assert actions == []
        assert warnings == []

    def test_momentum_threshold_fires_on_strong_uptrend(self):
        import pandas as pd

        invoc = strategy.load(self.EXAMPLES / "momentum_threshold.py")
        # Strategy uses LOOKBACK_BARS=5 and ENTRY_THRESHOLD=+5%; the
        # rolling return over the LAST 5 bars must clear +5%, not the
        # full series. Build a flat baseline then a +10% sprint over
        # the last 5 bars so rolling_return crosses the threshold.
        closes = [100.0] * 17 + [100.0, 102.0, 104.0, 106.0, 110.5]
        history = pd.DataFrame({"c": closes})
        actions, _ = invoc.invoke(
            {"cycle_index": 0, "cash_usd": 1000.0, "positions": []},
            {"WSOL": {"history": history}},
        )
        assert len(actions) == 1, f"expected 1 buy action, got {actions}"
        assert actions[0]["action"] == "buy"
