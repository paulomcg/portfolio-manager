"""`pm report` implementation.

Reads PM's `watch.cycle` audit row schema (v1.0.0), reconstructs an equity
time series, computes risk-adjusted metrics, and writes:
- report.json (stable schema)
- report.md (human-readable summary)
- equity.png (matplotlib equity curve + drawdown shading)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from . import audit as audit_mod
from . import config, metrics

SCHEMA_VERSION = "1.0.0"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_cycles(
    audit_path: Path,
    wallet: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[dict[str, Any]]:
    """Read audit.jsonl and return only watch.cycle rows in chronological order."""
    rows = audit_mod.read(path=audit_path)
    cycles: list[dict[str, Any]] = []
    for r in rows:
        if r.get("event") != "watch.cycle":
            continue
        if wallet is not None and r.get("wallet") != wallet:
            continue
        ts = r.get("ts_utc", "")
        if since and ts < since:
            continue
        if until and ts > until:
            continue
        cycles.append(r)
    # audit_mod.read returns newest-first; reverse for chronological order.
    cycles.sort(key=lambda r: r.get("ts_utc", ""))
    return cycles


def build_equity_series(cycles: list[dict[str, Any]]) -> pd.Series:
    """Construct an equity time series from cycle rows.

    Index = parsed timestamps; values = positions.total_equity_usd.
    """
    rows = []
    for c in cycles:
        eq = (c.get("positions") or {}).get("total_equity_usd")
        ts = c.get("ts_utc")
        if eq is None or ts is None:
            continue
        try:
            ts_parsed = pd.to_datetime(ts)
        except (TypeError, ValueError):
            continue
        rows.append((ts_parsed, float(eq)))
    if not rows:
        return pd.Series([], dtype=float, name="equity_usd")
    df = pd.DataFrame(rows, columns=["ts", "equity_usd"]).set_index("ts")
    # Collapse duplicates (multiple cycles at same ts) to last value
    df = df[~df.index.duplicated(keep="last")]
    return df["equity_usd"].sort_index()


def collect_fills(cycles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten fills[] across all cycles, preserving ts_utc + source."""
    out: list[dict[str, Any]] = []
    for c in cycles:
        for f in c.get("fills") or []:
            f2 = {**f, "_cycle_ts_utc": c.get("ts_utc")}
            out.append(f2)
    return out


def compute_metrics(equity: pd.Series, fills: list[dict[str, Any]]) -> dict[str, Any]:
    if len(equity) < 2:
        return {
            "schema_version": SCHEMA_VERSION,
            "bars": len(equity),
            "warning": "insufficient cycles for metrics (need >=2 cycles with total_equity_usd)",
        }
    ppy = metrics.infer_periods_per_year(equity.index)
    dd = metrics.max_drawdown(equity)
    return {
        "schema_version": SCHEMA_VERSION,
        "bars": len(equity),
        "periods_per_year": ppy,
        "initial_equity_usd": float(equity.iloc[0]),
        "final_equity_usd": float(equity.iloc[-1]),
        "total_return_pct": round(metrics.total_return_pct(equity), 6),
        "cagr_pct": round(metrics.cagr_pct(equity, ppy), 6),
        "sharpe": round(metrics.sharpe(equity, periods_per_year=ppy), 6),
        "sortino": round(metrics.sortino(equity, periods_per_year=ppy), 6),
        "calmar": round(metrics.calmar(equity, periods_per_year=ppy), 6),
        "max_drawdown_pct": round(dd["pct"], 6),
        "max_drawdown_peak_ts": str(dd["peak_idx"]) if dd["peak_idx"] is not None else None,
        "max_drawdown_trough_ts": str(dd["trough_idx"]) if dd["trough_idx"] is not None else None,
        "trades": metrics.trade_stats(fills),
        "per_asset_pnl_usd": metrics.per_asset_pnl(fills),
    }


def write_report_json(out_dir: Path, report: dict[str, Any]) -> Path:
    p = out_dir / "report.json"
    p.write_text(json.dumps(report, indent=2, default=str))
    return p


def write_equity_png(out_dir: Path, equity: pd.Series, title: str) -> Path | None:
    if len(equity) < 2:
        return None
    from . import chart
    return chart.render_equity_chart(equity, out_dir / "equity.png", title=title)


