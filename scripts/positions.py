"""Position derivation + HWM tracking + manual overrides.

Two responsibilities:

1. **Derivation** (pure function): combine a wallet snapshot + per-token PnL
   data + previously-observed high-water marks + any manual overrides into
   the canonical ``positions`` dict the rule engine consumes. Cost basis is
   derived from PnL data (value_usd − unrealized_pnl_usd) so users never have
   to record entries by hand for tokens OKX has indexed.

2. **SQLite persistence**: HWMs are tracked across cycles. Manual overrides
   live in their own table and merge on top of derived data.

The derivation function is the contract the future backtester consumes: it
takes the same inputs (wallet+pnl+hwm_state) and returns the same shape, so
PM and backtester can share the rule engine bit-identically.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import config

PORTFOLIO_HWM_KEY = "<PORTFOLIO>"


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------


SCHEMA = """
CREATE TABLE IF NOT EXISTS hwm (
    wallet_address  TEXT NOT NULL,
    asset           TEXT NOT NULL,  -- "<PORTFOLIO>" for portfolio-level
    high_water_mark_usd REAL NOT NULL,
    observed_at_utc TEXT NOT NULL,
    PRIMARY KEY (wallet_address, asset)
);

CREATE TABLE IF NOT EXISTS manual_overrides (
    wallet_address  TEXT NOT NULL,
    asset           TEXT NOT NULL,
    qty             REAL,
    cost_basis_usd  REAL,
    mark_price_usd  REAL,
    ts_utc          TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'manual',
    notes           TEXT,
    PRIMARY KEY (wallet_address, asset)
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id        TEXT PRIMARY KEY,
    created_at_utc  TEXT NOT NULL,
    wallet_address  TEXT NOT NULL,
    rule_id         TEXT NOT NULL,
    severity        TEXT NOT NULL,
    acked           INTEGER NOT NULL DEFAULT 0,
    acked_at_utc    TEXT,
    payload         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alerts_pending
    ON alerts(wallet_address, acked, severity);
CREATE INDEX IF NOT EXISTS idx_alerts_recent
    ON alerts(created_at_utc DESC);
"""


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    p = path or config.sqlite_path()
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Derivation — pure function (no I/O)
# ---------------------------------------------------------------------------


def derive_positions(
    wallet: dict[str, Any],
    pnl_by_token: dict[str, dict[str, Any]] | None = None,
    hwm_state: dict[str, float] | None = None,
    manual_overrides: dict[str, dict[str, Any]] | None = None,
    *,
    stablecoins: frozenset[str] = config.STABLECOIN_SYMBOLS,
) -> dict[str, Any]:
    """Compose a positions dict that's ready for rule_engine.evaluate.

    Args:
        wallet: WalletSnapshot dict — see schema below.
        pnl_by_token: mapping "<chain>:<address>" → {unrealized_pnl_usd,
            realized_pnl_usd, asset}. Missing entries default to zero PnL.
        hwm_state: mapping asset_or_"<PORTFOLIO>" → high_water_mark_usd.
            If a key is missing, HWM is initialized to current value.
        manual_overrides: mapping asset → override fields. When present,
            qty/cost_basis_usd/mark_price_usd from the override replace the
            wallet-derived values. Position is flagged source="manual".
        stablecoins: symbols treated as cash (rolled into cash_usd, not
            individual positions).

    Returns:
        {
            "wallet_address", "ts_utc",
            "total_equity_usd",
            "high_water_mark_usd",
            "drawdown_from_hwm_pct",
            "cash_usd",
            "positions": [{..per-position..}],
            "warnings": [strings]
        }

    Expected wallet shape:
        {
            "wallet_address": str,
            "ts_utc": str,
            "tokens": [
                {"asset": "WSOL", "chain": "solana", "address": "...",
                 "qty": float, "mark_price_usd": float, "value_usd": float},
                ...
            ]
        }
    """
    pnl_by_token = pnl_by_token or {}
    hwm_state = hwm_state or {}
    manual_overrides = manual_overrides or {}
    warnings: list[str] = []

    cash_usd = 0.0
    positions: list[dict[str, Any]] = []

    for tok in wallet.get("tokens", []):
        asset = tok["asset"]
        is_stable = asset.upper() in stablecoins
        qty = float(tok.get("qty", 0.0))
        mark = float(tok.get("mark_price_usd", 0.0))
        value = float(tok.get("value_usd", qty * mark))

        if is_stable:
            cash_usd += value
            continue
        if qty <= 0:
            # zero / dust positions: skip, but accumulate any realized PnL trail
            continue

        key = f"{tok.get('chain','')}:{tok.get('address','')}"
        pnl = pnl_by_token.get(key, {})
        unrealized = float(pnl.get("unrealized_pnl_usd", 0.0))
        realized = float(pnl.get("realized_pnl_usd", 0.0))
        cost_basis = value - unrealized
        if cost_basis < 0:
            warnings.append(
                f"{asset}: derived cost_basis negative "
                f"({cost_basis:.2f}); clamping to 0 (unrealized PnL exceeds value)"
            )
            cost_basis = 0.0

        source = "auto"
        override = manual_overrides.get(asset)
        if override is not None:
            if override.get("qty") is not None:
                qty = float(override["qty"])
                value = qty * mark
            if override.get("cost_basis_usd") is not None:
                cost_basis = float(override["cost_basis_usd"])
            if override.get("mark_price_usd") is not None:
                mark = float(override["mark_price_usd"])
                value = qty * mark
            source = "manual"

        avg_entry = (cost_basis / qty) if qty > 0 else 0.0

        # Position-level HWM: max of stored HWM and current value
        pos_hwm = max(hwm_state.get(asset, value), value)
        pos_dd_pct = ((pos_hwm - value) / pos_hwm * 100.0) if pos_hwm > 0 else 0.0

        positions.append(
            {
                "asset": asset,
                "chain": tok.get("chain"),
                "address": tok.get("address"),
                "qty": round(qty, 8),
                "mark_price_usd": round(mark, 8),
                "value_usd": round(value, 2),
                "cost_basis_usd": round(cost_basis, 2),
                "avg_entry_price_usd": round(avg_entry, 8),
                "unrealized_pnl_usd": round(value - cost_basis, 2),
                "realized_pnl_usd": round(realized, 2),
                "high_water_mark_usd": round(pos_hwm, 2),
                "drawdown_from_hwm_pct": round(pos_dd_pct, 4),
                "source": source,
            }
        )

    total_equity = sum(p["value_usd"] for p in positions) + cash_usd
    port_hwm = max(hwm_state.get(PORTFOLIO_HWM_KEY, total_equity), total_equity)
    port_dd_pct = ((port_hwm - total_equity) / port_hwm * 100.0) if port_hwm > 0 else 0.0

    return {
        "wallet_address": wallet.get("wallet_address"),
        "ts_utc": wallet.get("ts_utc") or datetime.now(timezone.utc).isoformat(),
        "total_equity_usd": round(total_equity, 2),
        "high_water_mark_usd": round(port_hwm, 2),
        "drawdown_from_hwm_pct": round(port_dd_pct, 4),
        "cash_usd": round(cash_usd, 2),
        "positions": positions,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# SQLite-backed HWM + manual override accessors
# ---------------------------------------------------------------------------


def load_hwm_state(wallet_address: str, conn: sqlite3.Connection | None = None) -> dict[str, float]:
    """Load all HWMs for a wallet from sqlite. Returns dict asset → hwm_usd."""
    if conn is None:
        with connect() as c:
            return _load_hwm(wallet_address, c)
    return _load_hwm(wallet_address, conn)


def _load_hwm(wallet_address: str, conn: sqlite3.Connection) -> dict[str, float]:
    cur = conn.execute(
        "SELECT asset, high_water_mark_usd FROM hwm WHERE wallet_address = ?",
        (wallet_address,),
    )
    return {row["asset"]: float(row["high_water_mark_usd"]) for row in cur}


def update_hwm_state(
    wallet_address: str,
    positions_state: dict[str, Any],
    conn: sqlite3.Connection | None = None,
) -> None:
    """Persist any HWM that increased this cycle. Idempotent — never lowers HWM."""
    if conn is None:
        with connect() as c:
            _update_hwm(wallet_address, positions_state, c)
        return
    _update_hwm(wallet_address, positions_state, conn)


def _update_hwm(
    wallet_address: str, positions_state: dict[str, Any], conn: sqlite3.Connection
) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    updates = [
        (wallet_address, PORTFOLIO_HWM_KEY, positions_state["high_water_mark_usd"], ts)
    ]
    for pos in positions_state.get("positions", []):
        updates.append(
            (wallet_address, pos["asset"], pos["high_water_mark_usd"], ts)
        )
    conn.executemany(
        """
        INSERT INTO hwm(wallet_address, asset, high_water_mark_usd, observed_at_utc)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(wallet_address, asset) DO UPDATE SET
            high_water_mark_usd = MAX(hwm.high_water_mark_usd, excluded.high_water_mark_usd),
            observed_at_utc = excluded.observed_at_utc
        """,
        updates,
    )


def load_manual_overrides(
    wallet_address: str, conn: sqlite3.Connection | None = None
) -> dict[str, dict[str, Any]]:
    if conn is None:
        with connect() as c:
            return _load_overrides(wallet_address, c)
    return _load_overrides(wallet_address, conn)


def _load_overrides(
    wallet_address: str, conn: sqlite3.Connection
) -> dict[str, dict[str, Any]]:
    cur = conn.execute(
        """SELECT asset, qty, cost_basis_usd, mark_price_usd, ts_utc, source, notes
           FROM manual_overrides WHERE wallet_address = ?""",
        (wallet_address,),
    )
    out: dict[str, dict[str, Any]] = {}
    for r in cur:
        out[r["asset"]] = {
            "qty": r["qty"],
            "cost_basis_usd": r["cost_basis_usd"],
            "mark_price_usd": r["mark_price_usd"],
            "ts_utc": r["ts_utc"],
            "source": r["source"],
            "notes": r["notes"],
        }
    return out


def upsert_manual_override(
    wallet_address: str,
    asset: str,
    qty: float | None = None,
    cost_basis_usd: float | None = None,
    mark_price_usd: float | None = None,
    ts_utc: str | None = None,
    notes: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    ts = ts_utc or datetime.now(timezone.utc).isoformat()

    def _do(c: sqlite3.Connection) -> None:
        c.execute(
            """INSERT INTO manual_overrides
               (wallet_address, asset, qty, cost_basis_usd, mark_price_usd, ts_utc, source, notes)
               VALUES (?, ?, ?, ?, ?, ?, 'manual', ?)
               ON CONFLICT(wallet_address, asset) DO UPDATE SET
                   qty = COALESCE(excluded.qty, manual_overrides.qty),
                   cost_basis_usd = COALESCE(excluded.cost_basis_usd, manual_overrides.cost_basis_usd),
                   mark_price_usd = COALESCE(excluded.mark_price_usd, manual_overrides.mark_price_usd),
                   ts_utc = excluded.ts_utc,
                   notes = COALESCE(excluded.notes, manual_overrides.notes)
            """,
            (wallet_address, asset, qty, cost_basis_usd, mark_price_usd, ts, notes),
        )

    if conn is None:
        with connect() as c:
            _do(c)
    else:
        _do(conn)


def delete_manual_override(
    wallet_address: str, asset: str, conn: sqlite3.Connection | None = None
) -> int:
    def _do(c: sqlite3.Connection) -> int:
        cur = c.execute(
            "DELETE FROM manual_overrides WHERE wallet_address = ? AND asset = ?",
            (wallet_address, asset),
        )
        return cur.rowcount

    if conn is None:
        with connect() as c:
            return _do(c)
    return _do(conn)
