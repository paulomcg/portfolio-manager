# portfolio-manager

**A complete agentic trading agent for the OKX Agentic Wallet.** Owns
the **strategy** (a Python `decide()` callback the agent authors —
"when to open"), the **rules** (drawdown halts, position caps,
trailing stops — "when to exit"), the **executor** (real swaps via
OnChainOS or a built-in synthetic engine for paper-trading), the
**alerts queue**, the **append-only audit log**, and the **metrics
engine** (Sharpe / Sortino / max DD / win rate computed locally from
any audit).

The same `.py` strategy file works in live mode AND against historical
OHLCV via the companion [`strategy-backtester`](https://github.com/paulomcg/strategy-backtester)
skill. **One artifact, two execution contexts.**

For agents: PM is the discipline layer. You decide what to buy. PM
decides when to exit, halts before the loss exceeds your cap, and
records every decision.

---

## What you can ask the agent to do

The skill is installed; the agent picks it up via the `SKILL.md`
frontmatter when the user's request matches. Below: natural-language
prompts the user says, and what the agent does with them.

---

> **"Run my weekly DCA into SOL, cap losses at $50."**

The agent drafts a small Python `decide()` callback (or picks one
from `examples/strategies/`), wires it into a `pm watch` loop with
both kill-switches enabled, and PM takes over. Every 7 days it buys
$100 of WSOL via OnChainOS swap; every cycle it checks the rules
(drawdown halt, position caps, trailing stops). The moment cumulative
realized loss crosses $50 OR wallet equity drops $100 below
baseline, the loop halts itself.

---

> **"Watch my wallet for the next hour and tell me what's risky."**

Agent starts PM in monitor mode against the address — no swaps fire,
no keys needed. PM polls the wallet, derives positions, evaluates the
configured rules every cycle. Whenever a rule would have fired, PM
emits a structured alert with the asset, the rule, the recommended
action, and the reason ("WSOL is 72% of portfolio, exceeds 40% cap
— trim to 40%"). The agent surfaces them.

---

> **"What happened to my portfolio overnight?"**

Episodic check-in. The agent reads PM's append-only audit log
(`state/audit.jsonl`) since the user last asked, summarizes any
rules that fired, any trades that executed, and current equity vs
the overnight low. The audit log is the source of truth; no remote
service is involved.

---

> **"Did anything critical alert that I haven't seen?"**

The daemon pattern. PM runs as a background watch process; the
agent queries the alerts queue, shows any unacked critical alerts,
and acks them once the user has been shown.

---

> **"How am I doing — Sharpe, max DD, win rate?"**

The agent computes those metrics locally from PM's audit log —
Sharpe, Sortino, Calmar, max DD, win rate, expectancy, CAGR — and
surfaces them. No external services. The agent can also export a
JSON payload if the user wants to chart it themselves.

---

> **"Backtest this same strategy on a year of historical data."**

The strategy `.py` the user authored for live mode is the same
artifact the companion
[`strategy-backtester`](https://github.com/paulomcg/strategy-backtester)
skill drives against historical OHLCV. The agent invokes the
backtester and gets back the same metric shape — same Sharpe,
same drawdown methodology, same audit-log format.

---

> **"Halt everything immediately."**

Agent kills the PM watch process. There is no persisted "live mode
on" state — closing the terminal terminates the loop. To restart
later, the user re-runs the original watch command.

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

### Wiring into an agent (Claude Code, Codex, custom harness)

| Method | Path |
|---|---|
| Drop into Claude Code's skills dir | `cp -r . ~/.claude/skills/portfolio-manager/` then restart Claude |
| Point a custom agent at SKILL.md | parse YAML frontmatter (name / description / trigger phrases); shell out to `bin/pm` per command |
| Register with the OKX Plugin Store | `plugin.yaml` schema_version: 1 |

Every command emits `{"ok": bool, "result": {...}}` JSON envelopes
on stdout — agents don't have to parse English. Errors print
`FAILED: <category> <detail>` to stderr with stable
machine-parseable category strings. See `SKILL.md` for the full
schema, error vocabulary, and embedded-Python examples.

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
| `scripts/config.py` | Paths overridable via `PM_STATE_DIR`, `PM_AUDIT_PATH`, etc. |

### Tests

```sh
.venv/bin/pytest tests/ -v
```

Covers the rule engine, schema validation, position derivation, HWM
persistence, alerts queue, audit log, watch loops (monitor + live),
swap executors, CLI integration, strategy loader, market data,
the metrics engine, and end-to-end strategy-in-cycle integration.
State is isolated per-test via `PM_STATE_DIR` env override.

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
