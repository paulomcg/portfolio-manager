# Submission — OKX Agentic Trading Contest, Skill Quality Award Track

**Skill name:** `portfolio-manager`
**Submitted by:** Paulo Goncalves
**OnChainOS as primary data + trading source:** ✅ (`onchainos wallet`, `onchainos market`, `onchainos swap` — every external call goes through it)
**Status:** v0.2.0, battle-tested live on 2026-05-20 with real capital

This document maps the skill's features directly to the five evaluation
criteria published with the contest.

---

## 1. Strategy completeness

A portfolio manager that owns the full lifecycle — entries, exits,
position-sizing, drawdown halts, and observability — not just one slice.

| What | Where |
|---|---|
| Pluggable Python strategy callback `decide(state, market_data) → list[Action]` | `scripts/strategy.py` |
| Stateless rule engine: `halt_on_drawdown`, `max_position_pct`, `trailing_stop`, plus user-defined extensions | `scripts/rule_engine.py`, `scripts/risk_rules.py` |
| Position ledger derived from wallet truth, with high-water-mark tracking and manual overrides | `scripts/positions.py` |
| Market-data adapter (OnChainOS kline + WebSocket bars) | `scripts/market_data.py` |
| Example strategies covering momentum, multi-source confirmation, and risk-managed memecoin rotation | `examples/strategies/` |
| Same `decide()` runs in monitor, live, AND backtester contexts | demonstrated via the companion `strategy-backtester` skill |

**Evidence:** the `smart_money_yolo` example in `examples/strategies/` ran
live on 2026-05-20, fused three OnChainOS signal sources (smart-money
tracker, KOL tracker, memepump migrations), and successfully gated entries
behind a multi-source confirmation rule. See `examples/strategies/smart_money_yolo.py`.

---

## 2. Risk control framework

Two independent kill-switches plus a composable rules engine. The wallet-
equity kill-switch was added after a live rug event proved the realized-
loss-only kill was insufficient — a real failure mode discovered and fixed
during the contest window.

| Control | Where | Why it matters |
|---|---|---|
| `--max-loss-usd` realized-loss kill-switch — required when `--live` is set | `scripts/watch.py:55`, `scripts/pm.py:770` | Hard cap on settled losses |
| `--max-wallet-loss-usd` wallet-equity kill-switch — independent of P&L accounting | `scripts/watch.py:55-72` | Catches rugged positions that never book a realized loss (no exit liquidity → no fill → existing kill never trips). Surfaced and fixed during 2026-05-20 live run. |
| `halt_on_drawdown` rule — portfolio-wide drawdown halt | `scripts/risk_rules.py` | Cross-cycle drawdown HWM tracking |
| `max_position_pct` rule — per-position size cap with trim-to action | `scripts/risk_rules.py` | Prevents one position from eating the wallet |
| `trailing_stop` rule — per-position trailing exit from HWM | `scripts/risk_rules.py` | Lock in gains, cut losers |
| Schema-validated YAML rule configs — bad configs fail at load, not runtime | `scripts/schema.py` | No silent config drift |
| Pre-swap projected-loss check + post-swap realized-loss check | `scripts/watch.py`, `scripts/executor.py` | Two-phase gate |

**Evidence — kill-switch trip in cycle records:**

```json
"kill_switch": {
  "wallet_baseline_equity_usd": 68.35,
  "wallet_current_equity_usd": 68.34,
  "wallet_loss_from_baseline_usd": 0.01,
  "max_wallet_loss_usd": 10.0
}
```

Halt fires automatically when `wallet_loss_from_baseline_usd > max_wallet_loss_usd`.

---

## 3. Execution reliability

Real OnChainOS swap execution with response-shape adaptation, error
categorization, and a synthetic-executor fallback for tests / dry runs.

