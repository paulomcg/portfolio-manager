"""Wallet snapshot + PnL sources for the watch loop.

Two implementations:

- `SyntheticWalletSource` — loads pre-built JSON from disk. Used for tests,
  no-keys demos, and the contest's monitor-mode evidence in the README.

- `OnchainosWalletSource` — invokes `onchainos portfolio all-balances` and
  `onchainos market portfolio-token-pnl` via subprocess, parses the JSON
  responses, and assembles the WalletSnapshot the derivation function
  expects. Requires `OKX_API_KEY` (and friends) to be set in the env.

Both produce the same tuple shape: (wallet_snapshot, pnl_by_token).
"""

from __future__ import annotations

import json
import subprocess
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class WalletSourceError(Exception):
    """Canonical token follows the colon: 'wallet_not_logged_in', 'pnl_fetch_failed', etc."""


class WalletSource(ABC):
    @abstractmethod
    def fetch(self) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        """Return (wallet_snapshot, pnl_by_token)."""


# ---------------------------------------------------------------------------
# Synthetic — file-backed for tests and the no-keys demo
# ---------------------------------------------------------------------------


class SyntheticWalletSource(WalletSource):
    """Reads (wallet, pnl) from one or two JSON files.

    If `wallet_path` points to a file with a top-level "wallet"/"pnl" pair,
    use that bundled form. Otherwise treat it as the wallet snapshot directly
    and look for `pnl_path` for PnL data (optional).
    """

    def __init__(self, wallet_path: str | Path, pnl_path: str | Path | None = None):
        self.wallet_path = Path(wallet_path)
        self.pnl_path = Path(pnl_path) if pnl_path else None

    def fetch(self) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        if not self.wallet_path.exists():
            raise WalletSourceError(
                f"wallet_snapshot_missing path={self.wallet_path}"
            )
        try:
            raw = json.loads(self.wallet_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise WalletSourceError(f"wallet_snapshot_invalid {e.msg}") from e

        # Allow either {"wallet": {...}, "pnl": {...}} or a bare wallet snapshot.
        if "wallet" in raw and "tokens" not in raw:
            wallet = raw["wallet"]
            pnl = raw.get("pnl", {})
        else:
            wallet = raw
            pnl = {}
            if self.pnl_path is not None:
                if not self.pnl_path.exists():
                    raise WalletSourceError(f"pnl_snapshot_missing path={self.pnl_path}")
                try:
                    pnl = json.loads(self.pnl_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as e:
                    raise WalletSourceError(f"pnl_snapshot_invalid {e.msg}") from e
        # Refresh ts_utc each fetch so we can tell cycles apart in tests.
        wallet = {**wallet, "ts_utc": datetime.now(timezone.utc).isoformat()}
        return wallet, pnl


# ---------------------------------------------------------------------------
# Onchainos CLI source — real wallet data (M5 wires it; v1 stub fails loud)
# ---------------------------------------------------------------------------


class OnchainosWalletSource(WalletSource):
    """Production source — shells out to the onchainos CLI."""

    def __init__(self, wallet_address: str, chain: str = "solana", cli_bin: str = "onchainos"):
        self.wallet_address = wallet_address
        self.chain = chain
        self.cli_bin = cli_bin

    def fetch(self) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        balances = self._call_json(
            [self.cli_bin, "portfolio", "all-balances",
             "--chain", self.chain,
             "--chains", self.chain,
             "--address", self.wallet_address]
        )
        wallet = self._adapt_balances(balances)
        pnl = self._fetch_pnl_for_tokens(wallet["tokens"])
        return wallet, pnl

    def _call_json(self, argv: list[str]) -> Any:
        try:
            res = subprocess.run(
                argv, capture_output=True, text=True, check=False, timeout=30
            )
        except FileNotFoundError as e:
            raise WalletSourceError(f"cli_not_found {e.filename}") from e
        except subprocess.TimeoutExpired as e:
            raise WalletSourceError(f"cli_timeout {' '.join(argv)}") from e
        if res.returncode != 0:
            # Try to parse a structured error from stdout/stderr
            stderr = res.stderr.strip().splitlines()[-1] if res.stderr else ""
            stdout = res.stdout.strip().splitlines()[-1] if res.stdout else ""
            blob = stderr or stdout or "non-zero exit"
            if "OK-ACCESS-KEY" in blob or "auth" in blob.lower():
                raise WalletSourceError("wallet_not_logged_in OKX API key missing/invalid")
            raise WalletSourceError(f"cli_failed {blob}")
        try:
            return json.loads(res.stdout)
        except json.JSONDecodeError as e:
            raise WalletSourceError(f"cli_output_invalid {e.msg}") from e

    def _adapt_balances(self, balances: Any) -> dict[str, Any]:
        """Map onchainos `portfolio all-balances` output to WalletSnapshot.

        Current CLI shape: `{"data": [{"tokenAssets": [<row>, ...]}, ...]}`.
        Older / alternative shapes (`{"data": [<row>]}`, `[<row>]`) still parsed.
        Per-row keys: `symbol`, `tokenContractAddress` (new) / `tokenAddress` (old),
        `balance`, `tokenPrice`. No USD value in the new shape — we compute
        qty * price ourselves.
        """
        rows = self._extract_token_rows(balances)
        tokens: list[dict[str, Any]] = []
        for row in rows:
            sym = row.get("symbol") or row.get("tokenSymbol") or row.get("asset")
            # IMPORTANT: in the new CLI shape `address` is the *wallet* address
            # repeated on every row. The token contract lives in
            # `tokenContractAddress`. Prefer that, fall back to old keys.
            addr = (
                row.get("tokenContractAddress")
                or row.get("tokenAddress")
                or row.get("contractAddress")
                or ""
            )
            qty = float(row.get("balance") or row.get("amount") or row.get("tokenAmount") or 0)
            price = float(row.get("tokenPrice") or row.get("price") or row.get("priceUsd") or 0)
            value = float(row.get("balanceUsd") or row.get("valueUsd") or row.get("usdValue") or qty * price)
            tokens.append(
                {
                    "asset": sym or "?",
                    "chain": self.chain,
                    "address": addr,
                    "qty": qty,
                    "mark_price_usd": price,
                    "value_usd": value,
                }
            )
        return {
            "wallet_address": self.wallet_address,
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "tokens": tokens,
        }

    @staticmethod
    def _extract_token_rows(balances: Any) -> list[dict[str, Any]]:
        """Pull a flat list of token rows out of any of the historical shapes:
        - Current:   {"data": [{"tokenAssets": [<row>, ...]}, ...]}
        - Legacy:    {"data": [<row>, ...]}
        - Bare list: [<row>, ...]
        """
        data = balances.get("data") if isinstance(balances, dict) else balances
        if data is None:
            return []
        if not isinstance(data, list):
            return []
        # Detect the nested-container shape: any element with `tokenAssets`.
        nested = [el for el in data if isinstance(el, dict) and "tokenAssets" in el]
        if nested:
            out: list[dict[str, Any]] = []
            for el in nested:
                rows = el.get("tokenAssets")
                if isinstance(rows, list):
                    out.extend(r for r in rows if isinstance(r, dict))
            return out
        # Fallback: treat each top-level element as a token row.
        return [r for r in data if isinstance(r, dict)]

    def _fetch_pnl_for_tokens(self, tokens: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for t in tokens:
            addr = t.get("address")
            if not addr:
                continue
            try:
                resp = self._call_json(
                    [self.cli_bin, "market", "portfolio-token-pnl",
                     "--chain", self.chain,
                     "--address", self.wallet_address,
                     "--token", addr]
                )
            except WalletSourceError:
                # Non-fatal — derivation defaults missing PnL to zero
                continue
            data = resp.get("data") if isinstance(resp, dict) else resp
            if not data:
                continue
            # Permissive field extraction; tightened after live verification.
            unrealized = float(
                data.get("unrealizedPnl") or data.get("unrealized_pnl_usd") or 0
            )
            realized = float(
                data.get("realizedPnl") or data.get("realized_pnl_usd") or 0
            )
            out[f"{self.chain}:{addr}"] = {
                "asset": t.get("asset"),
                "unrealized_pnl_usd": unrealized,
                "realized_pnl_usd": realized,
            }
        return out
