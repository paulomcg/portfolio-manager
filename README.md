# portfolio-manager

> **OKX Agentic Trading Contest, Skill Quality Award submission** — see
> [`SUBMISSION.md`](./SUBMISSION.md) for the explicit mapping of features
> to the five evaluation criteria (strategy completeness, risk control,
> execution reliability, user safety/onboarding, observability).

**A complete agentic trading agent for the OKX Agentic Wallet.** Owns the
**strategy** (a Python `decide()` callback the agent authors — answers "when
to open"), the **rules** (drawdown halts / position caps / trailing stops —
answer "when to exit"), the **executor** (real swaps via `okx-dex-swap` OR a
built-in synthetic engine), the **alerts queue**, the **append-only audit
log**, and **`pm report`** (Sharpe / Sortino / max DD / win rate / equity
chart from any audit).

The same `.py` strategy file works in live mode today and against historical
data when the upcoming backtester ships. One artifact, two execution
contexts. Without `--strategy`, PM falls back to v0.1.0's reactive
supervisor behavior — opens stay external.

> Status: v0.2.0 — submitted to the OKX Agentic Trading Contest (May 2026).
> MIT licensed. Not investment advice. Trade at your own risk. Test in
> monitor mode before going live.

---

## What's new in v0.2.0

- **Strategy hook** — `pm watch --strategy <py>` loads an agent-authored
  `decide(state, market_data) -> list[Action]` callback. Strategy actions
  execute through the existing swap-executor abstraction (real or synthetic).
- **Market data via `okx-dex-ws`** — bootstrap kline at startup, real-time
  bar events via WebSocket per cycle, strategy receives `{current, history}`
  per universe asset.
- **`pm report`** — read any audit log and compute Sharpe / Sortino / Calmar
  / max DD / win rate / expectancy / CAGR; emit `report.json` + `report.md`
  + `equity.png` with drawdown shading.
- **`pm dashboard`** — local read-only web UI for live observability.
  Single-page Chart.js + vanilla JS; SSE push-updates as PM writes audit
  rows. 6 JSON endpoints (`/api/snapshot`, `/api/equity`, `/api/metrics`,
  `/api/audit`, `/api/alerts/pending`, `/api/state`) for programmatic
  consumers. Stdlib-only — no extra deps.
- **Buy action in the executor** — opens go through the same Fill schema as
  exits, with `source: "strategy" | "rule"` attribution in the audit.
- **Backwards-compatible**: omit `--strategy` and v0.1.0's 111 tests + every
  existing flag behave byte-identical.

## What's in the box

- **Stateless rule engine** — pure function `(positions, rules) → decisions`.
  Same code path drives monitor mode, live mode, and any downstream consumer
  (e.g. the planned strategy-backtester skill calls `pm rules evaluate` via
  subprocess).
- **Three risk rule types**: `halt_on_drawdown`, `max_position_pct`,
  `trailing_stop`. Schema-validated YAML configs.
- **Auto-derived position ledger** — combines `okx-wallet-portfolio` balances
  with `okx-dex-market portfolio-token-pnl` to derive cost basis automatically.
  Tracks high-water marks across cycles. Manual overrides supported for edge
  cases.
- **Watch loop** with monitor (default) and live modes; both bounded by
  `--iterations` for testing.
- **Live-mode hard kill switch**: `--max-loss-usd` is required, enforced both
  pre-swap (projected loss) and post-swap (cumulative realized loss).
- **Three consumption patterns**: live stdout tail, audit log poll
  (`audit.jsonl`), and an ack-able alerts queue
  (`pm alerts pending` / `pm alerts ack`).
- **239 tests** (v0.1.0 + v0.2.0), all green, exercising the rule engine,
  schema validation, position derivation, HWM persistence, alerts queue,
  audit log, watch loops (monitor + live), swap executors (sells + buys),
  CLI integration, strategy loader, helpers, market data (WS subscribe +
  poll + reconnect), `pm report` (metrics + chart + Markdown),
  `pm dashboard` (API endpoints + server integration tests on a real
  ThreadingHTTPServer instance), and the strategy-in-cycle integration.

---

## Why this skill

