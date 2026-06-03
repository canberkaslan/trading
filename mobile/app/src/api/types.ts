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
  | 'CANCELLED';
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
