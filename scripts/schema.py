"""JSON Schema for the rule-config YAML loaded by pm rules validate / evaluate.

Imported by pm.py to validate input before passing to rule_engine.evaluate.
Per-rule-type field requirements are enforced via oneOf below (jsonschema draft-7).
"""

RULES_CONFIG_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["name", "rules"],
    "additionalProperties": True,
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "wallet": {"type": "string"},
        "universe": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["chain", "address", "symbol"],
                "properties": {
                    "chain": {"type": "string"},
                    "address": {"type": "string"},
                    "symbol": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "poll": {
            "type": "object",
            "properties": {
                "interval_seconds": {"type": "integer", "minimum": 5},
            },
            "additionalProperties": False,
        },
        "rules": {
            "type": "array",
            "minItems": 1,
            "items": {"$ref": "#/definitions/rule"},
        },
    },
    "definitions": {
        "rule": {
            "type": "object",
            "required": ["id", "type", "action"],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "type": {"enum": ["halt_on_drawdown", "max_position_pct", "trailing_stop"]},
                "threshold_pct": {"type": "number", "exclusiveMinimum": 0, "maximum": 100},
                "pct": {"type": "number", "exclusiveMinimum": 0, "maximum": 100},
                "applies_to": {"type": "string", "minLength": 1},
                "action": {"$ref": "#/definitions/action"},
            },
            "oneOf": [
                {
                    "properties": {
                        "type": {"const": "halt_on_drawdown"},
                    },
                    "required": ["threshold_pct"],
                },
                {
                    "properties": {
                        "type": {"const": "max_position_pct"},
                    },
                    "required": ["threshold_pct"],
                },
                {
                    "properties": {
                        "type": {"const": "trailing_stop"},
                    },
                    "required": ["pct", "applies_to"],
                },
            ],
            "additionalProperties": False,
        },
        "action": {
            "type": "object",
            "required": ["type"],
            "properties": {
                "type": {"enum": ["liquidate_all", "trim_to", "full_exit"]},
                "target_pct": {"type": "number", "exclusiveMinimum": 0, "maximum": 100},
                "reason": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
}


def validate(rules_config: dict) -> None:
    """Raise jsonschema.ValidationError on invalid config; return None on success.

    Caller catches ValidationError and maps to canonical
    'FAILED: rules_config_invalid <field>: <reason>' line.
    """
    import jsonschema

    jsonschema.validate(instance=rules_config, schema=RULES_CONFIG_SCHEMA)
