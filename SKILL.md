---
name: portfolio-manager
description: "Agentic trading agent for OKX Agentic Wallet. Owns the strategy + decision-making (when to open via a Python decide() callback the agent authors), the rules + supervision (when to exit via drawdown halts / position caps / trailing stops), and the audit + reports (Sharpe, Sortino, max DD, win rate, equity chart via pm report). TWO independent kill-switches in live mode: --max-loss-usd (cumulative realized loss) AND --max-wallet-loss-usd (wallet equity drop from baseline; catches rugged-token unrealized losses). Use when the user says: build a trading strategy, decide when to buy, run my strategy live, run my strategy on history, watch my portfolio, manage positions, set up risk rules, drawdown halt, trailing stop, position cap, max loss kill switch, wallet equity kill switch, rug protection, audit my trades, pm report, sharpe, equity curve."
version: "0.2.1"
license: MIT
metadata:
  author: paulomcg
  homepage: "https://github.com/paulomcg/portfolio-manager"
---

# Portfolio Manager

A complete agentic trading agent: PM owns the **strategy** (a Python
`decide()` callback the user / agent authors — answers "when to open?"),
the **rules** (drawdown halts / position caps / trailing stops — answer
"when to exit?"), the **executor** (composes existing `okx-dex-swap` for
real swaps OR a built-in synthetic executor for paper / cap-demo runs),
and the **audit + reports** (every decision logged; `pm report` produces
Sharpe / Sortino / Calmar / max DD / win rate / equity chart from any
audit log).

The same `decide(state, market_data)` callback works in live mode
(`pm watch --strategy ...`) and is the artifact a future backtester
drives against historical data. **Without `--strategy`**, PM falls back to
the v0.1.0 reactive supervisor (user opens positions externally, PM
watches + enforces rules).

## Pre-flight (before the first `pm` command this session)

1. `command -v pm` → if missing, install with `npx skills add okx/plugin-store --skill portfolio-manager` or clone the repo and add its `bin/` to `$PATH`.
2. `pm --version` → confirm.
3. For monitor-mode demos with synthetic state, skip to **Modes**. For real-wallet use:
   - `onchainos --version` → confirm CLI is present (install via `okx-agentic-wallet` skill if not).
   - `onchainos wallet status` → must report `loggedIn: true`. If not, `onchainos wallet login <email>` first.
   - `OKX_API_KEY` / `OKX_SECRET_KEY` / `OKX_PASSPHRASE` are read from env by the underlying CLI. PM never reads, logs, or persists them.

## Authoring a strategy (v0.2.0)

A strategy is a small Python file with one callable:

```python
# my-strategy.py
def decide(state, market_data):
    """
    state: positions snapshot (same shape rules see). Includes
      'cycle_index', 'cash_usd', 'total_equity_usd', 'positions': [...].
    market_data: per-asset {'current': bar_dict, 'history': pd.DataFrame}
      keyed by symbol; populated from a WS feed at startup + per cycle.

    Returns: list of action dicts:
      {'action': 'buy',  'asset': 'WSOL', 'amount_usd': 100.0}
      {'action': 'buy',  'asset': 'WSOL', 'qty': 0.77}
      {'action': 'sell', 'asset': 'WSOL', 'qty': 0.5}
      {'action': 'sell', 'asset': 'WSOL', 'sell_all': True}
      {'action': 'hold'}                                            # optional no-op
    """
    if state.get('cycle_index') == 0:
        return [{'action': 'buy', 'asset': 'WSOL', 'amount_usd': 100.0}]
    return []
```

PM ships small importable helpers in `pm.helpers` (also accessible via the
local repo path):

```python
from pm.helpers import (
    every_n_bars, calendar_aligned, rolling_return,
    has_position, position_pct_of_equity, cash_pct_of_equity,
)
```

Three reference strategies live in `examples/strategies/`:
- `buy_and_hold.py`: open once on cycle 0, hold forever
- `weekly_dca.py`: buy a fixed USD amount every 7 cycles
- `momentum_threshold.py`: enter when 20-bar return clears +5%, exit on -5%

Strategy errors are caught at the cycle level: a malformed action becomes
a cycle warning, an exception in `decide()` becomes a cycle warning, the
loop continues. Bad signature (e.g. `decide(x, y, z, w)`) fails loud at
load time with a canonical `FAILED: strategy_invalid <reason>` line.

## Modes

### Reactive monitor mode (v0.1.0 — no strategy)

