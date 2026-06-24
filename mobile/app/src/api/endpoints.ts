/**
 * Typed wrappers over the FastAPI backend.
 */

import { apiClient } from './client';
import type {
  AgentDecision,
  AnalyzeJob,
  KillSwitchState,
  OrderListItem,
  PortfolioSnapshot,
  PriceSeries,
} from './types';

export const api = {
  // Portfolio
  getPortfolio: () => apiClient.get('v1/portfolio/snapshot').json<PortfolioSnapshot>(),

  // Agents
  listDecisions: (params?: { ticker?: string; limit?: number }) => {
    const searchParams = new URLSearchParams();
    if (params?.ticker) searchParams.set('ticker', params.ticker);
    if (params?.limit) searchParams.set('limit', String(params.limit));
    const qs = searchParams.toString();
    return apiClient
      .get(`v1/agents/decisions${qs ? `?${qs}` : ''}`)
      .json<AgentDecision[]>();
  },
  getDecision: (decisionId: string) =>
    apiClient.get(`v1/agents/decisions/${decisionId}`).json<AgentDecision>(),

  // Prices (Polygon daily bars, proxied so the key stays server-side)
  getPrices: (ticker: string, days = 60) =>
    apiClient.get(`v1/prices/${ticker}?days=${days}`).json<PriceSeries>(),

  // On-demand analysis (WarrenAI-style). startAnalysis queues the 7-agent
  // pipeline; poll getAnalysisJob until status is 'done'/'error'.
  startAnalysis: (ticker: string) =>
    apiClient.post('v1/analyze', { json: { ticker } }).json<AnalyzeJob>(),
  getAnalysisJob: (jobId: string) =>
    apiClient.get(`v1/analyze/${jobId}`).json<AnalyzeJob>(),

  // Orders
  listOrders: () => apiClient.get('v1/orders').json<OrderListItem[]>(),
  listPendingOrders: () => apiClient.get('v1/orders/pending').json<OrderListItem[]>(),
  approveOrder: (orderId: string) =>
    apiClient
      .post(`v1/orders/${orderId}/approve`)
      .json<{ order_id: string; broker_order_id: string; status: string }>(),
  rejectOrder: (orderId: string) =>
    apiClient
      .post(`v1/orders/${orderId}/reject`)
      .json<{ order_id: string; status: string }>(),
  cancelOrder: (orderId: string) =>
    apiClient.post(`v1/orders/${orderId}/cancel`).json<{ status: string }>(),

  // Kill switch
  getKillSwitch: () =>
    apiClient.get('v1/orders/kill-switch').json<{ state: KillSwitchState }>(),
  setKillSwitch: (state: KillSwitchState) =>
    apiClient
      .post('v1/orders/kill-switch', { json: { state } })
      .json<{ state: KillSwitchState }>(),

  // Notifications
  registerPushToken: (token: string, platform: 'ios' | 'android' | 'web') =>
    apiClient
      .post('v1/notifications/register', { json: { token, platform } })
      .json<{ status: string; token: string }>(),
  testNotification: () =>
    apiClient.post('v1/notifications/test').json<{ status: string; sent: number }>(),

  // Health
  health: () => apiClient.get('healthz').json<{ status: string }>(),
};
