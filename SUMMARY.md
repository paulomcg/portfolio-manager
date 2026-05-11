## Overview

`portfolio-manager` is a **reactive wallet supervisor** for the OKX Agentic Wallet. You open and close positions however you like — manual swap, agentic CLI, scripted — and PM watches the resulting state, evaluates a declarative YAML rule config against it on a schedule, and either alerts you or auto-exits depending on mode. It composes existing OKX skills (`okx-wallet-portfolio`, `okx-dex-market`, `okx-dex-swap`) — it does not replace them.

Core operations:

- Poll wallet balances + per-token PnL on a configurable interval (default 60s)
- Auto-derive a position ledger with cost basis (from realized + unrealized PnL math), high-water marks tracked across cycles, and drawdown metrics
- Evaluate three rule types against the derived state: `halt_on_drawdown`, `max_position_pct`, `trailing_stop`
- **Monitor mode** (default): emit JSON alerts to stdout, sqlite-backed queue, and an append-only `audit.jsonl`. No swaps.
- **Live mode** (`--live --max-loss-usd <usd>`): execute recommended exits via `okx-dex-swap`. Hard kill switch halts the loop when cumulative realized loss exceeds the cap. Both flags are required every invocation — no persistent "live mode on" state.
- Three agent consumption patterns supported: live tail (read `pm watch` stdout), audit poll (`pm audit show --since <ts>`), and alerts queue (`pm alerts pending` / `pm alerts ack` for episodic agents)

Tags: `risk-management` `solana` `onchainos` `position-management` `trailing-stop` `drawdown` `alerts` `audit`

## Prerequisites

- Supported chain (v1): **Solana**. Other chains via `--chain <name>` accepted but stablecoin destination address only wired for Solana
- `onchainos` CLI installed (`onchainos --version`)
- Authenticated Agentic Wallet (`onchainos wallet status` reports `loggedIn: true`) — only required for `--live` and the `onchainos` wallet source. Monitor-mode demos run against synthetic JSON without keys.
- Python 3.10+ (standard library + `jsonschema` + `pyyaml`)
- Funded wallet only for the optional live-mode demo (recommended ≤ $20 of test capital)

## Quick Start

1. **Install the skill**: `npx skills add okx/plugin-store --skill portfolio-manager` (or clone this repo and add `bin/` to your `$PATH`)
2. **Pick a rule config**: copy `examples/conservative-majors.yaml` or `examples/meme-trailing.yaml` and edit thresholds / universe
3. **Try monitor mode with no keys**:
   ```
   pm watch --config examples/conservative-majors.yaml \
            --positions-source examples/synthetic-wallet.json \
            --pnl-source examples/synthetic-pnl.json \
            --interval 0 --iterations 3
   ```
4. **Connect a real wallet** (`onchainos wallet login <email>` first, then):
   ```
   pm watch --config examples/conservative-majors.yaml --wallet <address> --interval 60
   ```
5. **Switch to live mode** (after testing in monitor mode for a session):
   ```
   pm watch --config examples/conservative-majors.yaml --wallet <address> \
            --live --max-loss-usd 50 --interval 60
   ```
6. **Episodic agents**: leave `pm watch` running as a background process, then poll `pm alerts pending` whenever you check in.
