/**
 * TanStack Query hooks for the trading backend.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from './endpoints';
import type { KillSwitchState } from './types';

const REFETCH_INTERVAL_MS = 10_000;

export function usePortfolio() {
  return useQuery({
    queryKey: ['portfolio', 'snapshot'],
    queryFn: api.getPortfolio,
    refetchInterval: REFETCH_INTERVAL_MS,
    staleTime: 5_000,
  });
}

export function usePortfolioHistory(period = '1M') {
  return useQuery({
    queryKey: ['portfolio', 'history', period],
    queryFn: () => api.getPortfolioHistory(period),
    refetchInterval: REFETCH_INTERVAL_MS * 6, // the daily curve barely moves intraday
    staleTime: 30_000,
    retry: false,
  });
}

export function useConcentration() {
  return useQuery({
    queryKey: ['portfolio', 'concentration'],
    queryFn: api.getConcentration,
    refetchInterval: REFETCH_INTERVAL_MS * 3, // concentration shifts with fills, not ticks
    staleTime: 15_000,
    retry: false,
  });
}

/**
 * Realized round trips. The ledger is rebuilt by an hourly timer, so polling
 * faster than that only burns requests — refetch on the same cadence as the
 * daily equity curve and let `reconciled_at_utc` carry the freshness.
 */
export function useTrades(params?: { ticker?: string; limit?: number }) {
  return useQuery({
    queryKey: ['trades', params],
    queryFn: () => api.getTrades(params),
    refetchInterval: REFETCH_INTERVAL_MS * 6,
    staleTime: 30_000,
    retry: false,
  });
}

/**
 * Order-flow health. Derived from order rows written once per daily run, so it
 * changes at most daily — poll on the slow cadence and never let a failure
 * take a screen down (`retry: false`, callers render the card only when data
 * is present).
 */
export function useActionability(days = 30) {
  return useQuery({
    queryKey: ['diagnostics', 'actionability', days],
    queryFn: () => api.getActionability(days),
    refetchInterval: REFETCH_INTERVAL_MS * 6,
    staleTime: 60_000,
    retry: false,
  });
}

export function useDecisions(params?: { ticker?: string; limit?: number }) {
  return useQuery({
    queryKey: ['agents', 'decisions', params],
    queryFn: () => api.listDecisions(params),
    refetchInterval: REFETCH_INTERVAL_MS * 3, // decisions move slower than portfolio
    staleTime: 15_000,
  });
}

export function useOrders() {
  return useQuery({
    queryKey: ['orders'],
    queryFn: api.listOrders,
    refetchInterval: REFETCH_INTERVAL_MS,
  });
}

export function usePendingOrders() {
  return useQuery({
    queryKey: ['orders', 'pending'],
    queryFn: api.listPendingOrders,
    refetchInterval: REFETCH_INTERVAL_MS,
  });
}

export function useApproveOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (orderId: string) => api.approveOrder(orderId),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['orders'] });
      qc.invalidateQueries({ queryKey: ['portfolio'] });
    },
  });
}

export function useRejectOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (orderId: string) => api.rejectOrder(orderId),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['orders'] });
    },
  });
}

/**
 * Cancel an order that is already at the broker. Invalidates orders *and*
 * portfolio: a cancel that loses the race to a fill changes the book, and the
 * refetched broker status is what tells the user which way it went.
 */
export function useCancelOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (orderId: string) => api.cancelOrder(orderId),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['orders'] });
      qc.invalidateQueries({ queryKey: ['portfolio'] });
    },
  });
}

export function usePrices(ticker: string | null, days = 60) {
  return useQuery({
    queryKey: ['prices', ticker, days],
    queryFn: () => api.getPrices(ticker as string, days),
    enabled: !!ticker,
    staleTime: 5 * 60_000, // bars don't move faster than the 5-min server cache
  });
}

export function useStartAnalysis() {
  return useMutation({
    mutationFn: (ticker: string) => api.startAnalysis(ticker),
  });
}

/**
 * Polls an analysis job until it finishes. Pass `null` to disable.
 * The pipeline takes minutes, so we poll every 3s and stop once the
 * job reaches a terminal state.
 */
export function useAnalysisJob(jobId: string | null) {
  return useQuery({
    queryKey: ['analyze', jobId],
    queryFn: () => api.getAnalysisJob(jobId as string),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'done' || status === 'error' ? false : 3_000;
    },
  });
}

export function useKillSwitch() {
  return useQuery({
    queryKey: ['orders', 'kill-switch'],
    queryFn: api.getKillSwitch,
    refetchInterval: 30_000,
  });
}

export function useSetKillSwitch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (state: KillSwitchState) => api.setKillSwitch(state),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['orders', 'kill-switch'] });
    },
  });
}

export function useEval(period = '1M') {
  return useQuery({
    queryKey: ['eval', period],
    queryFn: () => api.getEval(period),
    refetchInterval: 5 * 60_000,
    retry: false,
  });
}

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: api.health,
    refetchInterval: 30_000,
    retry: false,
  });
}

/** Broker/DB reachability + paper|live mode for the global status banner. */
export function useReadiness() {
  return useQuery({
    queryKey: ['readiness'],
    queryFn: api.readiness,
    refetchInterval: 30_000,
    retry: false,
  });
}
