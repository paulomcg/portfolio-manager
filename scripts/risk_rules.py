"""Risk rule implementations.

Each function takes (positions_state, rule_config) and returns a list of Decision
dicts. Returns an empty list when the rule does not fire. Functions are pure:
no I/O, no logging, no time/random side effects.

Decision dict shape (stable contract):
    {
        "action":         "trim" | "exit" | "halt" | "no_op",
        "asset":          str | None,        # None for portfolio-level halts
        "qty":            float | None,      # None for halt or full exit
        "quote_usd_est":  float | None,
        "reason":         str,
        "rule_id":        str,
        "severity":       "info" | "warn" | "critical",
    }
"""

from __future__ import annotations

from typing import Any


def halt_on_drawdown(state: dict[str, Any], rule: dict[str, Any]) -> list[dict[str, Any]]:
    """Fires when portfolio drawdown_from_hwm_pct >= threshold."""
    threshold = rule["threshold_pct"]
    dd = state.get("drawdown_from_hwm_pct", 0.0)
    if dd >= threshold:
        return [
            {
                "action": "halt",
                "asset": None,
                "qty": None,
                "quote_usd_est": None,
                "reason": (
                    f"portfolio drawdown {dd:.2f}% >= {threshold}% "
                    f"(HWM ${state.get('high_water_mark_usd', 0.0):,.2f}, "
                    f"current ${state.get('total_equity_usd', 0.0):,.2f})"
                ),
                "rule_id": rule["id"],
                "severity": "critical",
            }
        ]
    return []


def max_position_pct(state: dict[str, Any], rule: dict[str, Any]) -> list[dict[str, Any]]:
    """Fires per-position when value_usd / total_equity_usd > threshold.

    Action: trim down to target_pct (defaults to threshold_pct if omitted).
    """
    threshold = rule["threshold_pct"]
    target = rule["action"].get("target_pct", threshold)
    equity = state.get("total_equity_usd", 0.0)
    if equity <= 0:
        return []
    decisions: list[dict[str, Any]] = []
    for pos in state.get("positions", []):
        value = pos.get("value_usd", 0.0)
        pct = (value / equity) * 100.0
        if pct <= threshold:
            continue
        target_value = equity * (target / 100.0)
        excess_value = value - target_value
        mark = pos.get("mark_price_usd", 0.0)
        excess_qty = (excess_value / mark) if mark > 0 else None
        decisions.append(
            {
                "action": "trim",
                "asset": pos["asset"],
                "qty": round(excess_qty, 8) if excess_qty is not None else None,
                "quote_usd_est": round(excess_value, 2),
                "reason": (
                    f"{pos['asset']} is {pct:.2f}% of portfolio, "
                    f"exceeds {threshold}% cap (trim to {target}%)"
                ),
                "rule_id": rule["id"],
                "severity": "warn",
            }
        )
    return decisions


def trailing_stop(state: dict[str, Any], rule: dict[str, Any]) -> list[dict[str, Any]]:
    """Fires per-position when position drawdown_from_hwm_pct >= pct.

    applies_to: "*" means all positions; otherwise match by asset symbol.
    Action: full exit (action.type must be full_exit).
    """
    threshold = rule["pct"]
    applies = rule["applies_to"]
    decisions: list[dict[str, Any]] = []
    for pos in state.get("positions", []):
        if applies != "*" and pos["asset"] != applies:
            continue
        dd = pos.get("drawdown_from_hwm_pct", 0.0)
        if dd < threshold:
            continue
        decisions.append(
            {
                "action": "exit",
                "asset": pos["asset"],
                "qty": pos.get("qty"),
                "quote_usd_est": round(pos.get("value_usd", 0.0), 2),
                "reason": (
                    f"{pos['asset']} down {dd:.2f}% from HWM "
                    f"${pos.get('high_water_mark_usd', 0.0):,.2f} "
                    f"(trailing stop {threshold}%)"
                ),
                "rule_id": rule["id"],
                "severity": "warn",
            }
        )
    return decisions
