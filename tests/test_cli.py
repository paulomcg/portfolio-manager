"""End-to-end CLI tests — invoke `pm` via subprocess and assert on JSON / FAILED lines.

These exercise the dispatcher, YAML loading, schema validation, JSON I/O,
and exit codes. The rule logic itself is unit-tested separately.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PM = ROOT / "bin" / "pm"
FIXTURES = ROOT / "tests" / "fixtures"
EXAMPLES = ROOT / "examples"


def _run(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(PM), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env={**os.environ, "PM_PYTHON": sys.executable},
        timeout=15,
    )


# ---------------------------------------------------------------------------
# Basic CLI shape
# ---------------------------------------------------------------------------


class TestCli:
    def test_version_flag(self):
        r = _run("--version")
        assert r.returncode == 0
        assert "pm " in r.stdout.strip().lower()

    def test_no_subcommand_fails_usage(self):
        r = _run()
        assert r.returncode != 0


# ---------------------------------------------------------------------------
# rules validate
# ---------------------------------------------------------------------------


class TestRulesValidate:
    def test_valid_config(self):
        r = _run("rules", "validate", "--config", str(EXAMPLES / "conservative-majors.yaml"))
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["ok"] is True
        assert out["result"]["name"] == "conservative-majors"
        assert out["result"]["rules"] == 3

    def test_missing_file(self, tmp_path):
        r = _run("rules", "validate", "--config", str(tmp_path / "nope.yaml"))
        assert r.returncode == 1
        assert r.stderr.startswith("FAILED: rules_config_invalid file:")

    def test_malformed_yaml(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("name: test\n  rules: [\n    - id: x\n")
        r = _run("rules", "validate", "--config", str(p))
        assert r.returncode == 1
        assert r.stderr.startswith("FAILED: rules_config_invalid yaml:")

    def test_top_level_not_mapping(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("- 1\n- 2\n- 3\n")
        r = _run("rules", "validate", "--config", str(p))
        assert r.returncode == 1
        assert "top-level must be a mapping" in r.stderr

    def test_missing_required_name(self, tmp_path):
        p = tmp_path / "noname.yaml"
        p.write_text(
            "rules:\n"
            "  - id: x\n"
            "    type: halt_on_drawdown\n"
            "    threshold_pct: 15\n"
            "    action: {type: liquidate_all}\n"
        )
        r = _run("rules", "validate", "--config", str(p))
        assert r.returncode == 1
        assert r.stderr.startswith("FAILED: rules_config_invalid")
        assert "name" in r.stderr

    def test_unknown_rule_type(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text(
            "name: test\n"
            "rules:\n"
            "  - id: x\n"
            "    type: lunar_eclipse_exit\n"
            "    action: {type: full_exit}\n"
        )
        r = _run("rules", "validate", "--config", str(p))
        assert r.returncode == 1
        assert r.stderr.startswith("FAILED: rules_config_invalid")

    def test_halt_missing_threshold_pct(self, tmp_path):
        p = tmp_path / "halt-bad.yaml"
        p.write_text(
            "name: test\n"
            "rules:\n"
            "  - id: x\n"
            "    type: halt_on_drawdown\n"
            "    action: {type: liquidate_all}\n"
        )
        r = _run("rules", "validate", "--config", str(p))
        assert r.returncode == 1
        # oneOf failure should use the human-friendly message
        assert "required fields for its type" in r.stderr


# ---------------------------------------------------------------------------
# rules evaluate
# ---------------------------------------------------------------------------


class TestRulesEvaluate:
    def test_evaluate_against_synthetic_state(self):
        r = _run(
            "rules", "evaluate",
            "--config", str(EXAMPLES / "conservative-majors.yaml"),
            "--positions", str(EXAMPLES / "synthetic-state.json"),
        )
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["ok"] is True
        assert out["schema_version"] == "1.0.0"
        # synthetic-state has WSOL at 72% (cap fires) + both positions at 13.33% DD
        # (trailing stop fires twice). Halt threshold is 15%, portfolio DD is 10%, so no halt.
        actions = {d["action"] for d in out["decisions"]}
        assert "trim" in actions
        assert "exit" in actions
        assert "halt" not in actions
        assert out["diagnostics"]["rules_fired"] == 2  # cap + trailing-stop (halt doesn't fire)

    def test_evaluate_positions_from_stdin(self):
        positions = (EXAMPLES / "synthetic-state.json").read_text()
        r = _run(
            "rules", "evaluate",
            "--config", str(EXAMPLES / "conservative-majors.yaml"),
            "--positions", "-",
            stdin=positions,
        )
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["ok"] is True
        assert len(out["decisions"]) >= 1

    def test_invalid_positions_path(self, tmp_path):
        r = _run(
            "rules", "evaluate",
            "--config", str(EXAMPLES / "conservative-majors.yaml"),
            "--positions", str(tmp_path / "nope.json"),
        )
        assert r.returncode == 1
        assert r.stderr.startswith("FAILED: positions_input_invalid file:")

    def test_invalid_positions_json_via_stdin(self):
        r = _run(
            "rules", "evaluate",
            "--config", str(EXAMPLES / "conservative-majors.yaml"),
            "--positions", "-",
            stdin="{not valid json",
        )
        assert r.returncode == 1
        assert r.stderr.startswith("FAILED: positions_input_invalid json:")

    def test_invalid_bar_json(self):
        r = _run(
            "rules", "evaluate",
            "--config", str(EXAMPLES / "conservative-majors.yaml"),
            "--positions", str(EXAMPLES / "synthetic-state.json"),
            "--bar", "{not json",
        )
        assert r.returncode == 1
        assert r.stderr.startswith("FAILED: bar_input_invalid json:")

    def test_proposed_order_evaluates_speculatively(self):
        # Existing WSOL is 72% of equity already. Propose adding 1 more WSOL —
        # cap still fires, but with the post-trade math.
        proposed = json.dumps(
            {"action": "buy", "asset": "WSOL", "qty": 1.0, "price_usd": 130.0}
        )
        r = _run(
            "rules", "evaluate",
            "--config", str(EXAMPLES / "conservative-majors.yaml"),
            "--positions", str(EXAMPLES / "synthetic-state.json"),
            "--proposed-order", proposed,
        )
        assert r.returncode == 0
        out = json.loads(r.stdout)
        # Decisions should still fire (state inflated by proposed order)
        assert len(out["decisions"]) >= 1


# ---------------------------------------------------------------------------
# Stub commands
# ---------------------------------------------------------------------------


class TestStubs:
    @pytest.mark.parametrize("name", ["position", "alerts", "audit", "watch"])
    def test_stub_returns_not_implemented(self, name):
        r = _run(name, "list")  # arbitrary subarg, captured by REMAINDER
        assert r.returncode == 1
        assert r.stderr.startswith(f"FAILED: not_implemented {name}")
