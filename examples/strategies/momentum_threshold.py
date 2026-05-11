"""Momentum Threshold — buys when N-bar return clears a positive threshold,
sells when it crosses a negative threshold. Signal-driven, single asset.

Reads `market_data['WSOL']['history']` (a pandas DataFrame); cold-starts
gracefully when there isn't enough history yet.

Usage:
    pm watch --config <rules.yaml> --strategy examples/strategies/momentum_threshold.py \
             --wallet <addr>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.helpers import has_position, rolling_return  # noqa: E402

ASSET = "WSOL"
LOOKBACK_BARS = 20
ENTRY_THRESHOLD = 0.05      # +5% rolling return → buy
EXIT_THRESHOLD = -0.05      # -5% rolling return → sell
ENTRY_AMOUNT_USD = 100.0


def decide(state, market_data):
    asset_md = market_data.get(ASSET, {})
    history = asset_md.get("history")
    if history is None:
        return []  # cold start: market_data layer hasn't delivered yet

    ret = rolling_return(history, LOOKBACK_BARS)
    if ret is None:
        return []  # not enough history yet

    holding = has_position(state, ASSET)
    if ret >= ENTRY_THRESHOLD and not holding:
        if state.get("cash_usd", 0) < ENTRY_AMOUNT_USD:
            return []
        return [{"action": "buy", "asset": ASSET, "amount_usd": ENTRY_AMOUNT_USD}]
    if ret <= EXIT_THRESHOLD and holding:
        return [{"action": "sell", "asset": ASSET, "sell_all": True}]
    return []
