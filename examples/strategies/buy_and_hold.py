"""Buy and Hold — opens one position at startup, then holds forever.

Usage:
    pm watch --config <rules.yaml> --strategy examples/strategies/buy_and_hold.py \
             --wallet <addr> --interval 60

By default it buys WSOL with all available cash on cycle 0. Adjust ASSET
and INITIAL_USD to taste.
"""

from __future__ import annotations

ASSET = "WSOL"          # which token to buy
INITIAL_USD = None      # None = "all cash on cycle 0"; or set a USD number


def decide(state, market_data):
    # Only act on the very first cycle of this PM session.
    if state.get("cycle_index", 0) != 0:
        return []

    # Don't re-buy if PM already sees the position (e.g., session restart).
    for p in state.get("positions", []):
        if p["asset"] == ASSET and float(p["qty"]) > 0:
            return []

    amount = INITIAL_USD if INITIAL_USD is not None else state.get("cash_usd", 0)
    if amount <= 0:
        return []

    return [{"action": "buy", "asset": ASSET, "amount_usd": float(amount)}]
