# Live-run evidence

Captured runtime artifacts from the 2026-05-20 live trading session
against a real funded OnChainOS Agentic Wallet on Solana mainnet. These
are not synthetic / fabricated — they are excerpts from the actual
`audit.jsonl` produced by PM during the OKX Agentic Trading Contest.

## `wallet-kill-switch-halt-2026-05-20.json`

The wallet-equity kill-switch (commit `bd4a17e`, `--max-wallet-loss-usd`)
firing in production after a rugged memecoin position bled $11.07 of
unrealized losses past the $10 cap.

**Sequence:**

1. PM started with baseline wallet equity $68.35 (cycle 0 captured it).
2. Position in `Pizza` memecoin (held from earlier in the session) had
   no exit liquidity — strategy emitted `trail_from_peak` sells every
   cycle, executor returned `execution_failed` (insufficient liquidity),
   no realized loss was ever booked.
3. Pizza price collapsed from $12.92 mark → $0.00 mark over ~5 cycles.
4. Wallet equity dropped to $57.28 — `loss_from_baseline = $11.07`,
   exceeding the `$10.00` cap.
5. PM raised `WatchHalt("wallet_loss_cap_exceeded ...")`, recorded the
   halt event in the cycle's `errors[]`, exited cleanly.

This is exactly the failure mode `--max-loss-usd` (realized-only) was
blind to: a rugged token never produces a fill, so the realized counter
stays at zero while the wallet bleeds. The wallet-equity kill is the
backstop.

**Output the watch loop returned on exit:**

```json
{
  "ok": true,
  "halted": true,
  "halt_reason": "wallet_loss_cap_exceeded baseline=$68.35 current=$57.28 loss=$11.07 cap=$10.00",
  "wallet_baseline_equity_usd": 68.35,
  "max_wallet_loss_usd": 10.0,
  "iterations": 59,
  "fills": 2
}
```

PM stayed halted. No manual intervention was needed — the operator
(me) was actively working on the submission writeup when the halt
fired; the demo continued safely without my attention.

## Why this matters for the submission

The contest's "Risk control framework" evaluation criterion is asking
specifically about *production safety under adverse conditions*. This
artifact shows that the kill-switch:

1. Was designed *because* of a failure mode discovered during the
   contest window (Pizza rug bled $22 unrealized before manual halt).
2. Was implemented (commit `bd4a17e`) + tested via the existing 30+
   watch/cli test suite.
3. Then fired in production on the very next adverse-condition
   exposure, halting PM exactly as designed.

The same audit log contains the bug-discovery commits that drove the
fix — `bd4a17e` (kill-switch), `fcc8b65` (Solana address case bug),
`e566b8d` (whipsaw cooldown). The submission is not lab work — it's
battle-tested.
