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
from .market_data import MarketDataSource
from .strategy import StrategyInvocation
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
    strategy: StrategyInvocation | None = None,
    market_data: MarketDataSource | None = None,
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
    audit.append({
        "event": "watch.start",
        "ts_utc": _now(),
        "mode": mode,
        "wallet": wallet_address,
        "max_loss_usd": max_loss_usd,
        "rules": rules_config.get("rules", []),
        "universe": rules_config.get("universe", []),
        "strategy_loaded": strategy is not None,
        "interval_seconds": interval_seconds,
        "iterations_cap": iterations,
    })
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
                    cycle_index=cycles,
                    executor=executor,
                    realized_loss_running=realized_loss,
                    max_loss_usd=max_loss_usd,
                    strategy=strategy,
                    market_data=market_data,
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
    cycle_index: int = 0,
    executor: SwapExecutor | None = None,
    realized_loss_running: float = 0.0,
    max_loss_usd: float | None = None,
    strategy: StrategyInvocation | None = None,
    market_data: MarketDataSource | None = None,
) -> float:
    """Run one cycle. Returns the updated cumulative realized-loss tally."""
    wallet, pnl = wallet_source.fetch()
    # Pump market data (best effort; failures are recorded as warnings).
    market_snapshot: dict[str, dict[str, Any]] = {}
    if market_data is not None:
        try:
            md_warnings = market_data.poll()
        except Exception as e:  # noqa: BLE001
            cycle_record["errors"].append(
                {"kind": "market_data_failure", "detail": f"{type(e).__name__}: {e}"}
            )
            md_warnings = []
        if md_warnings:
            cycle_record.setdefault("strategy", {}).setdefault("warnings", []).extend(md_warnings)
        market_snapshot = market_data.snapshot()

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

        cycle_record["positions"] = {
            "total_equity_usd": derived["total_equity_usd"],
            "high_water_mark_usd": derived["high_water_mark_usd"],
            "drawdown_from_hwm_pct": derived["drawdown_from_hwm_pct"],
            "n_positions": len(derived["positions"]),
            "warnings": derived.get("warnings", []),
        }
        cycle_record["fills"] = []

        # --- Strategy hook (M12) -----------------------------------------
        if strategy is not None:
            realized_loss_running = _apply_strategy(
                strategy=strategy,
                derived=derived,
                cycle_index=cycle_index,
                rules_config=rules_config,
                market_snapshot=market_snapshot,
                cycle_record=cycle_record,
                executor=executor,
                wallet_address=wallet_address,
                conn=conn,
                realized_loss_running=realized_loss_running,
                max_loss_usd=max_loss_usd,
            )
            # Refresh derived view of equity/dd for the rule pass.
            cycle_record["positions"]["total_equity_usd"] = derived["total_equity_usd"]
            cycle_record["positions"]["n_positions"] = len(derived["positions"])

        # --- Rule engine (post-strategy) ---------------------------------
        result = rule_engine.evaluate(
            positions=derived, rules_config=rules_config
        )
        cycle_record["decisions"] = result["decisions"]
        cycle_record["diagnostics"] = result["diagnostics"]

        # Index positions by asset for executor lookups (now reflects strategy fills)
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


def _apply_strategy(
    *,
    strategy: StrategyInvocation,
    derived: dict[str, Any],
    cycle_index: int,
    rules_config: dict[str, Any],
    market_snapshot: dict[str, dict[str, Any]],
    cycle_record: dict[str, Any],
    executor: SwapExecutor | None,
    wallet_address: str,
    conn,
    realized_loss_running: float,
    max_loss_usd: float | None,
) -> float:
    """Invoke the strategy, apply its actions through the executor, mutate
    ``derived`` in place to reflect resulting fills."""
    state_for_strategy = {
        **derived,
        "cycle_index": cycle_index,
        "wallet": wallet_address,
    }
    actions, warnings = strategy.invoke(state_for_strategy, market_snapshot)

    cycle_record.setdefault("strategy", {})
    cycle_record["strategy"]["actions"] = actions
    cycle_record["strategy"].setdefault("warnings", []).extend(warnings)

    if not actions:
        return realized_loss_running

    by_asset = {p["asset"]: p for p in derived["positions"]}
    universe = rules_config.get("universe") or []

    for action in actions:
        atype = action["action"]
        if atype == "hold":
            continue
        # Enrich buys with universe-derived chain+address; sells use the position
        if atype == "buy":
            _enrich_with_universe(action, universe)
        bar = (market_snapshot.get(action.get("asset", "")) or {}).get("current")
        position = by_asset.get(action.get("asset"))

        # Executor required for any non-hold action.
        if executor is None:
            cycle_record["errors"].append(
                {"kind": "strategy_action_without_executor",
                 "detail": f"action {atype} requires --live executor"}
            )
            continue

        try:
            fill = executor.execute(action, position=position, bar=bar)
        except SwapExecutorError as e:
            cycle_record["errors"].append(
                {"kind": "executor_error", "detail": str(e), "source": "strategy"}
            )
            continue
        fill["source"] = "strategy"
        cycle_record["fills"].append(fill)
        _apply_fill_to_state(derived, fill, by_asset)

        realized = fill.get("realized_pnl_usd") or 0.0
        realized_loss_running += max(0.0, -realized)
        if max_loss_usd is not None and realized_loss_running >= max_loss_usd:
            raise WatchHalt("cap_exceeded_during_strategy")
    return realized_loss_running


