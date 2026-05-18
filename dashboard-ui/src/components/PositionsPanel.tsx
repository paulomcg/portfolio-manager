import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { Wallet } from "lucide-react"
import { cn } from "@/lib/utils"
import type { Position } from "@/types"
import { fmtPct, fmtQty, fmtUsd, fmtUsdSigned } from "@/lib/format"

interface PositionsPanelProps {
  positions: Position[]
  cashUsd?: number
  totalEquityUsd?: number
}

export function PositionsPanel({
  positions,
  cashUsd,
  totalEquityUsd,
}: PositionsPanelProps) {
  const totalValue = positions.reduce((s, p) => s + (p.value_usd ?? 0), 0)
  const totalUnreal = positions.reduce(
    (s, p) => s + (p.unrealized_pnl_usd ?? 0),
    0,
  )

  return (
    <Card className="border-border bg-card py-0 shadow-none gap-0">
      <CardHeader className="px-5 py-4 border-b flex flex-row items-center justify-between gap-2 space-y-0">
        <CardTitle className="text-[11px] uppercase tracking-[0.08em] text-muted-foreground font-medium flex items-center gap-2">
          <Wallet className="size-3.5" />
          Positions <span className="ml-1 text-muted-foreground/60">{positions.length}</span>
        </CardTitle>
        {cashUsd != null && (
          <div className="text-[11px] text-muted-foreground tabular-nums">
            cash <span className="text-foreground font-medium">{fmtUsd(cashUsd)}</span>
            {totalEquityUsd != null && (
              <>
                {" · "}equity{" "}
                <span className="text-foreground font-medium">
                  {fmtUsd(totalEquityUsd)}
                </span>
              </>
            )}
          </div>
        )}
      </CardHeader>
      <CardContent className="p-0">
        {positions.length === 0 ? (
          <div className="p-8 text-center text-sm text-muted-foreground">
            no open positions
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium">Asset</TableHead>
                <TableHead className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium text-right">Qty</TableHead>
                <TableHead className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium text-right">Mark</TableHead>
                <TableHead className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium text-right">Value</TableHead>
                <TableHead className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium text-right">Cost</TableHead>
                <TableHead className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium text-right">Unreal. PnL</TableHead>
                <TableHead className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium text-right">DD from HWM</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {positions.map((p) => {
                const pnl = p.unrealized_pnl_usd ?? 0
                const dd = p.drawdown_from_hwm_pct ?? 0
                return (
                  <TableRow key={p.asset} className="border-border/60">
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-sm">{p.asset}</span>
                        {p.chain && (
                          <Badge
                            variant="outline"
                            className="font-mono text-[9px] uppercase tracking-wider"
                          >
                            {p.chain}
                          </Badge>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-sm">
                      {fmtQty(p.qty)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-sm">
                      {fmtUsd(p.mark_price_usd)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-sm font-medium">
                      {fmtUsd(p.value_usd)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-xs text-muted-foreground">
                      {p.cost_basis_usd != null ? fmtUsd(p.cost_basis_usd) : "—"}
                    </TableCell>
                    <TableCell
                      className={cn(
                        "text-right tabular-nums text-sm font-medium",
                        pnl > 0 && "text-positive",
                        pnl < 0 && "text-destructive",
                      )}
                    >
                      {fmtUsdSigned(pnl)}
                    </TableCell>
                    <TableCell
                      className={cn(
                        "text-right tabular-nums text-xs",
                        dd < -10 && "text-destructive",
                        dd < 0 && dd >= -10 && "text-amber-500",
                        dd >= 0 && "text-muted-foreground",
                      )}
                    >
                      {fmtPct(dd, 2)}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
        {positions.length > 0 && (
          <div className="px-5 py-3 border-t border-border/60 text-[11px] text-muted-foreground tabular-nums flex items-center justify-between">
            <span>
              total value:{" "}
              <span className="text-foreground font-medium">
                {fmtUsd(totalValue)}
              </span>
            </span>
            <span>
              total unrealized:{" "}
              <span
                className={cn(
                  "font-medium",
                  totalUnreal > 0 && "text-positive",
                  totalUnreal < 0 && "text-destructive",
                )}
              >
                {fmtUsdSigned(totalUnreal)}
              </span>
            </span>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
