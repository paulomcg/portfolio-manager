/** Currency / number / pct / duration formatters used across the report UI. */

const usdFmt = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

const usdFmtCompact = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
})

const decimalFmt2 = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

const decimalFmt4 = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 4,
  maximumFractionDigits: 4,
})

export function fmtUsd(n: number | null | undefined, compact = false): string {
  if (n == null || Number.isNaN(n)) return "—"
  return compact ? usdFmtCompact.format(n) : usdFmt.format(n)
}

export function fmtUsdSigned(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—"
  const formatted = usdFmt.format(Math.abs(n))
  return n >= 0 ? `+${formatted}` : `−${formatted}`
}

export function fmtPct(
  n: number | null | undefined,
  digits = 2,
  signed = false,
): string {
  if (n == null || Number.isNaN(n)) return "—"
  const fmt = new Intl.NumberFormat("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
  const v = fmt.format(Math.abs(n))
  if (!signed) return `${n.toFixed(digits)}%`
  return n >= 0 ? `+${v}%` : `−${v}%`
}

export function fmtNum(n: number | null | undefined, digits = 4): string {
  if (n == null || Number.isNaN(n)) return "—"
  return digits === 2 ? decimalFmt2.format(n) : decimalFmt4.format(n)
}

export function fmtQty(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—"
  if (Math.abs(n) >= 1) return decimalFmt4.format(n)
  // Crypto quantities can be tiny; show up to 8 places for small qtys.
  return n.toLocaleString("en-US", { maximumFractionDigits: 8 })
}

export function fmtTs(iso: string | null | undefined): string {
  // Guard against null/undefined/empty + "Invalid Date" which
  // toLocaleString silently emits when the constructor fails (no
  // exception thrown — that's why try/catch alone didn't help).
  if (!iso) return "—"
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return "—"
  return d.toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })
}

export function fmtTsShort(iso: string | null | undefined): string {
  if (!iso) return "—"
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return "—"
  return d.toLocaleString("en-US", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })
}

export function fmtDuration(startIso: string, endIso: string): string {
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime()
  if (!Number.isFinite(ms) || ms <= 0) return "—"
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.round(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  const remM = m % 60
  if (h < 24) return remM ? `${h}h ${remM}m` : `${h}h`
  const d = Math.floor(h / 24)
  const remH = h % 24
  return remH ? `${d}d ${remH}h` : `${d}d`
}

export function shortAddr(addr: string | null | undefined, n = 4): string {
  if (!addr) return "—"
  if (addr.length <= n * 2 + 2) return addr
  return `${addr.slice(0, n)}…${addr.slice(-n)}`
}

export function deltaClass(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n) || n === 0) return "text-muted-foreground"
  return n > 0 ? "text-positive" : "text-destructive"
}
