"""Tests for the market_data source.

Subprocess is mocked so tests don't require real `onchainos` / API keys.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd
import pytest

from scripts import market_data
from scripts.market_data import MarketDataSource


UNIVERSE = [
    {"chain": "solana", "address": "So11111111111111111111111111111111111111112",
     "symbol": "WSOL"},
    {"chain": "solana", "address": "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL",
     "symbol": "JTO"},
]


def _completed(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["onchainos"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _kline_payload(prices: list[float], start_ts_ms: int = 1735689600000) -> str:
    """Build an onchainos market kline-shaped JSON payload."""
    data = []
    for i, c in enumerate(prices):
        data.append({
            "ts": str(start_ts_ms + i * 86_400_000),  # daily bars
            "o": c - 1, "h": c + 2, "l": c - 2, "c": c,
            "vol": 100.0, "volUsd": 100.0 * c,
        })
    return json.dumps({"ok": True, "data": data})


def _ws_start_payload(session_id: str) -> str:
    return json.dumps({"ok": True, "data": {"id": session_id, "status": "running"}})


def _ws_poll_payload(prices: list[float], start_ts_ms: int = 1736294400000) -> str:
    """ws poll events have the same bar shape; usually 1–N new bars per call."""
    return _kline_payload(prices, start_ts_ms=start_ts_ms)


# -----------------------------------------------------------------------------
# Helpers / parsing
# -----------------------------------------------------------------------------


class TestNormalizeBar:
    def test_dict_form(self):
        # 1735689600000 ms = 2025-01-01 00:00:00 UTC
        bar = market_data._normalize_bar(
            {"ts": "1735689600000", "o": 100, "h": 102, "l": 98, "c": 101,
             "vol": 50, "volUsd": 5050}
        )
        assert bar["c"] == 101.0
        assert bar["volUsd"] == 5050.0
        assert bar["ts"].startswith("2025-01-01")

    def test_list_form(self):
        bar = market_data._normalize_bar(
            ["1735689600000", "100", "102", "98", "101", "50", "5050"]
        )
        assert bar["c"] == 101.0

    def test_missing_ts_returns_none(self):
        assert market_data._normalize_bar({"o": 1}) is None


class TestCandlesFromPayload:
    def test_extracts_data_array(self):
        payload = json.loads(_kline_payload([100, 101, 102]))
        bars = market_data._candles_from_payload(payload)
        assert len(bars) == 3
        assert [b["c"] for b in bars] == [100.0, 101.0, 102.0]

    def test_empty_payload(self):
        assert market_data._candles_from_payload({"ok": True, "data": []}) == []

    def test_non_dict_payload(self):
        assert market_data._candles_from_payload("not a dict") == []


class TestSessionIdFromPayload:
    def test_id_at_top_level(self):
        assert market_data._session_id_from_payload(
            {"id": "abc"}
        ) == "abc"

    def test_id_nested_in_data(self):
        assert market_data._session_id_from_payload(
            {"ok": True, "data": {"id": "xyz"}}
        ) == "xyz"

    def test_missing_id(self):
        assert market_data._session_id_from_payload({}) is None


# -----------------------------------------------------------------------------
# MarketDataSource lifecycle
# -----------------------------------------------------------------------------


class TestStart:
    def test_bootstrap_history_and_open_sessions(self):
        # Two assets: each gets a kline call + a ws start call.
        calls: list[list[str]] = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            sub = argv[1]
            if sub == "market":
                return _completed(_kline_payload([100, 101, 102]))
            if sub == "ws":
                action = argv[2]
                if action == "start":
                    return _completed(_ws_start_payload(f"sess-{argv[-1][:4]}"))
                if action == "stop":
                    return _completed(json.dumps({"ok": True}))
            return _completed(json.dumps({"ok": True}))

        with patch("subprocess.run", side_effect=fake_run):
            src = MarketDataSource(UNIVERSE, bar="1D", lookback_bars=50)
            src.start()

        # Each asset should have triggered a kline + ws start
        kline_calls = [c for c in calls if c[1] == "market"]
        ws_start_calls = [c for c in calls if c[1] == "ws" and c[2] == "start"]
        assert len(kline_calls) == 2
        assert len(ws_start_calls) == 2

        snap = src.snapshot()
        assert set(snap.keys()) == {"WSOL", "JTO"}
        # History has the bootstrap bars
        assert len(snap["WSOL"]["history"]) == 3
        # Current is the latest bar
        assert snap["WSOL"]["current"]["c"] == 102.0

    def test_bootstrap_failure_yields_empty_history(self):
        def fake_run(argv, **kwargs):
            sub = argv[1]
            if sub == "market":
                return _completed("", returncode=1, stderr="boom\n")
            return _completed(_ws_start_payload("sess-1"))

        with patch("subprocess.run", side_effect=fake_run):
            src = MarketDataSource(UNIVERSE[:1], bar="1D", lookback_bars=50)
            src.start()
            snap = src.snapshot()
            assert snap["WSOL"]["history"].empty
            assert snap["WSOL"]["current"] is None


class TestPoll:
    def test_poll_appends_new_bars(self):
        def fake_run(argv, **kwargs):
            sub = argv[1]
            if sub == "market":
                return _completed(_kline_payload([100, 101, 102]))
            if sub == "ws":
                action = argv[2]
                if action == "start":
                    return _completed(_ws_start_payload("sess-x"))
                if action == "poll":
                    # WS pushed 2 new bars
                    return _completed(_ws_poll_payload([103, 104]))
                if action == "stop":
                    return _completed("{}")
            return _completed("{}")

        with patch("subprocess.run", side_effect=fake_run):
            src = MarketDataSource(UNIVERSE[:1], bar="1D", lookback_bars=50)
            src.start()
            warnings = src.poll()

        assert warnings == []
        snap = src.snapshot()
        # 3 bootstrap + 2 new = 5
        assert len(snap["WSOL"]["history"]) == 5
        assert snap["WSOL"]["current"]["c"] == 104.0

    def test_poll_empty_means_no_changes(self):
        def fake_run(argv, **kwargs):
            sub = argv[1]
            if sub == "market":
                return _completed(_kline_payload([100, 101, 102]))
            if sub == "ws":
                action = argv[2]
                if action == "start":
                    return _completed(_ws_start_payload("sess-x"))
                if action == "poll":
                    return _completed(json.dumps({"ok": True, "data": []}))
                if action == "stop":
                    return _completed("{}")
            return _completed("{}")

        with patch("subprocess.run", side_effect=fake_run):
            src = MarketDataSource(UNIVERSE[:1], bar="1D", lookback_bars=50)
            src.start()
            warnings = src.poll()

        assert warnings == []
        assert len(src.snapshot()["WSOL"]["history"]) == 3  # still bootstrap

    def test_poll_failure_records_warning_and_reconnects(self):
        call_counter = {"poll": 0, "start": 0}

        def fake_run(argv, **kwargs):
            sub = argv[1]
            if sub == "market":
                return _completed(_kline_payload([100, 101, 102]))
            if sub == "ws":
                action = argv[2]
                if action == "start":
                    call_counter["start"] += 1
                    return _completed(_ws_start_payload(f"sess-{call_counter['start']}"))
                if action == "poll":
                    call_counter["poll"] += 1
                    return _completed("", returncode=1, stderr="session_dead")
                if action == "stop":
                    return _completed("{}")
            return _completed("{}")

        with patch("subprocess.run", side_effect=fake_run):
            src = MarketDataSource(UNIVERSE[:1], bar="1D", lookback_bars=50)
            src.start()
            warnings = src.poll()

        # Initial start + reconnect attempt → 2 starts
        assert call_counter["start"] == 2
        assert len(warnings) >= 1
        assert warnings[0]["kind"] == "market_data_poll_failed"


class TestSnapshot:
    def test_snapshot_returns_empty_df_when_no_bootstrap(self):
        # If start() never called, snapshot still returns the shape strategies expect.
        src = MarketDataSource(UNIVERSE[:1], bar="1D", lookback_bars=50)
        snap = src.snapshot()
        assert "WSOL" in snap
        assert isinstance(snap["WSOL"]["history"], pd.DataFrame)
        assert snap["WSOL"]["history"].empty
        assert snap["WSOL"]["current"] is None


class TestLookbackCap:
    def test_history_capped_to_lookback(self):
        def fake_run(argv, **kwargs):
            sub = argv[1]
            if sub == "market":
                return _completed(_kline_payload(list(range(100, 110))))  # 10 bars
            if sub == "ws":
                action = argv[2]
                if action == "start":
                    return _completed(_ws_start_payload("sess-x"))
                if action == "poll":
                    return _completed(_ws_poll_payload(list(range(120, 130))))
                if action == "stop":
                    return _completed("{}")
            return _completed("{}")

        with patch("subprocess.run", side_effect=fake_run):
            src = MarketDataSource(UNIVERSE[:1], bar="1D", lookback_bars=5)
            src.start()
            src.poll()

        # lookback=5 → only the most recent 5 bars retained
        assert len(src.snapshot()["WSOL"]["history"]) == 5


class TestStop:
    def test_stop_clears_sessions(self):
        def fake_run(argv, **kwargs):
            sub = argv[1]
            if sub == "market":
                return _completed(_kline_payload([100]))
            if sub == "ws":
                action = argv[2]
                if action == "start":
                    return _completed(_ws_start_payload("sess-x"))
                if action == "stop":
                    return _completed("{}")
            return _completed("{}")

        with patch("subprocess.run", side_effect=fake_run):
            src = MarketDataSource(UNIVERSE[:1], bar="1D", lookback_bars=5)
            src.start()
            src.stop()

        # After stop, no sessions tracked
        assert src._sessions == {}
        assert src._started is False
