import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { History, Cog, Circle, ArrowLeftRight } from "lucide-react"
import { cn } from "@/lib/utils"
import type { AuditRow } from "@/types"
import { fmtTsShort, fmtUsd } from "@/lib/format"

interface AuditPanelProps {
  rows: AuditRow[]
  limit?: number
}

function eventMeta(event: string) {
  if (event === "watch.cycle") {
    return {
      label: "cycle",
      icon: Cog,
      tone: "border-border text-muted-foreground bg-muted/30",
    }
  }
  if (event === "watch.start") {
    return {
      label: "watch.start",
      icon: ArrowLeftRight,
      tone: "border-accent/50 text-accent bg-accent/5",
    }
  }
  return {
    label: event,
    icon: Circle,
    tone: "border-border text-muted-foreground bg-muted/20",
  }
}

export function AuditPanel({ rows, limit = 20 }: AuditPanelProps) {
  const shown = rows.slice(0, limit)
  return (
    <Card className="border-border bg-card py-0 shadow-none gap-0">
      <CardHeader className="px-5 py-4 border-b flex flex-row items-center justify-between gap-2 space-y-0">
        <CardTitle className="text-[11px] uppercase tracking-[0.08em] text-muted-foreground font-medium flex items-center gap-2">
          <History className="size-3.5" />
          Recent activity
          <span className="ml-1 text-muted-foreground/60">
            {shown.length}/{rows.length}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        {shown.length === 0 ? (
          <div className="p-8 text-center text-sm text-muted-foreground">
            no audit rows yet — start a watch session to see events
          </div>
        ) : (
          <ul className="divide-y divide-border/60 max-h-[420px] overflow-auto">
            {shown.map((r, i) => {
              const meta = eventMeta(r.event)
              const Icon = meta.icon
              const fillsCount = Array.isArray(r.fills) ? r.fills.length : 0
              const decisionsCount = Array.isArray(r.decisions)
                ? r.decisions.length
                : 0
              const eq = r.positions?.total_equity_usd
              return (
                <li key={`${r.ts_utc}-${i}`} className="px-5 py-2.5">
                  <div className="flex items-center gap-3">
                    <div
                      className={cn(
                        "rounded-md border p-1.5",
                        meta.tone,
                      )}
                    >
                      <Icon className="size-3" />
                    </div>
                    <div className="flex-1 min-w-0 grid grid-cols-12 gap-3 items-baseline">
                      <span className="col-span-3 text-[10px] tabular-nums text-muted-foreground">
                        {fmtTsShort(r.ts_utc)}
                      </span>
                      <span className="col-span-2">
                        <Badge
                          variant="outline"
                          className={cn(
                            "font-mono text-[10px] uppercase tracking-wider",
                            meta.tone,
                          )}
                        >
                          {meta.label}
                        </Badge>
                      </span>
                      <span className="col-span-4 text-[11px] text-muted-foreground tabular-nums">
                        {r.cycle_index != null && (
                          <span>
                            cycle{" "}
                            <span className="text-foreground">
                              #{r.cycle_index}
                            </span>
                          </span>
                        )}
                        {fillsCount > 0 && (
                          <span className="ml-2">
                            {fillsCount} fill{fillsCount > 1 ? "s" : ""}
                          </span>
                        )}
                        {decisionsCount > 0 && (
                          <span className="ml-2">
                            {decisionsCount} dec
                          </span>
                        )}
                      </span>
                      <span className="col-span-3 text-right text-[11px] tabular-nums">
                        {eq != null ? (
                          <span className="text-foreground">
                            {fmtUsd(eq, true)}
                          </span>
                        ) : (
                          <span className="text-muted-foreground/50">—</span>
                        )}
                      </span>
                    </div>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
