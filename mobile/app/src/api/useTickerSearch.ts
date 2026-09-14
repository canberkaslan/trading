/**
 * Search the US listings while the operator types.
 *
 * Analysing any stock always worked — the endpoint never checked a universe.
 * What did not work was finding one: the Ask box is free text, so you had to
 * already know the symbol. For the eleven names in the daily run that is fine;
 * for the other twelve thousand it is the whole obstacle.
 *
 * Debounced, because this fires on a keystroke and the catalogue behind it is
 * a network call. 250ms is long enough that a typed word costs one request
 * rather than six, and short enough that the list does not feel late.
 *
 * Nothing is requested for a query shorter than two characters: a single
 * letter matches hundreds of symbols and none of them is the answer.
 */

import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/endpoints';

const DEBOUNCE_MS = 250;
const MIN_QUERY = 2;

export interface TickerHit {
  ticker: string;
  name: string;
}

export function useTickerSearch(query: string) {
  const [debounced, setDebounced] = useState(query);

  useEffect(() => {
    const id = setTimeout(() => setDebounced(query), DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [query]);

  const q = debounced.trim();
  return useQuery({
    queryKey: ['tickers', q],
    queryFn: () => api.searchTickers(q),
    enabled: q.length >= MIN_QUERY,
    // The listings do not change while someone types a word.
    staleTime: 5 * 60_000,
    retry: false,
  });
}


/**
 * The most traded US common shares, for picking when you do not have a symbol
 * in mind.
 *
 * Thirteen thousand symbols is not a list anyone reads. This is the previous
 * session's dollar-volume ranking with ETFs, warrants and units removed —
 * the names an operator would plausibly ask about, in the order the market
 * itself traded them.
 */
export function useBrowseTickers(enabled: boolean) {
  return useQuery({
    queryKey: ['tickers', 'browse'],
    queryFn: () => api.browseTickers(60),
    enabled,
    // The ranking is yesterday's session; it does not move while the app is open.
    staleTime: 60 * 60_000,
    retry: false,
  });
}
