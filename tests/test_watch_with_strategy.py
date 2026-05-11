"""Integration tests for the strategy hook in the watch cycle (M12)."""

from __future__ import annotations

import io
import json
import textwrap
from pathlib import Path

import pandas as pd
import pytest

from scripts import strategy as strategy_mod
from scripts import watch
from scripts.executor import SyntheticSwapExecutor
from scripts.market_data import MarketDataSource
from scripts.wallet_source import SyntheticWalletSource


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


def _write_strategy(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "strat.py"
    p.write_text(textwrap.dedent(body))
    return p


class _FakeMarketData:
    """In-memory market data that mimics MarketDataSource's snapshot shape."""

    def __init__(self, snapshot: dict[str, dict]):
        self._snapshot = snapshot

    def poll(self):
        return []

    def snapshot(self):
        return self._snapshot

    def stop(self):
        return []


def _wallet_source_with_usdc(amount: float = 1000.0) -> SyntheticWalletSource:
    """Synthesize a wallet that's just USDC (so PM has cash to spend)."""
    snap_path = FIXTURES / f"_test_wallet_usdc_only_{amount}.json"
    snap_path.write_text(json.dumps({
        "wallet_address": "test_wallet",
        "ts_utc": "2026-05-11T14:30:22Z",
        "tokens": [
            {
                "asset": "USDC",
                "chain": "solana",
                "address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "qty": amount,
                "mark_price_usd": 1.0,
                "value_usd": amount,
            }
        ],
    }))
    return SyntheticWalletSource(wallet_path=snap_path)


def _rules_with_universe() -> dict:
    return {
        "name": "test",
        "universe": [
            {"chain": "solana", "symbol": "WSOL",
             "address": "So11111111111111111111111111111111111111112"},
        ],
        "rules": [
            {
                "id": "halt-portfolio-dd",
                "type": "halt_on_drawdown",
                "threshold_pct": 50,  # high — won't fire in these tests
                "action": {"type": "liquidate_all"},
            },
        ],
    }


class TestStrategyEmitsBuy:
    def test_strategy_buy_captured_as_fill(self, tmp_path):
        strat_path = _write_strategy(tmp_path, """
            def decide(state, market_data):
                if state.get('cycle_index') == 0 and state.get('cash_usd', 0) > 0:
                    return [{'action': 'buy', 'asset': 'WSOL', 'amount_usd': 100.0}]
                return []
        """)
        strategy_invoc = strategy_mod.load(strat_path)
        md = _FakeMarketData({
            "WSOL": {"current": {"ts": "x", "o": 100, "h": 101, "l": 99, "c": 100,
                                 "vol": 1, "volUsd": 100},
                      "history": pd.DataFrame()},
        })

        sink = io.StringIO()
        summary = watch.run_monitor(
            rules_config=_rules_with_universe(),
            wallet_source=_wallet_source_with_usdc(1000),
            wallet_address="w1",
            interval_seconds=0,
            iterations=1,
            sink=sink,
            sleep_fn=lambda s: None,
            executor=SyntheticSwapExecutor(slippage_bps=0, fee_bps=0),
            max_loss_usd=10000,
            strategy=strategy_invoc,
            market_data=md,
        )

        assert summary["mode"] == "live"
        record = json.loads(sink.getvalue().splitlines()[0])
        # Strategy action recorded
        assert record["strategy"]["actions"][0]["asset"] == "WSOL"
        # Fill captured with source=strategy
        assert len(record["fills"]) == 1
        assert record["fills"][0]["source"] == "strategy"
        assert record["fills"][0]["action"] == "buy"
        assert record["fills"][0]["asset"] == "WSOL"

    def test_strategy_buy_updates_in_cycle_state(self, tmp_path):
        # After the buy, derived state should show WSOL position and reduced cash.
        strat_path = _write_strategy(tmp_path, """
            def decide(state, market_data):
                if state.get('cycle_index') == 0:
                    return [{'action': 'buy', 'asset': 'WSOL', 'amount_usd': 100.0}]
                return []
        """)
        strategy_invoc = strategy_mod.load(strat_path)
        md = _FakeMarketData({
            "WSOL": {"current": {"c": 100.0}, "history": pd.DataFrame()},
        })
        sink = io.StringIO()
        watch.run_monitor(
            rules_config=_rules_with_universe(),
            wallet_source=_wallet_source_with_usdc(1000),
            wallet_address="w1",
            interval_seconds=0,
            iterations=1,
            sink=sink,
            sleep_fn=lambda s: None,
            executor=SyntheticSwapExecutor(slippage_bps=0, fee_bps=0),
            max_loss_usd=10000,
            strategy=strategy_invoc,
            market_data=md,
        )
        record = json.loads(sink.getvalue().splitlines()[0])
        # Post-strategy position summary: WSOL is now visible to rule eval.
        assert record["positions"]["n_positions"] >= 1


class TestStrategyErrorCaptured:
    def test_strategy_exception_does_not_kill_cycle(self, tmp_path):
        strat_path = _write_strategy(tmp_path, """
            def decide(state, market_data):
                raise RuntimeError('boom')
        """)
        strategy_invoc = strategy_mod.load(strat_path)
        md = _FakeMarketData({})
        sink = io.StringIO()
        summary = watch.run_monitor(
            rules_config=_rules_with_universe(),
            wallet_source=_wallet_source_with_usdc(1000),
            wallet_address="w1",
            interval_seconds=0,
            iterations=2,
            sink=sink,
            sleep_fn=lambda s: None,
            executor=SyntheticSwapExecutor(),
            max_loss_usd=10000,
            strategy=strategy_invoc,
            market_data=md,
        )
        assert summary["iterations"] == 2
        records = [json.loads(l) for l in sink.getvalue().splitlines() if l.strip()]
        for r in records:
            warnings = r.get("strategy", {}).get("warnings", [])
            assert any(w["kind"] == "strategy_exception" for w in warnings)
            assert r["fills"] == []


class TestStrategyHold:
    def test_hold_action_is_a_noop(self, tmp_path):
        strat_path = _write_strategy(tmp_path, """
            def decide(state, market_data):
                return [{'action': 'hold'}]
        """)
        strategy_invoc = strategy_mod.load(strat_path)
        sink = io.StringIO()
        summary = watch.run_monitor(
            rules_config=_rules_with_universe(),
            wallet_source=_wallet_source_with_usdc(1000),
            wallet_address="w1",
            interval_seconds=0,
            iterations=1,
            sink=sink,
            sleep_fn=lambda s: None,
            executor=SyntheticSwapExecutor(),
            max_loss_usd=10000,
            strategy=strategy_invoc,
            market_data=_FakeMarketData({}),
        )
        record = json.loads(sink.getvalue().splitlines()[0])
        assert record["fills"] == []
        # Action still recorded for audit visibility
        assert record["strategy"]["actions"] == [{"action": "hold"}]


class TestStrategyWithoutExecutor:
    def test_monitor_mode_records_error_for_non_hold_action(self, tmp_path):
        strat_path = _write_strategy(tmp_path, """
            def decide(state, market_data):
                return [{'action': 'buy', 'asset': 'WSOL', 'amount_usd': 100}]
        """)
        strategy_invoc = strategy_mod.load(strat_path)
        sink = io.StringIO()
        watch.run_monitor(
            rules_config=_rules_with_universe(),
            wallet_source=_wallet_source_with_usdc(1000),
            wallet_address="w1",
            interval_seconds=0,
            iterations=1,
            sink=sink,
            sleep_fn=lambda s: None,
            executor=None,  # monitor mode
            strategy=strategy_invoc,
            market_data=_FakeMarketData({"WSOL": {"current": {"c": 100}, "history": pd.DataFrame()}}),
        )
        record = json.loads(sink.getvalue().splitlines()[0])
        errs = [e for e in record["errors"] if e["kind"] == "strategy_action_without_executor"]
        assert len(errs) == 1
        assert record["fills"] == []
