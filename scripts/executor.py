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
    def execute(self, decision: dict[str, Any], position: dict[str, Any]) -> dict[str, Any]:
        """Execute a decision against the matching position; return a Fill dict.

        Fill shape:
        {
          "ok": bool,
          "action": "trim"|"exit"|"halt",
          "asset": str,
          "qty_swapped": float,
          "fill_price_usd": float,
          "gross_proceeds_usd": float,
          "fees_usd": float,
          "slippage_usd": float,
          "realized_pnl_usd": float,        # vs cost basis chunk
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

    def execute(self, decision: dict[str, Any], position: dict[str, Any]) -> dict[str, Any]:
        action = decision["action"]
        if action == "halt":
            return self._halt_fill(decision)

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

    def execute(self, decision: dict[str, Any], position: dict[str, Any]) -> dict[str, Any]:
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

        src_addr = position.get("address")
        if not src_addr:
            raise SwapExecutorError(
                f"execution_failed missing source token address for {decision['asset']}"
            )
        dst_addr = USDC_BY_CHAIN.get(self.chain)
        if not dst_addr:
            raise SwapExecutorError(
                f"execution_failed no stablecoin destination configured for chain={self.chain}"
            )

        qty = float(decision.get("qty") or position.get("qty", 0))
        if qty <= 0:
            raise SwapExecutorError("execution_failed non-positive qty")

        argv = [
            self.cli_bin, "swap", "execute",
            "--chain", self.chain,
            "--wallet", self.wallet_address,
            "--from", src_addr,
            "--to", dst_addr,
            "--readable-amount", f"{qty}",
            "--gas-level", self.gas_level,
        ]
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

        # Permissive field extraction; tightened after first live verification.
        data = payload.get("data") if isinstance(payload, dict) else payload
        tx_hash = (data or {}).get("txHash") or (data or {}).get("transactionHash")
        fill_price = float((data or {}).get("executionPriceUsd") or position["mark_price_usd"])
        gross = qty * fill_price
        fees = float((data or {}).get("feesUsd") or 0)
        slippage = float((data or {}).get("slippageUsd") or 0)

        cost_basis = float(position.get("cost_basis_usd", 0))
        total_qty = float(position.get("qty", 0)) or qty
        cost_basis_chunk = cost_basis * (qty / total_qty)
        realized_pnl = (gross - fees) - cost_basis_chunk

        return {
            "ok": True,
            "action": action,
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
