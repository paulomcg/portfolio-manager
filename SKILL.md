---
name: portfolio-manager
description: "Reactive wallet supervisor for OKX Agentic Wallet. Watches positions, evaluates risk rules (halt-on-drawdown, max-position-pct, trailing-stop), emits structured alerts in monitor mode or executes exits via okx-dex-swap in live mode with a hard max-loss-usd kill switch. Use when the user says: watch my portfolio, manage positions, set up risk rules, halt on drawdown, trailing stop, position cap, alerts queue, ack alerts, max loss kill switch, audit my trades."
version: "0.1.0"
license: MIT
metadata:
  author: paulomcg
  homepage: "https://github.com/paulomcg/portfolio-manager"
---

# Portfolio Manager

A reactive supervisor that **observes** your wallet and **enforces** risk rules.
It does **not** decide what to buy and does **not** wrap `okx-dex-swap` — the
user (or the agent) opens positions freely; PM watches the resulting state and
takes rule-driven action.

## Pre-flight (before the first `pm` command this session)

1. `command -v pm` → if missing, install with `npx skills add okx/plugin-store --skill portfolio-manager` or clone the repo and add its `bin/` to `$PATH`.
2. `pm --version` → confirm.
3. For monitor-mode demos with synthetic state, skip to **Modes**. For real-wallet use:
   - `onchainos --version` → confirm CLI is present (install via `okx-agentic-wallet` skill if not).
   - `onchainos wallet status` → must report `loggedIn: true`. If not, `onchainos wallet login <email>` first.
   - `OKX_API_KEY` / `OKX_SECRET_KEY` / `OKX_PASSPHRASE` are read from env by the underlying CLI. PM never reads, logs, or persists them.

## Modes — pick one, default is monitor

### Monitor mode (default)

Reads wallet state, evaluates rules, emits structured alerts. **Never** calls a swap endpoint. Safe to demo without capital.

```
pm watch --config <rules.yaml> --wallet <address> [--interval 60] [--iterations N]
```

For no-keys / no-wallet demos use a synthetic state file:

```
pm watch --config examples/conservative-majors.yaml \
         --positions-source examples/synthetic-wallet.json \
         --interval 0 --iterations 3
```

### Live mode (explicit opt-in)

Executes recommended exits via the `onchainos swap execute` CLI. **Both** `--live` and `--max-loss-usd` are required every invocation — there is no persisted "live mode on" state.

```
pm watch --config <rules.yaml> --wallet <address> \
         --live --max-loss-usd <usd> [--interval 60] [--executor onchainos|synthetic]
```

The loop halts the instant cumulative realized loss across all swaps it executed exceeds `--max-loss-usd`. A conservative pre-check projects the worst-case loss of each individual swap and skips it (instead of executing it) if executing would push cumulative loss over the cap.

`--executor synthetic` runs the same code path with a fake swap engine — useful for testing or demoing the cap-enforcement behavior on a no-capital wallet.

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
