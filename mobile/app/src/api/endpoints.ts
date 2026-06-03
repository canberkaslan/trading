/**
 * Typed wrappers over the FastAPI backend.
 */

import { apiClient } from './client';
import type {
  AgentDecision,
  KillSwitchState,
  OrderListItem,
  PortfolioSnapshot,
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

  // Orders
  listOrders: () => apiClient.get('v1/orders').json<OrderListItem[]>(),
  cancelOrder: (orderId: string) =>
    apiClient.post(`v1/orders/${orderId}/cancel`).json<{ status: string }>(),

  // Kill switch
  getKillSwitch: () =>
    apiClient.get('v1/orders/kill-switch').json<{ state: KillSwitchState }>(),
  setKillSwitch: (state: KillSwitchState) =>
    apiClient
      .post('v1/orders/kill-switch', { json: { state } })
      .json<{ state: KillSwitchState }>(),

  // Health
  health: () => apiClient.get('healthz').json<{ status: string }>(),
};