The OKX Onchain OS suite already ships wallet, market data, swap, security,
and DeFi skills. What it does **not** ship is a *state machine over time* — a
ledger that tracks position history, a rule engine that fires on cross-cycle
state (high-water marks, drawdowns), an alerts queue an episodic agent can
poll, and a cumulative-loss kill switch. PM fills that gap and lets the
underlying primitives do what they're already good at.

For agents: PM is the discipline layer. You decide what to buy. PM decides
when to exit.

---

## Install

### From the OKX Plugin Store (recommended once published)

```sh
npx skills add okx/plugin-store --skill portfolio-manager
```

### From source

```sh
git clone https://github.com/paulomcg/portfolio-manager.git ~/Projects/portfolio-manager
cd ~/Projects/portfolio-manager
python3 -m venv .venv
.venv/bin/pip install jsonschema pyyaml pytest
echo 'export PATH="$HOME/Projects/portfolio-manager/bin:$PATH"' >> ~/.bashrc  # or .zshrc
pm --version  # → "pm 0.1.0"
```

For real-wallet runs, also:

```sh
onchainos wallet login <your-email>
onchainos wallet status                # expect loggedIn: true
export OKX_API_KEY=... OKX_SECRET_KEY=... OKX_PASSPHRASE=...
```

PM itself never reads those env vars — the underlying `onchainos` CLI does.

---

## Observability dashboard (v0.2.0)

A read-only local web UI for live observability:

```
pm dashboard --host 127.0.0.1 --port 7777
# open http://127.0.0.1:7777
```

```
+----------------------------------------------------------------+
| ● portfolio-manager  v0.2.0      wallet: bt-1 · 14:32:08 UTC   |
+----------------------------------------------------------------+
| Equity   | DD       | Return  | Sharpe | Cycles | Mode         |
| $1,176   | 2.13%    | +17.96% | 8.89   | 20     | backtest     |
+----------------------------------------------------------------+
|  ── equity curve ─────────────────────────────────────────────  |
|                                              ╱╲                |
|                                         ╱╲╱╲╱  ╲╲              |
|                                    ╱╲╱╲╱        ╲╱             |
|                              ╱╲╱╲╱                             |
|                         ╱╲╱╲╱                                  |
|                    ╱╲╱╲╱                                       |
+----------------------------------------------------------------+
| Positions                       | Pending alerts                |
| ────────────────────────────    | ────────────────────────────  |
| WSOL: 9.3 @ $133 ($1238)        | per-position-cap [warn]       |
| USDC: $0                        | trim WSOL — exceeds 60% cap   |
|                                 | trailing-stop [warn] ...      |
+----------------------------------------------------------------+
| Recent activity (last 50)                                      |
| 2025-01-20 04:00:00  watch.cycle  bt-1  0s / 1r  1  $1176     |
| 2025-01-19 04:00:00  watch.cycle  bt-1  0s / 0r  0  $1098     |
| 2025-01-18 04:00:00  watch.cycle  bt-1  0s / 1r  1  $1108     |
| ...                                                            |
+----------------------------------------------------------------+
```

Updates push via Server-Sent Events as PM writes new audit rows or
alerts. The whole UI is served by stdlib `http.server` — no extra
dependencies, no build step, ~250 lines of vanilla JS. JSON endpoints
underneath (`/api/snapshot`, `/api/equity`, `/api/metrics`, etc.) are
documented in SKILL.md and consumable by any agent that wants programmatic
access to PM's state.

**Read-only by design.** No buttons fire swaps; the dashboard never
mutates state. All write paths stay in the CLI.

## Authoring a strategy (v0.2.0)

A strategy is a small Python file with one callable. The agent (Claude
Code, Hermes, etc.) drafts it from the user's intent:

```python
# weekly_dca.py
from pm.helpers import every_n_bars

def decide(state, market_data):
    if every_n_bars(state['cycle_index'], 7) and state['cash_usd'] > 100:
        return [{'action': 'buy', 'asset': 'WSOL', 'amount_usd': 100.0}]
    return []
```

Then point PM at it:

```
pm watch --config conservative-majors.yaml \
         --strategy weekly_dca.py \
         --wallet <addr> --bar 1D --lookback-bars 365
```

The same `.py` file is the artifact the upcoming backtester will drive
against historical OHLCV — one file, two contexts.

