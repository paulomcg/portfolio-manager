"""Unit tests for the three v1 risk-rule implementations.

Each rule is tested in isolation against synthetic fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import risk_rules

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


# ---------------------------------------------------------------------------
# halt_on_drawdown
# ---------------------------------------------------------------------------


class TestHaltOnDrawdown:
    def test_no_fire_when_below_threshold(self):
        state = _load("positions_healthy")
        rule = {
            "id": "halt-15",
            "type": "halt_on_drawdown",
            "threshold_pct": 15,
            "action": {"type": "liquidate_all"},
        }
        assert risk_rules.halt_on_drawdown(state, rule) == []

    def test_fires_when_at_threshold(self):
        state = _load("positions_healthy")
        state["drawdown_from_hwm_pct"] = 15.0
        rule = {
            "id": "halt-15",
            "type": "halt_on_drawdown",
            "threshold_pct": 15,
            "action": {"type": "liquidate_all"},
        }
        decisions = risk_rules.halt_on_drawdown(state, rule)
        assert len(decisions) == 1
        assert decisions[0]["action"] == "halt"
        assert decisions[0]["severity"] == "critical"
        assert decisions[0]["rule_id"] == "halt-15"
        assert decisions[0]["asset"] is None
        assert decisions[0]["qty"] is None

    def test_fires_when_above_threshold(self):
        state = _load("positions_drawdown")
        rule = {
            "id": "halt-15",
            "type": "halt_on_drawdown",
            "threshold_pct": 15,
            "action": {"type": "liquidate_all"},
        }
        decisions = risk_rules.halt_on_drawdown(state, rule)
        assert len(decisions) == 1
        assert "16.00%" in decisions[0]["reason"]

    def test_no_fire_at_zero(self):
        state = _load("positions_healthy")
        rule = {
            "id": "halt-10",
            "type": "halt_on_drawdown",
            "threshold_pct": 10,
            "action": {"type": "liquidate_all"},
        }
        assert risk_rules.halt_on_drawdown(state, rule) == []


# ---------------------------------------------------------------------------
# max_position_pct
# ---------------------------------------------------------------------------


class TestMaxPositionPct:
    def test_no_fire_when_all_below_cap(self):
        # healthy fixture: WSOL is 60% of 5000 → over a 40% cap.
        # Use a generous 80% cap so nothing fires.
        state = _load("positions_healthy")
        rule = {
            "id": "cap-80",
            "type": "max_position_pct",
            "threshold_pct": 80,
            "action": {"type": "trim_to", "target_pct": 80},
        }
        assert risk_rules.max_position_pct(state, rule) == []

    def test_fires_per_overweight_position(self):
        # overweight fixture: WSOL = 78% of 5000, JTO = 20%. 40% cap fires only on WSOL.
        state = _load("positions_overweight")
        rule = {
            "id": "cap-40",
            "type": "max_position_pct",
            "threshold_pct": 40,
            "action": {"type": "trim_to", "target_pct": 40},
        }
        decisions = risk_rules.max_position_pct(state, rule)
        assert len(decisions) == 1
        d = decisions[0]
        assert d["asset"] == "WSOL"
        assert d["action"] == "trim"
        # target value = 5000 * 0.40 = 2000; excess = 3900 - 2000 = 1900
        assert d["quote_usd_est"] == pytest.approx(1900.0, abs=0.01)
        # excess qty = 1900 / 130 ≈ 14.615
        assert d["qty"] == pytest.approx(14.615385, abs=1e-5)
        assert "78.00%" in d["reason"]

    def test_target_defaults_to_threshold_when_omitted(self):
        state = _load("positions_overweight")
        rule = {
            "id": "cap-40",
            "type": "max_position_pct",
            "threshold_pct": 40,
            "action": {"type": "trim_to"},
        }
        decisions = risk_rules.max_position_pct(state, rule)
        assert len(decisions) == 1
        # same math as previous test
        assert decisions[0]["quote_usd_est"] == pytest.approx(1900.0, abs=0.01)

    def test_target_below_threshold_trims_deeper(self):
        # Same fixture, but trim down to 30% (more aggressive than the 40% threshold).
        state = _load("positions_overweight")
        rule = {
            "id": "cap-40-aggressive-trim",
            "type": "max_position_pct",
            "threshold_pct": 40,
            "action": {"type": "trim_to", "target_pct": 30},
        }
        decisions = risk_rules.max_position_pct(state, rule)
        # target = 5000 * 0.30 = 1500; excess = 3900 - 1500 = 2400
        assert decisions[0]["quote_usd_est"] == pytest.approx(2400.0, abs=0.01)

    def test_zero_equity_no_division_error(self):
        state = {"total_equity_usd": 0.0, "positions": []}
        rule = {
            "id": "cap-40",
            "type": "max_position_pct",
            "threshold_pct": 40,
            "action": {"type": "trim_to"},
        }
        assert risk_rules.max_position_pct(state, rule) == []


# ---------------------------------------------------------------------------
# trailing_stop
# ---------------------------------------------------------------------------


class TestTrailingStop:
    def test_no_fire_when_drawdown_below_threshold(self):
        state = _load("positions_healthy")  # both positions at 0% drawdown
        rule = {
            "id": "ts-wsol-12",
            "type": "trailing_stop",
            "pct": 12,
            "applies_to": "WSOL",
            "action": {"type": "full_exit"},
        }
        assert risk_rules.trailing_stop(state, rule) == []

    def test_fires_when_drawdown_above_threshold(self):
        # drawdown fixture: WSOL at 16.67% from HWM, JTO at 20%.
        state = _load("positions_drawdown")
        rule = {
            "id": "ts-wsol-12",
            "type": "trailing_stop",
            "pct": 12,
            "applies_to": "WSOL",
            "action": {"type": "full_exit"},
        }
        decisions = risk_rules.trailing_stop(state, rule)
        assert len(decisions) == 1
        d = decisions[0]
        assert d["asset"] == "WSOL"
        assert d["action"] == "exit"
        assert d["qty"] == 20.0
        assert d["quote_usd_est"] == pytest.approx(2500.0)
        assert "16.67%" in d["reason"]

    def test_applies_to_wildcard_fires_on_all_overdrawn(self):
        state = _load("positions_drawdown")
        rule = {
            "id": "ts-all-12",
            "type": "trailing_stop",
            "pct": 12,
            "applies_to": "*",
            "action": {"type": "full_exit"},
        }
        decisions = risk_rules.trailing_stop(state, rule)
        assert len(decisions) == 2
        assets = {d["asset"] for d in decisions}
        assert assets == {"WSOL", "JTO"}

    def test_applies_to_unknown_asset_no_fire(self):
        state = _load("positions_drawdown")
        rule = {
            "id": "ts-bonk-12",
            "type": "trailing_stop",
            "pct": 12,
            "applies_to": "BONK",
            "action": {"type": "full_exit"},
        }
        assert risk_rules.trailing_stop(state, rule) == []

    def test_position_at_exact_threshold_fires(self):
        state = _load("positions_drawdown")
        # WSOL is at 16.67%; threshold = 16.67% should still fire (>=).
        rule = {
            "id": "ts-wsol-1667",
            "type": "trailing_stop",
            "pct": 16.67,
            "applies_to": "WSOL",
            "action": {"type": "full_exit"},
        }
        assert len(risk_rules.trailing_stop(state, rule)) == 1
