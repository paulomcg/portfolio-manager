"""JSON endpoint handlers + audit / alerts / metrics readers for the dashboard.

Pure functions — server.py glues them onto routes. No I/O here beyond
reading PM's existing on-disk state (audit.jsonl, positions.sqlite,
alerts.jsonl). Strictly read-only.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import alerts, audit, config, metrics, positions, report

API_SCHEMA_VERSION = "1.0.0"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# /api/state — current positions + cash + equity from the latest audit cycle
# ---------------------------------------------------------------------------


def get_state(wallet: str | None = None) -> dict[str, Any]:
    """Return the most recent watch.cycle row's `positions` field +
    a manual-override view (from sqlite). When no audit exists yet, falls
    back to sqlite-only data."""
    rows = audit.read(limit=1)  # newest first
    last_cycle = next(
        (r for r in rows if r.get("event") == "watch.cycle"
         and (wallet is None or r.get("wallet") == wallet)),
        None,
    )
    overrides: dict[str, Any] = {}
    hwms: dict[str, float] = {}
    if wallet:
        try:
            overrides = positions.load_manual_overrides(wallet)
            hwms = positions.load_hwm_state(wallet)
        except sqlite3.Error:
            pass
    payload: dict[str, Any] = {
        "ok": True,
        "schema_version": API_SCHEMA_VERSION,
        "served_at_utc": _now_iso(),
        "wallet": wallet,
        "last_cycle": last_cycle,
        "manual_overrides": overrides,
        "high_water_marks": hwms,
    }
    if last_cycle is None:
        payload["warning"] = "no watch.cycle rows in audit yet"
    return payload


# ---------------------------------------------------------------------------
# /api/audit — recent audit rows (newest first)
# ---------------------------------------------------------------------------


def get_audit(
    *,
    limit: int = 50,
    since: str | None = None,
    event: str | None = None,
    wallet: str | None = None,
) -> dict[str, Any]:
    raw = audit.read(limit=None, since=since)
    if event:
        raw = [r for r in raw if r.get("event") == event]
    if wallet:
        raw = [r for r in raw if r.get("wallet") == wallet]
    rows = raw[: max(0, int(limit))]
    return {
        "ok": True,
        "schema_version": API_SCHEMA_VERSION,
        "count": len(rows),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# /api/alerts/pending — unacked alerts
# ---------------------------------------------------------------------------


def get_alerts_pending(
    *, wallet: str | None = None,
    severity: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    try:
        rows = alerts.pending(wallet_address=wallet, severity=severity, limit=limit)
    except sqlite3.Error as e:
        return {"ok": False, "error": str(e), "count": 0, "alerts": []}
    return {
        "ok": True,
        "schema_version": API_SCHEMA_VERSION,
        "count": len(rows),
        "alerts": rows,
    }


# ---------------------------------------------------------------------------
# /api/fills — flattened buy/sell history extracted from audit cycles
# ---------------------------------------------------------------------------


def get_fills(
    *,
    wallet: str | None = None,
    asset: str | None = None,
    limit: int = 100,
    since: str | None = None,
) -> dict[str, Any]:
    """Return the recent fills (buys + sells) across all watch cycles.

    The audit log is the source of truth; each `watch.cycle` event
    carries a `fills` list. This route flattens those into a single
    chronological stream so the UI can render an actual trades-history
    table without having to scan cycle records itself.

    Fields surfaced per fill: ts_utc, action (buy/sell/exit/trim),
    asset, qty_swapped, fill_price_usd, gross_proceeds_usd, fees_usd,
    slippage_usd, realized_pnl_usd, tx_hash, executor, source
    (strategy | rule), cycle_index.
    """
    raw = audit.read(limit=None, since=since)
    out: list[dict[str, Any]] = []
    for r in raw:
        if r.get("event") != "watch.cycle":
            continue
        if wallet and r.get("wallet") != wallet:
            continue
        cycle_ts = r.get("ts_utc")
        cycle_index = r.get("cycle_index")
        for f in r.get("fills") or []:
            if not isinstance(f, dict):
                continue
            if f.get("ok") is False:
                continue
            if asset and (f.get("asset") or "").upper() != asset.upper():
                continue
            out.append({
                "ts_utc": cycle_ts,
                "cycle_index": cycle_index,
                "action": f.get("action"),
                "asset": f.get("asset"),
                "qty_swapped": f.get("qty_swapped"),
                "fill_price_usd": f.get("fill_price_usd"),
                "gross_proceeds_usd": f.get("gross_proceeds_usd"),
                "fees_usd": f.get("fees_usd"),
                "slippage_usd": f.get("slippage_usd"),
                "realized_pnl_usd": f.get("realized_pnl_usd"),
                "tx_hash": f.get("tx_hash"),
                "executor": f.get("executor"),
                "source": f.get("source"),
            })
    # Newest first, then cap
    out.sort(key=lambda x: x.get("ts_utc") or "", reverse=True)
    out = out[: max(0, int(limit))]
    return {
        "ok": True,
        "schema_version": API_SCHEMA_VERSION,
        "count": len(out),
        "fills": out,
    }


# ---------------------------------------------------------------------------
# /api/equity — equity time series from audit
# ---------------------------------------------------------------------------


def get_equity(
    *, wallet: str | None = None,
    since: str | None = None,
    until: str | None = None,
    audit_path: Path | None = None,
) -> dict[str, Any]:
    """Reconstruct an equity series from the audit. Compatible with pm report's
    output but lighter — returns a plain list of {ts, equity, drawdown_pct}."""
    cycles = report.load_cycles(
        audit_path or config.audit_path(),
        wallet=wallet, since=since, until=until,
    )
    series = report.build_equity_series(cycles)
    if series.empty:
        return {"ok": True, "schema_version": API_SCHEMA_VERSION, "count": 0,
                "series": [], "warning": "no equity data in audit"}
    cummax = series.cummax()
    drawdown_pct = ((series - cummax) / cummax * 100.0).where(cummax > 0, 0.0)
    out = [
        {"ts": str(ts), "equity_usd": float(v), "drawdown_pct": round(float(d), 4)}
        for ts, v, d in zip(series.index, series.values, drawdown_pct.values)
    ]
    return {
        "ok": True,
        "schema_version": API_SCHEMA_VERSION,
        "count": len(out),
        "series": out,
    }


# ---------------------------------------------------------------------------
# /api/metrics — Sharpe/Sortino/maxDD computed live from the audit
# ---------------------------------------------------------------------------


def get_metrics(
    *, wallet: str | None = None,
    since: str | None = None,
    until: str | None = None,
    audit_path: Path | None = None,
) -> dict[str, Any]:
    cycles = report.load_cycles(
        audit_path or config.audit_path(),
        wallet=wallet, since=since, until=until,
    )
    equity = report.build_equity_series(cycles)
    fills = report.collect_fills(cycles)
    m = report.compute_metrics(equity, fills)
    return {"ok": True, "schema_version": API_SCHEMA_VERSION, "metrics": m,
            "cycle_count": len(cycles)}


# ---------------------------------------------------------------------------
# /api/safety — kill-switch budget + active rules, derived from watch.start + audit
# ---------------------------------------------------------------------------


def get_safety(*, wallet: str | None = None) -> dict[str, Any]:
    """Combine the most recent watch.start metadata with running realized loss
    so the dashboard can render the kill-switch + active-rules panels.

    Loss is summed from realized_pnl_usd < 0 across all fills since the most
    recent watch.start (or all history if none found).
    """
    raw = audit.read(limit=None)  # newest first

    start_row: dict[str, Any] | None = None
    for r in raw:
        if r.get("event") != "watch.start":
            continue
        if wallet is not None and r.get("wallet") != wallet:
            continue
        start_row = r
        break

    start_ts = start_row.get("ts_utc") if start_row else None
    max_loss_usd = float(start_row.get("max_loss_usd") or 0) if start_row else 0.0

    realized_loss = 0.0
    realized_gain = 0.0
    fills_count = 0
    for r in raw:
        if r.get("event") != "watch.cycle":
            continue
        if wallet is not None and r.get("wallet") != wallet:
            continue
        if start_ts and (r.get("ts_utc") or "") < start_ts:
            continue
        for f in r.get("fills") or []:
            if f.get("ok") is False:
                continue
            try:
                rpnl = float(f.get("realized_pnl_usd") or 0)
            except (TypeError, ValueError):
                continue
            fills_count += 1
            if rpnl < 0:
                realized_loss += -rpnl
            elif rpnl > 0:
                realized_gain += rpnl

    pct_consumed = (realized_loss / max_loss_usd * 100.0) if max_loss_usd > 0 else 0.0
    if pct_consumed >= 100:
        status = "halted"
    elif pct_consumed >= 80:
        status = "critical"
    elif pct_consumed >= 50:
        status = "warning"
    else:
        status = "ok"

    return {
        "ok": True,
        "schema_version": API_SCHEMA_VERSION,
        "served_at_utc": _now_iso(),
        "wallet": wallet,
        "kill_switch": {
            "active": start_row is not None and start_row.get("mode") == "live",
            "max_loss_usd": max_loss_usd,
            "realized_loss_usd": round(realized_loss, 4),
            "realized_gain_usd": round(realized_gain, 4),
            "net_realized_usd": round(realized_gain - realized_loss, 4),
            "percent_consumed": round(pct_consumed, 2),
            "status": status,
            "fills_since_start": fills_count,
            "started_at_utc": start_ts,
        },
        "rules": (start_row.get("rules") or []) if start_row else [],
        "universe": (start_row.get("universe") or []) if start_row else [],
        "mode": start_row.get("mode") if start_row else None,
        "strategy_loaded": bool(start_row.get("strategy_loaded")) if start_row else False,
    }


# ---------------------------------------------------------------------------
# /api/snapshot — combined state + recent audit + alerts (for one-shot page load)
# ---------------------------------------------------------------------------


def get_snapshot(*, wallet: str | None = None, audit_limit: int = 30) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": API_SCHEMA_VERSION,
        "served_at_utc": _now_iso(),
        "wallet": wallet,
        "state": get_state(wallet=wallet),
        "audit": get_audit(limit=audit_limit, wallet=wallet),
        "alerts_pending": get_alerts_pending(wallet=wallet),
        "metrics": get_metrics(wallet=wallet),
        "safety": get_safety(wallet=wallet),
    }