Three example strategies ship in `examples/strategies/`:
- `buy_and_hold.py` — open once, hold forever (5 lines of `decide()`)
- `weekly_dca.py` — buy a fixed USD amount every 7 cycles
- `momentum_threshold.py` — enter when 20-bar return > 5%, exit on -5%

## `pm report` quick-start

```
pm report --audit-path state/audit.jsonl --out ./report-may/
```

Reads your live audit log, computes Sharpe / Sortino / max DD / win rate /
expectancy / CAGR, writes `report.{json,md}` + `equity.png` into the out
dir. Works on any audit log conforming to the v1.0.0 schema documented in
SKILL.md.

## 60-second demo (no wallet, no capital)

The repo ships a synthetic wallet snapshot you can run against immediately:

```sh
pm watch --config examples/conservative-majors.yaml \
         --positions-source examples/synthetic-wallet.json \
         --interval 0 --iterations 3
```

Output — one JSON record per cycle, plus a summary line at the end:

```json
{"cycle_index": 0, "mode": "monitor", "wallet": "synthetic-state",
 "decisions": [{"action": "trim", "asset": "WSOL", "qty": 13.385,
                "quote_usd_est": 1740.0,
                "reason": "WSOL is 72.22% of portfolio, exceeds 40% cap (trim to 40%)",
                "rule_id": "per-position-cap", "severity": "warn"}],
 "alerts_emitted": [{"alert_id": "...", "rule_id": "per-position-cap"}],
 "fills": [], "errors": [],
 "positions": {"total_equity_usd": 5400.0, "n_positions": 2, ...},
 "diagnostics": {"rules_evaluated": 3, "rules_fired": 1, ...}}
...
{"ok": true, "result": {"mode": "monitor", "wallet": "synthetic-state",
                         "iterations": 3, "alerts_emitted": 3, "fills": 0,
                         "realized_loss_usd": 0.0, "halted": false, ...}}
```

Check the alerts queue:

```sh
pm alerts pending --limit 3
```

```json
{"ok": true, "result": {"count": 3, "alerts": [
  {"alert_id": "...", "rule_id": "per-position-cap", "severity": "warn",
   "decision": {"action": "trim", "asset": "WSOL", "qty": 13.385, ...}, ...},
  ...
]}}
```

Ack them when you've processed them:

```sh
pm alerts ack <id1> <id2> <id3>
```

---

## Live mode (small capital recommended)

After `onchainos wallet login` and a small test position (e.g. ~$20 of WSOL):

```sh
pm watch --config examples/conservative-majors.yaml \
         --wallet <your-solana-address> \
         --live --max-loss-usd 5 --interval 60
```

The loop polls `okx-wallet-portfolio` + `okx-dex-market portfolio-token-pnl`,
derives positions, evaluates rules, and when a rule fires, calls
`onchainos swap execute --from <token> --to USDC ...` to liquidate the
exact qty the rule recommends. Realized loss accumulates across cycles; the
loop halts the moment it crosses `--max-loss-usd`.

Closing the terminal terminates the loop. There is no persisted "live mode on"
state.

---

## Live-mode cap enforcement demo (synthetic executor)

Run live-mode against the synthetic state with the synthetic swap engine to
see cap enforcement without spending real money:

```sh
pm watch --config examples/conservative-majors.yaml \
         --positions-source examples/synthetic-wallet.json \
         --pnl-source tests/fixtures/pnl_snapshot.json \
         --interval 0 --iterations 5 \
         --live --max-loss-usd 0.50 --executor synthetic
```

(The synthetic positions are net-profitable in the sample fixtures, so the
loss cap rarely fires on the bundled demo. The pytest suite includes a
`HugeFeeExecutor` that *forces* the cap to fire — see
`tests/test_watch_live.py::test_cap_halt_triggers_when_realized_loss_exceeds`.)

---

## Three agent consumption patterns

PM publishes; the agent picks the pattern.

### Pattern 1: Live tail

The agent runs `pm watch` in its own context and reads JSON records from stdout
as they arrive. Best for in-session conversations like *"watch my portfolio for
the next hour."*

### Pattern 2: Audit poll

PM appends every cycle to `state/audit.jsonl`. The agent reads it on demand:

```sh
pm audit show --since "2026-05-11T14:00:00Z" --limit 100
```

