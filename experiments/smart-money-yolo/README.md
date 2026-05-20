# smart-money-yolo (experimental — NOT a clean exemplar)

This strategy lives outside `examples/strategies/` on purpose. It is a
useful real-world test bed, but it **violates the PM strategy contract**
and should not be used as a model for how to write strategies against PM.

## What's wrong with it

The PM strategy contract is:

```python
def decide(state: dict, market_data: dict) -> list[Action]: ...
```

A clean strategy receives **all** of its data via `state` (positions,
cash, cycle index, P&L) and `market_data` (OHLCV from PM's adapter). It
has no awareness of where any of it came from. That's the whole point —
the same `.py` runs live and in `backtester replay` because the data
plumbing is PM's job, not the strategy's.

`smart-money-yolo/strategy.py` violates that by **directly subprocess-
calling `onchainos`** for signal feeds:

- `onchainos tracker activities --tracker-type smart_money` (whale buys)
- `onchainos tracker activities --tracker-type kol` (KOL/influencer buys)
- `onchainos memepump tokens --stage MIGRATED` (newly bonded memecoins)

These feeds don't exist in PM's `market_data` adapter (which is OHLCV-
only). Rather than extend PM, the original implementation took the
shortcut of having the strategy escape PM's data layer entirely.

The consequences:

- **Can't backtest coherently.** A `backtester replay` of this strategy
  would query LIVE onchainos data while feeding historical OHLCV — the
  two are incoherent.
- **~600 lines of strategy code, 90% data plumbing** (subprocess calls,
  caching, response parsing, address normalization). A clean strategy
  is ~50-100 lines.
- **Hides external dependencies from PM.** PM can't tell the user this
  strategy needs the `onchainos` CLI installed; it silently breaks if
  not.

## Why it's still here

The strategy was the test bed that drove several real PM improvements
during the 2026-05 contest window:

- Surfaced the **wallet-equity kill-switch** gap (`--max-wallet-loss-usd`,
  shipped commit `bd4a17e`) — rugged-token unrealized losses were
  invisible to the realized-loss-only kill.
- Surfaced the **Solana base58 case-preservation** bug in executor
  address handling (commit `fcc8b65`).
- Surfaced the **dump-exit / re-entry whipsaw** failure mode and its
  cooldown fix.
- Surfaced the **insufficient-liquidity sell-spam** observability issue
  in the rule engine.

It also remains useful as a "real conditions" smoke test against the
live OnChainOS DEX-aggregator, smart-money tracker, KOL tracker, and
memepump endpoints.

## The clean v2

A proper rewrite waits for PM to grow a `SignalFeed` adapter (sibling
of `MarketDataSource`) that polls tracker/KOL/memepump feeds, caches
them, and exposes them via:

```python
market_data["signals"]["smart_money_buys"]
market_data["signals"]["kol_buys"]
market_data["signals"]["memepump_migrated"]
```

Once that ships, smart-money-yolo collapses to a ~80-line `decide()`
that consumes those feeds and applies the multi-source gating logic.
That's the version that belongs in `examples/strategies/`.

## Running it as-is

```sh
pm watch \
  --strategy experiments/smart-money-yolo/strategy.py \
  --config <rules.yaml> \
  --wallet <addr> \
  --chain solana \
  --live --max-loss-usd 15 \
  --max-wallet-loss-usd 10 \
  --executor onchainos
```

Requires `OKX_API_KEY` / `OKX_SECRET_KEY` / `OKX_PASSPHRASE` in env
(passed through to the `onchainos` CLI for the tracker/memepump calls).

## License

MIT (same as PM).
