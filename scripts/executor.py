"""SwapExecutor abstraction for live-mode execution.

The watch loop hands a `Decision` to an executor; the executor returns a
`Fill` describing what actually happened. Two implementations:

- `SyntheticSwapExecutor` — fakes execution using the position's current
  mark price and a configurable slippage. Used in tests and for the
  hard-cap-enforcement demo in the README.

- `OnchainosSwapExecutor` — shells out to ``onchainos swap execute`` for
  real on-chain swaps via the OKX Agentic Wallet. Requires
  ``OKX_API_KEY`` / ``OKX_SECRET_KEY`` / ``OKX_PASSPHRASE`` in env AND
  ``onchainos wallet login`` to have succeeded for the target wallet.

Both produce identical Fill dicts so the rest of the loop is agnostic.
"""

from __future__ import annotations

import json
import subprocess
from abc import ABC, abstractmethod
from typing import Any

# Stablecoin destination addresses by chain. Configurable when more chains land.
USDC_BY_CHAIN: dict[str, str] = {
    "solana": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
}


class SwapExecutorError(Exception):
    """First word should map to a canonical FAILED token."""


class SwapExecutor(ABC):
    @abstractmethod
    def execute(
        self,
        decision: dict[str, Any],
        position: dict[str, Any] | None = None,
        bar: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a decision against the matching position; return a Fill dict.

        Args:
            decision: action dict with at least 'action' and 'asset'.
              For 'buy': may have 'qty' or 'amount_usd', plus optional
              'address' / 'chain' fields populated by the watch loop from
              the universe config.
              For 'sell'/'trim'/'exit'/'halt': v0.1.0 shape unchanged.
            position: the matching position from PM's derived state. For
              opening buys against an asset PM doesn't yet hold, this can
              be None.
            bar: optional current OHLCV bar. Required for 'buy' against
              an asset PM doesn't yet hold (mark price source).

        Fill shape:
        {
          "ok": bool,
          "action": "buy"|"sell"|"trim"|"exit"|"halt",
          "asset": str | None,
          "qty_swapped": float,
          "fill_price_usd": float,
          "gross_proceeds_usd": float,     # negative for buys (paid out)
          "fees_usd": float,
          "slippage_usd": float,
          "realized_pnl_usd": float,        # 0.0 for opens (buys)
          "tx_hash": str | None,
          "executor": str,                   # "synthetic" or "onchainos"
          "raw": dict | None,                # raw response for audit
        }
        """


# ---------------------------------------------------------------------------
# Synthetic — never touches a wallet. Used in tests and the cap-enforcement demo.
# ---------------------------------------------------------------------------


class SyntheticSwapExecutor(SwapExecutor):
    """Pretends to swap at mark - slippage. Records nothing on-chain."""

    def __init__(self, slippage_bps: float = 50.0, fee_bps: float = 30.0):
        self.slippage_bps = slippage_bps
        self.fee_bps = fee_bps

    def execute(
        self,
        decision: dict[str, Any],
        position: dict[str, Any] | None = None,
        bar: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        action = decision["action"]
        if action == "halt":
            return self._halt_fill(decision)
        if action == "buy":
            return self._buy_fill(decision, position, bar)

        # sell / trim / exit
        if position is None:
            raise SwapExecutorError(
                f"execution_failed cannot {action} {decision.get('asset')}: no position"
            )

        qty = float(decision.get("qty") or position.get("qty", 0))
        mark = float(position.get("mark_price_usd", 0.0))
        slip = mark * (self.slippage_bps / 10000.0)
        fill_price = max(mark - slip, 0.0)
        gross = qty * fill_price
        fee = gross * (self.fee_bps / 10000.0)
        net_proceeds = gross - fee

        # Realized PnL = net_proceeds − (cost_basis pro-rated for this qty)
        total_qty = float(position.get("qty", 0))
        cost_basis = float(position.get("cost_basis_usd", 0))
        if total_qty > 0:
            cost_basis_chunk = cost_basis * (qty / total_qty)
        else:
            cost_basis_chunk = cost_basis
        realized_pnl = net_proceeds - cost_basis_chunk

        return {
            "ok": True,
            "action": action,
            "asset": decision["asset"],
            "qty_swapped": round(qty, 8),
            "fill_price_usd": round(fill_price, 8),
            "gross_proceeds_usd": round(gross, 2),
            "fees_usd": round(fee, 2),
            "slippage_usd": round(qty * slip, 2),
            "realized_pnl_usd": round(realized_pnl, 2),
            "tx_hash": None,
            "executor": "synthetic",
            "raw": None,
        }

    def _buy_fill(
        self,
        decision: dict[str, Any],
        position: dict[str, Any] | None,
        bar: dict[str, Any] | None,
    ) -> dict[str, Any]:
        # Mark-price source: prefer bar.c, fall back to existing position.
        mark = 0.0
        if bar is not None:
            mark = float(bar.get("c") or bar.get("mark_price_usd") or 0.0)
        if mark <= 0 and position is not None:
            mark = float(position.get("mark_price_usd", 0.0))
        if mark <= 0:
            raise SwapExecutorError(
                f"execution_failed cannot buy {decision.get('asset')}: no mark price available"
            )

        # Slippage on a BUY: pay above mark.
        slip = mark * (self.slippage_bps / 10000.0)
        fill_price = mark + slip

        amount_usd = decision.get("amount_usd")
        qty_input = decision.get("qty")
        if amount_usd is not None:
            qty = float(amount_usd) / fill_price
            gross_cost = float(amount_usd)
        elif qty_input is not None:
            qty = float(qty_input)
            gross_cost = qty * fill_price
        else:
            raise SwapExecutorError(
                f"execution_failed buy {decision.get('asset')}: needs qty or amount_usd"
            )

        fee = gross_cost * (self.fee_bps / 10000.0)

        return {
            "ok": True,
            "action": "buy",
            "asset": decision["asset"],
            "qty_swapped": round(qty, 8),
            "fill_price_usd": round(fill_price, 8),
            # Negative for buys — quote is the USD outflow.
            "gross_proceeds_usd": -round(gross_cost, 2),
            "fees_usd": round(fee, 2),
            "slippage_usd": round(qty * slip, 2),
            "realized_pnl_usd": 0.0,   # opens never realize PnL
            "tx_hash": None,
            "executor": "synthetic",
            "raw": None,
        }

    def _halt_fill(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "action": "halt",
            "asset": None,
            "qty_swapped": 0.0,
            "fill_price_usd": 0.0,
            "gross_proceeds_usd": 0.0,
            "fees_usd": 0.0,
            "slippage_usd": 0.0,
            "realized_pnl_usd": 0.0,
            "tx_hash": None,
            "executor": "synthetic",
            "raw": None,
        }


# ---------------------------------------------------------------------------
# Onchainos — real on-chain swaps via the OKX Agentic Wallet
# ---------------------------------------------------------------------------


class OnchainosSwapExecutor(SwapExecutor):
    """Shells out to ``onchainos swap execute`` for real execution.

    Halt actions are translated into a series of exits (one swap per
    position) — but the watch loop is responsible for ordering and
    cap-checking between each. This class handles a single decision at a
    time.
    """

    def __init__(
        self,
        wallet_address: str,
        chain: str = "solana",
        slippage_pct: float | None = None,
        cli_bin: str = "onchainos",
        gas_level: str = "average",
    ):
        self.wallet_address = wallet_address
        self.chain = chain
        self.slippage_pct = slippage_pct
        self.cli_bin = cli_bin
        self.gas_level = gas_level

    def execute(
        self,
        decision: dict[str, Any],
        position: dict[str, Any] | None = None,
        bar: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        action = decision["action"]
        if action == "halt":
            # The loop is responsible for translating "halt" into per-position exits.
            return {
                "ok": True,
                "action": "halt",
                "asset": None,
                "qty_swapped": 0.0,
                "fill_price_usd": 0.0,
                "gross_proceeds_usd": 0.0,
                "fees_usd": 0.0,
                "slippage_usd": 0.0,
                "realized_pnl_usd": 0.0,
                "tx_hash": None,
                "executor": "onchainos",
                "raw": None,
            }

        usdc_addr = USDC_BY_CHAIN.get(self.chain)
        if not usdc_addr:
            raise SwapExecutorError(
                f"execution_failed no stablecoin configured for chain={self.chain}"
            )

        if action == "buy":
            # USDC → asset
            dst_addr = (position or {}).get("address") or decision.get("address")
            if not dst_addr:
                raise SwapExecutorError(
                    f"execution_failed buy {decision.get('asset')}: missing token address"
                )
            amount_usd = decision.get("amount_usd")
            qty = decision.get("qty")
            if amount_usd is not None:
                readable_amount = float(amount_usd)
                src_addr, dst = usdc_addr, dst_addr
                argv_extra = ["--swap-mode", "exactIn"]
            elif qty is not None:
                # Specify the destination qty via exactOut so we land exactly `qty`.
                readable_amount = float(qty)
                src_addr, dst = usdc_addr, dst_addr
                argv_extra = ["--swap-mode", "exactOut"]
            else:
                raise SwapExecutorError(
                    f"execution_failed buy {decision.get('asset')}: needs qty or amount_usd"
                )
            return self._invoke_swap(
                decision=decision,
                position=position or {"address": dst_addr, "qty": 0,
                                       "cost_basis_usd": 0, "mark_price_usd":
                                       (bar or {}).get("c", 0)},
                src_addr=src_addr,
                dst_addr=dst,
                readable_amount=readable_amount,
                argv_extra=argv_extra,
                is_buy=True,
            )

        # sell / trim / exit
        if position is None:
            raise SwapExecutorError(
                f"execution_failed cannot {action} {decision.get('asset')}: no position"
            )
        src_addr = position.get("address")
        if not src_addr:
            raise SwapExecutorError(
                f"execution_failed missing source token address for {decision['asset']}"
            )

        qty = float(decision.get("qty") or position.get("qty", 0))
        if qty <= 0:
            raise SwapExecutorError("execution_failed non-positive qty")

        return self._invoke_swap(
            decision=decision,
            position=position,
            src_addr=src_addr,
            dst_addr=usdc_addr,
            readable_amount=qty,
            argv_extra=[],
            is_buy=False,
        )

    def _invoke_swap(
        self,
        decision: dict[str, Any],
        position: dict[str, Any],
        src_addr: str,
        dst_addr: str,
        readable_amount: float,
        argv_extra: list[str],
        is_buy: bool,
    ) -> dict[str, Any]:
        argv = [
            self.cli_bin, "swap", "execute",
            "--chain", self.chain,
            "--wallet", self.wallet_address,
            "--from", src_addr,
            "--to", dst_addr,
            "--readable-amount", f"{readable_amount}",
            "--gas-level", self.gas_level,
        ] + list(argv_extra)
        if self.slippage_pct is not None:
            argv.extend(["--slippage", f"{self.slippage_pct}"])

        try:
            res = subprocess.run(
                argv, capture_output=True, text=True, check=False, timeout=90
            )
        except FileNotFoundError as e:
            raise SwapExecutorError(f"execution_failed cli_not_found {e.filename}") from e
        except subprocess.TimeoutExpired as e:
            raise SwapExecutorError("execution_failed cli_timeout") from e

        if res.returncode != 0:
            blob = (res.stderr or res.stdout).strip().splitlines()[-1:]
            detail = blob[0] if blob else "non-zero exit"
            if "OK-ACCESS-KEY" in detail or "auth" in detail.lower():
                raise SwapExecutorError("execution_failed wallet_not_logged_in")
            raise SwapExecutorError(f"execution_failed {detail}")

        try:
            payload = json.loads(res.stdout)
        except json.JSONDecodeError:
            raise SwapExecutorError(
                "execution_failed cli_output_invalid (non-JSON stdout)"
            )

        data = payload.get("data") if isinstance(payload, dict) else payload
        data = data or {}
        parsed = self._parse_swap_response(data, position=position)
        tx_hash = parsed["tx_hash"]
        fill_price = parsed["fill_price_usd"]
        fees = parsed["fees_usd"]
        slippage = parsed["slippage_usd"]

        if is_buy:
            qty_filled = parsed["dest_qty"] or float(readable_amount)
            gross = parsed["gross_usd"] or (qty_filled * fill_price)
            return {
                "ok": True,
                "action": "buy",
                "asset": decision["asset"],
                "qty_swapped": round(qty_filled, 8),
                "fill_price_usd": round(fill_price, 8),
                "gross_proceeds_usd": -round(gross, 2),
                "fees_usd": round(fees, 2),
                "slippage_usd": round(slippage, 2),
                "realized_pnl_usd": 0.0,
                "tx_hash": tx_hash,
                "executor": "onchainos",
                "raw": data,
            }

        # sell / trim / exit — qty_swapped is the source-side amount (the asset
        # we sold); gross is the destination-side USDC received.
        qty = parsed["src_qty"] or float(readable_amount)
        gross = parsed["dest_qty_usd"] or (qty * fill_price)
        cost_basis = float(position.get("cost_basis_usd", 0))
        total_qty = float(position.get("qty", 0)) or qty
        cost_basis_chunk = cost_basis * (qty / total_qty) if total_qty > 0 else cost_basis
        realized_pnl = (gross - fees) - cost_basis_chunk

        return {
            "ok": True,
            "action": decision["action"],
            "asset": decision["asset"],
            "qty_swapped": round(qty, 8),
            "fill_price_usd": round(fill_price, 8),
            "gross_proceeds_usd": round(gross, 2),
            "fees_usd": round(fees, 2),
            "slippage_usd": round(slippage, 2),
            "realized_pnl_usd": round(realized_pnl, 2),
            "tx_hash": tx_hash,
            "executor": "onchainos",
            "raw": data,
        }

    @staticmethod
    def _parse_swap_response(
        data: dict[str, Any], position: dict[str, Any]
    ) -> dict[str, Any]:
        """Adapter for the current ``onchainos swap execute`` response shape.

        Real-world response (2026-05 verified) carries:
          - swapTxHash, approveTxHash
          - fromAmount/toAmount (string, minimal units)
          - fromToken/toToken (each with decimal + tokenUnitPrice in USD)
          - priceImpact (percent string, e.g. "-0.15")
          - gasUsed (minimal units of native gas; not yet converted to USD)

        Falls back to legacy keys (txHash, destAmount, executionPriceUsd,
        feesUsd, slippageUsd) when present, so older shapes still parse.
        """
        tx_hash = (
            data.get("swapTxHash")
            or data.get("txHash")
            or data.get("transactionHash")
        )

        def _amt(raw: Any, decimals: Any) -> float:
            try:
                d = int(decimals or 0)
                v = float(raw or 0)
                return v / (10 ** d) if d > 0 else v
            except (TypeError, ValueError):
                return 0.0

        from_token = data.get("fromToken") or {}
        to_token = data.get("toToken") or {}
        src_qty = _amt(data.get("fromAmount"), from_token.get("decimal"))
        dest_qty = _amt(data.get("toAmount"), to_token.get("decimal"))

        # Legacy path: destAmount + executionPriceUsd
        if not dest_qty:
            try:
                dest_qty = float(data.get("destAmount") or 0)
            except (TypeError, ValueError):
                pass

        try:
            to_unit_price = float(to_token.get("tokenUnitPrice") or 0)
        except (TypeError, ValueError):
            to_unit_price = 0.0
        try:
            from_unit_price = float(from_token.get("tokenUnitPrice") or 0)
        except (TypeError, ValueError):
            from_unit_price = 0.0

        # Best-available fill price: prefer the destination token's unit price
        # (the price we paid per unit of the *thing we received*), then any
        # explicit executionPriceUsd, then the position's existing mark.
        fill_price = (
            to_unit_price
            or float(data.get("executionPriceUsd") or 0)
            or float(position.get("mark_price_usd", 0))
        )

        # USD-denominated gross on each leg.
        gross_usd = src_qty * from_unit_price  # USD value spent (BUY) or USD-from-asset (SELL src)
        dest_qty_usd = dest_qty * to_unit_price  # USD received on SELL

        # Slippage from priceImpact (%) applied to the gross we spent.
        try:
            pi_pct = abs(float(data.get("priceImpact") or 0))
        except (TypeError, ValueError):
            pi_pct = 0.0
        slippage_usd = gross_usd * pi_pct / 100.0 if gross_usd > 0 else float(data.get("slippageUsd") or 0)

        # Fees: punt on gasUsed → USD conversion (needs native price); legacy
        # `feesUsd` is honored when present.
        try:
            fees_usd = float(data.get("feesUsd") or 0)
        except (TypeError, ValueError):
            fees_usd = 0.0

        return {
            "tx_hash": tx_hash,
            "src_qty": src_qty,
            "dest_qty": dest_qty,
            "gross_usd": gross_usd,
            "dest_qty_usd": dest_qty_usd,
            "fill_price_usd": fill_price,
            "fees_usd": fees_usd,
            "slippage_usd": slippage_usd,
        }
