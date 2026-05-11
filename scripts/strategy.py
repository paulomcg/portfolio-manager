"""Strategy loader + invocation wrapper for PM v0.2.0.

A strategy is a Python file with a top-level callable named ``decide``:

    def decide(state: dict, market_data: dict) -> list[dict]:
        ...

The loader:
  1. Imports the file via importlib.util.spec_from_file_location
  2. Asserts a ``decide`` callable exists with a 2-arg signature
  3. Returns an InvocationWrapper that catches per-call exceptions
     and surfaces them as structured cycle errors (so a bad strategy
     never crashes the watch loop).

The wrapper also validates the returned action list shape before passing
it back to the cycle.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

# Action dict keys we know about. Extra keys are tolerated (forward-compat).
ALLOWED_ACTIONS = frozenset({"buy", "sell", "hold"})


class StrategyError(Exception):
    """Canonical token follows the colon, e.g. 'strategy_invalid'."""


class StrategyInvocation:
    """Wraps a loaded ``decide`` function with error capture + validation."""

    def __init__(self, path: Path, decide_fn: Callable[..., Any], module_name: str):
        self.path = path
        self._decide = decide_fn
        self.module_name = module_name

    def invoke(
        self, state: dict[str, Any], market_data: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Call ``decide(state, market_data)`` and validate the result.

        Returns (actions, warnings). On caught exception, returns
        ([], [{"kind": "strategy_exception", "detail": "..."}]).
        Malformed action dicts are filtered out and reported as warnings.
        """
        try:
            raw = self._decide(state, market_data)
        except Exception as e:  # noqa: BLE001
            return [], [
                {
                    "kind": "strategy_exception",
                    "detail": f"{type(e).__name__}: {e}",
                    "traceback": traceback.format_exc(limit=4),
                }
            ]

        if raw is None:
            return [], []
        if not isinstance(raw, list):
            return [], [
                {
                    "kind": "strategy_bad_return",
                    "detail": f"expected list, got {type(raw).__name__}",
                }
            ]

        actions: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        for i, item in enumerate(raw):
            err = _validate_action(item, i)
            if err is None:
                actions.append(item)
            else:
                warnings.append(err)
        return actions, warnings


def _validate_action(item: Any, index: int) -> dict[str, Any] | None:
    """Return None if the action looks well-shaped, or a warning dict."""
    if not isinstance(item, dict):
        return {
            "kind": "strategy_bad_action",
            "index": index,
            "detail": f"action {index} is not a dict ({type(item).__name__})",
        }
    action = item.get("action")
    if action not in ALLOWED_ACTIONS:
        return {
            "kind": "strategy_bad_action",
            "index": index,
            "detail": f"action {index} has unknown action={action!r}",
        }
    if action == "hold":
        return None
    if "asset" not in item or not isinstance(item["asset"], str):
        return {
            "kind": "strategy_bad_action",
            "index": index,
            "detail": f"action {index} missing string 'asset'",
        }
    if action == "buy":
        has_qty = "qty" in item
        has_amt = "amount_usd" in item
        if not (has_qty ^ has_amt):
            return {
                "kind": "strategy_bad_action",
                "index": index,
                "detail": f"buy action {index} needs exactly one of 'qty' or 'amount_usd'",
            }
    elif action == "sell":
        has_qty = "qty" in item
        has_all = bool(item.get("sell_all"))
        if not (has_qty or has_all):
            return {
                "kind": "strategy_bad_action",
                "index": index,
                "detail": f"sell action {index} needs 'qty' or 'sell_all'",
            }
    return None


def load(path: str | Path) -> StrategyInvocation:
    """Load a strategy file. Raises StrategyError on any structural problem.

    Caller (pm.py / watch.py) catches StrategyError and maps to canonical
    'FAILED: strategy_invalid <reason>' line.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise StrategyError(f"strategy_invalid file not found: {p}")
    if p.suffix != ".py":
        raise StrategyError(f"strategy_invalid must be a .py file: {p}")

    module_name = f"_pm_strategy_{abs(hash(str(p)))}"
    spec = importlib.util.spec_from_file_location(module_name, p)
    if spec is None or spec.loader is None:
        raise StrategyError(f"strategy_invalid could not load spec from {p}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module  # so relative imports inside the file work
    try:
        spec.loader.exec_module(module)
    except Exception as e:  # noqa: BLE001
        raise StrategyError(
            f"strategy_invalid import error: {type(e).__name__}: {e}"
        ) from e

    decide = getattr(module, "decide", None)
    if decide is None or not callable(decide):
        raise StrategyError(
            f"strategy_invalid no callable 'decide' found in {p}"
        )

    try:
        sig = inspect.signature(decide)
    except (TypeError, ValueError) as e:
        raise StrategyError(f"strategy_invalid cannot introspect decide: {e}")

    # We accept any 2-arg signature; positional or keyword. Extra-args via
    # *args/**kwargs are also fine. The contract is "callable(state, market_data)".
    params = list(sig.parameters.values())
    required = [p for p in params if p.default is inspect.Parameter.empty
                and p.kind not in (
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                )]
    if len(required) > 2:
        raise StrategyError(
            f"strategy_invalid decide() must accept (state, market_data); "
            f"got {len(required)} required params"
        )

    return StrategyInvocation(path=p, decide_fn=decide, module_name=module_name)