Best for periodic check-ins like *"what happened to my portfolio overnight?"*

### Pattern 3: Alerts queue (the daemon pattern)

PM runs in the background; the agent comes back when it has spare cycles and
pulls unacked alerts:

```sh
pm watch --config rules.yaml --wallet <addr> --interval 60 &
# (later, in another session)
pm alerts pending --severity critical
pm alerts ack <ids>
```

Best for episodic agents — the common case. The queue is sqlite-backed
(`state/positions.sqlite`) and mirrored to `state/alerts.jsonl`.

---

## Rule YAML reference

See [`examples/conservative-majors.yaml`](examples/conservative-majors.yaml)
and [`examples/meme-trailing.yaml`](examples/meme-trailing.yaml) for working
configs. The full schema is in [`scripts/schema.py`](scripts/schema.py) and is
enforced by `pm rules validate`. Three rule types in v1:

| Type | Triggers when | Common action |
|---|---|---|
| `halt_on_drawdown` | total_equity drops `threshold_pct` from portfolio HWM | `liquidate_all` |
| `max_position_pct` | any position > `threshold_pct` of equity | `trim_to` `target_pct` |
| `trailing_stop` | position drops `pct` from its own HWM (per-asset or `applies_to: "*"`) | `full_exit` |

---

## Security

- API keys are read from env by `onchainos`, not by PM. Grep this codebase:
  `OKX_API_KEY` appears in no source file outside docs.
- Live mode requires both `--live` and `--max-loss-usd` every invocation.
  No persistence of "live mode on."
- All state under `state/` is local. Nothing leaves the host except the swap
  calls you've explicitly opted into via `--live`.
- The kill switch is enforced **twice**: a conservative pre-check projects
  the worst-case loss of each swap and skips it if executing would breach the
  cap; a post-swap accumulator halts the loop on any actual breach.

---

## Architecture

```
                       ┌──────────────────────────────────────┐
                       │           pm watch loop              │
                       │  (monitor or live; bounded by iters) │
                       └──────────────────────────────────────┘
                              │
       ┌──────────────────────┼──────────────────────────┐
       ▼                      ▼                          ▼
  WalletSource          PositionDeriver              RuleEngine
  (okx-wallet-          (positions.py)               (rule_engine.py
   portfolio +          ← HWM cache (sqlite)            + risk_rules.py)
   market-pnl)          ← manual overrides
                                                         │
                                                         ▼
                                          ┌── stdout tail (live)
                                          │
                                          │── audit.jsonl (append-only)
                                          │
                                          │── alerts queue (sqlite + jsonl mirror)
                                          │
                       (live mode only) ──▼── SwapExecutor
                                                │
                                                ▼
                                    onchainos swap execute
```

Files:

- `scripts/pm.py` — CLI dispatcher
- `scripts/rule_engine.py` — pure-function evaluate
- `scripts/risk_rules.py` — three rule type implementations
- `scripts/positions.py` — derivation, HWM tracking, manual overrides
- `scripts/wallet_source.py` — `SyntheticWalletSource`, `OnchainosWalletSource`
- `scripts/executor.py` — `SyntheticSwapExecutor`, `OnchainosSwapExecutor`
- `scripts/watch.py` — monitor + live loop
- `scripts/alerts.py` — ack-able queue
- `scripts/audit.py` — append-only JSONL
- `scripts/schema.py` — JSON Schema for rule YAML
- `scripts/config.py` — paths overridable via `PM_STATE_DIR` and friends

---

## Tests

```sh
.venv/bin/pytest tests/ -v
```

111 tests covering rule engine, schema, position derivation, alerts queue,
audit log, watch (monitor + live), swap executors, and full CLI integration.

State is isolated per-test via `PM_STATE_DIR` env override (see
`tests/test_positions.py`'s `isolated_state` fixture).

---

## License

MIT — see [`LICENSE`](LICENSE).

---

## Disclaimer

Cryptocurrency trading is high-risk. This skill manages risk *rules*; it does
not pick what to buy and does not guarantee gains. Test thoroughly in monitor
mode before enabling live mode. Use `--max-loss-usd` set to an amount you can
afford to lose. The author and contributors disclaim all liability for losses
arising from use of this software.
