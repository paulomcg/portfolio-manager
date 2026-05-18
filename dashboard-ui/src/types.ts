/** Mirrors the shape returned by PM's dashboard /api/* endpoints. */

export interface Position {
  asset: string
  chain?: string | null
  address?: string | null
  qty: number
  mark_price_usd: number
  value_usd: number
  cost_basis_usd?: number
  avg_entry_price_usd?: number
  unrealized_pnl_usd?: number
  hwm_usd?: number
  drawdown_from_hwm_pct?: number
}

export interface CycleState {
  cycle_id: string
  cycle_index: number
  ts_utc: string
  mode: "monitor" | "live"
  wallet: string
  positions?: {
    total_equity_usd?: number
    cash_usd?: number
    positions?: Position[]
  }
  decisions?: any[]
  fills?: any[]
  errors?: any[]
  alerts_emitted?: any[]
}

export interface StatePayload {
  ok: boolean
  schema_version: string
  served_at_utc: string
  wallet: string | null
  last_cycle: CycleState | null
  manual_overrides?: Record<string, unknown>
  high_water_marks?: Record<string, number>
  warning?: string
}

export interface AlertRow {
  id: number
  ts_utc: string
  rule_id?: string
  rule_type?: string
  severity: "info" | "warn" | "critical"
  asset?: string | null
  message: string
  acked?: boolean
}

export interface AuditRow {
  event: string
  ts_utc: string
  wallet?: string
  cycle_index?: number
  positions?: { total_equity_usd?: number }
  fills?: any[]
  decisions?: any[]
  errors?: any[]
  [k: string]: unknown
}

export interface MetricsPayload {
  schema_version: string
  bars: number
  periods_per_year?: number
  initial_equity_usd?: number
  final_equity_usd?: number
  total_return_pct?: number
  cagr_pct?: number
  sharpe?: number
  sortino?: number
  calmar?: number
  max_drawdown_pct?: number
  trades?: {
    trades: number
    winners: number
    losers: number
    win_rate: number
    expectancy_usd: number
    total_pnl_usd: number
  }
  per_asset_pnl_usd?: Record<string, number>
  warning?: string
}

export type RuleType = "halt_on_drawdown" | "max_position_pct" | "trailing_stop"

export interface RuleConfig {
  id?: string
  type: RuleType | string
  threshold?: number
  threshold_pct?: number
  scope?: string
  enabled?: boolean
  [k: string]: unknown
}

export interface UniverseEntry {
  symbol: string
  chain?: string
  address?: string
}

export interface KillSwitchPayload {
  active: boolean
  max_loss_usd: number
  realized_loss_usd: number
  realized_gain_usd: number
  net_realized_usd: number
  percent_consumed: number
  status: "ok" | "warning" | "critical" | "halted"
  fills_since_start: number
  started_at_utc?: string | null
}

export interface SafetyPayload {
  ok: boolean
  schema_version: string
  served_at_utc: string
  wallet: string | null
  kill_switch: KillSwitchPayload
  rules: RuleConfig[]
  universe: UniverseEntry[]
  mode: "live" | "monitor" | null
  strategy_loaded: boolean
}

export interface SnapshotPayload {
  ok: boolean
  schema_version: string
  served_at_utc: string
  wallet: string | null
  state: StatePayload
  audit: { ok: boolean; count: number; rows: AuditRow[] }
  alerts_pending: { ok: boolean; count: number; alerts: AlertRow[] }
  metrics: { ok: boolean; metrics: MetricsPayload; cycle_count: number }
  safety: SafetyPayload
}

export interface EquityPoint {
  ts: string
  equity_usd: number
  drawdown_pct: number
}

export interface EquityPayload {
  ok: boolean
  count: number
  series: EquityPoint[]
  warning?: string
}
