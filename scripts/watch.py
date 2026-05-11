"""Watch loop — monitor and (M5) live modes.

Cycle body (every interval seconds):
    1. WalletSource.fetch()                 → (wallet_snapshot, pnl_by_token)
    2. load HWMs + manual overrides from sqlite
    3. positions.derive_positions(...)      → derived ledger state
    4. positions.update_hwm_state(...)      → persist any growth
    5. rule_engine.evaluate(...)            → decisions
    6. for each decision:
         alerts.emit() + audit.append()
    7. write one JSON line per cycle to sink (default stdout)
    8. sleep(interval)

Loop exits when --iterations cap is hit, on SIGINT, or on unrecoverable error.
"""

from __future__ import annotations

import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO
from uuid import uuid4

from . import alerts, audit, positions, rule_engine
from .wallet_source import WalletSource, WalletSourceError

_DEFAULT_INTERVAL = 60


class WatchHalt(Exception):
    """Raised when the loop must stop cleanly mid-cycle (e.g., live cap exceeded)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_monitor(
    *,
    rules_config: dict[str, Any],
    wallet_source: WalletSource,
    wallet_address: str,
    interval_seconds: int = _DEFAULT_INTERVAL,
    iterations: int | None = None,
    sink: TextIO | None = None,
    sleep_fn=time.sleep,
) -> dict[str, Any]:
    """Run the monitor-mode loop. Returns a summary dict.

    `sleep_fn` is injected so tests can drive the loop without real sleeps.
    """
    sink = sink or sys.stdout
    cycles = 0
    alerts_emitted = 0
    halted = False
    halt_reason: str | None = None
    interrupted = False

    def _on_sigint(signum, frame):  # noqa: ARG001
        nonlocal interrupted
        interrupted = True

    prev_sigint = signal.signal(signal.SIGINT, _on_sigint)
    try:
        while not interrupted:
            if iterations is not None and cycles >= iterations:
                break
            cycle_id = uuid4().hex
            cycle_record: dict[str, Any] = {
                "cycle_id": cycle_id,
                "cycle_index": cycles,
                "ts_utc": _now(),
                "mode": "monitor",
                "wallet": wallet_address,
                "decisions": [],
                "alerts_emitted": [],
                "errors": [],
            }
            try:
                _run_one_cycle(
                    rules_config=rules_config,
                    wallet_source=wallet_source,
                    wallet_address=wallet_address,
                    cycle_record=cycle_record,
                )
            except WalletSourceError as e:
                cycle_record["errors"].append(
                    {"kind": "wallet_source_error", "detail": str(e)}
                )
            except Exception as e:  # noqa: BLE001 — defensive: cycle errors don't kill loop
                cycle_record["errors"].append(
                    {"kind": type(e).__name__, "detail": str(e)}
                )

            alerts_emitted += len(cycle_record["alerts_emitted"])
            cycles += 1
            sink.write(json.dumps(cycle_record, default=str) + "\n")
            sink.flush()
            audit.append({"event": "watch.cycle", **cycle_record})

            if iterations is not None and cycles >= iterations:
                break
            if interrupted:
                break
            sleep_fn(interval_seconds)
    finally:
        signal.signal(signal.SIGINT, prev_sigint)

    summary = {
        "ok": True,
        "mode": "monitor",
        "wallet": wallet_address,
        "iterations": cycles,
        "alerts_emitted": alerts_emitted,
        "halted": halted,
        "halt_reason": halt_reason,
        "interrupted": interrupted,
    }
    return summary


def _run_one_cycle(
    *,
    rules_config: dict[str, Any],
    wallet_source: WalletSource,
    wallet_address: str,
    cycle_record: dict[str, Any],
) -> None:
    wallet, pnl = wallet_source.fetch()
    with positions.connect() as conn:
        hwms = positions.load_hwm_state(wallet_address, conn=conn)
        overrides = positions.load_manual_overrides(wallet_address, conn=conn)
        derived = positions.derive_positions(
            wallet=wallet,
            pnl_by_token=pnl,
            hwm_state=hwms,
            manual_overrides=overrides,
        )
        positions.update_hwm_state(wallet_address, derived, conn=conn)

        result = rule_engine.evaluate(
            positions=derived, rules_config=rules_config
        )
        cycle_record["positions"] = {
            "total_equity_usd": derived["total_equity_usd"],
            "high_water_mark_usd": derived["high_water_mark_usd"],
            "drawdown_from_hwm_pct": derived["drawdown_from_hwm_pct"],
            "n_positions": len(derived["positions"]),
            "warnings": derived.get("warnings", []),
        }
        cycle_record["decisions"] = result["decisions"]
        cycle_record["diagnostics"] = result["diagnostics"]

        for decision in result["decisions"]:
            alert_id = alerts.emit(wallet_address, decision, conn=conn)
            cycle_record["alerts_emitted"].append(
                {"alert_id": alert_id, "rule_id": decision["rule_id"]}
            )
