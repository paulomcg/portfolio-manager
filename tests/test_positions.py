"""Tests for position derivation + sqlite persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts import positions

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Point sqlite + audit + alerts at a temp dir for each test."""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("PM_STATE_DIR", str(state))
    monkeypatch.setenv("PM_SQLITE_PATH", str(state / "positions.sqlite"))
    monkeypatch.setenv("PM_AUDIT_PATH", str(state / "audit.jsonl"))
    monkeypatch.setenv("PM_ALERTS_LOG_PATH", str(state / "alerts.jsonl"))
    yield state


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


class TestDerivation:
    def test_basic_derivation_with_pnl(self):
        wallet = _load("wallet_snapshot")
        pnl = _load("pnl_snapshot")
        result = positions.derive_positions(wallet=wallet, pnl_by_token=pnl)
        # WSOL value 3900, unrealized 900 → cost basis 3000, entry 100
        wsol = next(p for p in result["positions"] if p["asset"] == "WSOL")
        assert wsol["cost_basis_usd"] == 3000.0
        assert wsol["avg_entry_price_usd"] == 100.0
        assert wsol["source"] == "auto"
        # USDC should be rolled into cash, not in positions
        assert all(p["asset"] != "USDC" for p in result["positions"])
        assert result["cash_usd"] == 200.0
        # Total equity = 3900 + 1300 + 200 = 5400
        assert result["total_equity_usd"] == 5400.0

    def test_no_pnl_data_defaults_cost_basis_to_value(self):
        wallet = _load("wallet_snapshot")
        result = positions.derive_positions(wallet=wallet, pnl_by_token={})
        wsol = next(p for p in result["positions"] if p["asset"] == "WSOL")
        assert wsol["cost_basis_usd"] == wsol["value_usd"]
        assert wsol["unrealized_pnl_usd"] == 0.0

    def test_negative_cost_basis_clamped_to_zero_with_warning(self):
        wallet = _load("wallet_snapshot")
        # Unrealized > value → cost basis would be negative.
        pnl = {
            "solana:So11111111111111111111111111111111111111112": {
                "asset": "WSOL",
                "unrealized_pnl_usd": 5000.0,
                "realized_pnl_usd": 0.0,
            }
        }
        result = positions.derive_positions(wallet=wallet, pnl_by_token=pnl)
        wsol = next(p for p in result["positions"] if p["asset"] == "WSOL")
        assert wsol["cost_basis_usd"] == 0.0
        assert any("negative" in w for w in result["warnings"])

    def test_manual_override_replaces_derived_fields(self):
        wallet = _load("wallet_snapshot")
        pnl = _load("pnl_snapshot")
        overrides = {
            "WSOL": {
                "qty": 100.0,
                "cost_basis_usd": 5000.0,
                "mark_price_usd": None,
                "ts_utc": "2025-01-01",
                "source": "manual",
            }
        }
        result = positions.derive_positions(
            wallet=wallet, pnl_by_token=pnl, manual_overrides=overrides
        )
        wsol = next(p for p in result["positions"] if p["asset"] == "WSOL")
        assert wsol["qty"] == 100.0
        assert wsol["cost_basis_usd"] == 5000.0
        assert wsol["source"] == "manual"

    def test_zero_qty_position_skipped(self):
        wallet = {
            "wallet_address": "w",
            "ts_utc": "x",
            "tokens": [
                {
                    "asset": "DUST",
                    "chain": "solana",
                    "address": "d",
                    "qty": 0.0,
                    "mark_price_usd": 0.0,
                    "value_usd": 0.0,
                }
            ],
        }
        result = positions.derive_positions(wallet=wallet)
        assert result["positions"] == []

    def test_hwm_initializes_to_current_value(self):
        wallet = _load("wallet_snapshot")
        pnl = _load("pnl_snapshot")
        result = positions.derive_positions(wallet=wallet, pnl_by_token=pnl, hwm_state={})
        wsol = next(p for p in result["positions"] if p["asset"] == "WSOL")
        # First observation: HWM = current value, drawdown = 0
        assert wsol["high_water_mark_usd"] == wsol["value_usd"]
        assert wsol["drawdown_from_hwm_pct"] == 0.0
        assert result["high_water_mark_usd"] == result["total_equity_usd"]
        assert result["drawdown_from_hwm_pct"] == 0.0

    def test_hwm_state_drives_drawdown_calculation(self):
        wallet = _load("wallet_snapshot")
        pnl = _load("pnl_snapshot")
        # Pretend WSOL once hit $5000, portfolio once hit $6000
        hwm = {"WSOL": 5000.0, "<PORTFOLIO>": 6000.0}
        result = positions.derive_positions(
            wallet=wallet, pnl_by_token=pnl, hwm_state=hwm
        )
        wsol = next(p for p in result["positions"] if p["asset"] == "WSOL")
        # WSOL now $3900; HWM 5000 → drawdown 22%
        assert wsol["high_water_mark_usd"] == 5000.0
        assert wsol["drawdown_from_hwm_pct"] == pytest.approx(22.0, abs=0.01)
        # Portfolio now $5400; HWM 6000 → drawdown 10%
        assert result["high_water_mark_usd"] == 6000.0
        assert result["drawdown_from_hwm_pct"] == pytest.approx(10.0, abs=0.01)

    def test_hwm_only_increases(self):
        # When current value exceeds stored HWM, HWM is replaced.
        wallet = _load("wallet_snapshot")
        # WSOL value will be 3900 from fixture; pretend old HWM was lower
        hwm = {"WSOL": 1000.0}
        result = positions.derive_positions(wallet=wallet, hwm_state=hwm)
        wsol = next(p for p in result["positions"] if p["asset"] == "WSOL")
        assert wsol["high_water_mark_usd"] == 3900.0


