import { useMemo } from "react"
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import type { EquityPoint } from "@/types"
import { fmtTsShort, fmtUsd, fmtPct } from "@/lib/format"

export type EquityRange = "24h" | "7d" | "1m" | "all"

const RANGE_MS: Record<Exclude<EquityRange, "all">, number> = {
  "24h": 24 * 60 * 60 * 1000,
  "7d": 7 * 24 * 60 * 60 * 1000,
  "1m": 30 * 24 * 60 * 60 * 1000,
}

interface EquityChartProps {
  data: EquityPoint[]
  initialEquity?: number | null
  range?: EquityRange
}

function ChartTooltip({ active, payload }: any) {
  if (!active || !payload || !payload.length) return null
  const p = payload[0].payload as EquityPoint
  return (
    <div className="rounded-md border border-border bg-popover/95 backdrop-blur px-3 py-2 shadow-lg">
      <div className="text-[10px] text-muted-foreground tabular-nums">
        {fmtTsShort(p.ts)}
      </div>
      <div className="text-sm font-semibold tabular-nums mt-0.5">
        {fmtUsd(p.equity_usd)}
      </div>
      <div
        className={`text-[11px] tabular-nums mt-0.5 ${
          p.drawdown_pct < 0 ? "text-destructive" : "text-muted-foreground"
        }`}
      >
        DD {fmtPct(p.drawdown_pct, 2)}
      </div>
    </div>
  )
}

export function EquityChart({
  data,
  initialEquity,
  range = "all",
}: EquityChartProps) {
  // Filter to the selected range, then append a synthetic "now" point
  // holding the last known equity value flat. This makes the chart
  // extend to the right edge of the viewport even when PM has been
  // halted for hours — Paulo sees a continuous line from his last
  // trade to "now" instead of a chart that ends mid-axis.
  // We render the chart with a time-based numeric XAxis (epoch ms) so
  // a single padded "now" point at a far-future timestamp actually
  // extends the line to the right edge. With recharts' default
  // categorical XAxis, every point gets equal-width spacing and the
  // pad point is just one more category — invisible.
  const series = useMemo(() => {
    type Pt = EquityPoint & { tsMs: number }
    let s: Pt[] = data.map((p) => ({ ...p, tsMs: new Date(p.ts).getTime() }))
    if (range !== "all" && s.length) {
      const lastMs = s[s.length - 1].tsMs
      const nowMs = Date.now()
      const cutoffMs = Math.max(lastMs, nowMs) - RANGE_MS[range]
      s = s.filter((p) => p.tsMs >= cutoffMs)
    }
    if (s.length) {
      const last = s[s.length - 1]
      const nowMs = Date.now()
      if (nowMs - last.tsMs > 10_000) {
        s = [
          ...s,
          {
            ts: new Date(nowMs).toISOString(),
            tsMs: nowMs,
            equity_usd: last.equity_usd,
            drawdown_pct: last.drawdown_pct,
          },
        ]
      }
    }
    return s
  }, [data, range])

  const xDomain = useMemo(() => {
    if (!series.length) return ["auto", "auto"] as const
    const firstMs = series[0].tsMs
    const lastMs = series[series.length - 1].tsMs
    return [firstMs, lastMs] as [number, number]
  }, [series])

  const last = series[series.length - 1]?.equity_usd ?? initialEquity ?? 0
  const baseline = initialEquity ?? series[0]?.equity_usd ?? last
  const positive = last >= baseline
  const gradId = "equity-fill-live"

  const yDomain = useMemo(() => {
    if (!series.length) return [0, 1]
    const values = series.map((d) => d.equity_usd)
    const min = Math.min(...values, baseline)
    const max = Math.max(...values, baseline)
    const pad = Math.max((max - min) * 0.12, max * 0.02)
    return [Math.floor(min - pad), Math.ceil(max + pad)]
  }, [series, baseline])

  if (series.length === 0) {
    return (
      <div className="h-60 grid place-items-center text-sm text-muted-foreground">
        {data.length === 0 ? "no cycles recorded yet" : "no data in selected range"}
      </div>
    )
  }

  return (
    <div className="h-60 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart
          data={series}
          margin={{ top: 4, right: 8, left: 8, bottom: 0 }}
        >
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop
                offset="0%"
                stopColor={positive ? "var(--positive)" : "var(--destructive)"}
                stopOpacity={0.32}
              />
              <stop
                offset="100%"
                stopColor={positive ? "var(--positive)" : "var(--destructive)"}
                stopOpacity={0}
              />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} strokeDasharray="2 4" />
          <XAxis
            dataKey="tsMs"
            type="number"
            scale="time"
            domain={xDomain as [number, number] | readonly ["auto", "auto"]}
            tickFormatter={(v) => fmtTsShort(new Date(v).toISOString())}
            tickLine={false}
            axisLine={false}
            minTickGap={48}
          />
          <YAxis
            domain={yDomain}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => fmtUsd(Number(v), true)}
            width={56}
          />
          <Tooltip content={<ChartTooltip />} cursor={{ stroke: "var(--border)" }} />
          {baseline > 0 && (
            <ReferenceLine
              y={baseline}
              stroke="var(--border)"
              strokeDasharray="2 4"
              label={{
                value: `initial ${fmtUsd(baseline, true)}`,
                position: "insideTopLeft",
                fill: "var(--muted-foreground)",
                fontSize: 10,
              }}
            />
          )}
          <Area
            type="monotone"
            dataKey="equity_usd"
            stroke={positive ? "var(--positive)" : "var(--destructive)"}
            strokeWidth={1.75}
            fill={`url(#${gradId})`}
            dot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
