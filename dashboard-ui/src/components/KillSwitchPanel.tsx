import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Shield, ShieldAlert, ShieldX, AlertTriangle } from "lucide-react"
import { cn } from "@/lib/utils"
import type { KillSwitchPayload } from "@/types"
import { fmtTs, fmtUsd, fmtUsdSigned, fmtPct } from "@/lib/format"

interface KillSwitchPanelProps {
  ks: KillSwitchPayload
}

const STATUS_STYLES = {
  ok: {
    badge: "border-positive/40 text-positive bg-positive/10",
    label: "armed · safe",
    icon: Shield,
    bar: "bg-positive",
    cardBorder: "",
  },
  warning: {
    badge: "border-amber-500/40 text-amber-500 bg-amber-500/10",
    label: "armed · approaching cap",
    icon: ShieldAlert,
    bar: "bg-amber-500",
    cardBorder: "",
  },
  critical: {
    badge: "border-destructive/50 text-destructive bg-destructive/10",
    label: "armed · CRITICAL",
    icon: AlertTriangle,
    bar: "bg-destructive",
    cardBorder: "ring-1 ring-destructive/40",
  },
  halted: {
    badge: "border-destructive/70 text-destructive bg-destructive/20",
    label: "halted · cap breached",
    icon: ShieldX,
    bar: "bg-destructive",
    cardBorder: "ring-2 ring-destructive/60",
  },
} as const

export function KillSwitchPanel({ ks }: KillSwitchPanelProps) {
  const styles = STATUS_STYLES[ks.status]
  const Icon = styles.icon

  if (!ks.active) {
    return (
      <Card className="border-border bg-card py-0 shadow-none gap-0">
        <CardHeader className="px-5 py-4 border-b">
          <CardTitle className="text-[11px] uppercase tracking-[0.08em] text-muted-foreground font-medium flex items-center gap-2">
            <Shield className="size-3.5" />
            Live kill switch
          </CardTitle>
        </CardHeader>
        <CardContent className="px-5 py-6">
          <div className="flex items-center gap-3">
            <Shield className="size-5 text-muted-foreground" />
            <div>
              <div className="text-sm font-medium">Monitor mode</div>
              <div className="text-xs text-muted-foreground mt-0.5">
                No live execution. Kill switch armed only when{" "}
                <code className="font-mono text-[11px] bg-muted px-1.5 py-0.5 rounded">
                  --live --max-loss-usd
                </code>{" "}
                is set.
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    )
  }

  const remaining = Math.max(0, ks.max_loss_usd - ks.realized_loss_usd)
  const pct = Math.min(100, ks.percent_consumed)

  return (
    <Card
      className={cn(
        "border-border bg-card py-0 shadow-none gap-0",
        styles.cardBorder,
      )}
    >
      <CardHeader className="px-5 py-4 border-b flex flex-row items-center justify-between gap-3 space-y-0">
        <CardTitle className="text-[11px] uppercase tracking-[0.08em] text-muted-foreground font-medium flex items-center gap-2">
          <Icon className="size-3.5" />
          Live kill switch
        </CardTitle>
        <Badge variant="outline" className={cn("font-mono text-[10px] uppercase tracking-wider", styles.badge)}>
          {styles.label}
        </Badge>
      </CardHeader>
      <CardContent className="p-5 space-y-4">
        <div className="grid grid-cols-3 gap-4">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
              Cap
            </div>
            <div className="text-2xl font-semibold tabular-nums mt-1">
              {fmtUsd(ks.max_loss_usd)}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
              Realized loss
            </div>
            <div className="text-2xl font-semibold tabular-nums mt-1 text-destructive">
              {ks.realized_loss_usd > 0
                ? `−${fmtUsd(ks.realized_loss_usd)}`
                : fmtUsd(0)}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
              Remaining budget
            </div>
            <div
              className={cn(
                "text-2xl font-semibold tabular-nums mt-1",
                ks.status === "halted"
                  ? "text-destructive"
                  : ks.status === "critical"
                    ? "text-amber-500"
                    : "text-foreground",
              )}
            >
              {fmtUsd(remaining)}
            </div>
          </div>
        </div>

        <div>
          <div className="flex items-baseline justify-between mb-2">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
              consumption
            </span>
            <span className="text-sm font-semibold tabular-nums">
              {fmtPct(pct, 1)}
            </span>
          </div>
          <div className="h-1.5 w-full rounded-full bg-border/50 overflow-hidden">
            <div
              className={cn("h-full transition-all", styles.bar)}
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>

        <div className="flex items-center justify-between pt-3 border-t border-border/60 text-[11px] text-muted-foreground tabular-nums">
          <span>
            net realized:{" "}
            <span
              className={cn(
                "font-semibold",
                ks.net_realized_usd >= 0 ? "text-positive" : "text-destructive",
              )}
            >
              {fmtUsdSigned(ks.net_realized_usd)}
            </span>
            {" "}· {ks.fills_since_start} fills
          </span>
          {ks.started_at_utc && (
            <span>armed {fmtTs(ks.started_at_utc)}</span>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
