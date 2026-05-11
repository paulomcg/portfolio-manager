"""Equity curve PNG via matplotlib.

Imported only by report.py — guarded so missing matplotlib doesn't break
the rest of PM.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def render_equity_chart(
    equity: pd.Series,
    out_path: Path,
    title: str = "Equity Curve",
    drawdown_overlay: bool = True,
) -> Path:
    """Render equity + drawdown shading to a PNG at `out_path`. Returns the path."""
    import matplotlib

    matplotlib.use("Agg")  # headless
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    fig, axes = plt.subplots(
        nrows=2 if drawdown_overlay else 1,
        ncols=1,
        figsize=(10, 6 if drawdown_overlay else 4),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1]} if drawdown_overlay else None,
    )
    if drawdown_overlay:
        ax_eq, ax_dd = axes[0], axes[1]
    else:
        ax_eq = axes if not isinstance(axes, (list, tuple)) else axes[0]
        ax_dd = None

    # Equity line
    ax_eq.plot(equity.index, equity.values, linewidth=1.6, label="Equity (USD)")
    ax_eq.fill_between(equity.index, equity.cummax(), equity.values,
                       where=(equity.values < equity.cummax()),
                       alpha=0.15, label="Underwater")
    ax_eq.set_ylabel("Equity (USD)")
    ax_eq.set_title(title)
    ax_eq.grid(alpha=0.3)
    ax_eq.legend(loc="upper left", fontsize=9)

    if ax_dd is not None:
        dd = (equity - equity.cummax()) / equity.cummax() * 100.0
        ax_dd.fill_between(equity.index, dd.values, 0, alpha=0.35, color="C3")
        ax_dd.set_ylabel("Drawdown (%)")
        ax_dd.set_ylim(min(dd.min() * 1.1, -1.0), 1.0)
        ax_dd.grid(alpha=0.3)
        if isinstance(equity.index, pd.DatetimeIndex):
            ax_dd.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
            for label in ax_dd.get_xticklabels():
                label.set_rotation(35)
                label.set_horizontalalignment("right")

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path
