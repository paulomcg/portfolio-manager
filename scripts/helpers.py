"""Helpers strategies import to keep their decide() bodies short.

Pure functions, no I/O, no PM internals. The intent is that a typical
agent-authored strategy reads like:

    from pm.helpers import every_n_bars, has_position

    def decide(state, market_data):
        if every_n_bars(state['cycle_index'], 7) and not has_position(state, 'WSOL'):
            return [{'action': 'buy', 'asset': 'WSOL', 'amount_usd': 100}]
        return []

Strategies are also welcome to ignore these and do their own thing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

try:
    import pandas as pd
except ImportError:  # pandas is optional at import time so a strategy file with
    pd = None         # `from pm.helpers import x` works in environments without pd.

_WEEKDAYS = {
    "mon": 0, "monday": 0,
    "tue": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def every_n_bars(cycle_index: int, n: int) -> bool:
    """True when cycle_index is a multiple of n. Strategies use this for
    scheduled actions (e.g. every 7 cycles = weekly on a 1D-bar feed)."""
    if n <= 0:
        raise ValueError("n must be positive")
    return cycle_index % n == 0


def calendar_aligned(ts_utc: str, weekday: str = "mon") -> bool:
    """True when the ts (ISO 8601) falls on the given weekday. Use for
    calendar-driven schedules ('every Monday') instead of cycle-counting.
    """
    target = _WEEKDAYS.get(weekday.lower())
    if target is None:
        raise ValueError(f"unknown weekday: {weekday!r}")
    # Tolerate trailing 'Z'
    if ts_utc.endswith("Z"):
        ts_utc = ts_utc[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts_utc)
    return dt.weekday() == target


def rolling_return(history, lookback_bars: int) -> float | None:
    """Return over the last `lookback_bars` bars, computed from close prices.

    Accepts either a pandas DataFrame with a 'c' (close) column, or a list
    of dicts with a 'c' key (mirrors the bar shape PM passes). Returns None
    when there aren't enough bars.
    """
    if pd is not None and isinstance(history, pd.DataFrame):
        if len(history) < lookback_bars + 1:
            return None
        first = float(history["c"].iloc[-lookback_bars - 1])
        last = float(history["c"].iloc[-1])
    else:
        if not isinstance(history, list) or len(history) < lookback_bars + 1:
            return None
        first = float(history[-lookback_bars - 1]["c"])
        last = float(history[-1]["c"])
    if first <= 0:
        return None
    return (last - first) / first


def has_position(state: dict[str, Any], asset: str) -> bool:
    """True when `state` contains a non-zero position in `asset`."""
    for p in state.get("positions", []):
        if p.get("asset") == asset and float(p.get("qty", 0)) > 0:
            return True
    return False


def position_pct_of_equity(state: dict[str, Any], asset: str) -> float:
    """Position's USD value as a % of total equity. 0.0 when not held."""
    equity = float(state.get("total_equity_usd", 0))
    if equity <= 0:
        return 0.0
    for p in state.get("positions", []):
        if p.get("asset") == asset:
            return (float(p.get("value_usd", 0)) / equity) * 100.0
    return 0.0


def cash_pct_of_equity(state: dict[str, Any]) -> float:
    """Cash as a % of total equity."""
    equity = float(state.get("total_equity_usd", 0))
    if equity <= 0:
        return 0.0
    return (float(state.get("cash_usd", 0)) / equity) * 100.0


def get_position(state: dict[str, Any], asset: str) -> dict[str, Any] | None:
    """Convenience: return the position dict for `asset`, or None."""
    for p in state.get("positions", []):
        if p.get("asset") == asset:
            return p
    return None
