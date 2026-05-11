"""Tests for the SwapExecutor abstractions."""

from __future__ import annotations

import pytest

from scripts.executor import SyntheticSwapExecutor


def _position(qty=30, mark=130, cost_basis=3000):
    return {
        "asset": "WSOL",
        "chain": "solana",
        "address": "addr",
        "qty": qty,
        "mark_price_usd": mark,
        "value_usd": qty * mark,
        "cost_basis_usd": cost_basis,
    }


class TestSyntheticExecutor:
    def test_trim_produces_fill_with_pro_rata_cost_basis(self):
        execu = SyntheticSwapExecutor(slippage_bps=50, fee_bps=30)
        decision = {
            "action": "trim",
            "asset": "WSOL",
            "qty": 10.0,
            "rule_id": "cap-40",
            "severity": "warn",
        }
        fill = execu.execute(decision, _position(qty=30, mark=130, cost_basis=3000))
        # Slippage 50bps on 130 = 0.65; fill = 129.35; gross = 1293.5; fee 30bps = 3.8805
        assert fill["ok"] is True
        assert fill["asset"] == "WSOL"
        assert fill["qty_swapped"] == 10.0
        assert fill["fill_price_usd"] == pytest.approx(129.35, abs=0.01)
        assert fill["gross_proceeds_usd"] == pytest.approx(1293.50, abs=0.01)
        assert fill["fees_usd"] == pytest.approx(3.88, abs=0.01)
        # Pro-rata cost basis: 3000 * (10/30) = 1000.
        # Realized PnL = (1293.50 - 3.88) - 1000 = 289.62
        assert fill["realized_pnl_usd"] == pytest.approx(289.62, abs=0.05)

    def test_exit_with_null_qty_uses_position_qty(self):
        execu = SyntheticSwapExecutor()
        decision = {"action": "exit", "asset": "WSOL", "qty": None,
                    "rule_id": "ts", "severity": "warn"}
        fill = execu.execute(decision, _position(qty=30))
        assert fill["qty_swapped"] == 30.0

    def test_halt_returns_no_op_fill(self):
        execu = SyntheticSwapExecutor()
        decision = {"action": "halt", "asset": None, "qty": None,
                    "rule_id": "halt-15", "severity": "critical"}
        fill = execu.execute(decision, _position())
        assert fill["action"] == "halt"
        assert fill["qty_swapped"] == 0.0
        assert fill["realized_pnl_usd"] == 0.0

    def test_realized_pnl_negative_when_below_cost_basis(self):
        # cost_basis 3000 @ 30 qty = $100 each. mark @ $50 → big loss.
        execu = SyntheticSwapExecutor(slippage_bps=0, fee_bps=0)
        decision = {"action": "exit", "asset": "WSOL", "qty": 30.0,
                    "rule_id": "ts", "severity": "warn"}
        fill = execu.execute(decision, _position(qty=30, mark=50, cost_basis=3000))
        # gross = 1500, fees=0, slippage=0, cost basis chunk = 3000 → realized = -1500
        assert fill["realized_pnl_usd"] == pytest.approx(-1500.0, abs=0.01)