def _enrich_with_universe(action: dict[str, Any], universe: list[dict[str, Any]]) -> None:
    """Populate ``address`` and ``chain`` on a buy action from the rule
    config's universe entry. No-op when the asset isn't in the universe."""
    asset = action.get("asset")
    for entry in universe:
        if entry.get("symbol") == asset:
            action.setdefault("address", entry.get("address"))
            action.setdefault("chain", entry.get("chain"))
            return


def _apply_fill_to_state(
    state: dict[str, Any],
    fill: dict[str, Any],
    by_asset: dict[str, dict[str, Any]],
) -> None:
    """Mutate ``state`` in place to reflect a fill so subsequent rule eval
    runs against post-strategy positions. Used for both backtests and
    live cycles (live's next cycle will re-poll the real wallet)."""
    asset = fill.get("asset")
    if asset is None:
        return  # halts have no positional effect at this layer
    qty = float(fill.get("qty_swapped", 0))
    fill_price = float(fill.get("fill_price_usd", 0))
    fees = float(fill.get("fees_usd", 0))
    action = fill.get("action")
    pos = by_asset.get(asset)

    if action == "buy":
        cost = abs(float(fill.get("gross_proceeds_usd", 0)))
        state["cash_usd"] = float(state.get("cash_usd", 0)) - cost - fees
        if pos is None:
            new_pos = {
                "asset": asset,
                "chain": None,
                "address": None,
                "qty": qty,
                "mark_price_usd": fill_price,
                "value_usd": qty * fill_price,
                "cost_basis_usd": cost,
                "avg_entry_price_usd": cost / qty if qty > 0 else 0,
                "unrealized_pnl_usd": qty * fill_price - cost,
                "realized_pnl_usd": 0.0,
                "high_water_mark_usd": qty * fill_price,
                "drawdown_from_hwm_pct": 0.0,
                "source": "strategy",
            }
            state["positions"].append(new_pos)
            by_asset[asset] = new_pos
        else:
            pos["qty"] += qty
            pos["cost_basis_usd"] += cost
            pos["mark_price_usd"] = fill_price
            pos["value_usd"] = pos["qty"] * fill_price
            if pos["qty"] > 0:
                pos["avg_entry_price_usd"] = pos["cost_basis_usd"] / pos["qty"]
    elif action in ("sell", "exit", "trim"):
        proceeds = float(fill.get("gross_proceeds_usd", 0))
        state["cash_usd"] = float(state.get("cash_usd", 0)) + proceeds - fees
        if pos is not None:
            prev_qty = pos["qty"]
            new_qty = max(0.0, prev_qty - qty)
            if prev_qty > 0:
                pos["cost_basis_usd"] *= new_qty / prev_qty
            pos["qty"] = new_qty
            pos["value_usd"] = new_qty * fill_price
            pos["realized_pnl_usd"] = float(pos.get("realized_pnl_usd", 0)) + \
                float(fill.get("realized_pnl_usd", 0))
            if new_qty == 0:
                # Position closed — remove from list.
                state["positions"] = [p for p in state["positions"] if p["asset"] != asset]
                by_asset.pop(asset, None)
    # Refresh total_equity_usd
    state["total_equity_usd"] = sum(
        float(p.get("value_usd", 0)) for p in state["positions"]
    ) + float(state.get("cash_usd", 0))


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
                    "source": "rule",
                    "reason": (
                        f"projected_loss {projected_loss:.2f} would breach cap "
                        f"({realized_loss_running:.2f} + {projected_loss:.2f} > {max_loss_usd})"
                    ),
                }
            )
            return realized_loss_running + projected_loss

    try:
        fill = executor.execute(decision, position=position)
    except SwapExecutorError as e:
        cycle_record["errors"].append(
            {"kind": "executor_error", "detail": str(e), "source": "rule"}
        )
        return realized_loss_running

    fill["source"] = "rule"
    cycle_record["fills"].append(fill)
    realized = fill.get("realized_pnl_usd") or 0.0
    # Only LOSSES count toward the cap.
    return realized_loss_running + max(0.0, -realized)
