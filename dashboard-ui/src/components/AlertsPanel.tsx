import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { BellRing, BellOff } from "lucide-react"
import { cn } from "@/lib/utils"
import type { AlertRow } from "@/types"
import { fmtTsShort } from "@/lib/format"

interface AlertsPanelProps {
  alerts: AlertRow[]
}

const SEV_STYLES: Record<AlertRow["severity"], string> = {
  info: "border-border text-muted-foreground bg-muted/30",
  warn: "border-amber-500/40 text-amber-500 bg-amber-500/10",
  critical: "border-destructive/50 text-destructive bg-destructive/10",
}

export function AlertsPanel({ alerts }: AlertsPanelProps) {
  return (
    <Card className="border-border bg-card py-0 shadow-none gap-0">
      <CardHeader className="px-5 py-4 border-b flex flex-row items-center justify-between gap-2 space-y-0">
        <CardTitle className="text-[11px] uppercase tracking-[0.08em] text-muted-foreground font-medium flex items-center gap-2">
          {alerts.length > 0 ? (
            <BellRing className="size-3.5" />
          ) : (
            <BellOff className="size-3.5" />
          )}
          Pending alerts
          <span className="ml-1 text-muted-foreground/60">{alerts.length}</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        {alerts.length === 0 ? (
          <div className="p-8 text-center text-sm text-muted-foreground">
            no pending alerts
          </div>
        ) : (
          <ul className="divide-y divide-border/60 max-h-[320px] overflow-auto">
            {alerts.map((a) => (
              <li key={a.id} className="px-5 py-3">
                <div className="flex items-start gap-3">
                  <Badge
                    variant="outline"
                    className={cn(
                      "font-mono text-[10px] uppercase tracking-wider shrink-0 mt-0.5",
                      SEV_STYLES[a.severity],
                    )}
                  >
                    {a.severity}
                  </Badge>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="font-medium text-sm">
                        {a.rule_id ?? a.rule_type ?? "alert"}
                      </span>
                      <span className="text-[10px] text-muted-foreground tabular-nums">
                        {fmtTsShort(a.ts_utc)}
                      </span>
                    </div>
                    <p className="text-[12px] text-muted-foreground mt-1">
                      {a.message}
                    </p>
                    {a.asset && (
                      <div className="mt-1 text-[10px] font-mono text-muted-foreground/80 uppercase tracking-wider">
                        {a.asset}
                      </div>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
