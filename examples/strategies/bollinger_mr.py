"""Strategy #2 — Bollinger Band mean reversion.

Hypothesis: extreme price deviations from a rolling mean revert. Crypto's
chop-vs-trend regimes can be diagnosed by whether this strategy works on
the asset's history — if it profits, the asset is mean-reverting in the
backtest window; if it gets run over, the asset is in a trending regime
and momentum strategies are the right shape instead.

Logic:
  - Compute rolling mean μ and stddev σ over N bars (default 20).
  - Enter long when close < μ − 2σ (oversold by 2σ).
  - Exit when close ≥ μ (back at the mean).
  - Stop-loss at μ − 3σ is left to the rule engine's trailing-stop
    (configure rules.yaml to taste).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _signal_helpers import closes, holding, rolling_mean, rolling_std  # noqa: E402

ASSET = "WSOL"
LOOKBACK_BARS = 20
ENTRY_K = 2.0     # buy at μ − ENTRY_K * σ
EXIT_K = 0.0      # exit at μ (set to e.g. 1.0 for "halfway band exit")
ENTRY_AMOUNT_USD = 30.0


def decide(state, market_data):
    md = market_data.get(ASSET) or {}
    closes_list = closes(md.get("history"))
    if len(closes_list) < LOOKBACK_BARS + 1:
        return []

    mu = rolling_mean(closes_list, LOOKBACK_BARS)
    sigma = rolling_std(closes_list, LOOKBACK_BARS)
    if mu is None or sigma is None or sigma <= 0:
        return []

    current_close = closes_list[-1]
    lower_band = mu - ENTRY_K * sigma
    exit_level = mu - EXIT_K * sigma

    have_position = holding(state, ASSET)

    if not have_position:
        if current_close < lower_band:
            if state.get("cash_usd", 0) < ENTRY_AMOUNT_USD:
                return []
            return [{"action": "buy", "asset": ASSET, "amount_usd": ENTRY_AMOUNT_USD}]
        return []

    # holding — exit when price has reverted at or above the exit level
    if current_close >= exit_level:
        return [{"action": "sell", "asset": ASSET, "sell_all": True}]
    return []
