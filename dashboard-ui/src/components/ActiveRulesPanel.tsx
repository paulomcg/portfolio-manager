import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Sliders, TrendingDown, Activity, ListCheck } from "lucide-react"
import { cn } from "@/lib/utils"
import type { RuleConfig, UniverseEntry } from "@/types"

interface ActiveRulesPanelProps {
  rules: RuleConfig[]
  universe: UniverseEntry[]
  strategyLoaded: boolean
}

const RULE_META: Record<
  string,
  { label: string; icon: typeof TrendingDown; tone: string; describe: (r: RuleConfig) => string }
> = {
  halt_on_drawdown: {
    label: "Halt on drawdown",
    icon: TrendingDown,
    tone: "border-destructive/40 text-destructive bg-destructive/5",
    describe: (r) =>
      `Stops the loop and exits every position when wallet drawdown from HWM exceeds ${pctStr(r.threshold_pct ?? r.threshold)}.`,
  },
  max_position_pct: {
    label: "Max position size",
    icon: Sliders,
    tone: "border-amber-500/40 text-amber-500 bg-amber-500/5",
    describe: (r) =>
      `Exits a position whenever its value exceeds ${pctStr(r.threshold_pct ?? r.threshold)} of total equity.`,
  },
  trailing_stop: {
    label: "Trailing stop",
    icon: Activity,
    tone: "border-accent/50 text-accent bg-accent/5",
    describe: (r) =>
      `Exits a position when it drops ${pctStr(r.threshold_pct ?? r.threshold)} from its per-position high-water mark.`,
  },
}

function pctStr(v: unknown): string {
  if (typeof v !== "number" || Number.isNaN(v)) return "—"
  // The YAML schema lets thresholds be fractional (0.10) or pct-ish (10).
  const pct = v < 1 ? v * 100 : v
  return `${pct.toFixed(pct >= 10 ? 0 : 1)}%`
}

export function ActiveRulesPanel({
  rules,
  universe,
  strategyLoaded,
}: ActiveRulesPanelProps) {
  return (
    <Card className="border-border bg-card py-0 shadow-none gap-0">
      <CardHeader className="px-5 py-4 border-b flex flex-row items-center justify-between gap-2 space-y-0">
        <CardTitle className="text-[11px] uppercase tracking-[0.08em] text-muted-foreground font-medium flex items-center gap-2">
          <ListCheck className="size-3.5" />
          Active rules
          <span className="ml-1 text-muted-foreground/60">{rules.length}</span>
        </CardTitle>
        <div className="flex items-center gap-2">
          <Badge
            variant="outline"
            className={cn(
              "font-mono text-[10px] uppercase tracking-wider",
              strategyLoaded
                ? "border-accent/40 text-accent"
                : "border-border text-muted-foreground",
            )}
          >
            strategy {strategyLoaded ? "loaded" : "—"}
          </Badge>
          <Badge
            variant="outline"
            className="font-mono text-[10px] uppercase tracking-wider"
          >
            universe {universe.length}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {rules.length === 0 ? (
          <div className="p-8 text-center text-sm text-muted-foreground">
            no rules loaded — start a watch session with{" "}
            <code className="font-mono text-[11px] bg-muted px-1.5 py-0.5 rounded">
              pm watch --config rules.yaml
            </code>
          </div>
        ) : (
          <ul className="divide-y divide-border/60">
            {rules.map((rule, i) => {
              const meta = RULE_META[rule.type as string] ?? {
                label: rule.type,
                icon: ListCheck,
                tone: "border-border text-muted-foreground bg-muted/30",
                describe: () => "custom rule — see config for details",
              }
              const Icon = meta.icon
              return (
                <li key={rule.id ?? `${rule.type}-${i}`} className="px-5 py-3">
                  <div className="flex items-start gap-3">
                    <div
                      className={cn(
                        "rounded-md border p-1.5 mt-0.5",
                        meta.tone,
                      )}
                    >
                      <Icon className="size-3.5" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-baseline justify-between gap-2">
                        <div className="font-medium text-sm">
                          {meta.label}
                        </div>
                        <Badge
                          variant="outline"
                          className="font-mono text-[10px]"
                        >
                          {rule.id ?? rule.type}
                        </Badge>
                      </div>
                      <p className="text-[12px] text-muted-foreground mt-1">
                        {meta.describe(rule)}
                      </p>
                      {rule.scope && (
                        <div className="mt-1.5 text-[10px] text-muted-foreground/70 font-mono uppercase tracking-wider">
                          scope: {rule.scope}
                        </div>
                      )}
                    </div>
                  </div>
                </li>
              )
            })}
          </ul>
        )}

        {universe.length > 0 && (
          <div className="px-5 py-3 border-t border-border/60">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-2">
              universe
            </div>
            <div className="flex flex-wrap gap-1.5">
              {universe.map((u) => (
                <Badge
                  key={`${u.symbol}-${u.address ?? ""}`}
                  variant="secondary"
                  className="font-mono text-[10px] uppercase tracking-wider"
                >
                  {u.symbol}
                  {u.chain && (
                    <span className="ml-1.5 text-muted-foreground/80 normal-case">
                      · {u.chain}
                    </span>
                  )}
                </Badge>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
