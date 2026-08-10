/**
 * Realized-vs-unrealized presentation helpers — pure, unit-tested, RN-free.
 *
 * Why this exists: every number the app showed until now (hero equity, daily
 * P&L, the eval scorecard) is *mark-to-market on open positions*. The realized
 * ledger (GET /v1/trades, derived from the broker's fill feed) answers a
 * different question — of the round trips that actually closed, how many paid?
 * On this book the two disagree sharply, so the card renders them side by side
 * instead of letting one stand for performance.
 *
 * Tone rules are deliberately conservative:
 *  - Below {@link MIN_SAMPLE} closed trades nothing is colored green/red. A 3-trade
 *    100% win rate is noise, and coloring it green is the app asserting an edge
 *    the data cannot support.
 *  - `profit_factor: null` (no losses yet) renders as an em dash, never as ∞ or
 *    as a very large number — same reason.
 */

import type { Position, TradeStats } from '@/api/types';

export type Tone = 'up' | 'down' | 'neutral';

/**
 * Minimum closed round trips before win rate / expectancy get a directional
 * color. Thirty is the usual rule-of-thumb floor for a mean estimate to mean
 * anything; below it the card stays neutral and says so.
 */
export const MIN_SAMPLE = 30;

const EM_DASH = '—';

/** Sum of unrealized P&L across open positions (the mark-to-market half). */
export function unrealizedTotal(positions: Position[] | null | undefined): number {
  if (!positions?.length) return 0;
  return positions.reduce((acc, p) => acc + (Number.isFinite(p.unrealized_pnl) ? p.unrealized_pnl : 0), 0);
}

/** Sign → tone, with a hard neutral for exactly flat. */
export function pnlTone(value: number | null | undefined): Tone {
  if (value == null || !Number.isFinite(value) || value === 0) return 'neutral';
  return value > 0 ? 'up' : 'down';
}

/**
 * Tone for a statistic that only means something on a large enough sample.
 * Under {@link MIN_SAMPLE} trades the value still renders — it just isn't
 * colored as if it were a verdict.
 */
export function sampleTone(value: number | null | undefined, trades: number): Tone {
  if (trades < MIN_SAMPLE) return 'neutral';
  return pnlTone(value);
}

/** Win rate 0..1 → "26.7%". Null-safe; 0 trades renders as an em dash. */
export function formatWinRate(stats: Pick<TradeStats, 'win_rate' | 'trades'> | null | undefined): string {
  if (!stats || stats.trades === 0) return EM_DASH;
  return `${(stats.win_rate * 100).toFixed(1)}%`;
}

/**
 * Profit factor display. `null` means no losing trade has closed yet, so the
 * ratio is undefined — show an em dash, not "∞" and not a fabricated ceiling.
 */
export function formatProfitFactor(pf: number | null | undefined): string {
  if (pf == null || !Number.isFinite(pf)) return EM_DASH;
  return pf.toFixed(2);
}

export interface SplitShare {
  realized: number;
  unrealized: number;
  total: number;
  /** Realized share of total P&L, 0..1. 0 when the total is flat. */
  realizedShare: number;
}

/**
 * Decompose total P&L into its realized and unrealized halves.
 *
 * `realizedShare` uses the sum of absolute magnitudes, not the net total: with
 * realized −$532 against unrealized +$9,500 a net-denominated share would read
 * "−5% realized", which is meaningless. Magnitude-denominated says what the
 * card actually claims — how much of the P&L *activity* has been banked.
 */
export function pnlSplit(realized: number, unrealized: number): SplitShare {
  const magnitude = Math.abs(realized) + Math.abs(unrealized);
  return {
    realized,
    unrealized,
    total: realized + unrealized,
    realizedShare: magnitude === 0 ? 0 : Math.abs(realized) / magnitude,
  };
}

export interface Freshness {
  /** Short TR label, e.g. "3 sa önce". */
  label: string;
  /** True when the hourly reconcile has clearly not run (or never ran). */
  stale: boolean;
}

/** Reconcile runs hourly; past this the ledger is behind the broker. */
const STALE_AFTER_MS = 3 * 60 * 60 * 1000;

/**
 * How fresh the ledger is. The endpoint deliberately does NOT reconcile on
 * request, so a stale timer must be visible rather than silently rendering
 * yesterday's numbers as current.
 */
export function reconcileFreshness(
  reconciledAtUtc: string | null | undefined,
  now: Date,
): Freshness {
  if (!reconciledAtUtc) return { label: 'hiç çalışmadı', stale: true };
  const then = new Date(reconciledAtUtc.endsWith('Z') ? reconciledAtUtc : `${reconciledAtUtc}Z`);
  const ms = now.getTime() - then.getTime();
  if (!Number.isFinite(ms)) return { label: 'bilinmiyor', stale: true };
  const stale = ms > STALE_AFTER_MS;
  if (ms < 60_000) return { label: 'az önce', stale };
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 60) return { label: `${minutes} dk önce`, stale };
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return { label: `${hours} sa önce`, stale };
  return { label: `${Math.floor(hours / 24)} gün önce`, stale };
}

/**
 * One-line honest reading of the ledger for the card footer. Ordered so the
 * caveat that most changes the interpretation comes first.
 */
export function realizedCaveat(
  stats: Pick<TradeStats, 'trades' | 'net_pnl'> | null | undefined,
  unrealized: number,
): string | null {
  if (!stats || stats.trades === 0) return 'Henüz kapanan işlem yok — tüm P&L açık pozisyonlarda.';
  if (stats.trades < MIN_SAMPLE) {
    return `${stats.trades} kapanan işlem — istatistiksel olarak anlamlı değil (≥${MIN_SAMPLE} gerekir).`;
  }
  if (stats.net_pnl < 0 && unrealized > 0) {
    return 'Kapanan işlemler net zararda; raporlanan kazancın tamamı açık pozisyonların değerlemesinden geliyor.';
  }
  return null;
}
