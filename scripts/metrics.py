"""Risk-adjusted metric calculations.

Pure math: every function takes a pandas Series of equity (or a list of
trade dicts) and returns floats. No I/O. Callers wire them up against the
audit log via report.py.

Functions are tested against hand-computed truth (test_metrics.py).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

PERIODS_PER_YEAR_DEFAULT = 365  # daily bars assumed; override per-call when bar differs


def equity_returns(equity: pd.Series) -> pd.Series:
    """Per-bar simple returns from an equity series. First value is NaN."""
    return equity.pct_change()


def total_return_pct(equity: pd.Series) -> float:
    """End/start - 1, in percent."""
    if len(equity) < 2 or equity.iloc[0] == 0:
        return 0.0
    return float((equity.iloc[-1] / equity.iloc[0] - 1.0) * 100.0)


def cagr_pct(equity: pd.Series, periods_per_year: int = PERIODS_PER_YEAR_DEFAULT) -> float:
    """Compound annual growth rate, in percent."""
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return 0.0
    years = (len(equity) - 1) / periods_per_year
    if years <= 0:
        return 0.0
    final_over_initial = equity.iloc[-1] / equity.iloc[0]
    if final_over_initial <= 0:
        return -100.0
    return float((final_over_initial ** (1.0 / years) - 1.0) * 100.0)


def sharpe(equity: pd.Series, rf_per_period: float = 0.0,
           periods_per_year: int = PERIODS_PER_YEAR_DEFAULT) -> float:
    """Annualized Sharpe ratio. Uses per-bar simple returns. 0.0 when stdev is 0."""
    rets = equity_returns(equity).dropna()
    if len(rets) < 2:
        return 0.0
    excess = rets - rf_per_period
    std = excess.std(ddof=1)
    if std == 0 or math.isnan(std):
        return 0.0
    return float(excess.mean() / std * math.sqrt(periods_per_year))


def sortino(equity: pd.Series, rf_per_period: float = 0.0,
            periods_per_year: int = PERIODS_PER_YEAR_DEFAULT) -> float:
    """Annualized Sortino — uses downside (negative-excess) std only."""
    rets = equity_returns(equity).dropna()
    if len(rets) < 2:
        return 0.0
    excess = rets - rf_per_period
    downside = excess[excess < 0]
    if len(downside) < 2:
        # Strategy has no losing periods — undefined; return 0 conservatively.
        return 0.0
    downside_std = downside.std(ddof=1)
    if downside_std == 0 or math.isnan(downside_std):
        return 0.0
    return float(excess.mean() / downside_std * math.sqrt(periods_per_year))


def max_drawdown(equity: pd.Series) -> dict[str, Any]:
    """Return {pct, peak_idx, trough_idx, peak_value, trough_value}.

    pct is positive (e.g. 12.5 means a 12.5% drawdown). 0.0 when equity
    is monotonically non-decreasing.
    """
    if len(equity) < 2:
        return {"pct": 0.0, "peak_idx": None, "trough_idx": None,
                "peak_value": None, "trough_value": None}
    cumax = equity.cummax()
    drawdowns = (equity - cumax) / cumax  # negative or zero
    trough_pos = drawdowns.idxmin()
    if drawdowns.loc[trough_pos] >= 0:
        return {"pct": 0.0, "peak_idx": None, "trough_idx": None,
                "peak_value": None, "trough_value": None}
    # The peak is the cumax up to the trough.
    peak_val = float(cumax.loc[trough_pos])
    trough_val = float(equity.loc[trough_pos])
    # Find the latest index where equity == peak_val before the trough.
    pre_trough = equity.loc[:trough_pos]
    peak_pos = pre_trough[pre_trough == peak_val].index[-1]
    pct = abs(float(drawdowns.loc[trough_pos]) * 100.0)
    return {
        "pct": pct,
        "peak_idx": peak_pos,
        "trough_idx": trough_pos,
        "peak_value": peak_val,
        "trough_value": trough_val,
    }


def calmar(equity: pd.Series, periods_per_year: int = PERIODS_PER_YEAR_DEFAULT) -> float:
    """CAGR / max DD (as decimal). 0.0 when no drawdown."""
    cagr = cagr_pct(equity, periods_per_year=periods_per_year) / 100.0
    dd = max_drawdown(equity)["pct"] / 100.0
    if dd == 0:
        return 0.0
    return float(cagr / dd)


def trade_stats(fills: list[dict[str, Any]]) -> dict[str, Any]:
    """Win rate, expectancy, count, sum_pnl across a list of fill dicts.

    Each fill must have a numeric ``realized_pnl_usd``. Buys with no realized
    pnl (still open) are skipped.
    """
    if not fills:
        return {"trades": 0, "win_rate": 0.0, "expectancy_usd": 0.0,
                "total_pnl_usd": 0.0, "winners": 0, "losers": 0}
    closed = [
        f for f in fills
        if "realized_pnl_usd" in f
        and f.get("realized_pnl_usd") is not None
        # Buys can have realized_pnl=0 if we record opens too; only count
        # actions that actually realized something.
        and f.get("action") in ("sell", "exit", "trim", "halt")
    ]
    if not closed:
        return {"trades": 0, "win_rate": 0.0, "expectancy_usd": 0.0,
                "total_pnl_usd": 0.0, "winners": 0, "losers": 0}
    pnls = [float(f["realized_pnl_usd"]) for f in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    n = len(pnls)
    total = sum(pnls)
    win_rate = len(wins) / n if n else 0.0
    expectancy = total / n if n else 0.0
    return {
        "trades": n,
        "winners": len(wins),
        "losers": len(losses),
        "win_rate": round(win_rate, 4),
        "expectancy_usd": round(expectancy, 4),
        "total_pnl_usd": round(total, 2),
    }


def per_asset_pnl(fills: list[dict[str, Any]]) -> dict[str, float]:
    """Sum realized PnL per asset across closed fills."""
    out: dict[str, float] = {}
    for f in fills:
        if f.get("action") not in ("sell", "exit", "trim", "halt"):
            continue
        pnl = f.get("realized_pnl_usd")
        asset = f.get("asset")
        if asset is None or pnl is None:
            continue
        out[asset] = round(out.get(asset, 0.0) + float(pnl), 2)
    return out


def infer_periods_per_year(ts_index: pd.Series | pd.DatetimeIndex) -> int:
    """Estimate the natural bars-per-year from the median bar interval.

    Helps `pm report` annualize Sharpe/Sortino without a flag.
    """
    if len(ts_index) < 2:
        return PERIODS_PER_YEAR_DEFAULT
    if isinstance(ts_index, pd.DatetimeIndex):
        deltas = pd.Series(ts_index).diff().dropna()
    else:
        deltas = pd.to_datetime(ts_index).diff().dropna()
    if len(deltas) == 0:
        return PERIODS_PER_YEAR_DEFAULT
    median = deltas.median()
    seconds = median.total_seconds()
    if seconds <= 0:
        return PERIODS_PER_YEAR_DEFAULT
    seconds_per_year = 365 * 24 * 3600
    return max(1, int(round(seconds_per_year / seconds)))
