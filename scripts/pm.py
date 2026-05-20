"""pm — Portfolio Manager CLI dispatcher.

All commands emit JSON to stdout. Successful commands print
    {"ok": true, "result": ...}
Failures print
    FAILED: <canonical-line-from-vocabulary>
to stderr and exit non-zero. See SKILL.md for the full failure vocabulary.

Stateless commands (M2):
    pm rules validate --config <yaml-path>
    pm rules evaluate
        --config <yaml-path>
        --positions <json-path-or-->         # use '-' to read positions JSON from stdin
        [--bar <json>]
        [--proposed-order <json>]

Stateful and watch commands are wired in later milestones.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

# Allow `python scripts/pm.py …` (no package context) by injecting repo root.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts import alerts, audit, positions, report, rule_engine, schema, watch  # noqa: E402
from scripts.dashboard import server as dashboard_server  # noqa: E402
from scripts import strategy as strategy_mod  # noqa: E402
from scripts.executor import (  # noqa: E402
    OnchainosSwapExecutor,
    SwapExecutor,
    SyntheticSwapExecutor,
)
from scripts.market_data import (  # noqa: E402
    MarketDataError,
    MarketDataSource,
    StaticMarketData,
    load_static_snapshot,
)
from scripts.wallet_source import (  # noqa: E402
    OnchainosWalletSource,
    SyntheticWalletSource,
    WalletSource,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _ok(result: Any) -> int:
    """Print {"ok": true, "result": …} to stdout and return EXIT_OK."""
    print(json.dumps({"ok": True, "result": result}, sort_keys=False, default=str))
    return EXIT_OK


def _failed(line: str) -> int:
    """Print 'FAILED: …' to stderr and return EXIT_FAILED.

    `line` should be the substring AFTER 'FAILED: ' (i.e. the canonical token
    plus any specific detail).
    """
    print(f"FAILED: {line}", file=sys.stderr)
    return EXIT_FAILED


def _wrap(handler: Callable[..., int]) -> Callable[..., int]:
    """Catch unexpected exceptions and map them to canonical FAILED lines.

    Each handler must already use _ok / _failed for expected paths; this is
    the last-line safety net so partial output never reaches stdout.
    """

    @functools.wraps(handler)
    def _w(args: argparse.Namespace) -> int:
        try:
            return handler(args)
        except KeyboardInterrupt:
            return _failed("interrupted")
        except Exception as e:  # noqa: BLE001 — top-level safety net
            return _failed(f"internal_error {type(e).__name__}: {e}")

    return _w


# ---------------------------------------------------------------------------
# Helpers shared across commands
# ---------------------------------------------------------------------------


def _read_yaml(path: str) -> tuple[bool, Any]:
    """Return (ok, parsed_or_error_message). ok=False → message is human readable."""
    import yaml

    p = Path(path)
    if not p.exists():
        return False, f"rules_config_invalid file: not found at {path}"
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        return False, f"rules_config_invalid file: {e}"
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return False, f"rules_config_invalid yaml: {e.__class__.__name__}: {e}"
    if not isinstance(parsed, dict):
        return False, "rules_config_invalid yaml: top-level must be a mapping"
    return True, parsed


def _read_positions(arg: str) -> tuple[bool, Any]:
    """Read a positions snapshot JSON from a path or '-' for stdin."""
    if arg == "-":
        raw = sys.stdin.read()
        if not raw.strip():
            return False, "positions_input_invalid empty stdin"
    else:
        p = Path(arg)
        if not p.exists():
            return False, f"positions_input_invalid file: not found at {arg}"
        raw = p.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return False, f"positions_input_invalid json: {e.msg} (line {e.lineno} col {e.colno})"
    if not isinstance(parsed, dict):
        return False, "positions_input_invalid json: top-level must be an object"
    return True, parsed


def _read_inline_json(arg: str, kind: str) -> tuple[bool, Any]:
    """Parse a JSON string passed via --bar or --proposed-order."""
    try:
        parsed = json.loads(arg)
    except json.JSONDecodeError as e:
        token = "bar_input_invalid" if kind == "bar" else "positions_input_invalid"
        return False, f"{token} json: {e.msg} (line {e.lineno} col {e.colno})"
    if not isinstance(parsed, dict):
        token = "bar_input_invalid" if kind == "bar" else "positions_input_invalid"
        return False, f"{token} json: must be an object"
    return True, parsed


def _format_schema_error(e: Any) -> str:
    """Render a jsonschema.ValidationError into a canonical FAILED detail.

    Special-cases oneOf failures (per-rule-type field requirements) since
    jsonschema's default message dumps the whole instance, which is noisy.
    """
    field = ".".join(str(p) for p in e.absolute_path) or "<root>"
    if e.validator == "oneOf":
        return (
            f"rules_config_invalid {field}: rule does not satisfy the "
            "required fields for its type (halt_on_drawdown/max_position_pct "
            "need threshold_pct; trailing_stop needs pct and applies_to)"
        )
    msg = e.message
    if len(msg) > 200:
        msg = msg[:197] + "..."
    return f"rules_config_invalid {field}: {msg}"


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


@_wrap
def cmd_rules_validate(args: argparse.Namespace) -> int:
    ok, parsed = _read_yaml(args.config)
    if not ok:
        return _failed(parsed)
    try:
        schema.validate(parsed)
    except Exception as e:  # jsonschema.ValidationError
        return _failed(_format_schema_error(e))
    return _ok({"name": parsed.get("name"), "rules": len(parsed.get("rules", []))})


@_wrap
def cmd_rules_evaluate(args: argparse.Namespace) -> int:
    ok, rules_cfg = _read_yaml(args.config)
    if not ok:
        return _failed(rules_cfg)
    try:
        schema.validate(rules_cfg)
    except Exception as e:
        return _failed(_format_schema_error(e))

    ok, positions = _read_positions(args.positions)
    if not ok:
        return _failed(positions)

    bar = None
    if args.bar is not None:
        ok, bar = _read_inline_json(args.bar, kind="bar")
        if not ok:
            return _failed(bar)

    proposed = None
    if args.proposed_order is not None:
        ok, proposed = _read_inline_json(args.proposed_order, kind="positions")
        if not ok:
            return _failed(proposed)

    result = rule_engine.evaluate(
        positions=positions,
        rules_config=rules_cfg,
        bar=bar,
        proposed_order=proposed,
    )
    # Result is already the public schema shape (ok/schema_version/decisions/...).
    print(json.dumps(result, sort_keys=False, default=str))
    return EXIT_OK


# ---------------------------------------------------------------------------
# Position commands (M3)
# ---------------------------------------------------------------------------


def _require_wallet(args: argparse.Namespace) -> str | None:
    w = getattr(args, "wallet", None)
    if not w:
        _failed("wallet_required pass --wallet <address>")
        return None
    return w


@_wrap
def cmd_position_list(args: argparse.Namespace) -> int:
    """List currently-tracked positions: manual overrides + last-known HWMs.

    For derived positions from a live wallet, use `pm position snapshot`
    (which re-runs derivation against the latest wallet+pnl data).
    """
    wallet = _require_wallet(args)
    if wallet is None:
        return EXIT_FAILED
    overrides = positions.load_manual_overrides(wallet)
    hwms = positions.load_hwm_state(wallet)
    return _ok(
        {
            "wallet_address": wallet,
            "manual_overrides": overrides,
            "high_water_marks": hwms,
        }
    )


@_wrap
def cmd_position_add(args: argparse.Namespace) -> int:
    wallet = _require_wallet(args)
    if wallet is None:
        return EXIT_FAILED
    positions.upsert_manual_override(
        wallet_address=wallet,
        asset=args.asset,
        qty=args.qty,
        cost_basis_usd=args.cost_usd,
        ts_utc=args.ts,
        notes=args.notes,
    )
    audit.append(
        {
            "event": "position.add",
            "wallet": wallet,
            "asset": args.asset,
            "qty": args.qty,
            "cost_basis_usd": args.cost_usd,
            "notes": args.notes,
        }
    )
    return _ok(
        {
            "wallet": wallet,
            "asset": args.asset,
            "qty": args.qty,
            "cost_basis_usd": args.cost_usd,
            "source": "manual",
        }
    )


@_wrap
def cmd_position_update(args: argparse.Namespace) -> int:
    wallet = _require_wallet(args)
    if wallet is None:
        return EXIT_FAILED
    positions.upsert_manual_override(
        wallet_address=wallet,
        asset=args.asset,
        mark_price_usd=args.mark_price,
        notes=args.notes,
    )
    audit.append(
        {
            "event": "position.update",
            "wallet": wallet,
            "asset": args.asset,
            "mark_price_usd": args.mark_price,
        }
    )
    return _ok({"wallet": wallet, "asset": args.asset, "mark_price_usd": args.mark_price})


@_wrap
def cmd_position_close(args: argparse.Namespace) -> int:
    wallet = _require_wallet(args)
    if wallet is None:
        return EXIT_FAILED
    deleted = positions.delete_manual_override(wallet, args.asset)
    audit.append(
        {
            "event": "position.close",
            "wallet": wallet,
            "asset": args.asset,
            "qty": args.qty,
            "price_usd": args.price,
            "removed_override": deleted > 0,
        }
    )
    return _ok(
        {
            "wallet": wallet,
            "asset": args.asset,
            "qty": args.qty,
            "price_usd": args.price,
            "removed_manual_override": deleted > 0,
        }
    )


@_wrap
def cmd_position_snapshot(args: argparse.Namespace) -> int:
    """Run position derivation against an explicit wallet/pnl JSON fixture.

    In M4 this will also be wired against live okx CLI calls; here we accept
    a JSON file so the synthetic-demo flow works without API keys.
    """
    wallet = _require_wallet(args)
    if wallet is None:
        return EXIT_FAILED
    if not args.wallet_snapshot:
        return _failed("positions_input_invalid pass --wallet-snapshot <json-path>")
    ok, snap = _read_positions(args.wallet_snapshot)  # JSON loader; same shape rules
    if not ok:
        return _failed(snap)
    pnl_path = args.pnl_snapshot
    pnl_by_token: dict = {}
    if pnl_path:
        ok, pnl_by_token = _read_positions(pnl_path)
        if not ok:
            return _failed(pnl_by_token)
    hwms = positions.load_hwm_state(wallet)
    overrides = positions.load_manual_overrides(wallet)
    derived = positions.derive_positions(
        wallet=snap, pnl_by_token=pnl_by_token, hwm_state=hwms, manual_overrides=overrides
    )
    # Persist any HWM growth
    positions.update_hwm_state(wallet, derived)
    return _ok(derived)


# ---------------------------------------------------------------------------
# Alerts commands (M3)
# ---------------------------------------------------------------------------


@_wrap
def cmd_alerts_pending(args: argparse.Namespace) -> int:
    rows = alerts.pending(
        wallet_address=args.wallet, severity=args.severity, limit=args.limit
    )
    return _ok({"count": len(rows), "alerts": rows})


@_wrap
def cmd_alerts_ack(args: argparse.Namespace) -> int:
    result = alerts.ack(args.alert_ids)
    if result["acked"] == 0 and result["not_found"] == len(args.alert_ids):
        return _failed(f"alert_not_found id={','.join(args.alert_ids)}")
    return _ok(result)


@_wrap
def cmd_alerts_history(args: argparse.Namespace) -> int:
    rows = alerts.history(
        wallet_address=args.wallet, since=args.since, limit=args.limit
    )
    return _ok({"count": len(rows), "alerts": rows})


# ---------------------------------------------------------------------------
# Audit commands (M3)
# ---------------------------------------------------------------------------


@_wrap
def cmd_audit_show(args: argparse.Namespace) -> int:
    rows = audit.read(limit=args.limit, since=args.since)
    return _ok({"count": len(rows), "rows": rows})


# ---------------------------------------------------------------------------
# Watch loop (M4 monitor mode; live mode lands in M5)
# ---------------------------------------------------------------------------


def _build_wallet_source(args: argparse.Namespace) -> tuple[WalletSource | None, str | None]:
    """Construct the right WalletSource. Returns (source, failed_line_or_None)."""
    if args.positions_source:
        src = SyntheticWalletSource(
            wallet_path=args.positions_source, pnl_path=args.pnl_source
        )
        return src, None
    if not args.wallet:
        return None, "wallet_required pass --wallet <address> or --positions-source <json>"
    src = OnchainosWalletSource(wallet_address=args.wallet, chain=args.chain)
    return src, None


@_wrap
def cmd_watch(args: argparse.Namespace) -> int:
    ok, rules_cfg = _read_yaml(args.config)
    if not ok:
        return _failed(rules_cfg)
    try:
        schema.validate(rules_cfg)
    except Exception as e:
        return _failed(_format_schema_error(e))

    src, err = _build_wallet_source(args)
    if err is not None:
        return _failed(err)

    # Resolve wallet_address used for HWM/audit keys.
    wallet_address = (
        args.wallet
        or (args.positions_source and Path(args.positions_source).stem)
        or "synthetic"
    )
    interval = args.interval if args.interval is not None else (
        rules_cfg.get("poll", {}).get("interval_seconds", 60)
    )

    # Build the executor only when --live; live mode REQUIRES --max-loss-usd.
    executor: SwapExecutor | None = None
    if args.live:
        if args.max_loss_usd is None:
            return _failed("live_mode_missing_flag --max-loss-usd")
        if args.executor == "synthetic":
            # Allow backtest harnesses to override the simulator's cost
            # assumptions via env vars; defaults match SyntheticSwapExecutor.
            _fb = os.environ.get("PM_SYNTHETIC_FEE_BPS")
            _sb = os.environ.get("PM_SYNTHETIC_SLIPPAGE_BPS")
            executor = SyntheticSwapExecutor(
                fee_bps=float(_fb) if _fb else 30.0,
                slippage_bps=float(_sb) if _sb else 50.0,
            )
        else:
            if not args.wallet:
                return _failed("wallet_required live mode needs --wallet <address>")
            executor = OnchainosSwapExecutor(
                wallet_address=args.wallet, chain=args.chain
            )

    # v0.2.0: optional strategy hook + market data source.
    strategy_invoc = None
    market_data = None
    if args.strategy:
        try:
            strategy_invoc = strategy_mod.load(args.strategy)
        except strategy_mod.StrategyError as e:
            return _failed(str(e))
        if args.market_data_source:
            # Static / file-backed snapshot — used by the backtester. Mirrors
            # the existing --positions-source / --pnl-source pattern.
            try:
                market_data = load_static_snapshot(args.market_data_source)
            except MarketDataError as e:
                return _failed(str(e))
        elif rules_cfg.get("universe") and not args.no_market_data:
            market_data = MarketDataSource(
                universe=rules_cfg["universe"],
                bar=args.bar,
                lookback_bars=args.lookback_bars,
            )
            try:
                market_data.start()
            except Exception as e:  # noqa: BLE001
                # Bootstrap failures are recorded; the strategy still runs
                # against an empty history (its cold-start contract).
                print(f"# market_data start warning: {e}", file=sys.stderr)

    try:
        summary = watch.run_monitor(
            rules_config=rules_cfg,
            wallet_source=src,
            wallet_address=wallet_address,
            interval_seconds=interval,
            iterations=args.iterations,
            executor=executor,
            max_loss_usd=args.max_loss_usd,
            max_wallet_loss_usd=args.max_wallet_loss_usd,
            strategy=strategy_invoc,
            market_data=market_data,
        )
    except Exception as e:  # noqa: BLE001
        return _failed(f"internal_error watch loop: {type(e).__name__}: {e}")
    finally:
        if market_data is not None:
            try:
                market_data.stop()
            except Exception:  # noqa: BLE001
                pass
    print(json.dumps({"ok": True, "result": summary}, default=str))
    return EXIT_OK


# ---------------------------------------------------------------------------
# Dashboard command (M21 / v0.2.0)
# ---------------------------------------------------------------------------


@_wrap
def cmd_dashboard(args: argparse.Namespace) -> int:
    """Start the local read-only observability dashboard."""
    try:
        dashboard_server.serve_forever(host=args.host, port=args.port)
    except OSError as e:
        return _failed(f"dashboard_port_in_use {e}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Report command (M9 / v0.2.0)
# ---------------------------------------------------------------------------


@_wrap
def cmd_report(args: argparse.Namespace) -> int:
    audit_path = Path(args.audit_path) if args.audit_path else None
    if not args.out:
        return _failed("report_invalid --out <dir> is required")
    try:
        result = report.run(
            audit_path=audit_path,
            wallet=args.wallet,
            since=args.since,
            until=args.until,
            out=Path(args.out),
            title=args.title,
        )
    except Exception as e:  # noqa: BLE001
        return _failed(f"report_failed {type(e).__name__}: {e}")
    return _ok(
        {
            "report_path": str(Path(args.out) / "report.json"),
            "markdown_path": str(Path(args.out) / "report.md"),
            "equity_chart_path": str(Path(args.out) / "equity.png"),
            "cycle_count": result["cycle_count"],
            "metrics_summary": {
                k: result["metrics"].get(k)
                for k in (
                    "bars", "total_return_pct", "cagr_pct", "sharpe",
                    "sortino", "max_drawdown_pct"
                )
            },
        }
    )


# Generic stub for commands not yet implemented.


@_wrap
def cmd_stub(args: argparse.Namespace) -> int:
    name = getattr(args, "_stub_name", "this command")
    return _failed(f"not_implemented {name} ships in a later milestone")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pm",
        description=(
            "Portfolio Manager — reactive wallet supervisor. Composes OKX "
            "Onchain OS skills (okx-wallet-portfolio, okx-dex-market, "
            "okx-dex-swap) with a stateless rule engine."
        ),
    )
    p.add_argument("--version", action="version", version="pm 0.2.0")

    sub = p.add_subparsers(dest="cmd", required=True)

    # rules ---------------------------------------------------------
    rules = sub.add_parser("rules", help="Rule-config commands")
    rules_sub = rules.add_subparsers(dest="subcmd", required=True)

    rv = rules_sub.add_parser("validate", help="Validate a rules YAML against the schema")
    rv.add_argument("--config", required=True, help="Path to rules YAML")
    rv.set_defaults(_handler=cmd_rules_validate)

    re = rules_sub.add_parser(
        "evaluate",
        help="Evaluate rules against a positions snapshot; emit decisions JSON",
    )
    re.add_argument("--config", required=True, help="Path to rules YAML")
    re.add_argument(
        "--positions",
        required=True,
        help="Path to positions JSON (or '-' to read from stdin)",
    )
    re.add_argument(
        "--bar",
        default=None,
        help="Optional inline JSON for the current OHLCV bar (advanced; unused in v1 rules)",
    )
    re.add_argument(
        "--proposed-order",
        default=None,
        help="Optional inline JSON for a hypothetical order (mediated-open path)",
    )
    re.set_defaults(_handler=cmd_rules_evaluate)

    # position --------------------------------------------------------
    pos = sub.add_parser("position", help="Position ledger commands")
    pos_sub = pos.add_subparsers(dest="subcmd", required=True)

    pl = pos_sub.add_parser("list", help="Show manual overrides + HWMs for a wallet")
    pl.add_argument("--wallet", required=True)
    pl.set_defaults(_handler=cmd_position_list)

    pa = pos_sub.add_parser("add", help="Record a manual position override")
    pa.add_argument("--wallet", required=True)
    pa.add_argument("--asset", required=True)
    pa.add_argument("--qty", type=float, required=True)
    pa.add_argument("--cost-usd", dest="cost_usd", type=float, required=True)
    pa.add_argument("--ts", default=None, help="Optional ISO 8601 timestamp for the open")
    pa.add_argument("--notes", default=None)
    pa.set_defaults(_handler=cmd_position_add)

    pu = pos_sub.add_parser("update", help="Update a manual override's mark price")
    pu.add_argument("--wallet", required=True)
    pu.add_argument("--asset", required=True)
    pu.add_argument("--mark-price", dest="mark_price", type=float, required=True)
    pu.add_argument("--notes", default=None)
    pu.set_defaults(_handler=cmd_position_update)

    pc = pos_sub.add_parser("close", help="Remove a manual override (records a closing trade)")
    pc.add_argument("--wallet", required=True)
    pc.add_argument("--asset", required=True)
    pc.add_argument("--qty", type=float, required=True)
    pc.add_argument("--price", type=float, required=True)
    pc.set_defaults(_handler=cmd_position_close)

    ps = pos_sub.add_parser(
        "snapshot", help="Derive a positions snapshot from wallet+pnl JSON inputs"
    )
    ps.add_argument("--wallet", required=True)
    ps.add_argument(
        "--wallet-snapshot",
        dest="wallet_snapshot",
        required=True,
        help="Path to a WalletSnapshot JSON (or '-' for stdin)",
    )
    ps.add_argument(
        "--pnl-snapshot",
        dest="pnl_snapshot",
        default=None,
        help="Optional path to per-token PnL JSON (or '-' for stdin)",
    )
    ps.set_defaults(_handler=cmd_position_snapshot)

    # alerts ----------------------------------------------------------
    al = sub.add_parser("alerts", help="Alerts queue commands")
    al_sub = al.add_subparsers(dest="subcmd", required=True)

    ap = al_sub.add_parser("pending", help="List unacked alerts (newest first)")
    ap.add_argument("--wallet", default=None)
    ap.add_argument("--severity", choices=["info", "warn", "critical"], default=None)
    ap.add_argument("--limit", type=int, default=100)
    ap.set_defaults(_handler=cmd_alerts_pending)

    aa = al_sub.add_parser("ack", help="Mark one or more alerts as acked")
    aa.add_argument("alert_ids", nargs="+")
    aa.set_defaults(_handler=cmd_alerts_ack)

    ah = al_sub.add_parser("history", help="Show all alerts (acked + pending)")
    ah.add_argument("--wallet", default=None)
    ah.add_argument("--since", default=None, help="ISO 8601 timestamp")
    ah.add_argument("--limit", type=int, default=100)
    ah.set_defaults(_handler=cmd_alerts_history)

    # audit -----------------------------------------------------------
    au = sub.add_parser("audit", help="Audit log queries")
    au_sub = au.add_subparsers(dest="subcmd", required=True)

    aush = au_sub.add_parser("show", help="Read audit log entries (newest first)")
    aush.add_argument("--since", default=None, help="ISO 8601 timestamp")
    aush.add_argument("--limit", type=int, default=100)
    aush.set_defaults(_handler=cmd_audit_show)

    # dashboard -------------------------------------------------------
    db = sub.add_parser(
        "dashboard",
        help="Start the local read-only observability web dashboard",
    )
    db.add_argument("--host", default="127.0.0.1", help="Bind host (default: localhost)")
    db.add_argument("--port", type=int, default=7777, help="Bind port (default: 7777)")
    db.set_defaults(_handler=cmd_dashboard)

    # report ----------------------------------------------------------
    rp = sub.add_parser(
        "report",
        help="Compute Sharpe/Sortino/maxDD/win-rate/etc. from a PM audit log",
    )
    rp.add_argument("--audit-path", dest="audit_path", default=None,
                    help="Audit log to read (default: state/audit.jsonl)")
    rp.add_argument("--wallet", default=None, help="Filter to a single wallet address")
    rp.add_argument("--since", default=None, help="ISO 8601 timestamp lower bound")
    rp.add_argument("--until", default=None, help="ISO 8601 timestamp upper bound")
    rp.add_argument("--out", default=None, required=True,
                    help="Output directory; will be created if missing")
    rp.add_argument("--title", default=None, help="Override the chart title")
    rp.set_defaults(_handler=cmd_report)

    # watch -----------------------------------------------------------
    wa = sub.add_parser(
        "watch",
        help="Run the monitor/live watch loop against a wallet + rule config",
    )
    wa.add_argument("--config", required=True, help="Path to rules YAML")
    wa.add_argument("--wallet", default=None, help="Wallet address (omit when --positions-source is set)")
    wa.add_argument("--chain", default="solana")
    wa.add_argument(
        "--positions-source",
        dest="positions_source",
        default=None,
        help="Path to a wallet snapshot JSON for synthetic / no-keys runs",
    )
    wa.add_argument(
        "--pnl-source",
        dest="pnl_source",
        default=None,
        help="Optional path to a per-token PnL JSON (synthetic mode)",
    )
    wa.add_argument("--interval", type=int, default=None, help="Seconds between cycles")
    wa.add_argument("--iterations", type=int, default=None, help="Cap on iterations (omit for infinite)")
    wa.add_argument("--live", action="store_true", help="Execute swaps via the chosen executor")
    wa.add_argument(
        "--max-loss-usd",
        dest="max_loss_usd",
        type=float,
        default=None,
        help="Hard kill switch — REQUIRED when --live is set; loop halts when cumulative realized loss exceeds this",
    )
    wa.add_argument(
        "--max-wallet-loss-usd",
        dest="max_wallet_loss_usd",
        type=float,
        default=None,
        help=(
            "Secondary kill switch comparing current wallet equity to a "
            "baseline captured on the first cycle. Halts when "
            "baseline - current > this cap. Optional but recommended for "
            "live trading — catches rug scenarios that --max-loss-usd "
            "misses entirely (rugged positions never book a realized loss)."
        ),
    )
    wa.add_argument(
        "--executor",
        choices=["onchainos", "synthetic"],
        default="onchainos",
        help="Swap executor for live mode (default: onchainos; use 'synthetic' for cap-enforcement demos / tests)",
    )
    # v0.2.0 strategy + market data flags
    wa.add_argument(
        "--strategy",
        default=None,
        help="Path to a Python file with `decide(state, market_data) -> list[Action]`. Optional.",
    )
    wa.add_argument(
        "--bar",
        default="1D",
        help="Bar timeframe for the strategy's market data feed (default: 1D)",
    )
    wa.add_argument(
        "--lookback-bars",
        dest="lookback_bars",
        type=int,
        default=365,
        help="How many historical bars to bootstrap per asset (default: 365)",
    )
    wa.add_argument(
        "--no-market-data",
        dest="no_market_data",
        action="store_true",
        help="Skip WS market data subscription even when --strategy is set",
    )
    wa.add_argument(
        "--market-data-source",
        dest="market_data_source",
        default=None,
        help=(
            "Path to a static market-data JSON file shaped as "
            "{symbol: {current: <bar>, history: [<bars>...]}}. Bypasses WS — "
            "used by the backtester to feed PM offline."
        ),
    )
    wa.set_defaults(_handler=cmd_watch)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "_handler", None)
    if handler is None:
        parser.error("no handler for this command")  # exits EXIT_USAGE
        return EXIT_USAGE
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
