import { useEffect, useMemo } from "react"
import { DashboardHeader } from "@/components/DashboardHeader"
import { HeroMetrics } from "@/components/HeroMetrics"
import { KillSwitchPanel } from "@/components/KillSwitchPanel"
import { ActiveRulesPanel } from "@/components/ActiveRulesPanel"
import { EquityChart } from "@/components/EquityChart"
import { PositionsPanel } from "@/components/PositionsPanel"
import { AlertsPanel } from "@/components/AlertsPanel"
import { AuditPanel } from "@/components/AuditPanel"
import { TradesPanel } from "@/components/TradesPanel"
import { useFills } from "@/hooks/useFills"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { TriangleAlert, LineChart as LineChartIcon } from "lucide-react"
import { useDashboardState } from "@/hooks/useDashboardState"

export default function App() {
  useEffect(() => {
    document.documentElement.classList.add("dark")
  }, [])

  const wallet =
    typeof window !== "undefined"
      ? new URLSearchParams(window.location.search).get("wallet")
      : null

  const { snapshot, equity, conn, error } = useDashboardState(wallet)

  // Refetch fills whenever a new snapshot arrives (cheapest "something
  // happened" signal — the SSE pump bumps the snapshot reference).
  const refreshKey = useMemo(() => snapshot?.state?.last_cycle?.cycle_id ?? 0, [snapshot])
  const { fills } = useFills(wallet, refreshKey as unknown as number, 100)

  const positions = useMemo(
    () => snapshot?.state?.last_cycle?.positions?.positions ?? [],
    [snapshot],
  )

  if (error && !snapshot) {
    return (
      <div className="min-h-screen bg-background text-foreground grid place-items-center p-6">
        <div className="max-w-md w-full">
          <Alert variant="destructive">
            <TriangleAlert className="size-4" />
            <AlertTitle>Dashboard unreachable</AlertTitle>
            <AlertDescription className="space-y-2">
              <div>{error}</div>
              <div className="text-xs opacity-80">
                The PM dashboard server runs at{" "}
                <code className="font-mono">
                  http://127.0.0.1:7777
                </code>
                . Start it with{" "}
                <code className="font-mono">pm dashboard</code>.
              </div>
            </AlertDescription>
          </Alert>
        </div>
      </div>
    )
  }

  const state = snapshot?.state
  const safety = snapshot?.safety
  const metrics = snapshot?.metrics?.metrics ?? ({} as any)
  const cycleCount = snapshot?.metrics?.cycle_count ?? 0
  const alerts = snapshot?.alerts_pending?.alerts ?? []
  const auditRows = snapshot?.audit?.rows ?? []
  const cash = state?.last_cycle?.positions?.cash_usd
  const totalEquity = state?.last_cycle?.positions?.total_equity_usd

  return (
    <div className="min-h-screen bg-background text-foreground">
      <DashboardHeader
        conn={conn}
        wallet={snapshot?.wallet ?? null}
        servedAtUtc={snapshot?.served_at_utc}
        mode={safety?.mode ?? null}
      />

      <main className="mx-auto max-w-[1400px] px-6 py-6 space-y-6">
        {state?.warning && (
          <Alert className="border-amber-500/40 bg-amber-500/10">
            <TriangleAlert className="size-4 text-amber-500" />
            <AlertTitle>No audit data yet</AlertTitle>
            <AlertDescription>{state.warning}</AlertDescription>
          </Alert>
        )}

        <HeroMetrics
          state={state ?? ({} as any)}
          metrics={metrics}
          cycleCount={cycleCount}
        />

        <div className="grid gap-6 lg:grid-cols-2">
          {safety && <KillSwitchPanel ks={safety.kill_switch} />}
          {safety && (
            <ActiveRulesPanel
              rules={safety.rules}
              universe={safety.universe}
              strategyLoaded={safety.strategy_loaded}
            />
          )}
        </div>

        <Card className="border-border bg-card py-0 shadow-none gap-0">
          <CardHeader className="px-5 py-4 border-b flex flex-row items-center justify-between gap-2 space-y-0">
            <CardTitle className="text-[11px] uppercase tracking-[0.08em] text-muted-foreground font-medium flex items-center gap-2">
              <LineChartIcon className="size-3.5" />
              Equity curve
            </CardTitle>
            <div
              className="text-[10px] text-muted-foreground tabular-nums"
              title="One data point per pm watch iteration"
            >
              {equity?.count ?? 0} updates
            </div>
          </CardHeader>
          <CardContent className="px-3 pt-4 pb-3">
            <EquityChart
              data={equity?.series ?? []}
              initialEquity={metrics.initial_equity_usd}
            />
          </CardContent>
        </Card>

        <PositionsPanel
          positions={positions}
          cashUsd={cash}
          totalEquityUsd={totalEquity}
        />

        <TradesPanel fills={fills} />

        <div className="grid gap-6 lg:grid-cols-2">
          <AlertsPanel alerts={alerts} />
          <AuditPanel rows={auditRows} />
        </div>

        <footer className="pt-6 pb-12 text-center text-[11px] text-muted-foreground">
          read-only · localhost · SSE updates ·{" "}
          <a
            href="/api/snapshot"
            target="_blank"
            rel="noreferrer"
            className="text-accent hover:underline"
          >
            /api/snapshot
          </a>
        </footer>
      </main>
    </div>
  )
}
