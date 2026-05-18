import { Card, CardContent } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import type { ReactNode } from "react"

interface MetricCardProps {
  label: string
  value: ReactNode
  hint?: ReactNode
  tone?: "default" | "positive" | "negative" | "muted"
  size?: "default" | "lg"
}

export function MetricCard({
  label,
  value,
  hint,
  tone = "default",
  size = "default",
}: MetricCardProps) {
  return (
    <Card className="border-border bg-card py-0 shadow-none gap-0">
      <CardContent className="p-4">
        <div className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
          {label}
        </div>
        <div
          className={cn(
            "tabular-nums font-semibold mt-1.5",
            size === "lg" ? "text-2xl" : "text-xl",
            tone === "positive" && "text-positive",
            tone === "negative" && "text-destructive",
            tone === "muted" && "text-muted-foreground",
          )}
        >
          {value}
        </div>
        {hint != null && (
          <div className="mt-1 text-[11px] text-muted-foreground tabular-nums">
            {hint}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
