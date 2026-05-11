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
from .executor import SwapExecutor, SwapExecutorError
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
    executor: SwapExecutor | None = None,
    max_loss_usd: float | None = None,
) -> dict[str, Any]:
    """Run the watch loop. When ``executor`` is None it's monitor mode (no
    swaps); when provided it's live mode and ``max_loss_usd`` MUST be set —
    halts the loop when cumulative realized loss exceeds the cap.
    """
    sink = sink or sys.stdout
    mode = "live" if executor is not None else "monitor"
    if executor is not None and max_loss_usd is None:
        raise ValueError("max_loss_usd is required in live mode")
    cycles = 0
    alerts_emitted = 0
    fills = 0
    halted = False
    halt_reason: str | None = None
    interrupted = False
    realized_loss = 0.0

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
                "mode": mode,
                "wallet": wallet_address,
                "decisions": [],
                "alerts_emitted": [],
                "fills": [],
                "errors": [],
            }
            try:
                realized_loss = _run_one_cycle(
                    rules_config=rules_config,
                    wallet_source=wallet_source,
                    wallet_address=wallet_address,
                    cycle_record=cycle_record,
                    executor=executor,
                    realized_loss_running=realized_loss,
                    max_loss_usd=max_loss_usd,
                )
            except WatchHalt as e:
                halted = True
                halt_reason = str(e) or "capital_cap_exceeded"
                cycle_record["errors"].append(
                    {"kind": "watch_halt", "detail": halt_reason}
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
            fills += sum(1 for f in cycle_record["fills"] if f.get("ok"))
            cycles += 1
            sink.write(json.dumps(cycle_record, default=str) + "\n")
            sink.flush()
            audit.append({"event": "watch.cycle", **cycle_record})

            if halted:
                break
            if iterations is not None and cycles >= iterations:
                break
            if interrupted:
                break
            sleep_fn(interval_seconds)
    finally:
        signal.signal(signal.SIGINT, prev_sigint)

    return {
        "ok": True,
        "mode": mode,
        "wallet": wallet_address,
        "iterations": cycles,
        "alerts_emitted": alerts_emitted,
        "fills": fills,
        "realized_loss_usd": round(realized_loss, 2),
        "max_loss_usd": max_loss_usd,
        "halted": halted,
        "halt_reason": halt_reason,
        "interrupted": interrupted,
    }


def _run_one_cycle(
    *,
    rules_config: dict[str, Any],
    wallet_source: WalletSource,
    wallet_address: str,
    cycle_record: dict[str, Any],
    executor: SwapExecutor | None = None,
    realized_loss_running: float = 0.0,
    max_loss_usd: float | None = None,
) -> float:
    """Run one cycle. Returns the updated cumulative realized-loss tally."""
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
        cycle_record["fills"] = []

        # Index positions by asset for executor lookups
        by_asset = {p["asset"]: p for p in derived["positions"]}

        for decision in result["decisions"]:
            alert_id = alerts.emit(wallet_address, decision, conn=conn)
            cycle_record["alerts_emitted"].append(
                {"alert_id": alert_id, "rule_id": decision["rule_id"]}
            )

            if executor is None:
                continue  # monitor mode — no execution

            # Live mode: execute the decision.
            if decision["action"] == "halt":
                # Translate "halt" → exit every position (in alphabetical order
                # for deterministic behavior). Cap-check between each.
                for asset in sorted(by_asset):
                    exit_decision = {
                        **decision,
                        "action": "exit",
                        "asset": asset,
                        "qty": by_asset[asset].get("qty"),
                    }
                    realized_loss_running = _execute_and_record(
                        executor,
                        exit_decision,
                        by_asset[asset],
                        cycle_record,
                        realized_loss_running,
                        max_loss_usd,
                    )
                    if (
                        max_loss_usd is not None
                        and realized_loss_running >= max_loss_usd
                    ):
                        # Halt enforcement caused by halt rule still respects the cap.
                        raise WatchHalt("cap_exceeded_during_halt")
                continue

            pos = by_asset.get(decision["asset"])
            if pos is None:
                cycle_record["errors"].append(
                    {"kind": "missing_position", "detail": decision["asset"]}
                )
                continue
            realized_loss_running = _execute_and_record(
                executor,
                decision,
                pos,
                cycle_record,
                realized_loss_running,
                max_loss_usd,
            )
            if max_loss_usd is not None and realized_loss_running >= max_loss_usd:
                raise WatchHalt("cap_exceeded")
    return realized_loss_running


def _execute_and_record(
    executor: SwapExecutor,
    decision: dict[str, Any],
    position: dict[str, Any],
    cycle_record: dict[str, Any],
    realized_loss_running: float,
    max_loss_usd: float | None,
) -> float:
    """Project the fill, refuse if it would breach the cap, else execute + record."""
    # Conservative pre-check: if we already know this loss would breach the cap,
    # skip and record the skip. Uses a worst-case estimate (qty × mark) since
    # we can't know exact slippage in advance.
    if max_loss_usd is not None and decision.get("action") != "halt":
        qty = float(decision.get("qty") or position.get("qty", 0))
        mark = float(position.get("mark_price_usd", 0))
        cost_basis = float(position.get("cost_basis_usd", 0))
        total_qty = float(position.get("qty", 0)) or qty
        cost_basis_chunk = cost_basis * (qty / total_qty) if total_qty > 0 else cost_basis
        projected_loss = max(0.0, cost_basis_chunk - qty * mark)
        if realized_loss_running + projected_loss > max_loss_usd:
            cycle_record["fills"].append(
                {
                    "ok": False,
                    "action": decision["action"],
                    "asset": decision["asset"],
                    "skipped": True,
                    "reason": (
                        f"projected_loss {projected_loss:.2f} would breach cap "
                        f"({realized_loss_running:.2f} + {projected_loss:.2f} > {max_loss_usd})"
                    ),
                }
            )
            return realized_loss_running + projected_loss

    try:
        fill = executor.execute(decision, position)
    except SwapExecutorError as e:
        cycle_record["errors"].append({"kind": "executor_error", "detail": str(e)})
        return realized_loss_running

    cycle_record["fills"].append(fill)
    realized = fill.get("realized_pnl_usd") or 0.0
    # Only LOSSES count toward the cap.
    return realized_loss_running + max(0.0, -realized)
