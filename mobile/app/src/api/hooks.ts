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
