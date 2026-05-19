"""Shared signal-computation helpers used by the strategy examples.

Designed to be called from `decide(state, market_data)` callbacks. The
`history` argument is permissive — accepts a pandas DataFrame (live mode,
from market_data.py) or a list of bar-dicts (backtest mode, from
replay.py). All helpers return None when there isn't enough data.
"""

from __future__ import annotations

from math import sqrt
from typing import Any


def closes(history: Any) -> list[float]:
    """Extract the close-price series (chronological order) from history."""
    if history is None:
        return []
    if hasattr(history, "iloc"):  # pandas DataFrame
        return [float(c) for c in history["c"].tolist()]
    return [float(b["c"]) for b in history]


def window_return(closes_list: list[float], lookback: int) -> float | None:
    """Simple return over the last `lookback` bars. None if insufficient data."""
    if len(closes_list) < lookback + 1:
        return None
    base = closes_list[-lookback - 1]
    last = closes_list[-1]
    if base <= 0:
        return None
    return (last - base) / base


def rolling_mean(closes_list: list[float], lookback: int) -> float | None:
    if len(closes_list) < lookback:
        return None
    window = closes_list[-lookback:]
    return sum(window) / len(window)


def rolling_std(closes_list: list[float], lookback: int) -> float | None:
    if len(closes_list) < lookback:
        return None
    window = closes_list[-lookback:]
    m = sum(window) / len(window)
    var = sum((x - m) ** 2 for x in window) / len(window)
    return sqrt(var)


def realized_vol(closes_list: list[float], lookback: int) -> float | None:
    """Realized vol = stdev of bar-to-bar simple returns over `lookback` bars.

    Returns the *per-bar* standard deviation. Multiply by sqrt(bars_per_day)
    to get a daily-vol estimate, or sqrt(bars_per_year) for annualized.
    """
    if len(closes_list) < lookback + 1:
        return None
    rets: list[float] = []
    for i in range(-lookback, 0):
        base = closes_list[i - 1]
        cur = closes_list[i]
        if base <= 0:
            return None
        rets.append((cur - base) / base)
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / len(rets)
    return sqrt(var)


def holding(state: dict[str, Any], asset: str) -> bool:
    for p in state.get("positions", []) or []:
        if p.get("asset") == asset and float(p.get("qty", 0)) > 0:
            return True
    return False
