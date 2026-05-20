"""YOLO strategy — actively trade on smart-money signals.

What this strategy does each PM cycle (60s):

  1) Pull recent smart-money BUYS and SELLS from `onchainos tracker`.
  2) For each currently-held position:
       - If the unrealized PnL is at or above TAKE_PROFIT_PCT → SELL all.
       - If the unrealized PnL is at or below STOP_LOSS_PCT → SELL all.
       - If ≥ DUMP_WALLETS_THRESHOLD distinct smart-money wallets are
         actively SELLING this token in the SIGNAL_WINDOW_MIN window → SELL all.
  3) Rank buy-side candidates (≥ MIN_WALLETS distinct smart-money buyers,
     mcap ≥ MIN_MARKET_CAP_USD, age ≤ SIGNAL_WINDOW_MIN).
  4) For each candidate not currently held, if we're under MAX_POSITIONS
     and have cash ≥ ENTRY_USD, OPEN a new position.

Multi-position: up to MAX_POSITIONS concurrent positions, each ENTRY_USD.
PM's rule engine (trailing-stop, halt-on-drawdown, max-position-pct) and
the `--max-loss-usd` kill switch are the safety floor under everything
this strategy does.

Live-only — no backtest path. Inputs come from CLI subprocess to the
tracker; market_data is unused.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any

# ---------------- tunables ----------------
CHAIN_INDEX = 501  # Solana

ENTRY_USD = 25.0
MAX_POSITIONS = 3        # concurrent open positions cap

MIN_WALLETS = 2          # distinct smart-money BUYERS required to enter
DUMP_WALLETS_THRESHOLD = 2   # distinct smart-money SELLERS required to exit
MIN_VOLUME_USD = 500
MIN_MARKET_CAP_USD = 250_000
SIGNAL_WINDOW_MIN = 90   # 1.5h — favor fresh explosive signals only
SIGNAL_CACHE_SEC = 50    # < PM cycle (60s) so cache refreshes once per cycle

# Trailing exit from per-position high — replaces fixed take-profit.
# Once a position has printed above its cost basis at some point, exit when
# the current value drops TRAIL_FROM_PEAK_PCT from the high-water mark.
# Effect: lock in gains without waiting for a fixed +20% target, and act as
# a tighter floor on losing entries (HWM stays at entry if price never rises).
TRAIL_FROM_PEAK_PCT = 0.05  # 5% from HWM
STOP_LOSS_PCT = -0.15    # -15% absolute loss vs cost basis (backstop)

STABLES_AND_SKIP = {"USDC", "USDT", "USDG", "DAI", "PYUSD", "FDUSD", "SOL"}

CLI_BIN = "onchainos"

# Per-process caches (PM watch is a long-running process, so caches persist).
_buy_cache: dict[str, Any] = {"ts": 0.0, "ranked": []}
_sell_cache: dict[str, Any] = {"ts": 0.0, "by_addr": {}}


# ---------------- signal helpers ----------------

def _poll_tracker(trade_type: int) -> list[dict[str, Any]]:
    """Subprocess `onchainos tracker activities` and return raw trades."""
    argv = [
        CLI_BIN, "tracker", "activities",
        "--tracker-type", "smart_money",
        "--chain", "solana",
        "--trade-type", str(trade_type),
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
    return ((payload.get("data") or {}).get("trades")) or []


def _aggregate(trades: list[dict[str, Any]], window_min: int) -> dict[str, dict[str, Any]]:
    """Aggregate trades by tokenContractAddress over the freshness window."""
    now_ms = int(time.time() * 1000)
    window_ms = window_min * 60 * 1000
    SOL_USD = float(os.environ.get("YOLO_SOL_USD", "85"))
    agg: dict[str, dict[str, Any]] = {}
    for t in trades:
        addr = t.get("tokenContractAddress")
        if not addr:
            continue
        if str(t.get("chainIndex") or "") != str(CHAIN_INDEX):
            continue
        ts = int(t.get("tradeTime") or 0)
        if ts <= 0 or (now_ms - ts) > window_ms:
            continue
        try:
            mcap = float(t.get("marketCap") or 0)
        except (TypeError, ValueError):
            mcap = 0.0
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
        a["mcap"] = max(a["mcap"], mcap)
        qty = float(t.get("quoteTokenAmount") or 0)
        qsym = (t.get("quoteTokenSymbol") or "").upper()
        a["usd"] += qty * (SOL_USD if qsym in ("SOLANA", "SOL", "WSOL") else 1.0)
        a["last_seen"] = max(a["last_seen"], ts)
    return agg


def _ranked_buys() -> list[dict[str, Any]]:
    """Cached top buy-side candidates."""
    now = time.time()
    if now - _buy_cache["ts"] < SIGNAL_CACHE_SEC and _buy_cache["ranked"]:
        return _buy_cache["ranked"]
    trades = _poll_tracker(trade_type=1)
    agg = _aggregate(trades, SIGNAL_WINDOW_MIN)
    ranked: list[dict[str, Any]] = []
    for addr, a in agg.items():
        if len(a["wallets"]) < MIN_WALLETS:
            continue
        if a["mcap"] < MIN_MARKET_CAP_USD:
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
    _buy_cache["ts"] = now
    _buy_cache["ranked"] = ranked
    return ranked


def _sell_pressure_by_addr() -> dict[str, int]:
    """Cached map of {tokenContractAddress: distinct_seller_count} for dumps."""
    now = time.time()
    if now - _sell_cache["ts"] < SIGNAL_CACHE_SEC and _sell_cache["by_addr"]:
        return _sell_cache["by_addr"]
    trades = _poll_tracker(trade_type=2)
    agg = _aggregate(trades, SIGNAL_WINDOW_MIN)
    by_addr: dict[str, int] = {a["address"].lower(): len(a["wallets"]) for a in agg.values()}
    _sell_cache["ts"] = now
    _sell_cache["by_addr"] = by_addr
    return by_addr


# ---------------- position helpers ----------------

def _strategy_held(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Strategy-tradable positions = SPL tokens with a known contract address."""
    out: list[dict[str, Any]] = []
    for p in state.get("positions", []) or []:
        sym = (p.get("asset") or "").upper()
        if sym in STABLES_AND_SKIP:
            continue
        if not (p.get("address") or "").strip():
            continue
        if float(p.get("qty", 0)) > 0:
            out.append(p)
    return out


