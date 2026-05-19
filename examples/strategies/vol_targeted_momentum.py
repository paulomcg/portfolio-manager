"""Strategy #5 — Volatility-targeted momentum.

Hypothesis: vol-targeting smooths the equity curve and improves risk-
adjusted returns vs flat-sizing. Standard quant tradition: same entry
signal as #1, but position size scales inversely with realized vol so
high-vol regimes get smaller bets and low-vol regimes get larger ones.

Default tunings target 1H bars:
  ENTRY_BARS=6  →  6-hour return threshold for entry
  VOL_BARS=24   →  realized vol estimated over the prior 24 bars
  TARGET_DAILY_DOLLAR_VOL = USD volatility we're willing to tolerate
                            per day (so size = target / actual)
  BARS_PER_DAY = 24 (on 1H bars)

Sizing:
  per_bar_vol = stdev(returns over VOL_BARS)
  daily_vol = per_bar_vol * sqrt(BARS_PER_DAY)
  size_usd = TARGET_DAILY_DOLLAR_VOL / daily_vol
  clamped to [MIN_USD, MAX_USD]
"""

from __future__ import annotations

import sys
from math import sqrt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _signal_helpers import closes, holding, realized_vol, window_return  # noqa: E402

ASSET = "WSOL"
ENTRY_BARS = 6
VOL_BARS = 24
BARS_PER_DAY = 24  # 1H bars → 24/day
ENTRY_THRESHOLD = 0.005
EXIT_THRESHOLD = -0.002
TARGET_DAILY_DOLLAR_VOL = 1.0   # willing to swing $1 of equity per day from this position
MIN_USD = 10.0
MAX_USD = 40.0


def decide(state, market_data):
    md = market_data.get(ASSET) or {}
    closes_list = closes(md.get("history"))
    short_ret = window_return(closes_list, ENTRY_BARS)
    bar_vol = realized_vol(closes_list, VOL_BARS)
    if short_ret is None or bar_vol is None or bar_vol <= 0:
        return []

    have_position = holding(state, ASSET)

    if have_position:
        # Exit on momentum reversal — same signal as entry, sign-flipped.
        if short_ret <= EXIT_THRESHOLD:
            return [{"action": "sell", "asset": ASSET, "sell_all": True}]
        return []

    # Not holding — check entry + vol-target sizing.
    if short_ret < ENTRY_THRESHOLD:
        return []

    daily_vol_frac = bar_vol * sqrt(BARS_PER_DAY)
    if daily_vol_frac <= 0:
        return []
    size_usd = TARGET_DAILY_DOLLAR_VOL / daily_vol_frac
    size_usd = max(MIN_USD, min(MAX_USD, size_usd))
    if state.get("cash_usd", 0) < size_usd:
        return []
    return [{"action": "buy", "asset": ASSET, "amount_usd": size_usd}]