# ---------------------------------------------------------------------------
# SQLite persistence — HWM + manual overrides
# ---------------------------------------------------------------------------


class TestHwmPersistence:
    def test_load_hwm_empty_when_no_prior_state(self):
        assert positions.load_hwm_state("new-wallet") == {}

    def test_update_then_load_round_trip(self):
        derived = {
            "high_water_mark_usd": 6000.0,
            "positions": [
                {"asset": "WSOL", "high_water_mark_usd": 5000.0},
                {"asset": "JTO", "high_water_mark_usd": 1500.0},
            ],
        }
        positions.update_hwm_state("w1", derived)
        loaded = positions.load_hwm_state("w1")
        assert loaded["WSOL"] == 5000.0
        assert loaded["JTO"] == 1500.0
        assert loaded["<PORTFOLIO>"] == 6000.0

    def test_hwm_max_semantic_never_lowers(self):
        positions.update_hwm_state(
            "w1",
            {
                "high_water_mark_usd": 5000.0,
                "positions": [{"asset": "WSOL", "high_water_mark_usd": 4000.0}],
            },
        )
        # Subsequent lower observation must not overwrite
        positions.update_hwm_state(
            "w1",
            {
                "high_water_mark_usd": 3000.0,
                "positions": [{"asset": "WSOL", "high_water_mark_usd": 2500.0}],
            },
        )
        loaded = positions.load_hwm_state("w1")
        assert loaded["WSOL"] == 4000.0
        assert loaded["<PORTFOLIO>"] == 5000.0


class TestManualOverrides:
    def test_upsert_and_load(self):
        positions.upsert_manual_override(
            "w1", "WSOL", qty=100.0, cost_basis_usd=5000.0
        )
        overrides = positions.load_manual_overrides("w1")
        assert overrides["WSOL"]["qty"] == 100.0
        assert overrides["WSOL"]["cost_basis_usd"] == 5000.0

    def test_upsert_partial_update_preserves_existing_fields(self):
        positions.upsert_manual_override(
            "w1", "WSOL", qty=100.0, cost_basis_usd=5000.0
        )
        # Now update mark price only; qty / cost_basis must remain
        positions.upsert_manual_override("w1", "WSOL", mark_price_usd=140.0)
        overrides = positions.load_manual_overrides("w1")
        assert overrides["WSOL"]["qty"] == 100.0
        assert overrides["WSOL"]["cost_basis_usd"] == 5000.0
        assert overrides["WSOL"]["mark_price_usd"] == 140.0

    def test_delete_returns_rowcount(self):
        positions.upsert_manual_override("w1", "WSOL", qty=1.0, cost_basis_usd=1.0)
        assert positions.delete_manual_override("w1", "WSOL") == 1
        assert positions.delete_manual_override("w1", "WSOL") == 0
        assert positions.load_manual_overrides("w1") == {}
