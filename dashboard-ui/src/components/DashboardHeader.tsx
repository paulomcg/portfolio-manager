import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { cn } from "@/lib/utils"
import type { ConnState } from "@/hooks/useDashboardState"
import type { AlertRow } from "@/types"
import { fmtTs, shortAddr } from "@/lib/format"
import { NotificationBell } from "@/components/NotificationBell"

interface DashboardHeaderProps {
  conn: ConnState
  wallet: string | null
  servedAtUtc?: string
  mode: "live" | "monitor" | null
  alerts?: AlertRow[]
}

export function DashboardHeader({
  conn,
  wallet,
  servedAtUtc,
  mode,
  alerts = [],
}: DashboardHeaderProps) {
  const dotClass =
    conn === "live"
      ? "bg-accent shadow-[0_0_0_4px_color-mix(in_oklab,var(--accent)_20%,transparent)]"
      : conn === "connecting"
        ? "bg-amber-400 shadow-[0_0_0_4px_color-mix(in_oklab,oklch(0.78_0.15_70)_20%,transparent)]"
        : "bg-destructive shadow-[0_0_0_4px_color-mix(in_oklab,var(--destructive)_20%,transparent)]"

  return (
    <header className="border-b border-border bg-background/95 sticky top-0 z-10 backdrop-blur-md">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-6 gap-y-3 px-6 py-4">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <span className={cn("inline-block size-2 rounded-full transition-colors", dotClass)} />
            <span className="font-semibold tracking-tight text-base">
              portfolio-manager
            </span>
            <Badge
              variant="secondary"
              className="font-mono text-[10px] uppercase tracking-wider"
            >
              v0.2.0
            </Badge>
            {mode && (
              <Badge
                variant="outline"
                className={cn(
                  "font-mono text-[10px] uppercase tracking-wider",
                  mode === "live"
                    ? "border-positive/50 text-positive bg-positive/10"
                    : "border-border text-muted-foreground",
                )}
              >
                {mode}
              </Badge>
            )}
          </div>
        </div>

        <Separator orientation="vertical" className="hidden h-6 sm:block" />

        <div className="text-sm">
          <span className="text-muted-foreground text-xs">wallet</span>
          <span className="ml-1.5 font-mono text-xs">
            {wallet ? shortAddr(wallet, 6) : "—"}
          </span>
        </div>

        <div className="ms-auto flex items-center gap-4">
          <div className="flex flex-col items-end text-right">
            <div className="text-xs text-muted-foreground tabular-nums">
              {servedAtUtc ? `updated ${fmtTs(servedAtUtc)}` : "—"}
            </div>
            <div className="font-mono text-[10px] text-muted-foreground/70 uppercase tracking-wider">
              {conn === "live" ? "● live · SSE" : conn === "connecting" ? "○ connecting…" : "✕ offline"}
            </div>
          </div>
          <NotificationBell alerts={alerts} />
        </div>
      </div>
    </header>
  )
}
