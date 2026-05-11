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


def _run(
    *args: str, stdin: str | None = None, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
    env = {**os.environ, "PM_PYTHON": sys.executable}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(PM), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


@pytest.fixture
def isolated_state(tmp_path) -> dict[str, str]:
    """Returns extra_env that points pm state at a temp dir for this test."""
    s = tmp_path / "state"
    s.mkdir()
    return {
        "PM_STATE_DIR": str(s),
        "PM_SQLITE_PATH": str(s / "positions.sqlite"),
        "PM_AUDIT_PATH": str(s / "audit.jsonl"),
        "PM_ALERTS_LOG_PATH": str(s / "alerts.jsonl"),
    }


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


class TestWatchCli:
    def test_monitor_mode_three_iterations(self, isolated_state):
        r = _run(
            "watch",
            "--config", str(EXAMPLES / "conservative-majors.yaml"),
            "--positions-source", str(FIXTURES / "wallet_snapshot.json"),
            "--pnl-source", str(FIXTURES / "pnl_snapshot.json"),
            "--interval", "0",
            "--iterations", "3",
            extra_env=isolated_state,
        )
        assert r.returncode == 0
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        # 3 cycle records + 1 final summary line
        assert len(lines) == 4
        cycles = [json.loads(l) for l in lines[:3]]
        for c in cycles:
            assert c["mode"] == "monitor"
            assert c["errors"] == []
        summary = json.loads(lines[3])
        assert summary["ok"] is True
        assert summary["result"]["iterations"] == 3

    def test_live_flag_rejected_until_m5(self, isolated_state):
        r = _run(
            "watch",
            "--config", str(EXAMPLES / "conservative-majors.yaml"),
            "--positions-source", str(FIXTURES / "wallet_snapshot.json"),
            "--live",
            "--max-loss-usd", "10",
            extra_env=isolated_state,
        )
        assert r.returncode == 1
        assert r.stderr.startswith("FAILED: not_implemented live_mode")

    def test_missing_wallet_and_no_source(self, isolated_state):
        r = _run(
            "watch",
            "--config", str(EXAMPLES / "conservative-majors.yaml"),
            "--iterations", "1",
            extra_env=isolated_state,
        )
        assert r.returncode == 1
        assert r.stderr.startswith("FAILED: wallet_required")


# ---------------------------------------------------------------------------
# position commands (M3)
# ---------------------------------------------------------------------------


class TestPositionCommands:
    def test_position_add_then_list(self, isolated_state):
        r = _run(
            "position", "add",
            "--wallet", "w1",
            "--asset", "WSOL",
            "--qty", "30",
            "--cost-usd", "3000",
            extra_env=isolated_state,
        )
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["ok"] is True
        assert out["result"]["asset"] == "WSOL"
        assert out["result"]["source"] == "manual"

        r2 = _run("position", "list", "--wallet", "w1", extra_env=isolated_state)
        out2 = json.loads(r2.stdout)
        assert out2["result"]["manual_overrides"]["WSOL"]["qty"] == 30.0
        assert out2["result"]["manual_overrides"]["WSOL"]["cost_basis_usd"] == 3000.0

    def test_position_snapshot_persists_hwm(self, isolated_state):
        r = _run(
            "position", "snapshot",
            "--wallet", "w1",
            "--wallet-snapshot", str(FIXTURES / "wallet_snapshot.json"),
            "--pnl-snapshot", str(FIXTURES / "pnl_snapshot.json"),
            extra_env=isolated_state,
        )
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["ok"] is True
        positions = out["result"]["positions"]
        wsol = next(p for p in positions if p["asset"] == "WSOL")
        assert wsol["cost_basis_usd"] == 3000.0
        assert wsol["avg_entry_price_usd"] == 100.0
        # HWM persisted
        r2 = _run("position", "list", "--wallet", "w1", extra_env=isolated_state)
        out2 = json.loads(r2.stdout)
        hwms = out2["result"]["high_water_marks"]
        assert hwms["WSOL"] == 3900.0
        assert hwms["<PORTFOLIO>"] == 5400.0

    def test_position_snapshot_missing_wallet_snapshot_fails(self, isolated_state):
        # Missing required arg → argparse returns exit 2; this is a usage error,
        # not a controlled FAILED line.
        r = _run("position", "snapshot", "--wallet", "w1", extra_env=isolated_state)
        assert r.returncode != 0

    def test_position_snapshot_bad_path(self, isolated_state, tmp_path):
        r = _run(
            "position", "snapshot",
            "--wallet", "w1",
            "--wallet-snapshot", str(tmp_path / "nope.json"),
            extra_env=isolated_state,
        )
        assert r.returncode == 1
        assert r.stderr.startswith("FAILED: positions_input_invalid file:")


# ---------------------------------------------------------------------------
# alerts + audit commands (M3)
# ---------------------------------------------------------------------------


class TestAlertsAndAudit:
    def test_audit_show_after_position_add(self, isolated_state):
        _run(
            "position", "add",
            "--wallet", "w1", "--asset", "WSOL", "--qty", "10", "--cost-usd", "1500",
            extra_env=isolated_state,
        )
        r = _run("audit", "show", "--limit", "10", extra_env=isolated_state)
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["result"]["count"] == 1
        assert out["result"]["rows"][0]["event"] == "position.add"

    def test_alerts_pending_empty_initially(self, isolated_state):
        r = _run("alerts", "pending", extra_env=isolated_state)
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["result"]["count"] == 0
        assert out["result"]["alerts"] == []

    def test_alerts_ack_unknown_returns_failed(self, isolated_state):
        r = _run("alerts", "ack", "no-such-alert-id", extra_env=isolated_state)
        assert r.returncode == 1
        assert r.stderr.startswith("FAILED: alert_not_found")
