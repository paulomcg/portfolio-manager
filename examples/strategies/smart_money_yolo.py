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
MIN_MARKET_CAP_USD = 20_000  # anti-rug filters compensate for low-cap risk;
                              # memepump-momentum tokens worth catching live
                              # in the $20k-200k mcap range
SIGNAL_WINDOW_MIN = 90   # 1.5h — favor fresh explosive signals only
SIGNAL_CACHE_SEC = 30    # cache TTL; balances API spend vs reaction speed

# Trailing exit from per-position high — replaces fixed take-profit.
# Once a position has printed above its cost basis at some point, exit when
# the current value drops TRAIL_FROM_PEAK_PCT from the high-water mark.
# Effect: lock in gains without waiting for a fixed +20% target, and act as
# a tighter floor on losing entries (HWM stays at entry if price never rises).
TRAIL_FROM_PEAK_PCT = 0.05  # 5% from HWM
STOP_LOSS_PCT = -0.15    # -15% absolute loss vs cost basis (backstop)

# Signal decay exit: if a held position falls out of the ranked-buys feed
# for STALE_CYCLES_THRESHOLD consecutive cycles, exit it. Rationale: the
# whole strategy thesis is "follow smart money". A position smart money
# stopped buying and isn't dumping either is dead weight — exit and free
# the capital for the next fresh signal. At 10s cycles, 30 cycles = ~5
# minutes of "no smart-money buy-side conviction" before we walk away.
STALE_CYCLES_THRESHOLD = 30

# Anti-rug filters applied via memepump tokens metadata. A candidate is
# REJECTED if ANY of these thresholds is exceeded — these are the standard
# indicators that a memecoin is a sniper pump-and-dump, a dev-controlled
# rug, or has dangerous holder concentration.
MAX_SNIPERS_PCT = 30.0       # > 30% sniper holdings = avoid
MAX_BUNDLERS_PCT = 20.0      # > 20% bundlers = bundled distribution scheme
MAX_DEV_HOLDINGS_PCT = 15.0  # > 15% dev = dev can dump on us
MAX_INSIDERS_PCT = 15.0      # > 15% insiders = same wallet cluster
MAX_TOP10_PCT = 40.0         # > 40% in top 10 holders = whale dump risk
MIN_HOLDERS = 100            # < 100 holders = too thin / pre-distribution

# Momentum thresholds (memepump 1h stats)
MIN_VOLUME_USD_1H = 1000.0    # need real activity, not zombie tokens

# Pure-momentum entry thresholds — looser than smart-money but stricter
# than the baseline. When SM/KOL feeds are quiet, the strategy can still
# enter on memepump-detected momentum alone if:
#   - vol_1h >= STRONG_VOLUME_USD_1H (genuine flow)
#   - buy_1h > sell_1h * STRONG_BUY_RATIO (net-buying pressure)
#   - mcap >= MIN_MARKET_CAP_USD (existing floor)
#   - anti-rug all pass
# Caps the score so SM-confirmed signals still rank higher when both exist.
STRONG_VOLUME_USD_1H = 30_000.0
STRONG_BUY_RATIO = 1.20

STABLES_AND_SKIP = {"USDC", "USDT", "USDG", "DAI", "PYUSD", "FDUSD", "SOL"}

CLI_BIN = "onchainos"

# Per-process caches (PM watch is a long-running process, so caches persist).
_buy_cache: dict[str, Any] = {"ts": 0.0, "ranked": []}
_sell_cache: dict[str, Any] = {"ts": 0.0, "by_addr": {}}
_kol_cache: dict[str, Any] = {"ts": 0.0, "by_addr": {}}        # tracker KOL buys (cached longer than smart_money)
_memepump_cache: dict[str, Any] = {"ts": 0.0, "by_addr": {}}   # memepump MIGRATED list + anti-rug data

# Tracks consecutive cycles each held position has been ABSENT from the
# ranked-buys feed. Reset to 0 when the position reappears. Persists across
# decide() calls in the same PM process.
_stale_counter: dict[str, int] = {}

# Tracks unix-ts of the last sell for each token. Used to enforce a re-buy
# cooldown — if we just sold a token (dump-detect, trailing-stop, signal-decay,
# or any other exit), we don't want the strategy whipsawing back in 10s later
# at a worse price. Persists across decide() calls.
_recently_sold: dict[str, float] = {}