```
pm watch --config <rules.yaml> --wallet <address> [--interval 60] [--iterations N]
```

User opens positions externally; PM watches + enforces rules. **Never**
calls a swap endpoint without `--live`.

### Active monitor mode (v0.2.0 — strategy, no executor)

```
pm watch --config <rules.yaml> --wallet <address> \
         --strategy my-strategy.py [--bar 1D] [--lookback-bars 365]
```

Strategy fires each cycle with market data, but actions are recorded as
audit-only warnings (no execution). Useful for dry-running a new strategy
against a real wallet feed before going live.

### Live mode (explicit opt-in — required for any execution)

```
pm watch --config <rules.yaml> --wallet <address> \
         [--strategy my-strategy.py] \
         --live --max-loss-usd <usd> [--executor onchainos|synthetic]
```

Strategy AND rule actions execute via the chosen executor:
- `--executor onchainos` (default): real `onchainos swap execute` calls.
- `--executor synthetic`: built-in simulator with `slippage_bps + fee_bps`
  applied — used for tests, cap-enforcement demos, and offline runs.

The cap halts the loop the instant cumulative realized loss crosses
`--max-loss-usd`. A conservative pre-check projects worst-case loss per
swap and skips swaps that would breach the cap rather than execute them.

## Three consumption patterns — agent picks one

PM publishes; consumption is the agent's responsibility. Same data, three surfaces.

### Pattern 1: Live tail (best for in-session conversation)

```
pm watch --config rules.yaml --wallet <addr> --interval 60
```

Each cycle emits one JSON record to stdout. The agent reads them as they appear. Final line is a summary `{"ok": true, "result": {...}}`.

### Pattern 2: Audit poll (best for periodic check-ins)

```
pm audit show --since "2026-05-11T14:00:00Z" --limit 100
```

Returns audit rows newest-first. Each cycle records one `event: "watch.cycle"` row with `decisions`, `fills`, `errors`, and pre/post position state.

### Pattern 3: Alerts queue (best for episodic agents — the daemon pattern)

```
# PM runs as a long-lived background process (launchd, systemd, nohup, tmux):
pm watch --config rules.yaml --wallet <addr> --interval 60 &

# Agent comes back later:
pm alerts pending                              # unacked alerts, newest first
pm alerts pending --severity critical          # filter
pm alerts pending --wallet <addr> --limit 20
pm alerts ack <id1> <id2>                      # mark them read
pm alerts history --limit 50 --since <iso>     # acked + unacked
```

Alerts are stored in sqlite (`state/positions.sqlite`) and mirrored to `state/alerts.jsonl` for tail-based consumption.

## Rule YAML schema

```yaml
name: conservative-majors                  # required, identifies the config
wallet: agentic                            # optional default; --wallet at the CLI overrides
universe:                                  # optional filter; omit to manage every holding
  - { chain: solana, address: "So11...1112", symbol: WSOL }
  - { chain: solana, address: "JTO...",      symbol: JTO }
poll:
  interval_seconds: 60                     # default; --interval at the CLI overrides
rules:                                     # required, non-empty
  - id: halt-on-portfolio-dd
    type: halt_on_drawdown                 # portfolio-level drawdown
    threshold_pct: 15                      # fires when total_equity drops 15% from HWM
    action:
      type: liquidate_all
      reason: "portfolio drawdown limit"

  - id: per-position-cap
    type: max_position_pct                 # per-position concentration
    threshold_pct: 40                      # fires when any position > 40% of equity
    action:
      type: trim_to
      target_pct: 40                       # trim back down to this %

  - id: trailing-stop-all
    type: trailing_stop                    # per-position trailing stop
    pct: 12                                # fires when position drops 12% from its HWM
    applies_to: "*"                        # "*" for every position, or a symbol like "WSOL"
    action:
      type: full_exit
```

`pm rules validate --config <path>` schema-validates the file before you commit to running the loop. Bad input produces a specific `FAILED: rules_config_invalid <field>: <reason>` line.

## Manual position overrides (rare — most users skip this)

PM auto-derives positions from `onchainos portfolio` + `onchainos market portfolio-token-pnl`. Cost basis comes from the math `value_usd - unrealized_pnl_usd` so users don't have to record entries. For edge cases (positions older than OKX's PnL index window, off-chain inventory, tax-adjusted cost basis):

