/**
 * Polls /api/fills and returns the recent trade history. The dashboard
 * SSE pump invalidates the cache when a new cycle lands; we refetch on
 * snapshot updates (cheapest signal of "something happened").
 */
import { useEffect, useState } from "react"

export interface FillRow {
  ts_utc: string | null
  cycle_index: number | null
  action: "buy" | "sell" | "exit" | "trim" | string | null
  asset: string | null
  qty_swapped: number | null
  fill_price_usd: number | null
  gross_proceeds_usd: number | null
  fees_usd: number | null
  slippage_usd: number | null
  realized_pnl_usd: number | null
  tx_hash: string | null
  executor: string | null
  source: "strategy" | "rule" | null
}

interface FillsPayload {
  ok: boolean
  schema_version: string
  count: number
  fills: FillRow[]
}

export function useFills(
  wallet: string | null,
  refreshKey: number,
  limit = 100,
): { fills: FillRow[]; count: number; error: string | null } {
  const [state, setState] = useState<{
    fills: FillRow[]
    count: number
    error: string | null
  }>({ fills: [], count: 0, error: null })

  useEffect(() => {
    let cancelled = false
    const url = new URL("/api/fills", window.location.origin)
    if (wallet) url.searchParams.set("wallet", wallet)
    url.searchParams.set("limit", String(limit))

    fetch(url.toString())
      .then((r) => r.json() as Promise<FillsPayload>)
      .then((data) => {
        if (cancelled) return
        if (!data.ok) {
          setState({ fills: [], count: 0, error: "fetch_failed" })
          return
        }
        setState({
          fills: data.fills || [],
          count: data.count || 0,
          error: null,
        })
      })
      .catch((e) => {
        if (cancelled) return
        setState({ fills: [], count: 0, error: String(e) })
      })

    return () => {
      cancelled = true
    }
  }, [wallet, refreshKey, limit])

  return state
}
