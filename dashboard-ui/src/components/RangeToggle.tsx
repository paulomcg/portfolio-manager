/**
 * Compact segmented control for the equity-chart time range.
 * Used in the equity-chart card header.
 */
import { cn } from "@/lib/utils"
import type { EquityRange } from "@/components/EquityChart"

interface RangeToggleProps {
  value: EquityRange
  onChange: (r: EquityRange) => void
}

const OPTIONS: { value: EquityRange; label: string }[] = [
  { value: "24h", label: "24H" },
  { value: "7d", label: "7D" },
  { value: "1m", label: "1M" },
  { value: "all", label: "ALL" },
]

export function RangeToggle({ value, onChange }: RangeToggleProps) {
  return (
    <div
      className="inline-flex items-center rounded-md border border-border bg-muted/20 p-0.5"
      role="group"
      aria-label="Equity chart time range"
    >
      {OPTIONS.map((opt) => {
        const active = opt.value === value
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            aria-pressed={active}
            className={cn(
              "px-2.5 py-1 text-[10px] font-mono uppercase tracking-wider rounded-sm transition-colors",
              active
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}
