/**
 * Recent fills (buy/sell/exit/trim) from the audit log. Source of
 * truth: /api/fills, which flattens fills out of `watch.cycle` audit
 * events. Most-recent first.
 */
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { ArrowDownLeft, ArrowUpRight, Repeat } from "lucide-react"
import { cn } from "@/lib/utils"
import type { FillRow } from "@/hooks/useFills"
import { fmtTsShort, fmtUsdSigned, fmtUsd, fmtQty } from "@/lib/format"

interface TradesPanelProps {
  fills: FillRow[]
}

const ACTION_STYLES: Record<string, string> = {
  buy: "border-emerald-500/40 text-emerald-500 bg-emerald-500/10",
  sell: "border-rose-500/40 text-rose-500 bg-rose-500/10",
  exit: "border-rose-500/40 text-rose-500 bg-rose-500/10",
  trim: "border-amber-500/40 text-amber-500 bg-amber-500/10",
}

function actionIcon(action: string | null) {
  if (action === "buy") return <ArrowDownLeft className="size-3.5" />
  if (action === "sell" || action === "exit") return <ArrowUpRight className="size-3.5" />
  return <Repeat className="size-3.5" />
}

export function TradesPanel({ fills }: TradesPanelProps) {
  return (
    <Card className="border-border bg-card py-0 shadow-none gap-0">
      <CardHeader className="px-5 py-4 border-b flex flex-row items-center justify-between gap-2 space-y-0">
        <CardTitle className="text-[11px] uppercase tracking-[0.08em] text-muted-foreground font-medium flex items-center gap-2">
          <Repeat className="size-3.5" />
          Trades
          <span className="ml-1 text-muted-foreground/60">{fills.length}</span>
        </CardTitle>
        <div className="text-[10px] text-muted-foreground">
          newest first · realized PnL only
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {fills.length === 0 ? (
          <div className="p-8 text-center text-sm text-muted-foreground">
            no fills yet
          </div>
        ) : (
          <div className="max-h-[420px] overflow-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-card border-b">
                <tr className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  <th className="text-left px-4 py-2 font-medium">when</th>
                  <th className="text-left px-2 py-2 font-medium">action</th>
                  <th className="text-left px-2 py-2 font-medium">asset</th>
                  <th className="text-right px-2 py-2 font-medium">qty</th>
                  <th className="text-right px-2 py-2 font-medium">fill px</th>
                  <th className="text-right px-2 py-2 font-medium">rpnl</th>
                  <th className="text-left px-4 py-2 font-medium">tx</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {fills.map((f, i) => {
                  const action = (f.action || "").toLowerCase()
                  const rpnl = f.realized_pnl_usd ?? 0
                  const tx = f.tx_hash || ""
                  return (
                    <tr key={`${f.ts_utc}-${i}`} className="hover:bg-muted/30">
                      <td className="px-4 py-2 text-muted-foreground tabular-nums whitespace-nowrap">
                        {fmtTsShort(f.ts_utc)}
                      </td>
                      <td className="px-2 py-2">
                        <Badge
                          variant="outline"
                          className={cn(
                            "font-mono text-[10px] uppercase tracking-wider gap-1",
                            ACTION_STYLES[action] || "border-border",
                          )}
                        >
                          {actionIcon(f.action)}
                          {f.action || "?"}
                        </Badge>
                      </td>
                      <td className="px-2 py-2 font-medium">{f.asset || "?"}</td>
                      <td className="px-2 py-2 text-right tabular-nums text-muted-foreground">
                        {fmtQty(f.qty_swapped ?? 0)}
                      </td>
                      <td className="px-2 py-2 text-right tabular-nums text-muted-foreground">
                        {f.fill_price_usd != null ? fmtUsd(f.fill_price_usd) : "—"}
                      </td>
                      <td
                        className={cn(
                          "px-2 py-2 text-right tabular-nums font-medium",
                          rpnl > 0 && "text-emerald-500",
                          rpnl < 0 && "text-rose-500",
                          rpnl === 0 && "text-muted-foreground",
                        )}
                      >
                        {rpnl !== 0 ? fmtUsdSigned(rpnl) : "—"}
                      </td>
                      <td className="px-4 py-2 font-mono text-[10px] text-muted-foreground">
                        {tx ? `${tx.slice(0, 6)}…${tx.slice(-4)}` : "—"}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
