/**
 * Header notification bell with a popover that opens the alerts list
 * on click. Click-outside closes the popover. No external dep — uses
 * useRef + useEffect for the outside-click handler.
 */
import { useEffect, useRef, useState } from "react"
import { Bell, BellOff } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import type { AlertRow } from "@/types"
import { fmtTsShort } from "@/lib/format"

interface NotificationBellProps {
  alerts: AlertRow[]
}

const SEV_STYLES: Record<AlertRow["severity"], string> = {
  info: "border-border text-muted-foreground bg-muted/30",
  warn: "border-amber-500/40 text-amber-500 bg-amber-500/10",
  critical: "border-destructive/50 text-destructive bg-destructive/10",
}

const SEV_DOT: Record<AlertRow["severity"], string> = {
  info: "bg-muted-foreground",
  warn: "bg-amber-500",
  critical: "bg-destructive",
}

export function NotificationBell({ alerts }: NotificationBellProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [open])

  const count = alerts.length
  const hasUnread = count > 0
  const worstSev = alerts.reduce<AlertRow["severity"]>((acc, a) => {
    if (a.severity === "critical") return "critical"
    if (a.severity === "warn" && acc !== "critical") return "warn"
    return acc
  }, "info")

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "relative inline-flex items-center justify-center rounded-md border p-2 transition-colors",
          "hover:bg-muted/50",
          open && "bg-muted/50 border-foreground/20",
          !open && "border-border",
        )}
        aria-label={`${count} pending alerts`}
      >
        {hasUnread ? (
          <Bell className="size-4" />
        ) : (
          <BellOff className="size-4 text-muted-foreground" />
        )}
        {hasUnread && (
          <span
            className={cn(
              "absolute -top-1 -right-1 inline-flex items-center justify-center rounded-full",
              "min-w-[18px] h-[18px] px-1 text-[10px] font-semibold tabular-nums leading-none",
              "border border-background",
              worstSev === "critical" && "bg-destructive text-destructive-foreground",
              worstSev === "warn" && "bg-amber-500 text-background",
              worstSev === "info" && "bg-muted-foreground text-background",
            )}
          >
            {count > 99 ? "99+" : count}
          </span>
        )}
      </button>

      {open && (
        <div
          className={cn(
            "absolute right-0 mt-2 w-[420px] max-w-[calc(100vw-2rem)]",
            "rounded-md border border-border bg-card shadow-lg z-50",
          )}
        >
          <div className="px-4 py-3 border-b flex items-center justify-between gap-2">
            <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Pending alerts
            </div>
            <span className="text-[11px] text-muted-foreground tabular-nums">
              {count} {count === 1 ? "alert" : "alerts"}
            </span>
          </div>

          {count === 0 ? (
            <div className="p-8 text-center text-sm text-muted-foreground">
              no pending alerts
            </div>
          ) : (
            <ul className="divide-y divide-border/60 max-h-[420px] overflow-auto">
              {alerts.map((a) => {
                const ts = a.created_at_utc ?? a.ts_utc
                const id = a.alert_id ?? a.id ?? `${ts ?? ""}-${a.rule_id ?? ""}`
                const msg = a.message ?? a.decision?.message ?? a.rule_id ?? "alert"
                const asset = a.asset ?? a.decision?.asset
                const ruleType = a.rule_type ?? a.decision?.rule_type
                return (
                  <li key={id} className="px-4 py-3 hover:bg-muted/30">
                    <div className="flex items-start gap-3">
                      <span
                        className={cn(
                          "size-1.5 rounded-full mt-1.5 shrink-0",
                          SEV_DOT[a.severity],
                        )}
                      />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-baseline justify-between gap-2">
                          <Badge
                            variant="outline"
                            className={cn(
                              "font-mono text-[10px] uppercase tracking-wider shrink-0",
                              SEV_STYLES[a.severity],
                            )}
                          >
                            {a.rule_id ?? ruleType ?? "alert"}
                          </Badge>
                          <span className="text-[10px] text-muted-foreground tabular-nums">
                            {fmtTsShort(ts)}
                          </span>
                        </div>
                        <p className="text-[12px] text-foreground/90 mt-1 break-words">
                          {msg}
                        </p>
                        {asset && (
                          <div className="mt-1 text-[10px] font-mono text-muted-foreground/80 uppercase tracking-wider">
                            {asset}
                          </div>
                        )}
                      </div>
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