# Cooldown window — 30 min. Long enough that a momentary dump that triggered
# our exit isn't immediately followed by re-entry on the bounce (the TROLL
# / PERP whipsaw pattern), but short enough that genuinely fresh momentum
# (different signal, hours later) can still re-enter.
REBUY_COOLDOWN_SEC = 1800


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
    """Build the candidate pool from MULTIPLE sources and rank by composite
    score. Sources merged:

      1) Smart-money tracker buys — distinct wallets in window
      2) KOL tracker buys — different wallet class, cross-source confirmation
      3) memepump MIGRATED metadata — anti-rug filters + 1h momentum

    Scoring:
      score = sm_wallets + kol_wallets * 0.7 + memepump_momentum_bonus
      where memepump_momentum_bonus rewards strong 1h vol + positive buy/sell ratio.

    Filters (any failure → reject):
      - At least one source must show ≥ MIN_WALLETS smart-money buyers
        OR the same threshold of KOL buyers
      - Market cap (from memepump if available, else tracker estimate) ≥ MIN_MARKET_CAP_USD
      - Anti-rug pass on memepump metadata (if available)
      - Momentum: memepump vol_1h ≥ MIN_VOLUME_USD_1H (if metadata available)
    """
    now = time.time()
    if now - _buy_cache["ts"] < SIGNAL_CACHE_SEC and _buy_cache["ranked"]:
        return _buy_cache["ranked"]

    # Pull all sources (each independently cached)
    sm_trades = _poll_tracker(trade_type=1)
    sm_agg = _aggregate(sm_trades, SIGNAL_WINDOW_MIN)
    kol_by_addr = _kol_buys_by_addr()
    mp_by_addr = _memepump_metadata()

    # Build candidate pool — token appears if any source flags it.
    # Memepump is a third entry source: tokens with strong 1h momentum can
    # qualify even without SM/KOL wallet signals.
    pool: dict[str, dict[str, Any]] = {}
    for addr, a in sm_agg.items():
        pool[addr.lower()] = {
            "address": addr,
            "symbol": a["symbol"],
            "sm_wallets": len(a["wallets"]),
            "sm_count": a["count"],
            "sm_usd": a["usd"],
            "sm_mcap": a["mcap"],
            "last_seen_ms": a["last_seen"],
            "kol_wallets": 0,
            "kol_count": 0,
        }
    for addr_l, kol in kol_by_addr.items():
        entry = pool.setdefault(addr_l, {
            "address": kol.get("address") or addr_l,
            "symbol": kol["symbol"],
            "sm_wallets": 0, "sm_count": 0, "sm_usd": 0.0, "sm_mcap": 0.0,
            "last_seen_ms": 0,
            "kol_wallets": 0, "kol_count": 0,
        })
        entry["kol_wallets"] = kol["wallets"]
        entry["kol_count"] = kol["count"]
        if not entry.get("symbol") or entry["symbol"] == "?":
            entry["symbol"] = kol["symbol"]
    # Add memepump-only candidates not already in the pool
    for addr_l, meta in mp_by_addr.items():
        if addr_l in pool:
            continue
        pool[addr_l] = {
            "address": meta.get("address") or addr_l,
            "symbol": meta["symbol"],
            "sm_wallets": 0, "sm_count": 0, "sm_usd": 0.0, "sm_mcap": 0.0,
            "last_seen_ms": 0,
            "kol_wallets": 0, "kol_count": 0,
        }

    # Apply filters + score each candidate
    ranked: list[dict[str, Any]] = []
    for addr_l, c in pool.items():
        meta = mp_by_addr.get(addr_l, {})

        # Multi-track gating: pass if EITHER
        #  (a) ≥ MIN_WALLETS SM or KOL buyers, OR
        #  (b) memepump strong-momentum criteria
        has_sm_signal = c["sm_wallets"] >= MIN_WALLETS or c["kol_wallets"] >= MIN_WALLETS
        has_momentum_signal = bool(
            meta
            and meta["vol_1h"] >= STRONG_VOLUME_USD_1H
            and meta["buy_1h"] > 0
            and meta["sell_1h"] >= 0
            and meta["buy_1h"] >= meta["sell_1h"] * STRONG_BUY_RATIO
        )
        if not (has_sm_signal or has_momentum_signal):
            continue

        # Market cap — prefer memepump (live), fall back to tracker estimate
        mcap = meta.get("mcap") or c["sm_mcap"]
        if mcap < MIN_MARKET_CAP_USD:
            continue

        # Anti-rug — only applies when we have memepump metadata
        rug_ok, rug_reason = _anti_rug_pass(meta)
        if not rug_ok:
            continue

        # Momentum filter (only when memepump metadata exists)
        if meta:
            if meta["vol_1h"] < MIN_VOLUME_USD_1H:
                continue
            # Reject tokens being net-sold (more sells than buys in last 1h)
            if meta["buy_1h"] > 0 and meta["sell_1h"] > meta["buy_1h"] * 1.5:
                continue

        # Composite score: smart-money is primary weight, KOL is cross-source
        # bonus, memepump momentum is a small kicker.
        momentum_bonus = 0.0
        if meta and meta["vol_1h"] > 0:
            momentum_bonus = min(3.0, meta["vol_1h"] / 30_000)
            if meta["buy_1h"] > meta["sell_1h"]:
                momentum_bonus += 0.5
            # Bonus for big mcap with strong vol (less rug-prone)
            if mcap >= 200_000 and meta["vol_1h"] >= 50_000:
                momentum_bonus += 1.0

        score = c["sm_wallets"] + c["kol_wallets"] * 0.7 + momentum_bonus

        ranked.append({
            "address": c["address"],
            "symbol": c["symbol"],
            "wallet_count": c["sm_wallets"],
            "kol_wallets": c["kol_wallets"],
            "signal_count": c["sm_count"] + c["kol_count"],
            "usd_volume": round(c["sm_usd"], 2),
            "market_cap_usd": mcap,
            "vol_1h": meta.get("vol_1h", 0.0),
            "buy_1h": meta.get("buy_1h", 0),
            "sell_1h": meta.get("sell_1h", 0),
            "holders": meta.get("holders", 0),
            "snipers_pct": meta.get("snipers_pct", 0.0),
            "score": round(score, 3),
            "last_seen_ms": c["last_seen_ms"],
        })

    ranked.sort(key=lambda x: x["score"], reverse=True)
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


