/**
 * Wire types — keep in lockstep with `agent/tradingagents_us/schemas.py`
 * and `agent/api/routes/orders.py:OrderListItem`. Phase 5+1 will generate
 * these from the FastAPI OpenAPI schema via openapi-typescript.
 */

export type Rating = 'Buy' | 'Overweight' | 'Hold' | 'Underweight' | 'Sell';
export type Side = 'BUY' | 'SELL';
export type OrderStatus =
  | 'PENDING'
  | 'ACCEPTED'
  | 'PARTIAL'
  | 'FILLED'
  | 'REJECTED'
  | 'CANCELLED'
  | 'NEEDS_RECONCILE';
export type KillSwitchState = 'RUN' | 'PAUSE_NEW' | 'FLATTEN_ALL';

export interface AgentReasoning {
  agent: string;
  model: string;
  summary: string;
  tokens_in: number;
  tokens_out: number;
  latency_ms: number;
}

export interface AgentDecision {
  ticker: string;
  market: 'US';
  quote_currency: 'USD';
  rating: Rating;
  entry_price: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  price_target: number | null;
  time_horizon: string | null;
  suggested_size_pct: number;
  reasoning: AgentReasoning[];
  debate_transcript: Record<string, string>;
  final_decision_text: string | null;
  timestamp_utc: string;
  decision_id: string;
}

export interface Bar {
  t: string;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface PriceSeries {
  ticker: string;
  bars: Bar[];
  first: number | null;
  last: number | null;
  change_pct: number | null;
}

export interface EvalGate {
  name: string;
  passed: boolean | null;
  detail: string;
}

export interface EvalResult {
  verdict: 'GO' | 'NO-GO' | 'TOO EARLY';
  provisional_verdict: 'GO' | 'NO-GO' | null;
  reasons: string[];
  days: number;
  days_required: number;
  days_remaining: number;
  eval_complete: boolean;
  total_return_pct: number;
  sharpe: number;
  sortino: number;
  max_dd_pct: number;
  calmar: number;
  spy_return_pct: number | null;
  gate_sharpe: number;
  gate_max_dd_pct: number;
  gates: EvalGate[];
}

export type AnalyzeStatus = 'queued' | 'running' | 'done' | 'error';

export interface AnalyzeJob {
  job_id: string;
  ticker: string;
  status: AnalyzeStatus;
  decision: AgentDecision | null;
  error: string | null;
  created_utc: string;
  finished_utc: string | null;
}

export interface Position {
  ticker: string;
  market: 'US';
  quantity: number;
  avg_entry_price: number;
  current_price: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  stop_loss: number;
  sector: string | null;
  opened_at_utc: string;
}

export interface EquityPoint {
  date: string;
  equity: number;
  return_pct: number;
  drawdown_pct: number;
}

export interface EquityHistory {
  period: string;
  days: number;
  start_equity: number;
  end_equity: number;
  total_return_pct: number;
  max_drawdown_pct: number;
  points: EquityPoint[];
}

export interface PortfolioSnapshot {
  user_id: string;
  cash_usd: number;
  positions: Position[];
  total_equity_usd: number;
  daily_pnl_usd: number;
  daily_pnl_pct: number;
  max_drawdown_today: number;
  timestamp_utc: string;
}

export interface ConcentrationTrendPoint {
  ts: string;
  n_positions: number;
  top_weight_pct: number;
  equity: number;
}

export interface Concentration {
  n_positions: number;
  gross_exposure_pct: number;
  cash_pct: number;
  top_weight_pct: number;
  top3_weight_pct: number;
  hhi: number;
  effective_n: number;
  flags: string[];
  trend: ConcentrationTrendPoint[];
}

export type TradingMode = 'paper' | 'live';

export interface Health {
  status: string;
  trading_mode: TradingMode;
}

export interface Readiness {
  status: 'ok' | 'degraded';
  alpaca: boolean;
  db: boolean;
  trading_mode: TradingMode;
}

export interface OrderListItem {
  order_id: string;
  decision_id: string;
  ticker: string;
  side: Side;
  quantity: number;
  order_type: string;
  stop_loss: number;
  risk_approved: boolean;
  rejection_reasons: string[];
  broker_order_id: string | null;
  broker_status: string | null;
  filled_qty: number;
  avg_fill_price: number | null;
  submitted_at_utc: string;
}

/** One closed round trip from the FIFO fill ledger (GET /v1/trades). */
export interface ClosedTrade {
  trade_id: string;
  ticker: string;
  direction: 'LONG' | 'SHORT';
  quantity: number;
  entry_price: number;
  exit_price: number;
  realized_pnl: number;
  /** Fraction, not percent: 0.1729 = +17.29%. */
  realized_pnl_pct: number;
  holding_days: number;
  opened_at_utc: string;
  closed_at_utc: string;
}

/**
 * Stats over exactly the trades returned by the same call (a ticker filter
 * yields that name's record, not the account's).
 */
export interface TradeStats {
  trades: number;
  wins: number;
  losses: number;
  scratches: number;
  /** Fraction, not percent. */
  win_rate: number;
  gross_profit: number;
  gross_loss: number;
  net_pnl: number;
  avg_win: number;
  avg_loss: number;
  /** null while nothing has lost yet — an undefined ratio, never ∞. */
  profit_factor: number | null;
  expectancy: number;
  avg_holding_days: number;
  best_trade: number;
  worst_trade: number;
}

export interface TradesResponse {
  trades: ClosedTrade[];
  stats: TradeStats;
  /** null = the reconcile job has never run; show that, not a flat zero. */
  reconciled_at_utc: string | null;
  /**
   * 'eval' = only round trips ENTERED after the eval cutoff, matching what the
   * scorecard measures. 'all_time' = the full history. The card must label
   * which one it is showing.
   */
  window: 'eval' | 'all_time';
  /** The cutoff itself; null when the deployment configures no window. */
  eval_start_utc: string | null;
  /** How many round trips the cutoff hides (reported in both windows). */
  excluded_pre_eval: number;
}
