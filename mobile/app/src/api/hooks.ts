/**
 * TanStack Query hooks for the trading backend.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from './endpoints';

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

export function useKillSwitch() {
  return useQuery({
    queryKey: ['orders', 'kill-switch'],
    queryFn: api.getKillSwitch,
    refetchInterval: 30_000,
  });
}
