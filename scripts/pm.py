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
import sys
from pathlib import Path
from typing import Any, Callable

# Allow `python scripts/pm.py …` (no package context) by injecting repo root.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts import rule_engine, schema  # noqa: E402

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


# Stub handlers for future-milestone commands so `pm position`, `pm alerts`, etc.
# fail loudly rather than silently doing nothing.


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
    p.add_argument("--version", action="version", version="pm 0.1.0")

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

    # position / alerts / audit / watch — stubs in M2 -------------------
    for stub_name in ("position", "alerts", "audit", "watch"):
        sp = sub.add_parser(stub_name, help=f"{stub_name} commands (not yet implemented)")
        sp.add_argument("rest", nargs=argparse.REMAINDER)
        sp.set_defaults(_handler=cmd_stub, _stub_name=stub_name)

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
