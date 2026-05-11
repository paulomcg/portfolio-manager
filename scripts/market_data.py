"""Market data source backed by `okx-dex-ws` + a one-shot `onchainos market kline`.

Lifecycle (only active when `pm watch` is invoked with `--strategy`):

    src = MarketDataSource(universe, bar="1D", lookback_bars=365)
    src.start()                # bootstrap history + open WS sessions
    while not stopped:
        src.poll()             # drain WS events, refresh history + current
        market_data = src.snapshot()
        decide(state, market_data)
    src.stop()

Each subprocess call is small (~100ms). The WS connection lives inside the
`onchainos ws` background process, so PM stays sync.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

# Channel name template — onchainos accepts e.g. "dex-token-candle1D".
_CANDLE_CHANNEL = "dex-token-candle{bar}"


class MarketDataError(Exception):
    """Token after the colon maps to a canonical FAILED line, e.g.
    'market_data_bootstrap_failed', 'market_data_subscribe_failed'."""


class MarketDataSource:
    """Per-cycle bar-data feed for one or more universe assets."""

    def __init__(
        self,
        universe: list[dict[str, Any]],
        bar: str = "1D",
        lookback_bars: int = 365,
        cli_bin: str = "onchainos",
    ):
        self.universe = universe
        self.bar = bar
        self.lookback_bars = lookback_bars
        self.cli_bin = cli_bin
        self._sessions: dict[str, str] = {}     # symbol -> WS session id
        self._history: dict[str, pd.DataFrame] = {}
        self._current: dict[str, dict[str, Any]] = {}
        self._warnings: list[dict[str, Any]] = []
        self._started = False

    # -- lifecycle ----------------------------------------------------

    def start(self) -> None:
        """Bootstrap recent kline history + open one WS session per asset."""
        if self._started:
            return
        for tok in self.universe:
            self._bootstrap_history(tok)
            self._open_ws(tok)
        self._started = True

    def poll(self) -> list[dict[str, Any]]:
        """Drain WS events for every asset; return the cycle's warnings.

        Side effect: updates self._history + self._current. Reconnects on
        session failure.
        """
        warnings: list[dict[str, Any]] = []
        for tok in self.universe:
            try:
                events = self._poll_ws(tok)
            except MarketDataError as e:
                warnings.append({"kind": "market_data_poll_failed",
                                 "asset": tok.get("symbol"), "detail": str(e)})
                self._reconnect(tok, warnings)
                continue
            if not events:
                continue
            self._apply_events(tok["symbol"], events)
        return warnings

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Return the market_data dict strategies consume."""
        out: dict[str, dict[str, Any]] = {}
        for tok in self.universe:
            sym = tok["symbol"]
            out[sym] = {
                "current": self._current.get(sym),
                "history": self._history.get(sym, pd.DataFrame(
                    columns=["ts", "o", "h", "l", "c", "vol", "volUsd"]
                )),
            }
        return out

    def stop(self) -> list[dict[str, Any]]:
        """Best-effort tear down of every WS session."""
        warnings: list[dict[str, Any]] = []
        for sym, sid in list(self._sessions.items()):
            try:
                self._run([self.cli_bin, "ws", "stop", "--id", sid])
            except Exception as e:  # noqa: BLE001 — never raise from teardown
                warnings.append({"kind": "market_data_stop_failed",
                                 "asset": sym, "detail": str(e)})
        self._sessions.clear()
        self._started = False
        return warnings

    # -- internals ----------------------------------------------------

    def _bootstrap_history(self, tok: dict[str, Any]) -> None:
        sym = tok["symbol"]
        try:
            payload = self._run([
                self.cli_bin, "market", "kline",
                "--chain", tok["chain"],
                "--address", tok["address"],
                "--bar", self.bar,
                "--limit", str(min(self.lookback_bars, 299)),
            ])
        except MarketDataError as e:
            self._warnings.append({
                "kind": "market_data_bootstrap_failed",
                "asset": sym, "detail": str(e),
            })
            self._history[sym] = pd.DataFrame(
                columns=["ts", "o", "h", "l", "c", "vol", "volUsd"]
            )
            return
        rows = _candles_from_payload(payload)
        self._history[sym] = _bars_to_df(rows)
        if not self._history[sym].empty:
            last_row = self._history[sym].iloc[-1]
            self._current[sym] = {
                "ts": last_row["ts"],
                "o": float(last_row["o"]), "h": float(last_row["h"]),
                "l": float(last_row["l"]), "c": float(last_row["c"]),
                "vol": float(last_row["vol"]),
                "volUsd": float(last_row["volUsd"]),
            }

    def _open_ws(self, tok: dict[str, Any]) -> None:
        sym = tok["symbol"]
        try:
            payload = self._run([
                self.cli_bin, "ws", "start",
                "--chain", tok["chain"],
                "--channel", _CANDLE_CHANNEL.format(bar=self.bar),
                "--token", tok["address"],
            ])
        except MarketDataError as e:
            self._warnings.append({
                "kind": "market_data_subscribe_failed",
                "asset": sym, "detail": str(e),
            })
            return
        sid = _session_id_from_payload(payload)
        if sid is None:
            self._warnings.append({
                "kind": "market_data_subscribe_failed",
                "asset": sym, "detail": "no session id in response",
            })
            return
        self._sessions[sym] = sid

    def _poll_ws(self, tok: dict[str, Any]) -> list[dict[str, Any]]:
        sym = tok["symbol"]
        sid = self._sessions.get(sym)
        if sid is None:
            return []
        payload = self._run([self.cli_bin, "ws", "poll", "--id", sid])
        return _candles_from_payload(payload)

    def _apply_events(self, symbol: str, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        new_df = _bars_to_df(events)
        existing = self._history.get(symbol)
        if existing is None or existing.empty:
            merged = new_df
        else:
            merged = pd.concat([existing, new_df], ignore_index=False)
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        # Cap history at lookback_bars
        if len(merged) > self.lookback_bars:
            merged = merged.iloc[-self.lookback_bars:]
        self._history[symbol] = merged
        if len(merged) > 0:
            last_row = merged.iloc[-1]
            self._current[symbol] = {
                "ts": last_row["ts"],
                "o": float(last_row["o"]), "h": float(last_row["h"]),
                "l": float(last_row["l"]), "c": float(last_row["c"]),
                "vol": float(last_row["vol"]),
                "volUsd": float(last_row["volUsd"]),
            }

    def _reconnect(self, tok: dict[str, Any], warnings: list[dict[str, Any]]) -> None:
        sym = tok["symbol"]
        old_sid = self._sessions.pop(sym, None)
        if old_sid is not None:
            try:
                self._run([self.cli_bin, "ws", "stop", "--id", old_sid])
            except MarketDataError:
                pass  # best-effort
        self._open_ws(tok)
        if sym not in self._sessions:
            warnings.append({
                "kind": "market_data_reconnect_failed",
                "asset": sym,
                "detail": "could not re-open session after poll error",
            })

    def _run(self, argv: list[str]) -> Any:
        """Run an `onchainos` subprocess and parse its JSON stdout."""
        try:
            res = subprocess.run(
                argv, capture_output=True, text=True, check=False, timeout=30
            )
        except FileNotFoundError as e:
            raise MarketDataError(f"cli_not_found {e.filename}") from e
        except subprocess.TimeoutExpired as e:
            raise MarketDataError(f"cli_timeout {' '.join(argv)}") from e
        if res.returncode != 0:
            tail = (res.stderr or res.stdout).strip().splitlines()[-1:] or [""]
            raise MarketDataError(f"cli_failed {tail[0]}")
        try:
            return json.loads(res.stdout)
        except json.JSONDecodeError as e:
            raise MarketDataError(f"cli_output_invalid {e.msg}") from e


# -----------------------------------------------------------------------------
# Helpers — payload shape adapters (permissive; tightened after first live run)
# -----------------------------------------------------------------------------


def _candles_from_payload(payload: Any) -> list[dict[str, Any]]:
    """Extract a list of bar dicts from an `onchainos market kline` or
    `onchainos ws poll` response. Returns [] when no bars are present."""
    if not isinstance(payload, dict):
        return []
    raw = payload.get("data") if "data" in payload else payload
    if isinstance(raw, dict):
        # Sometimes wrapped further: {"data": {"candles": [...]}} or similar.
        for key in ("candles", "data", "events", "items"):
            if key in raw and isinstance(raw[key], list):
                raw = raw[key]
                break
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        bar = _normalize_bar(item)
        if bar is not None:
            out.append(bar)
    return out


def _normalize_bar(item: Any) -> dict[str, Any] | None:
    """Accept either {ts,o,h,l,c,vol,volUsd} object or [ts,o,h,l,c,vol,volUsd] array."""
    if isinstance(item, dict):
        ts = item.get("ts") or item.get("timestamp") or item.get("time")
        if ts is None:
            return None
        try:
            return {
                "ts": _coerce_ts(ts),
                "o": float(item.get("o", item.get("open", 0))),
                "h": float(item.get("h", item.get("high", 0))),
                "l": float(item.get("l", item.get("low", 0))),
                "c": float(item.get("c", item.get("close", 0))),
                "vol": float(item.get("vol", item.get("volume", 0))),
                "volUsd": float(item.get("volUsd", item.get("volumeUsd", 0))),
            }
        except (TypeError, ValueError):
            return None
    if isinstance(item, list) and len(item) >= 6:
        try:
            return {
                "ts": _coerce_ts(item[0]),
                "o": float(item[1]),
                "h": float(item[2]),
                "l": float(item[3]),
                "c": float(item[4]),
                "vol": float(item[5]),
                "volUsd": float(item[6]) if len(item) > 6 else 0.0,
            }
        except (TypeError, ValueError):
            return None
    return None


def _coerce_ts(value: Any) -> str:
    """Coerce a kline ts (ms-since-epoch as int/numeric-string, or ISO string)
    to ISO 8601 UTC. The OKX kline payload uses millisecond strings (per docs)."""
    if isinstance(value, str):
        s = value.strip()
        # Numeric string → treat as ms (OKX's documented kline format).
        if s.lstrip("-").isdigit():
            ms = int(s)
            seconds = ms / 1000 if abs(ms) > 1e12 else ms
            return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
        # Otherwise assume ISO 8601.
        try:
            ts = pd.to_datetime(s, utc=True)
            return ts.isoformat()
        except (TypeError, ValueError):
            return s
    if isinstance(value, (int, float)):
        ms = int(value)
        seconds = ms / 1000 if abs(ms) > 1e12 else ms
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    return str(value)


def _bars_to_df(bars: list[dict[str, Any]]) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame(columns=["ts", "o", "h", "l", "c", "vol", "volUsd"])
    df = pd.DataFrame(bars)
    df["_ts_idx"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df = df.dropna(subset=["_ts_idx"]).set_index("_ts_idx").sort_index()
    df.index.name = None
    return df


def _session_id_from_payload(payload: Any) -> str | None:
    """Extract the session id from an `onchainos ws start` response."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") or payload
    if isinstance(data, dict):
        for key in ("id", "sessionId", "session_id"):
            val = data.get(key)
            if isinstance(val, str) and val:
                return val
    return None
