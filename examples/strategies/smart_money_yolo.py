"""YOLO strategy — follow smart-money buy clusters on Solana.

Polls `onchainos tracker activities` for the last N smart-money BUY
transactions on Solana, aggregates by token, ranks by (distinct wallet
count, signal count), and all-ins on the top candidate that clears the
liquidity filter. Rotates when a different token surfaces with strictly
more distinct buying wallets.

Inputs come from outside `market_data` — this strategy doesn't use
historical OHLCV. Backtesting it requires a different harness (signal
replay), so it's live-only.

Hard rules:
  - Only acts on Solana chainIndex 501 (contest-eligible).
  - Requires ≥ MIN_WALLETS distinct smart-money buyers in the last
    SIGNAL_WINDOW_MIN minutes.
  - Requires market_cap_usd ≥ MIN_MARKET_CAP_USD for slippage safety.
  - Trailing-stop / max-loss / position-cap is the rule-engine's job
    (PM's rules.yaml). Strategy emits sells only on rotation.

Conservative defaults — TUNE these before going live with real $$.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any

# ---------------- tunables ----------------
CHAIN_INDEX = 501  # Solana
ENTRY_USD = 25.0   # per-position notional; sized for a $80 bag
MIN_WALLETS = 2    # smart-money buyer threshold (2 = looser; raise to 3 for stricter)
MIN_VOLUME_USD = 500  # per-trade min for the tracker query
MIN_MARKET_CAP_USD = 250_000  # liquidity floor
SIGNAL_WINDOW_MIN = 240  # 4h — broad enough to catch overnight signal lulls
SIGNAL_CACHE_SEC = 60   # don't re-poll the tracker more often than this
CLI_BIN = "onchainos"

# Caching to avoid hammering the tracker API every PM cycle.
_signal_cache: dict[str, Any] = {"ts": 0.0, "ranked": []}


def _poll_signals() -> list[dict[str, Any]]:
    """Subprocess the tracker, aggregate by token, return ranked candidates."""
    now = time.time()
    if now - _signal_cache["ts"] < SIGNAL_CACHE_SEC and _signal_cache["ranked"]:
        return _signal_cache["ranked"]

    argv = [
        CLI_BIN, "tracker", "activities",
        "--tracker-type", "smart_money",
        "--chain", "solana",
        "--trade-type", "1",  # buys
        "--min-volume", str(int(MIN_VOLUME_USD)),
    ]
    try:
        res = subprocess.run(
            argv, capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if res.returncode != 0:
        return []
    try:
        payload = json.loads(res.stdout)
    except json.JSONDecodeError:
        return []

    trades = ((payload.get("data") or {}).get("trades")) or []
    # Approximate USD valuation (quoteTokenAmount in SOL × ~SOL_USD).
    # Live mode could replace this with a real SOL price feed; for now a
    # rough constant is OK because we sort by wallet count, not USD.
    SOL_USD = float(os.environ.get("YOLO_SOL_USD", "85"))
    now_ms = int(now * 1000)
    window_ms = SIGNAL_WINDOW_MIN * 60 * 1000

    agg: dict[str, dict[str, Any]] = {}
    for t in trades:
        addr = t.get("tokenContractAddress")
        if not addr:
            continue
        if str(t.get("chainIndex") or "") != str(CHAIN_INDEX):
            continue
        ts_ms = int(t.get("tradeTime") or 0)
        if ts_ms <= 0 or (now_ms - ts_ms) > window_ms:
            continue
        try:
            mcap = float(t.get("marketCap") or 0)
        except (TypeError, ValueError):
            mcap = 0.0
        if mcap < MIN_MARKET_CAP_USD:
            continue
        a = agg.setdefault(addr, {
            "address": addr,
            "symbol": t.get("tokenSymbol") or "?",
            "wallets": set(),
            "count": 0,
            "usd": 0.0,
            "mcap": mcap,
            "price": float(t.get("tokenPrice") or 0),
            "last_seen": 0,
        })
        a["wallets"].add(t.get("walletAddress"))
        a["count"] += 1
        qty = float(t.get("quoteTokenAmount") or 0)
        qsym = (t.get("quoteTokenSymbol") or "").upper()
        a["usd"] += qty * (SOL_USD if qsym in ("SOLANA", "SOL", "WSOL") else 1.0)
        a["last_seen"] = max(a["last_seen"], ts_ms)

    ranked: list[dict[str, Any]] = []
    for addr, a in agg.items():
        if len(a["wallets"]) < MIN_WALLETS:
            continue
        ranked.append({
            "address": addr,
            "symbol": a["symbol"],
            "wallet_count": len(a["wallets"]),
            "signal_count": a["count"],
            "usd_volume": round(a["usd"], 2),
            "market_cap_usd": a["mcap"],
            "price": a["price"],
            "last_seen_ms": a["last_seen"],
        })
    ranked.sort(
        key=lambda x: (x["wallet_count"], x["signal_count"], x["usd_volume"]),
        reverse=True,
    )

    _signal_cache["ts"] = now
    _signal_cache["ranked"] = ranked
    return ranked


def _current_position(state: dict[str, Any]) -> dict[str, Any] | None:
    """Return the strategy's active rotation slot — a non-stable position with
    a known token contract address (i.e., an SPL token we can sell via swap).

    Skips positions without a contract address:
      - Native SOL has no address; selling it would require wrapping. Leave it
        alone, it acts as a passive baseline.
      - Brand-new positions from this very cycle's fill (PM's
        _apply_fill_to_state initializes address=None and the next wallet
        poll backfills it). Skipping avoids attempting a sell with no source
        token address — which fails the executor's preflight.
    """
    stables = {"USDC", "USDT", "USDG", "DAI", "PYUSD", "FDUSD", "SOL"}
    for p in state.get("positions", []) or []:
        sym = (p.get("asset") or "").upper()
        if sym in stables:
            continue
        if not (p.get("address") or "").strip():
            continue  # no contract address → not strategy-tradable
        if float(p.get("qty", 0)) > 0:
            return p
    return None


def decide(state, market_data):  # noqa: ARG001 — market_data unused (signals come from CLI)
    ranked = _poll_signals()
    if not ranked:
        return []

    top = ranked[0]
    have = _current_position(state)

    if have is None:
        # No position — enter the top candidate if we can afford it.
        if state.get("cash_usd", 0) < ENTRY_USD:
            return []
        return [{
            "action": "buy",
            "asset": top["symbol"],
            "amount_usd": ENTRY_USD,
            "address": top["address"],
            "chain": "solana",
        }]

    # Holding something — only rotate if a DIFFERENT token has strictly more
    # distinct wallets buying right now.
    held_addr = (have.get("address") or "").lower()
    if held_addr == top["address"].lower():
        return []  # already in the top candidate

    # Find the held position's current rank for context.
    held_rank = next(
        (r for r in ranked if r["address"].lower() == held_addr),
        None,
    )
    held_wallets = held_rank["wallet_count"] if held_rank else 0
    if top["wallet_count"] <= held_wallets:
        return []  # not enough edge to justify rotation churn

    # Rotate: exit current → buy new.
    return [
        {"action": "sell", "asset": have["asset"], "sell_all": True},
        {
            "action": "buy",
            "asset": top["symbol"],
            "amount_usd": ENTRY_USD,
            "address": top["address"],
            "chain": "solana",
        },
    ]
