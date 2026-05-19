"""Strategy #1 — Dual-horizon momentum confirmation.

Hypothesis: most single-timeframe momentum signals in crypto get chopped
because they fire on noise. Requiring agreement between a short window
and a longer window filters out signals that don't have multi-horizon
support, at the cost of slower entries.

Default tunings target 1H bars (the backtester's cached SOL fixture):
  SHORT_BARS=6   →  6-hour return
  LONG_BARS=24   →  24-hour (one-day) return
Adjust if running on a different bar size.

Behavior:
  - Enter long when BOTH the short return and the long return clear
    ENTRY_THRESHOLD positively.
  - Exit when EITHER drops below EXIT_THRESHOLD.
  - Rule engine (trailing-stop / halt-on-drawdown) is the safety net
    on top of strategy exits.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _signal_helpers import closes, holding, window_return  # noqa: E402

ASSET = "WSOL"
SHORT_BARS = 6
LONG_BARS = 24
ENTRY_THRESHOLD = 0.005   # +0.5% on both timeframes to open
EXIT_THRESHOLD = -0.002   # −0.2% on either timeframe to close
ENTRY_AMOUNT_USD = 30.0


def decide(state, market_data):
    md = market_data.get(ASSET) or {}
    closes_list = closes(md.get("history"))

    short_ret = window_return(closes_list, SHORT_BARS)
    long_ret = window_return(closes_list, LONG_BARS)
    if short_ret is None or long_ret is None:
        return []

    have_position = holding(state, ASSET)

    if not have_position:
        if short_ret >= ENTRY_THRESHOLD and long_ret >= ENTRY_THRESHOLD:
            if state.get("cash_usd", 0) < ENTRY_AMOUNT_USD:
                return []
            return [{"action": "buy", "asset": ASSET, "amount_usd": ENTRY_AMOUNT_USD}]
        return []

    # holding — check for exit
    if short_ret <= EXIT_THRESHOLD or long_ret <= EXIT_THRESHOLD:
        return [{"action": "sell", "asset": ASSET, "sell_all": True}]
    return []