def write_report_md(out_dir: Path, report: dict[str, Any], png_path: Path | None) -> Path:
    metrics_section = report.get("metrics", {})
    trades = metrics_section.get("trades", {})
    per_asset = metrics_section.get("per_asset_pnl_usd", {})

    def _fmt_pct(x):
        return f"{x:.2f}%" if isinstance(x, (int, float)) else "n/a"

    def _fmt_num(x, digits=4):
        return f"{x:.{digits}f}" if isinstance(x, (int, float)) else "n/a"

    lines = [
        f"# PM Report — {report.get('wallet_filter', 'all wallets')}",
        "",
        f"_Generated {report.get('generated_at_utc', '')}_  ",
        f"Audit source: `{report.get('audit_path', '')}`  ",
        f"Cycles: **{report.get('cycle_count', 0)}**  "
        f"Bars used: **{metrics_section.get('bars', 0)}**  "
        f"Inferred periods/year: **{metrics_section.get('periods_per_year', '?')}**",
        "",
        "## Headline metrics",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Initial equity (USD) | {_fmt_num(metrics_section.get('initial_equity_usd'), 2)} |",
        f"| Final equity (USD) | {_fmt_num(metrics_section.get('final_equity_usd'), 2)} |",
        f"| Total return | {_fmt_pct(metrics_section.get('total_return_pct'))} |",
        f"| CAGR | {_fmt_pct(metrics_section.get('cagr_pct'))} |",
        f"| Sharpe | {_fmt_num(metrics_section.get('sharpe'))} |",
        f"| Sortino | {_fmt_num(metrics_section.get('sortino'))} |",
        f"| Calmar | {_fmt_num(metrics_section.get('calmar'))} |",
        f"| Max drawdown | {_fmt_pct(metrics_section.get('max_drawdown_pct'))} |",
        "",
        "## Trade activity",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Closed trades | {trades.get('trades', 0)} |",
        f"| Winners | {trades.get('winners', 0)} |",
        f"| Losers | {trades.get('losers', 0)} |",
        f"| Win rate | {_fmt_pct((trades.get('win_rate') or 0) * 100)} |",
        f"| Expectancy / trade (USD) | {_fmt_num(trades.get('expectancy_usd'), 2)} |",
        f"| Total realized PnL (USD) | {_fmt_num(trades.get('total_pnl_usd'), 2)} |",
        "",
    ]
    if per_asset:
        lines.append("## Realized PnL by asset")
        lines.append("")
        lines.append("| Asset | PnL (USD) |")
        lines.append("|---|---|")
        for asset, pnl in sorted(per_asset.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {asset} | {_fmt_num(pnl, 2)} |")
        lines.append("")
    if png_path is not None:
        lines.append(f"![Equity curve]({png_path.name})")
        lines.append("")
    if "warning" in metrics_section:
        lines.append(f"> ⚠️  {metrics_section['warning']}")
        lines.append("")

    p = out_dir / "report.md"
    p.write_text("\n".join(lines))
    return p


def run(
    audit_path: Path | None = None,
    wallet: str | None = None,
    since: str | None = None,
    until: str | None = None,
    out: Path | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Top-level entry: produce report.{json,md,png} from an audit log.

    Returns the report dict (also persisted as report.json).
    """
    audit_path = Path(audit_path) if audit_path else config.audit_path()
    if out is None:
        raise ValueError("report.run requires --out <dir>")
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)

    cycles = load_cycles(audit_path, wallet=wallet, since=since, until=until)
    equity = build_equity_series(cycles)
    fills = collect_fills(cycles)
    m = compute_metrics(equity, fills)

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _now_iso(),
        "audit_path": str(audit_path),
        "wallet_filter": wallet or "(all)",
        "since": since,
        "until": until,
        "cycle_count": len(cycles),
        "metrics": m,
        "equity_curve": [
            {"ts_utc": str(ts), "equity_usd": float(v)}
            for ts, v in equity.items()
        ],
        "fills_count": len(fills),
    }
    png = write_equity_png(
        out_dir, equity,
        title=title or f"PM equity — {wallet or 'all wallets'} ({len(equity)} bars)",
    )
    write_report_json(out_dir, report)
    write_report_md(out_dir, report, png)
    return report
