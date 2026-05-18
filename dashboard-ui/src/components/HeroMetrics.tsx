import { MetricCard } from "@/components/MetricCard"
import type { MetricsPayload, StatePayload } from "@/types"
import { fmtUsd, fmtPct, fmtNum } from "@/lib/format"

interface HeroMetricsProps {
  state: StatePayload
  metrics: MetricsPayload
  cycleCount: number
}

export function HeroMetrics({ state, metrics, cycleCount }: HeroMetricsProps) {
  const equity = state.last_cycle?.positions?.total_equity_usd ?? metrics.final_equity_usd ?? 0
  const ret = metrics.total_return_pct ?? 0
  const dd = metrics.max_drawdown_pct ?? 0
  const sharpe = metrics.sharpe ?? 0

  return (
    <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      <MetricCard
        label="Total equity"
        value={fmtUsd(equity)}
        tone={equity > 0 ? "default" : "muted"}
        size="lg"
        hint={
          metrics.initial_equity_usd != null
            ? `init ${fmtUsd(metrics.initial_equity_usd, true)}`
            : undefined
        }
      />
      <MetricCard
        label="Return"
        value={fmtPct(ret, 2, true)}
        tone={ret > 0 ? "positive" : ret < 0 ? "negative" : "default"}
        size="lg"
      />
      <MetricCard
        label="Max DD"
        value={fmtPct(dd, 2)}
        tone={dd < -10 ? "negative" : dd < -5 ? "default" : "muted"}
        size="lg"
        hint="from HWM"
      />
      <MetricCard
        label="Sharpe"
        value={fmtNum(sharpe, 2)}
        tone={sharpe >= 1 ? "positive" : sharpe < 0 ? "negative" : "default"}
        size="lg"
      />
      <MetricCard
        label="Sortino"
        value={fmtNum(metrics.sortino ?? 0, 2)}
        tone={(metrics.sortino ?? 0) >= 1 ? "positive" : "default"}
        size="lg"
      />
      <MetricCard
        label="Cycles"
        value={cycleCount}
        size="lg"
        hint={metrics.bars > 0 ? `${metrics.bars} bars` : undefined}
      />
    </section>
  )
}
