"""Weekly DCA — buys a fixed USD amount on a 7-cycle cadence.

Assumes a 1D bar timeframe / 1-day cycle (--interval 86400 in live mode,
or daily bars in backtest). Adjust DCA_EVERY_N_CYCLES if your bar/interval
differs.

Usage:
    pm watch --config <rules.yaml> --strategy examples/strategies/weekly_dca.py \
             --wallet <addr>
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow the strategy file to be loaded standalone OR via PM's importlib path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.helpers import every_n_bars  # noqa: E402

ASSET = "WSOL"
DCA_AMOUNT_USD = 100.0
DCA_EVERY_N_CYCLES = 7    # 7 daily bars = weekly


def decide(state, market_data):
    cycle = state.get("cycle_index", 0)
    if not every_n_bars(cycle, DCA_EVERY_N_CYCLES):
        return []
    if state.get("cash_usd", 0) < DCA_AMOUNT_USD:
        return []
    return [{"action": "buy", "asset": ASSET, "amount_usd": DCA_AMOUNT_USD}]