```
pm position add --wallet <addr> --asset WSOL --qty 100 --cost-usd 12000 --notes "pre-pm holding"
pm position update --wallet <addr> --asset WSOL --mark-price 145.50
pm position close --wallet <addr> --asset WSOL --qty 50 --price 148.00
pm position list --wallet <addr>            # all overrides + HWMs
pm position snapshot --wallet <addr> \
    --wallet-snapshot <json> [--pnl-snapshot <json>]   # advanced: derive without wallet API
```

Manual entries are flagged `source: manual` in the derived ledger so audit attribution is clear.

## Stateless decision-engine command — also consumable by the backtester / other agents

`pm rules evaluate` is a pure function: given a positions snapshot + rules YAML, return the decisions. No sqlite, no audit, no I/O beyond stdout.

```
pm rules evaluate \
    --config rules.yaml \
    --positions positions.json \                  # or '-' to read from stdin
    [--bar bar.json] \                            # optional OHLCV bar (advanced)
    [--proposed-order order.json]                 # optional mediated-open evaluation
```

The `--proposed-order` flag evaluates "if I were to apply this hypothetical order, would rules fire?" — useful for pre-trade gating from another agent.

Output:

```json
{
  "ok": true,
  "schema_version": "1.0.0",
  "evaluated_at_utc": "...",
  "input_hashes": {"rules_yaml": "...", "positions": "..."},
  "decisions": [
    {"action": "trim|exit|halt", "asset": "...", "qty": ..., "quote_usd_est": ...,
     "reason": "...", "rule_id": "...", "severity": "info|warn|critical"}
  ],
  "diagnostics": {"rules_evaluated": ..., "rules_fired": ..., "rules_skipped": [...], "warnings": [...]}
}
```

## Observability dashboard (`pm dashboard` — v0.2.0)

Start a local web UI that surfaces PM's state in real time:

```
pm dashboard [--host 127.0.0.1] [--port 7777]
```

Opens an HTTP server bound to localhost; navigate to
`http://127.0.0.1:7777` in any browser. The dashboard is **read-only by
design** — no buttons mutate state, no swaps fire from a browser tab. All
write paths stay in the CLI (`pm position add`, `pm alerts ack`, etc.).

What you see:
- Six metric cards: total equity, drawdown from HWM, return, Sharpe,
  cycle count, current mode (monitor / live / backtest).
- Equity curve (Chart.js) computed live from the audit log.
- Positions table (asset, qty, mark, value, cost basis, unrealized PnL,
  per-position drawdown).
- Pending alerts panel (rule id, severity, reason, age, alert id).
- Recent activity table (last 50 audit rows newest-first: timestamp,
  event, wallet, strategy/rule counts, fill count, equity at close).

Updates flow via Server-Sent Events: the server watches `audit.jsonl` +
`alerts.jsonl` mtimes and pushes `cycle` / `alert` events as soon as PM
writes a new row. The frontend re-fetches `/api/snapshot` on each event.
A 10s polling fallback covers the case where SSE drops.

Underlying JSON endpoints (also usable by other agents / tooling):

```
GET /api/state                          # most recent watch.cycle + sqlite overrides
GET /api/audit?limit=50&since=<iso>
GET /api/alerts/pending?severity=critical
GET /api/equity                         # equity series + drawdown_pct
GET /api/metrics                        # full pm report metrics
GET /api/snapshot                       # combined payload (state + audit + alerts + metrics)
GET /events                             # SSE stream
```

All endpoints share the v1.0.0 schema documented above. The dashboard is
just a UI layer over them — anything you can do in the browser is also
available as raw JSON for an agent that wants to consume PM observability
programmatically.

## Reports (`pm report` — v0.2.0)

Compute risk-adjusted metrics from any PM audit log. Works on live audits
that PM has been writing for weeks, on a single backtest run's audit, or
any subset filtered by wallet + time range.

```
pm report
    [--audit-path <jsonl>]            # default: state/audit.jsonl
    [--wallet <addr>] [--since <iso>] [--until <iso>]
    [--title "<chart title>"]
    --out <dir>                       # required
```

Reads `watch.cycle` rows, reconstructs the equity time series from
`positions.total_equity_usd` per cycle, computes:

- Total return / CAGR
- Sharpe / Sortino / Calmar (auto-annualized from inferred bar cadence)
- Max drawdown (with peak + trough timestamps)
- Win rate / expectancy / avg trade duration from `fills[].realized_pnl_usd`
- Per-asset realized PnL

