"""Default paths and constants for pm.

Every path is overridable via env vars (PM_STATE_DIR / PM_AUDIT_PATH / etc.)
so tests can point at temp dirs without monkeypatching modules.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def state_dir() -> Path:
    d = Path(os.environ.get("PM_STATE_DIR", ROOT / "state"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def sqlite_path() -> Path:
    return Path(os.environ.get("PM_SQLITE_PATH", state_dir() / "positions.sqlite"))


def audit_path() -> Path:
    return Path(os.environ.get("PM_AUDIT_PATH", state_dir() / "audit.jsonl"))


def alerts_log_path() -> Path:
    return Path(os.environ.get("PM_ALERTS_LOG_PATH", state_dir() / "alerts.jsonl"))


# Stablecoins treated as cash for portfolio accounting.
STABLECOIN_SYMBOLS = frozenset(
    {"USDC", "USDT", "DAI", "USDE", "PYUSD", "FDUSD", "TUSD"}
)
