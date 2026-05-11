"""Tests for the JSON Schema validator for rule configs."""

from __future__ import annotations

import pytest
from jsonschema.exceptions import ValidationError

from scripts import schema


def _valid_config() -> dict:
    return {
        "name": "test",
        "rules": [
            {
                "id": "halt-15",
                "type": "halt_on_drawdown",
                "threshold_pct": 15,
                "action": {"type": "liquidate_all"},
            }
        ],
    }


class TestSchema:
    def test_valid_config_passes(self):
        schema.validate(_valid_config())  # no exception

    def test_valid_with_all_three_rule_types(self):
        config = {
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
                    "id": "ts-wsol",
                    "type": "trailing_stop",
                    "pct": 12,
                    "applies_to": "WSOL",
                    "action": {"type": "full_exit"},
                },
            ],
        }
        schema.validate(config)

    def test_missing_name_fails(self):
        cfg = _valid_config()
        del cfg["name"]
        with pytest.raises(ValidationError):
            schema.validate(cfg)

    def test_missing_rules_fails(self):
        with pytest.raises(ValidationError):
            schema.validate({"name": "test"})

    def test_empty_rules_array_fails(self):
        with pytest.raises(ValidationError):
            schema.validate({"name": "test", "rules": []})

    def test_unknown_rule_type_fails(self):
        cfg = _valid_config()
        cfg["rules"][0]["type"] = "lunar_eclipse_exit"
        with pytest.raises(ValidationError):
            schema.validate(cfg)

    def test_halt_missing_threshold_pct_fails(self):
        cfg = _valid_config()
        del cfg["rules"][0]["threshold_pct"]
        with pytest.raises(ValidationError):
            schema.validate(cfg)

    def test_trailing_stop_missing_applies_to_fails(self):
        cfg = {
            "name": "test",
            "rules": [
                {
                    "id": "ts",
                    "type": "trailing_stop",
                    "pct": 12,
                    "action": {"type": "full_exit"},
                }
            ],
        }
        with pytest.raises(ValidationError):
            schema.validate(cfg)

    def test_trailing_stop_missing_pct_fails(self):
        cfg = {
            "name": "test",
            "rules": [
                {
                    "id": "ts",
                    "type": "trailing_stop",
                    "applies_to": "WSOL",
                    "action": {"type": "full_exit"},
                }
            ],
        }
        with pytest.raises(ValidationError):
            schema.validate(cfg)

    def test_threshold_above_100_fails(self):
        cfg = _valid_config()
        cfg["rules"][0]["threshold_pct"] = 101
        with pytest.raises(ValidationError):
            schema.validate(cfg)

    def test_threshold_zero_fails(self):
        cfg = _valid_config()
        cfg["rules"][0]["threshold_pct"] = 0
        with pytest.raises(ValidationError):
            schema.validate(cfg)

    def test_unknown_action_type_fails(self):
        cfg = _valid_config()
        cfg["rules"][0]["action"] = {"type": "moon_us"}
        with pytest.raises(ValidationError):
            schema.validate(cfg)

    def test_poll_interval_below_minimum_fails(self):
        cfg = _valid_config()
        cfg["poll"] = {"interval_seconds": 1}
        with pytest.raises(ValidationError):
            schema.validate(cfg)
