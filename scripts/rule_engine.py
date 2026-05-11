"""Stateless rule-engine dispatcher.

Given a positions state and a parsed rules config, returns a structured
decisions payload. Pure function: no I/O, no time access, no randomness.

Used by `pm rules evaluate` directly and (later) by the backtester via subprocess.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from . import risk_rules

SCHEMA_VERSION = "1.0.0"

RULE_DISPATCH = {
    "halt_on_drawdown": risk_rules.halt_on_drawdown,
    "max_position_pct": risk_rules.max_position_pct,
    "trailing_stop": risk_rules.trailing_stop,
}


def _hash(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def evaluate(
    positions: dict[str, Any],
    rules_config: dict[str, Any],
    bar: dict[str, Any] | None = None,
    proposed_order: dict[str, Any] | None = None,
    _now_utc: str | None = None,
) -> dict[str, Any]:
    """Evaluate every rule in rules_config against positions; return decisions.

    Args:
        positions: derived ledger state (see positions.py for the shape).
        rules_config: parsed YAML (already schema-validated by caller).
        bar: optional current OHLCV bar for time-aware rules (unused in v1).
        proposed_order: optional hypothetical order to evaluate against
            (mediated-open path). v1 evaluates rules on the post-order
            state if provided, else on current state.
        _now_utc: test hook — override the timestamp in the response.

    Returns:
        {
            "ok": True,
            "schema_version": "1.0.0",
            "evaluated_at_utc": "...",
            "input_hashes": {"rules_yaml": "...", "positions": "..."},
            "decisions": [...],
            "diagnostics": {
                "rules_evaluated": int,
                "rules_fired": int,
                "rules_skipped": [...],
                "warnings": [...],
            },
        }
    """
    eval_state = (
        _apply_proposed_order(positions, proposed_order)
        if proposed_order is not None
        else positions
    )

    decisions: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {
        "rules_evaluated": 0,
        "rules_fired": 0,
        "rules_skipped": [],
        "warnings": [],
    }

    for rule in rules_config.get("rules", []):
        rule_type = rule.get("type")
        fn = RULE_DISPATCH.get(rule_type)
        if fn is None:
            diagnostics["rules_skipped"].append(
                {"id": rule.get("id"), "reason": f"unknown rule type: {rule_type}"}
            )
            continue
        diagnostics["rules_evaluated"] += 1
        try:
            rule_decisions = fn(eval_state, rule)
        except KeyError as e:
            diagnostics["warnings"].append(
                {"rule_id": rule.get("id"), "warning": f"missing required field: {e}"}
            )
            continue
        if rule_decisions:
            diagnostics["rules_fired"] += 1
            decisions.extend(rule_decisions)

    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "evaluated_at_utc": _now_utc or datetime.now(timezone.utc).isoformat(),
        "input_hashes": {
            "rules_yaml": _hash(rules_config),
            "positions": _hash(eval_state),
        },
        "decisions": decisions,
        "diagnostics": diagnostics,
    }


def _apply_proposed_order(positions: dict[str, Any], order: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of positions with a hypothetical order applied.

    Used for mediated-open evaluation: 'would this open violate my rules?'.
    Conservative implementation: only handles 'buy' action, adds qty at given
    price to the matching position (or creates it). Equity / HWM unchanged.
    """
    if order.get("action") != "buy":
        # exits / trims are evaluated against current state directly
        return positions
    asset = order["asset"]
    qty = float(order["qty"])
    price = float(order["price_usd"])
    value = qty * price

    new_positions = json.loads(json.dumps(positions))  # deep copy
    found = False
    for pos in new_positions.get("positions", []):
        if pos["asset"] == asset:
            old_qty = pos["qty"]
            old_cb = pos.get("cost_basis_usd", 0.0)
            pos["qty"] = old_qty + qty
            pos["cost_basis_usd"] = old_cb + value
            pos["value_usd"] = pos["qty"] * pos.get("mark_price_usd", price)
            found = True
            break
    if not found:
        new_positions.setdefault("positions", []).append(
            {
                "asset": asset,
                "qty": qty,
                "mark_price_usd": price,
                "value_usd": value,
                "cost_basis_usd": value,
                "avg_entry_price_usd": price,
                "unrealized_pnl_usd": 0.0,
                "realized_pnl_usd": 0.0,
                "high_water_mark_usd": value,
                "drawdown_from_hwm_pct": 0.0,
                "source": "proposed",
            }
        )

    # recompute total equity to reflect cash spent
    cash = new_positions.get("cash_usd", 0.0)
    new_positions["cash_usd"] = max(0.0, cash - value)
    new_positions["total_equity_usd"] = sum(
        p["value_usd"] for p in new_positions.get("positions", [])
    ) + new_positions["cash_usd"]
    return new_positions
