"""Tests for the rule-engine dispatcher.

These exercise the integration: schema-shaped rules config → decisions list +
diagnostics. risk_rules itself is unit-tested separately in test_risk_rules.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import rule_engine

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


# ---------------------------------------------------------------------------
# Dispatcher / output shape
# ---------------------------------------------------------------------------


class TestEvaluate:
    def test_no_rules_returns_empty_decisions(self):
        result = rule_engine.evaluate(_load("positions_healthy"), {"rules": []})
        assert result["ok"] is True
        assert result["decisions"] == []
        assert result["diagnostics"]["rules_evaluated"] == 0
        assert result["diagnostics"]["rules_fired"] == 0
        assert result["schema_version"] == "1.0.0"
        assert "input_hashes" in result

    def test_multiple_rules_all_evaluated(self):
        rules_config = {
            "name": "test",
            "rules": [
                {
                    "id": "halt-15",
                    "type": "halt_on_drawdown",
                    "threshold_pct": 15,
                    "action": {"type": "liquidate_all"},
                },
                {
                    "id": "cap-40",
                    "type": "max_position_pct",
                    "threshold_pct": 40,
                    "action": {"type": "trim_to", "target_pct": 40},
                },
                {
                    "id": "ts-all-12",
                    "type": "trailing_stop",
                    "pct": 12,
                    "applies_to": "*",
                    "action": {"type": "full_exit"},
                },
            ],
        }
        result = rule_engine.evaluate(_load("positions_drawdown"), rules_config)
        assert result["diagnostics"]["rules_evaluated"] == 3
        # On positions_drawdown: WSOL=$2500/$4200=59.5% (over 40% cap → trim),
        # both positions over 12% drawdown (→ 2x exit), and portfolio
        # drawdown=16% (over 15% halt). All three rule types fire.
        assert result["diagnostics"]["rules_fired"] == 3
        actions = {d["action"] for d in result["decisions"]}
        assert {"halt", "trim", "exit"} <= actions

    def test_unknown_rule_type_skipped_with_diagnostic(self):
        rules_config = {
            "name": "test",
            "rules": [
                {
                    "id": "halt-15",
                    "type": "halt_on_drawdown",
                    "threshold_pct": 15,
                    "action": {"type": "liquidate_all"},
                },
                {
                    "id": "made-up",
                    "type": "lunar_eclipse_exit",
                    "action": {"type": "full_exit"},
                },
            ],
        }
        result = rule_engine.evaluate(_load("positions_drawdown"), rules_config)
        assert result["diagnostics"]["rules_evaluated"] == 1
        assert len(result["diagnostics"]["rules_skipped"]) == 1
        assert result["diagnostics"]["rules_skipped"][0]["id"] == "made-up"
        assert "unknown rule type" in result["diagnostics"]["rules_skipped"][0]["reason"]

    def test_decisions_are_appended_in_rule_order(self):
        rules_config = {
            "name": "test",
            "rules": [
                {
                    "id": "ts-jto",
                    "type": "trailing_stop",
                    "pct": 12,
                    "applies_to": "JTO",
                    "action": {"type": "full_exit"},
                },
                {
                    "id": "ts-wsol",
                    "type": "trailing_stop",
                    "pct": 12,
                    "applies_to": "WSOL",
                    "action": {"type": "full_exit"},
                },
            ],
        }
        result = rule_engine.evaluate(_load("positions_drawdown"), rules_config)
        rule_ids = [d["rule_id"] for d in result["decisions"]]
        assert rule_ids == ["ts-jto", "ts-wsol"]

    def test_deterministic_evaluated_at_when_now_passed(self):
        result = rule_engine.evaluate(
            _load("positions_healthy"),
            {"rules": []},
            _now_utc="2026-05-11T14:30:22+00:00",
        )
        assert result["evaluated_at_utc"] == "2026-05-11T14:30:22+00:00"

    def test_input_hashes_stable(self):
        positions = _load("positions_healthy")
        rules = {"rules": []}
        r1 = rule_engine.evaluate(positions, rules, _now_utc="x")
        r2 = rule_engine.evaluate(positions, rules, _now_utc="x")
        assert r1["input_hashes"] == r2["input_hashes"]

    def test_missing_required_field_caught_as_warning(self):
        # A halt_on_drawdown rule without threshold_pct triggers KeyError in
        # risk_rules.halt_on_drawdown — engine should catch and degrade gracefully.
        rules_config = {
            "name": "test",
            "rules": [
                {
                    "id": "halt-broken",
                    "type": "halt_on_drawdown",
                    "action": {"type": "liquidate_all"},
                },
            ],
        }
        result = rule_engine.evaluate(_load("positions_drawdown"), rules_config)
        assert result["decisions"] == []
        assert len(result["diagnostics"]["warnings"]) == 1
        assert result["diagnostics"]["warnings"][0]["rule_id"] == "halt-broken"


# ---------------------------------------------------------------------------
# Mediated-open (proposed_order) path
# ---------------------------------------------------------------------------


class TestProposedOrder:
    def test_proposed_buy_inflates_position_and_triggers_cap(self):
        # Healthy fixture has WSOL at 60% of equity already; cap is 50%.
        # Proposing to buy another $1000 of WSOL should make cap fire.
        rules_config = {
            "name": "test",
            "rules": [
                {
                    "id": "cap-50",
                    "type": "max_position_pct",
                    "threshold_pct": 50,
                    "action": {"type": "trim_to", "target_pct": 50},
                }
            ],
        }
        proposed = {
            "action": "buy",
            "asset": "WSOL",
            "qty": 6.0,
            "price_usd": 150.0,  # adds $900 of WSOL exposure
        }
        result = rule_engine.evaluate(
            _load("positions_healthy"), rules_config, proposed_order=proposed
        )
        assert len(result["decisions"]) == 1
        assert result["decisions"][0]["asset"] == "WSOL"
        assert result["decisions"][0]["action"] == "trim"

    def test_proposed_buy_within_cap_no_fire(self):
        rules_config = {
            "name": "test",
            "rules": [
                {
                    "id": "cap-80",
                    "type": "max_position_pct",
                    "threshold_pct": 80,
                    "action": {"type": "trim_to", "target_pct": 80},
                }
            ],
        }
        proposed = {"action": "buy", "asset": "WSOL", "qty": 1.0, "price_usd": 150.0}
        result = rule_engine.evaluate(
            _load("positions_healthy"), rules_config, proposed_order=proposed
        )
        assert result["decisions"] == []

    def test_non_buy_proposed_order_uses_current_state(self):
        # Trims/exits aren't applied speculatively; evaluation falls through to current state.
        rules_config = {"name": "test", "rules": []}
        proposed = {"action": "trim", "asset": "WSOL", "qty": 5.0}
        result = rule_engine.evaluate(
            _load("positions_healthy"), rules_config, proposed_order=proposed
        )
        assert result["decisions"] == []
