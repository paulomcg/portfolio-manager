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

interface EquityChartProps {
  data: EquityPoint[]
  initialEquity?: number | null
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

export function EquityChart({ data, initialEquity }: EquityChartProps) {
  const last = data[data.length - 1]?.equity_usd ?? initialEquity ?? 0
  const baseline = initialEquity ?? data[0]?.equity_usd ?? last
  const positive = last >= baseline
  const gradId = "equity-fill-live"

  const yDomain = useMemo(() => {
    if (!data.length) return [0, 1]
    const values = data.map((d) => d.equity_usd)
    const min = Math.min(...values, baseline)
    const max = Math.max(...values, baseline)
    const pad = Math.max((max - min) * 0.12, max * 0.02)
    return [Math.floor(min - pad), Math.ceil(max + pad)]
  }, [data, baseline])

  if (data.length === 0) {
    return (
      <div className="h-60 grid place-items-center text-sm text-muted-foreground">
        no cycles recorded yet
      </div>
    )
  }

  return (
    <div className="h-60 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart
          data={data}
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
            dataKey="ts"
            tickFormatter={(v) => fmtTsShort(v)}
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