def _kol_buys_by_addr() -> dict[str, dict[str, Any]]:
    """Cached map of {addr_lower: {wallets:int, count:int, symbol:str}} for KOL buys.
    Different signal source from smart_money — KOLs/influencers, not whales.
    Cached at 2x cycle interval (since multi-source confirmation is the
    benefit; we don't need this as fresh as the primary smart_money feed).
    """
    now = time.time()
    if now - _kol_cache["ts"] < SIGNAL_CACHE_SEC * 2 and _kol_cache["by_addr"]:
        return _kol_cache["by_addr"]
    argv = [
        CLI_BIN, "tracker", "activities",
        "--tracker-type", "kol",
        "--chain", "solana",
        "--trade-type", "1",
        "--min-volume", str(int(MIN_VOLUME_USD)),
    ]
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    if res.returncode != 0:
        return {}
    try:
        payload = json.loads(res.stdout)
    except json.JSONDecodeError:
        return {}
    trades = ((payload.get("data") or {}).get("trades")) or []
    agg = _aggregate(trades, SIGNAL_WINDOW_MIN)
    by_addr = {
        a["address"].lower(): {
            "address": a["address"],
            "wallets": len(a["wallets"]),
            "count": a["count"],
            "symbol": a["symbol"],
        }
        for a in agg.values()
    }
    _kol_cache["ts"] = now
    _kol_cache["by_addr"] = by_addr
    return by_addr


def _memepump_metadata() -> dict[str, dict[str, Any]]:
    """Cached map of {addr_lower: {anti-rug fields + momentum fields}} from the
    memepump MIGRATED token list. Used for two things:
      1) ANTI-RUG filter — reject candidates with high snipers/bundlers/dev/etc
      2) MOMENTUM filter — boost candidates with strong 1h volume + buy ratio

    Cached at 4x cycle interval (60s default) since this list updates slowly
    and we don't want to burn budget repolling.
    """
    now = time.time()
    if now - _memepump_cache["ts"] < SIGNAL_CACHE_SEC * 4 and _memepump_cache["by_addr"]:
        return _memepump_cache["by_addr"]
    argv = [
        CLI_BIN, "memepump", "tokens",
        "--chain", "solana",
        "--stage", "MIGRATED",
    ]
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    if res.returncode != 0:
        return {}
    try:
        payload = json.loads(res.stdout)
    except json.JSONDecodeError:
        return {}
    data = payload.get("data")
    tokens = data if isinstance(data, list) else (data.get("tokens") if isinstance(data, dict) else []) or []

    def _fpct(v: Any) -> float:
        try:
            return float(v) if v is not None and v != "" else 0.0
        except (TypeError, ValueError):
            return 0.0

    by_addr: dict[str, dict[str, Any]] = {}
    for t in tokens:
        # Keep original-case address — Solana base58 is case-sensitive and
        # `l` is not a valid base58 char, so .lower() will produce invalid
        # addresses for some tokens. Use lowercased version ONLY as a dict
        # key for cross-source matching.
        addr_orig = t.get("tokenAddress") or ""
        if not addr_orig:
            continue
        addr = addr_orig.lower()
        market = t.get("market") or {}
        tags = t.get("tags") or {}
        by_addr[addr] = {
            "address": addr_orig,
            "symbol": t.get("symbol") or "?",
            "name": t.get("name") or "",
            "mcap": _fpct(market.get("marketCapUsd")),
            "vol_1h": _fpct(market.get("volumeUsd1h")),
            "buy_1h": int(_fpct(market.get("buyTxCount1h"))),
            "sell_1h": int(_fpct(market.get("sellTxCount1h"))),
            "snipers_pct": _fpct(tags.get("snipersPercent")),
            "bundlers_pct": _fpct(tags.get("bundlersPercent")),
            "dev_pct": _fpct(tags.get("devHoldingsPercent")),
            "insiders_pct": _fpct(tags.get("insidersPercent")),
            "top10_pct": _fpct(tags.get("top10HoldingsPercent")),
            "holders": int(_fpct(tags.get("totalHolders"))),
        }
    _memepump_cache["ts"] = now
    _memepump_cache["by_addr"] = by_addr
    return by_addr