def _unrealized_pct(p: dict[str, Any]) -> float | None:
    """Unrealized PnL % vs cost basis, or None if cost basis unknown."""
    cost = float(p.get("cost_basis_usd") or 0)
    if cost <= 0:
        return None
    pnl = float(p.get("unrealized_pnl_usd") or 0)
    return pnl / cost


# ---------------- main entry ----------------

def decide(state, market_data):  # noqa: ARG001 — market_data unused
    cash = float(state.get("cash_usd", 0))
    held = _strategy_held(state)
    actions: list[dict[str, Any]] = []

    # 1) Check exits on every held position.
    sell_pressure = _sell_pressure_by_addr()
    sold_addrs: set[str] = set()
    for p in held:
        addr_lower = (p["address"] or "").lower()
        pct = _unrealized_pct(p)

        # Trailing-from-peak: exit when position is down TRAIL_FROM_PEAK_PCT
        # from its high-water mark AND has actually been in profit at some
        # point (HWM > cost_basis). This locks in gains without holding for
        # a fixed target.
        try:
            hwm = float(p.get("high_water_mark_usd") or 0)
            cost = float(p.get("cost_basis_usd") or 0)
            current_val = float(p.get("value_usd") or 0)
        except (TypeError, ValueError):
            hwm = cost = current_val = 0.0
        trailing_triggered = (
            hwm > 0
            and cost > 0
            and hwm > cost  # has been profitable at some point
            and current_val < hwm * (1.0 - TRAIL_FROM_PEAK_PCT)
        )

        exit_reason: str | None = None
        if trailing_triggered:
            exit_reason = "trail_from_peak"
        elif pct is not None and pct <= STOP_LOSS_PCT:
            exit_reason = "stop_loss"
        elif sell_pressure.get(addr_lower, 0) >= DUMP_WALLETS_THRESHOLD:
            exit_reason = "smart_money_dump"

        if exit_reason:
            actions.append({
                "action": "sell",
                "asset": p["asset"],
                "sell_all": True,
                "reason": exit_reason,
            })
            sold_addrs.add(addr_lower)

    # 2) Find buy candidates we don't already hold (and aren't selling).
    #    Also exclude any token with active dump pressure — smart money
    #    selling at the same time as buying is a contradicting signal we
    #    won't fade into.
    held_addrs = {(p["address"] or "").lower() for p in held} - sold_addrs
    ranked = _ranked_buys()
    new_candidates = [
        c for c in ranked
        if c["address"].lower() not in held_addrs
        and sell_pressure.get(c["address"].lower(), 0) < DUMP_WALLETS_THRESHOLD
    ]

    # 3) How many positions will we hold after the exits this cycle?
    surviving_positions = len(held) - len(sold_addrs)
    open_slots = MAX_POSITIONS - surviving_positions
    if open_slots <= 0:
        return actions

    # 4) Available cash, including the proceeds from sells we're about to do.
    sold_value = sum(
        float(p.get("value_usd", 0))
        for p in held
        if (p["address"] or "").lower() in sold_addrs
    )
    available_cash = cash + sold_value

    for c in new_candidates:
        if open_slots <= 0:
            break
        if available_cash < ENTRY_USD:
            break
        actions.append({
            "action": "buy",
            "asset": c["symbol"],
            "amount_usd": ENTRY_USD,
            "address": c["address"],
            "chain": "solana",
        })
        open_slots -= 1
        available_cash -= ENTRY_USD

    return actions
