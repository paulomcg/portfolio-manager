import { useEffect, useRef, useState, useCallback } from "react"
import type { EquityPayload, SnapshotPayload } from "@/types"

export type ConnState = "connecting" | "live" | "offline"

interface DashboardState {
  snapshot: SnapshotPayload | null
  equity: EquityPayload | null
  conn: ConnState
  lastEvent: string | null
  error: string | null
  refetch: () => void
}

/** Fetches /api/snapshot + /api/equity, subscribes to /events for live updates. */
export function useDashboardState(wallet?: string | null): DashboardState {
  const [snapshot, setSnapshot] = useState<SnapshotPayload | null>(null)
  const [equity, setEquity] = useState<EquityPayload | null>(null)
  const [conn, setConn] = useState<ConnState>("connecting")
  const [lastEvent, setLastEvent] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const esRef = useRef<EventSource | null>(null)

  const fetchAll = useCallback(async () => {
    try {
      const q = wallet ? `?wallet=${encodeURIComponent(wallet)}` : ""
      const [sRes, eRes] = await Promise.all([
        fetch(`/api/snapshot${q}`),
        fetch(`/api/equity${q}`),
      ])
      if (!sRes.ok) throw new Error(`snapshot ${sRes.status}`)
      if (!eRes.ok) throw new Error(`equity ${eRes.status}`)
      const sJson: SnapshotPayload = await sRes.json()
      const eJson: EquityPayload = await eRes.json()
      setSnapshot(sJson)
      setEquity(eJson)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [wallet])

  // Initial fetch + on wallet change.
  useEffect(() => {
    void fetchAll()
  }, [fetchAll])

  // SSE subscription.
  useEffect(() => {
    const url = "/events"
    setConn("connecting")
    const es = new EventSource(url)
    esRef.current = es

    es.addEventListener("hello", () => {
      setConn("live")
    })
    es.addEventListener("cycle", (ev: any) => {
      setLastEvent("cycle")
      try {
        const j = JSON.parse(ev.data)
        setLastEvent(`cycle @ ${j.ts}`)
      } catch {}
      void fetchAll()
    })
    es.addEventListener("alert", () => {
      setLastEvent("alert")
      void fetchAll()
    })
    es.addEventListener("ping", () => {
      setConn("live")
    })
    es.onerror = () => {
      setConn("offline")
    }

    return () => {
      es.close()
      esRef.current = null
    }
  }, [fetchAll])

  return { snapshot, equity, conn, lastEvent, error, refetch: fetchAll }
}