def _anti_rug_pass(meta: dict[str, Any]) -> tuple[bool, str]:
    """Apply rug filters to a memepump metadata blob. Returns (pass, reason).

    Returns (True, "") when token clears all filters; (False, "rug:<field>")
    when something disqualifies it. No-metadata tokens (not in memepump list)
    pass by default — we don't have rug data so we lean on the smart-money
    signal alone.
    """
    if not meta:
        return True, ""
    if meta["snipers_pct"] > MAX_SNIPERS_PCT:
        return False, f"snipers={meta['snipers_pct']:.1f}%"
    if meta["bundlers_pct"] > MAX_BUNDLERS_PCT:
        return False, f"bundlers={meta['bundlers_pct']:.1f}%"
    if meta["dev_pct"] > MAX_DEV_HOLDINGS_PCT:
        return False, f"dev={meta['dev_pct']:.1f}%"
    if meta["insiders_pct"] > MAX_INSIDERS_PCT:
        return False, f"insiders={meta['insiders_pct']:.1f}%"
    if meta["top10_pct"] > MAX_TOP10_PCT:
        return False, f"top10={meta['top10_pct']:.1f}%"
    if meta["holders"] < MIN_HOLDERS:
        return False, f"holders={meta['holders']}"
    return True, ""


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

    # 0) Refresh buy/sell signal feeds first — used by both the exit checks
    #    (sell_pressure / stale-signal) and the new-entry section below.
    ranked = _ranked_buys()
    ranked_addrs = {r["address"].lower() for r in ranked}
    sell_pressure = _sell_pressure_by_addr()

    # Update the stale-signal counter: increment for held positions not in
    # the current ranked set, reset for those that are.
    held_addrs_now = {(p["address"] or "").lower() for p in held}
    for addr in list(_stale_counter.keys()):
        if addr not in held_addrs_now:
            del _stale_counter[addr]  # position is gone, forget it
    for p in held:
        addr_lower = (p["address"] or "").lower()
        if addr_lower in ranked_addrs:
            _stale_counter[addr_lower] = 0
        else:
            _stale_counter[addr_lower] = _stale_counter.get(addr_lower, 0) + 1

    # 1) Check exits on every held position.
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

        # Signal decay: position has been absent from ranked buys for
        # STALE_CYCLES_THRESHOLD consecutive cycles → smart money has
        # walked away, exit and free the capital for fresh signals.
        stale_triggered = _stale_counter.get(addr_lower, 0) >= STALE_CYCLES_THRESHOLD

        exit_reason: str | None = None
        if trailing_triggered:
            exit_reason = "trail_from_peak"
        elif pct is not None and pct <= STOP_LOSS_PCT:
            exit_reason = "stop_loss"
        elif sell_pressure.get(addr_lower, 0) >= DUMP_WALLETS_THRESHOLD:
            exit_reason = "smart_money_dump"
        elif stale_triggered:
            exit_reason = "signal_decay"

        if exit_reason:
            actions.append({
                "action": "sell",
                "asset": p["asset"],
                "sell_all": True,
                "reason": exit_reason,
            })
            sold_addrs.add(addr_lower)
            _recently_sold[addr_lower] = time.time()

    # 2) Find buy candidates we don't already hold (and aren't selling).
    #    Also exclude any token with active dump pressure — smart money
    #    selling at the same time as buying is a contradicting signal we
    #    won't fade into. `ranked` was already fetched at the top of decide().
    held_addrs = {(p["address"] or "").lower() for p in held} - sold_addrs
    now_ts = time.time()
    new_candidates = [
        c for c in ranked
        if c["address"].lower() not in held_addrs
        and sell_pressure.get(c["address"].lower(), 0) < DUMP_WALLETS_THRESHOLD
        and (now_ts - _recently_sold.get(c["address"].lower(), 0)) >= REBUY_COOLDOWN_SEC
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