| What | Where |
|---|---|
| OnChainOS swap adapter handling current response shape (`swapTxHash`, `fromAmount`/`toAmount` minimal units, `tokenUnitPrice`, `priceImpact`) + legacy shape (`txHash`, `destAmount`, `executionPriceUsd`) | `scripts/executor.py:419-505` (`_parse_swap_response`) |
| Address case-preservation through to executor — Solana base58 is case-sensitive | `examples/strategies/smart_money_yolo.py` (commit `fcc8b65`) |
| Re-buy cooldown after every sell to prevent dump-exit / re-entry whipsaws | `examples/strategies/smart_money_yolo.py` (commit `e566b8d`) |
| Error categorization: `wallet_not_logged_in`, `cli_not_found`, `cli_timeout`, `execution_failed`, `cli_output_invalid` | `scripts/executor.py:350-365` |
| Synthetic executor (`--executor synthetic`) for cap-enforcement demos without burning capital | `scripts/executor.py` |
| Position ledger refresh AFTER strategy fills so the same cycle's rule pass sees the correct state | `scripts/watch.py:234-238` |

**Evidence — live run 2026-05-20:** 30+ real swaps executed against
OnChainOS Solana, successfully handled token-not-supported errors,
insufficient-liquidity errors (rug detection), and slippage events. Three
real bugs were discovered AND fixed in-flight during the contest window:

1. `wallet_source.py --chains` flag migration (commit `8f06e24`)
2. Swap response shape adapter for new `tokenUnitPrice` field (commit `f05c427`)
3. Solana address case preservation through to executor (commit `fcc8b65`)

These are intentionally kept in `git log` as evidence the skill survives
real-world API drift, not lab conditions.

---

## 4. User safety + onboarding experience

Three lines from clone to monitor-mode dry run; explicit secrets contract;
local web dashboard for at-a-glance observability.

| What | Where |
|---|---|
| Quickstart in README — `pip install` → `pm watch --config example.yaml --wallet $ADDR` (monitor mode, zero-risk) | `README.md:80-120` |
| Live mode REQUIRES `--max-loss-usd` — `live_mode_missing_flag` error if omitted | `scripts/pm.py:464-465` |
| `OKX_API_KEY` / `OKX_SECRET_KEY` / `OKX_PASSPHRASE` read by underlying `onchainos` CLI only; PM never reads, logs, or persists secrets — grep verified | `SKILL.md:35`, `README.md:353` |
| `pm dashboard` — read-only web UI on `localhost:7777`, stdlib-only HTTP server, no extra deps | `scripts/pm.py` (`dashboard` subcommand) |
| `plugin.yaml` manifest for skill registry | `plugin.yaml` |
| SKILL.md with command-by-command reference, audit-log format, output schemas | `SKILL.md` |
| Example rules YAMLs covering conservative-monitor, aggressive-momentum, and risk-managed memecoin profiles | `examples/rules/` |
| `pm rules validate` — preview rule firings against a snapshot before going live | `scripts/pm.py` |

---

## 5. Observability

Three independent observability surfaces: live JSONL stream, append-only
audit log, and a web dashboard. All three see the same source-of-truth
state.

| What | Where |
|---|---|
| Per-cycle JSON record on stdout — positions, decisions, fills, errors, kill-switch state, diagnostics | `scripts/watch.py:97-141` |
| Append-only audit log (`audit.jsonl`) with `watch.cycle` events | `scripts/audit.py` |
| Ack-able alerts queue (`pm alerts pending` / `pm alerts ack`) | `scripts/alerts.py` |
| Web dashboard with live equity chart, position panel, kill-switch headroom, active rules, recent alerts — Chart.js + SSE push | `scripts/pm.py dashboard`, `dashboard-ui/` |
| `pm report` — Sharpe / Sortino / Calmar / max DD / win rate / expectancy / CAGR / equity chart from any audit log | `scripts/report.py` |
| Schema definitions for every event type | `scripts/schema.py` |

**Evidence — dashboard:** runs on `http://localhost:7777`, served by stdlib
`ThreadingHTTPServer`. Six JSON endpoints (`/api/snapshot`, `/api/equity`,
`/api/metrics`, `/api/audit`, `/api/alerts/pending`, `/api/state`) for
programmatic consumers. Tested against a real server instance.

---

## Bundled companion skill

This submission is paired with `strategy-backtester` — the same `.py`
strategy file works in live PM and against historical OHLCV via that
skill. One artifact, two execution contexts. See that repo's
`SUBMISSION.md` for its own axis mapping.

## Test coverage

239 tests across rule engine, schema validation, position derivation,
HWM persistence, alerts queue, audit log, watch loops (monitor + live),
swap executors, CLI integration, strategy loader, market data, `pm
report`, `pm dashboard`, and the strategy-in-cycle integration. Run
`pytest -q` after install.

## License

MIT.
