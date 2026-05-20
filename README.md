# portfolio-manager

**A complete agentic trading agent for the OKX Agentic Wallet.** Owns
the **strategy** (a Python `decide()` callback the agent authors —
"when to open"), the **rules** (drawdown halts, position caps,
trailing stops — "when to exit"), the **executor** (real swaps via
OnChainOS or a built-in synthetic engine for paper-trading), the
**alerts queue**, the **append-only audit log**, the **`pm report`**
metrics generator (Sharpe / Sortino / max DD / win rate / equity
chart from any audit), and a **read-only local web dashboard** for
live observability.

The same `.py` strategy file works in live mode AND against historical
OHLCV via the companion [`strategy-backtester`](https://github.com/paulomcg/strategy-backtester)
skill. **One artifact, two execution contexts.**

For agents: PM is the discipline layer. You decide what to buy. PM
decides when to exit, halts before the loss exceeds your cap, and
records every decision.

---

## What you can do with it

### Run a "weekly DCA into SOL" strategy live

Author a 7-line Python strategy:

```python
# weekly_dca_wsol.py
from pm.helpers import every_n_bars

def decide(state, market_data):
    if every_n_bars(state['cycle_index'], 7) and state['cash_usd'] > 100:
        return [{'action': 'buy', 'asset': 'WSOL', 'amount_usd': 100.0}]
    return []
```

Point PM at it:

```sh
pm watch --config examples/conservative-majors.yaml \
         --strategy weekly_dca_wsol.py \
         --wallet <your-solana-address> \
         --live --max-loss-usd 50 --max-wallet-loss-usd 100 \
         --interval 86400
```

Every 7 days PM buys $100 of WSOL via OnChainOS swap. Every cycle PM
checks the rules (drawdown halt at 30%, per-position cap at 60%,
trailing stop at 25%) — when any trips, PM auto-exits. If cumulative
realized loss crosses `--max-loss-usd` OR wallet equity drops more
than `--max-wallet-loss-usd` from baseline, the loop halts itself.

### Monitor an existing wallet without trading

Run PM in monitor mode against any wallet — no swaps fire, no keys
needed, you just get structured alerts when rules would have triggered:

```sh
pm watch --config examples/conservative-majors.yaml \
         --wallet <wallet-to-watch> \
         --interval 60
```

One JSON record per cycle on stdout:

```json
{"cycle_index": 0, "mode": "monitor", "wallet": "8Pv2y2...nekP",
 "decisions": [{"action": "trim", "asset": "WSOL", "qty": 13.385,
                "quote_usd_est": 1740.0,
                "reason": "WSOL is 72.22% of portfolio, exceeds 40% cap (trim to 40%)",
                "rule_id": "per-position-cap", "severity": "warn"}],
 "alerts_emitted": [{"alert_id": "...", "rule_id": "per-position-cap"}],
 "positions": {"total_equity_usd": 5400.0, "n_positions": 2, ...},
 "diagnostics": {"rules_evaluated": 3, "rules_fired": 1}}
```

### Pull pending alerts (the agent-daemon pattern)

PM runs in the background; your agent comes back later and pulls
unacked alerts:

```sh
pm alerts pending --severity warn --limit 10
```

```json
{"ok": true, "result": {"count": 1, "alerts": [
  {"alert_id": "0a3c...", "rule_id": "trailing-stop-meme",
   "severity": "warn",
   "decision": {"action": "full_exit", "asset": "BONK", "qty": 1234567,
                "reason": "BONK dropped 31% from HWM (trail set at 25%)"},
   "created_at_utc": "2026-05-20T13:07:06+00:00"}]}}
```

```sh
pm alerts ack 0a3c...
```

### Generate a report from any audit log

```sh
pm report --audit-path state/audit.jsonl --out ./report-may/
```

Writes `report.json` + `report.md` + `equity.png` (with drawdown
shading). Sharpe / Sortino / Calmar / max-DD / win rate / expectancy /
CAGR computed from the audit, no external services.

### Open the live dashboard from another machine via Tailscale

```sh
pm dashboard --host $(tailscale ip -4 | head -1) --port 7777
```

Then on any tailnet-connected device, open `http://<this-host>:7777`.
Hero metrics (Equity / Return / Max DD / Sharpe / Sortino), live
equity curve with 24H/7D/1M/ALL range toggle, kill-switch headroom,
active-rules panel, positions table, full trade history with
clickable tx-explorer links (solscan / etherscan / basescan auto-
routed by tx-hash format), and a header bell for pending alerts with
per-alert + "clear all" ack buttons. SSE-pushed updates on every new
audit row.

### Demo end-to-end without a wallet or keys

The repo ships a synthetic wallet snapshot:

```sh
pm watch --config examples/conservative-majors.yaml \
         --positions-source examples/synthetic-wallet.json \
         --interval 0 --iterations 3
```

The rule engine evaluates against the static snapshot, fires alerts,
appends to the audit log. Zero risk, zero capital, full observability.

### Use it from Claude Code / Codex / a custom agent

Three integration paths:

| Method | Path |
|---|---|
| Drop into Claude Code's skills dir | `cp -r . ~/.claude/skills/portfolio-manager/` then restart Claude |
| Point a custom agent at the SKILL.md | parse YAML frontmatter (name / description / trigger phrases); shell out to `bin/pm` per command |
| Register with the OKX Plugin Store | `plugin.yaml` carries the manifest (schema_version: 1) |

Every command emits `{"ok": bool, "result": {...}}` JSON envelopes on
stdout. Errors print `FAILED: <category> <detail>` to stderr with
stable machine-parseable category strings. See `SKILL.md` for the
full schema, error vocabulary, and embedded-Python examples.

---

## Install

```sh
git clone https://github.com/paulomcg/portfolio-manager.git ~/Projects/portfolio-manager
cd ~/Projects/portfolio-manager
./install.sh                                # one-shot: venv + deps + smoke tests
echo 'export PATH="$HOME/Projects/portfolio-manager/bin:$PATH"' >> ~/.bashrc
pm --version
```

For real-wallet runs:

```sh
onchainos wallet login <your-email>
export OKX_API_KEY=... OKX_SECRET_KEY=... OKX_PASSPHRASE=...
```

PM itself never reads those env vars — the underlying `onchainos` CLI
does. Grep the source: no `OKX_*` references outside docs.

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
  (onchainos              (positions.py)              (rule_engine.py
   wallet / market)       ← HWM cache (sqlite)         + risk_rules.py)
                          ← manual overrides
                                                         │
                                                         ▼
                                          ┌── stdout tail (live)
                                          │── audit.jsonl (append-only)
                                          │── alerts queue (sqlite + jsonl mirror)
                                          │── dashboard (SSE → browser)
                                          │
                       (live mode only) ──▼── SwapExecutor
                                                │
                                                ▼
                                    onchainos swap execute
```

### Core invariants

- **Stateless rule engine** — pure function `(positions, rules) → decisions`.
  Same code path drives monitor mode, live mode, and the backtester.
- **Two independent kill-switches in live mode**:
  - `--max-loss-usd` — cumulative realized loss across cycles; required
    when `--live` is set
  - `--max-wallet-loss-usd` — wallet equity drop from baseline; catches
    rugged-token unrealized losses the realized-only kill misses entirely
- **Auto-derived position ledger** combines wallet balances with
  market-pnl to derive cost basis; tracks high-water marks per
  position across cycles; manual overrides supported.
- **Three observability surfaces** see the same source-of-truth:
  - Per-cycle JSONL on stdout
  - Append-only audit log (`state/audit.jsonl`)
  - Ack-able alerts queue (sqlite + jsonl mirror)
  - Read-only dashboard (HTTP + SSE)

### Three risk rule types

| Type | Triggers when | Common action |
|---|---|---|
| `halt_on_drawdown` | total_equity drops `threshold_pct` from portfolio HWM | `liquidate_all` |
| `max_position_pct` | any position > `threshold_pct` of equity | `trim_to` `target_pct` |
| `trailing_stop` | position drops `pct` from its own HWM (per-asset or `applies_to: "*"`) | `full_exit` |

Schema-validated by `pm rules validate` against `scripts/schema.py`.
Bad configs fail at load, not runtime.

### Three agent consumption patterns

| Pattern | When to use |
|---|---|
| Live stdout tail | Conversational: "watch my portfolio for the next hour" |
| Audit-log poll (`pm audit show --since ...`) | Check-ins: "what happened overnight?" |
| Alerts-queue daemon (`pm alerts pending` / `ack`) | Episodic agents — the common case |

### Files

| File | Role |
|---|---|
| `scripts/pm.py` | CLI dispatcher |
| `scripts/watch.py` | Monitor + live loop |
| `scripts/rule_engine.py` | Pure-function evaluate |
| `scripts/risk_rules.py` | Rule type implementations |
| `scripts/positions.py` | Derivation, HWM tracking, manual overrides |
| `scripts/wallet_source.py` | `OnchainosWalletSource`, `SyntheticWalletSource` |
| `scripts/executor.py` | `OnchainosSwapExecutor`, `SyntheticSwapExecutor` |
| `scripts/alerts.py` | Ack-able alerts queue |
| `scripts/audit.py` | Append-only JSONL |
| `scripts/schema.py` | JSON Schema for rule YAML |
| `scripts/dashboard/` | Read-only HTTP + SSE dashboard server |
| `dashboard-ui/` | React + Tailwind frontend (built into `scripts/dashboard/static/`) |
| `scripts/config.py` | Paths overridable via `PM_STATE_DIR`, `PM_AUDIT_PATH`, etc. |

### Tests

```sh
.venv/bin/pytest tests/ -v
```

Covers the rule engine, schema validation, position derivation, HWM
persistence, alerts queue, audit log, watch loops (monitor + live),
swap executors, CLI integration, strategy loader, market data,
`pm report`, dashboard endpoints (on a real ThreadingHTTPServer
instance), and end-to-end strategy-in-cycle integration. State is
isolated per-test via `PM_STATE_DIR` env override.

---

## Security

- API keys read by `onchainos`, never by PM. Grep verified.
- Live mode requires both `--live` and `--max-loss-usd` every
  invocation. No persisted "live mode on" state.
- All state under `state/` is local. Nothing leaves the host except
  the swap calls explicitly opted into via `--live`.
- The realized-loss kill switch is enforced twice: a pre-swap
  projection skips swaps that would breach the cap, and a post-swap
  accumulator halts the loop on any actual breach.
- The wallet-equity kill switch is the safety net for the rug case
  — a position whose liquidity vanishes mid-hold doesn't book a
  realized fill, so the realized-only kill stays at zero while the
  wallet bleeds. The wallet-equity kill catches that.
- The dashboard is read-only by design. No buttons fire swaps; the UI
  never mutates state. All write paths stay in the CLI.

---

## License

MIT — see [`LICENSE`](LICENSE).

## Disclaimer

Cryptocurrency trading is high-risk. This skill manages risk *rules*;
it does not pick what to buy and does not guarantee gains. Test
thoroughly in monitor mode before enabling live mode. Use
`--max-loss-usd` and `--max-wallet-loss-usd` set to amounts you can
afford to lose. The author and contributors disclaim all liability
for losses arising from use of this software.
