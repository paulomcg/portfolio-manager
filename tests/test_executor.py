"""Tests for the SwapExecutor abstractions."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from scripts.executor import OnchainosSwapExecutor, SwapExecutorError, SyntheticSwapExecutor


def _completed(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["onchainos"], returncode=returncode, stdout=stdout, stderr=stderr
    )


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


# ---------------------------------------------------------------------------
# Buy action (M11 — v0.2.0)
# ---------------------------------------------------------------------------


class TestSyntheticBuy:
    def test_buy_amount_usd_with_bar(self):
        execu = SyntheticSwapExecutor(slippage_bps=50, fee_bps=30)
        # bar.c = 100, slippage 50bps → fill_price = 100.5
        # amount_usd = 100 → qty = 100 / 100.5 ≈ 0.99502
        # fee = 100 * 30bps = 0.30
        # slippage_usd = qty * 0.5 ≈ 0.4975
        bar = {"c": 100.0, "vol": 50.0, "volUsd": 5050.0}
        decision = {"action": "buy", "asset": "WSOL", "amount_usd": 100.0}
        fill = execu.execute(decision, position=None, bar=bar)
        assert fill["action"] == "buy"
        assert fill["asset"] == "WSOL"
        assert fill["fill_price_usd"] == pytest.approx(100.5, abs=1e-6)
        assert fill["qty_swapped"] == pytest.approx(0.99502, abs=1e-4)
        assert fill["fees_usd"] == pytest.approx(0.30, abs=1e-4)
        assert fill["gross_proceeds_usd"] == pytest.approx(-100.0, abs=0.01)
        assert fill["realized_pnl_usd"] == 0.0  # opens never realize PnL

    def test_buy_qty_with_bar(self):
        execu = SyntheticSwapExecutor(slippage_bps=50, fee_bps=30)
        bar = {"c": 100.0}
        decision = {"action": "buy", "asset": "WSOL", "qty": 0.5}
        fill = execu.execute(decision, position=None, bar=bar)
        # qty fixed, gross_cost = 0.5 * 100.5 = 50.25, fees = 0.15
        assert fill["qty_swapped"] == 0.5
        assert fill["fill_price_usd"] == pytest.approx(100.5, abs=1e-6)
        assert fill["gross_proceeds_usd"] == pytest.approx(-50.25, abs=1e-4)
        # fill fees rounded to 2 decimals in the Fill dict
        assert fill["fees_usd"] == pytest.approx(0.15, abs=1e-4)

    def test_buy_slippage_direction_higher_than_mark(self):
        # Sells fill BELOW mark; buys fill ABOVE mark. Sanity check both.
        execu = SyntheticSwapExecutor(slippage_bps=100, fee_bps=0)
        bar = {"c": 200.0}
        buy = execu.execute(
            {"action": "buy", "asset": "X", "qty": 1.0}, position=None, bar=bar
        )
        sell = execu.execute(
            {"action": "exit", "asset": "X", "qty": 1.0},
            position={"asset": "X", "qty": 1.0, "mark_price_usd": 200.0,
                      "cost_basis_usd": 200.0},
        )
        assert buy["fill_price_usd"] > 200.0
        assert sell["fill_price_usd"] < 200.0

    def test_buy_without_bar_uses_existing_position_mark(self):
        execu = SyntheticSwapExecutor(slippage_bps=0, fee_bps=0)
        # Adding to an existing position; bar is None but position has mark.
        existing = {"asset": "WSOL", "qty": 5, "mark_price_usd": 100.0,
                    "cost_basis_usd": 500.0}
        decision = {"action": "buy", "asset": "WSOL", "amount_usd": 100.0}
        fill = execu.execute(decision, position=existing, bar=None)
        assert fill["fill_price_usd"] == 100.0
        assert fill["qty_swapped"] == pytest.approx(1.0, abs=1e-6)

    def test_buy_without_mark_anywhere_raises(self):
        execu = SyntheticSwapExecutor()
        with pytest.raises(SwapExecutorError, match="no mark price"):
            execu.execute(
                {"action": "buy", "asset": "WSOL", "amount_usd": 100.0},
                position=None, bar=None,
            )

    def test_buy_missing_size_raises(self):
        execu = SyntheticSwapExecutor()
        with pytest.raises(SwapExecutorError, match="needs qty or amount_usd"):
            execu.execute(
                {"action": "buy", "asset": "WSOL"},
                position=None, bar={"c": 100.0},
            )


class TestSellWithoutPositionFails:
    def test_sell_with_no_position_raises(self):
        execu = SyntheticSwapExecutor()
        with pytest.raises(SwapExecutorError, match="no position"):
            execu.execute(
                {"action": "sell", "asset": "WSOL", "qty": 1.0},
                position=None,
            )


# ---------------------------------------------------------------------------
# OnchainosSwapExecutor — argv shape (subprocess mocked)
# ---------------------------------------------------------------------------


class TestOnchainosBuy:
    def _good_response(self, qty: float, price: float) -> str:
        return json.dumps({
            "ok": True,
            "data": {
                "txHash": "0xdeadbeef",
                "executionPriceUsd": price,
                "feesUsd": 0.10,
                "slippageUsd": 0.05,
                "destAmount": qty,
            },
        })

    def test_buy_amount_usd_uses_exactIn_swap_mode(self):
        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            return _completed(self._good_response(qty=0.5, price=100.0))

        execu = OnchainosSwapExecutor(wallet_address="W", chain="solana")
        decision = {
            "action": "buy", "asset": "WSOL", "amount_usd": 100.0,
            "address": "So11111111111111111111111111111111111111112",
        }
        with patch("subprocess.run", side_effect=fake_run):
            fill = execu.execute(decision, position=None, bar={"c": 200.0})

        argv = captured["argv"]
        assert argv[1:3] == ["swap", "execute"]
        # --from = USDC, --to = the buy target
        assert argv[argv.index("--from") + 1].startswith("EPjFWdd5AufqSSqe")  # USDC mint
        assert argv[argv.index("--to") + 1] == \
            "So11111111111111111111111111111111111111112"
        # exactIn for amount_usd
        assert "exactIn" in argv
        assert fill["action"] == "buy"
        assert fill["gross_proceeds_usd"] < 0  # paid out
        assert fill["tx_hash"] == "0xdeadbeef"

    def test_buy_qty_uses_exactOut_swap_mode(self):
        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            return _completed(self._good_response(qty=1.0, price=100.0))

        execu = OnchainosSwapExecutor(wallet_address="W", chain="solana")
        decision = {
            "action": "buy", "asset": "WSOL", "qty": 1.0,
            "address": "So11111111111111111111111111111111111111112",
        }
        with patch("subprocess.run", side_effect=fake_run):
            execu.execute(decision, position=None, bar={"c": 100.0})
        assert "exactOut" in captured["argv"]

    def test_buy_without_target_address_raises(self):
        execu = OnchainosSwapExecutor(wallet_address="W", chain="solana")
        with pytest.raises(SwapExecutorError, match="missing token address"):
            execu.execute(
                {"action": "buy", "asset": "X", "amount_usd": 100.0},
                position=None, bar={"c": 100.0},
            )

    def test_buy_handles_auth_error(self):
        def fake_run(argv, **kwargs):
            return _completed("", returncode=1, stderr="OK-ACCESS-KEY missing\n")

        execu = OnchainosSwapExecutor(wallet_address="W", chain="solana")
        decision = {
            "action": "buy", "asset": "WSOL", "amount_usd": 100.0,
            "address": "So11",
        }
        with patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(SwapExecutorError, match="wallet_not_logged_in"):
                execu.execute(decision, position=None, bar={"c": 100.0})