Emits three files into `--out`:
- `report.json` — stable v1.0.0 schema, deterministic given the same audit
- `report.md` — human-readable Markdown summary with embedded chart
- `equity.png` — matplotlib equity curve with drawdown shading

### Audit row schema (v1.0.0 — stable contract)

The audit log PM has written since v0.1.0 is the integration surface for
`pm report` and any downstream consumer (including the future backtester).
Each watch-cycle row:

```json
{
  "ts_utc": "2026-05-11T14:30:22+00:00",
  "event": "watch.cycle",
  "cycle_id": "...",
  "cycle_index": 0,
  "mode": "monitor" | "live",
  "wallet": "<addr>",
  "positions": {
    "total_equity_usd": 1000.0,
    "high_water_mark_usd": 1000.0,
    "drawdown_from_hwm_pct": 0.0,
    "n_positions": 0,
    "warnings": []
  },
  "decisions": [ ... ],          // rule-driven decisions
  "strategy": {                  // v0.2.0 only when --strategy
    "actions": [ ... ],
    "warnings": [ ... ]
  },
  "fills": [
    {"ok": true, "action": "buy|sell|trim|exit|halt",
     "asset": "...", "qty_swapped": ..., "fill_price_usd": ...,
     "gross_proceeds_usd": ..., "fees_usd": ..., "slippage_usd": ...,
     "realized_pnl_usd": ..., "tx_hash": ..., "executor": "...",
     "source": "strategy" | "rule"}                  // v0.2.0 attribution
  ],
  "alerts_emitted": [...],
  "errors": [...],
  "diagnostics": {...}
}
```

## Failure vocabulary (canonical FAILED lines)

PM prints `FAILED: <token> <detail>` to stderr and exits 1. The token is stable across versions; the detail is human-readable and free-form.

| Situation | Canonical token |
|---|---|
| Bad rules YAML (parse / schema / missing required field) | `rules_config_invalid` |
| Bad positions JSON input | `positions_input_invalid` |
| Bad `--bar` JSON | `bar_input_invalid` |
| Wallet not logged in (no API keys / wallet status reports loggedIn:false) | `wallet_not_logged_in` |
| Per-token PnL fetch failed | `pnl_fetch_failed` |
| Live mode missing the kill-switch flag | `live_mode_missing_flag` |
| Live mode hard cap exceeded | `live_mode_capital_cap_exceeded` |
| Swap execution failed | `execution_failed` |
| Rule action type not supported | `rule_action_unsupported` |
| Alert id not found | `alert_not_found` |
| Wallet address required for the current command | `wallet_required` |
| Catch-all for unexpected exceptions | `internal_error` |
| Strategy file invalid (missing decide, bad signature, import error) | `strategy_invalid` |
| Report invocation invalid (missing --out, etc.) | `report_invalid` |
| Report computation crashed | `report_failed` |
| Dashboard port already in use | `dashboard_port_in_use` |

`OK:` is the success counterpart; format depends on the command (see each section above).

## Security & UX rules

- `OKX_API_KEY` / `OKX_SECRET_KEY` / `OKX_PASSPHRASE` are read by the underlying `onchainos` CLI from environment variables. **PM does not read, log, persist, or echo them.** Grep the source: there is no reference to those names outside this paragraph.
- Live mode requires **both** `--live` and `--max-loss-usd` flags **every invocation**. There is no persisted "live mode on" state. Closing your terminal turns it off.
- The cap is enforced pre-swap (conservative projection) AND post-swap (cumulative realized loss). Both checks must pass; either breach halts the loop.
- "halt" rule actions translate to per-position exits in alphabetical order, with the cap re-checked between each. A halt mid-sequence stops the rest.
- All durable state lives under `state/` in the repo (configurable via `PM_STATE_DIR`). Audit log is append-only JSONL; alerts queue is sqlite. No data leaves the host.

## Examples + tests

- `examples/conservative-majors.yaml` — 15% halt, 40% position cap, 12% trailing on all
- `examples/meme-trailing.yaml` — 25% halt, 20% cap, 18% trailing
- `examples/synthetic-wallet.json` — a sample wallet snapshot for no-keys demos
- `tests/` — 111 tests covering rule engine, schema, position derivation, alerts queue, audit log, watch loop (monitor + live), swap executors, CLI integration

Run them with `.venv/bin/pytest tests/` after `python3 -m venv .venv && .venv/bin/pip install jsonschema pyyaml pytest`.
