"""Append-only JSONL audit log.

One row per watch cycle (monitor + live) and one row per stateful CLI call
that mutates positions or alerts. Audit rows are the single most important
artifact for explaining 'why did pm exit my BONK position at 14:32?'.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config


def append(row: dict[str, Any], path: Path | None = None) -> None:
    """Append a single row to the audit log. ts_utc auto-filled if missing."""
    p = path or config.audit_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts_utc": datetime.now(timezone.utc).isoformat(), **row}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str, sort_keys=False) + "\n")


def read(
    limit: int | None = None,
    since: str | None = None,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Read audit rows. Filters in memory — fine for v1 volumes.

    `since` is an ISO 8601 timestamp; rows with ts_utc >= since are returned.
    `limit` caps the result count (most-recent first).
    """
    p = path or config.audit_path()
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # tolerate a bad row; never block reads on corruption
                continue
    if since:
        rows = [r for r in rows if r.get("ts_utc", "") >= since]
    # Most-recent first
    rows.reverse()
    if limit is not None:
        rows = rows[:limit]
    return rows
