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
    }
