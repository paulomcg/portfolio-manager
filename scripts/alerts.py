"""Alerts queue — durable, ack-able alert stream for the daemon consumption pattern.

Backed by the ``alerts`` table in ``positions.sqlite``. Mirrored to a JSONL
log (``alerts.jsonl``) for tools that prefer tailing a file over polling.

Each watch-cycle decision becomes an alert row when emitted in monitor mode.
The agent calls ``pm alerts pending`` to fetch unacked rows and ``pm alerts ack``
to mark them processed.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config, positions


def emit(
    wallet_address: str,
    decision: dict[str, Any],
    conn: sqlite3.Connection | None = None,
    log_path: Path | None = None,
) -> str:
    """Insert one alert into the queue. Returns the new alert_id.

    Also appends a JSON line to ``alerts.jsonl`` (mirrored stream).
    """
    alert_id = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()
    payload_json = json.dumps(decision, default=str, sort_keys=False)
    rule_id = decision.get("rule_id", "")
    severity = decision.get("severity", "info")

    def _insert(c: sqlite3.Connection) -> None:
        c.execute(
            """INSERT INTO alerts
               (alert_id, created_at_utc, wallet_address, rule_id, severity, acked, payload)
               VALUES (?, ?, ?, ?, ?, 0, ?)""",
            (alert_id, created_at, wallet_address, rule_id, severity, payload_json),
        )

    if conn is None:
        with positions.connect() as c:
            _insert(c)
    else:
        _insert(conn)

    # Mirror to alerts.jsonl
    p = log_path or config.alerts_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "alert_id": alert_id,
                    "created_at_utc": created_at,
                    "wallet_address": wallet_address,
                    "rule_id": rule_id,
                    "severity": severity,
                    "decision": decision,
                },
                default=str,
                sort_keys=False,
            )
            + "\n"
        )
    return alert_id


def pending(
    wallet_address: str | None = None,
    severity: str | None = None,
    limit: int = 100,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """List unacked alerts (most-recent first)."""

    def _do(c: sqlite3.Connection) -> list[dict[str, Any]]:
        sql = (
            "SELECT alert_id, created_at_utc, wallet_address, rule_id, severity, payload "
            "FROM alerts WHERE acked = 0"
        )
        params: list[Any] = []
        if wallet_address is not None:
            sql += " AND wallet_address = ?"
            params.append(wallet_address)
        if severity is not None:
            sql += " AND severity = ?"
            params.append(severity)
        sql += " ORDER BY created_at_utc DESC LIMIT ?"
        params.append(limit)
        return [_row_to_alert(r) for r in c.execute(sql, params)]

    if conn is None:
        with positions.connect() as c:
            return _do(c)
    return _do(conn)


def ack(alert_ids: list[str], conn: sqlite3.Connection | None = None) -> dict[str, int]:
    """Mark alerts as acked. Returns {"acked": n, "not_found": n}."""
    if not alert_ids:
        return {"acked": 0, "not_found": 0}

    def _do(c: sqlite3.Connection) -> dict[str, int]:
        ts = datetime.now(timezone.utc).isoformat()
        placeholders = ",".join("?" for _ in alert_ids)
        # Count which ones exist (and aren't already acked)
        exists_cur = c.execute(
            f"SELECT alert_id FROM alerts WHERE alert_id IN ({placeholders})",
            alert_ids,
        )
        existing = {r["alert_id"] for r in exists_cur}
        cur = c.execute(
            f"UPDATE alerts SET acked = 1, acked_at_utc = ? "
            f"WHERE alert_id IN ({placeholders}) AND acked = 0",
            [ts, *alert_ids],
        )
        return {"acked": cur.rowcount, "not_found": len(set(alert_ids) - existing)}

    if conn is None:
        with positions.connect() as c:
            return _do(c)
    return _do(conn)


def history(
    wallet_address: str | None = None,
    since: str | None = None,
    limit: int = 100,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """All alerts (acked + unacked), most-recent first."""

    def _do(c: sqlite3.Connection) -> list[dict[str, Any]]:
        sql = (
            "SELECT alert_id, created_at_utc, wallet_address, rule_id, severity, "
            "acked, acked_at_utc, payload FROM alerts WHERE 1=1"
        )
        params: list[Any] = []
        if wallet_address is not None:
            sql += " AND wallet_address = ?"
            params.append(wallet_address)
        if since is not None:
            sql += " AND created_at_utc >= ?"
            params.append(since)
        sql += " ORDER BY created_at_utc DESC LIMIT ?"
        params.append(limit)
        return [_row_to_alert(r) for r in c.execute(sql, params)]

    if conn is None:
        with positions.connect() as c:
            return _do(c)
    return _do(conn)


def _row_to_alert(row: sqlite3.Row) -> dict[str, Any]:
    keys = row.keys()
    return {
        "alert_id": row["alert_id"],
        "created_at_utc": row["created_at_utc"],
        "wallet_address": row["wallet_address"],
        "rule_id": row["rule_id"],
        "severity": row["severity"],
        "acked": bool(row["acked"]) if "acked" in keys else False,
        "acked_at_utc": row["acked_at_utc"] if "acked_at_utc" in keys else None,
        "decision": json.loads(row["payload"]),
    }
