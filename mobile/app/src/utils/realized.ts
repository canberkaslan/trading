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

import type { ExitBucket, Position, TradeStats, TradesResponse } from '@/api/types';
import { parseUtc, relativeAgeTr } from './format';

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
  const then = parseUtc(reconciledAtUtc);
  if (!then) return { label: 'bilinmiyor', stale: true };
  const ms = now.getTime() - then.getTime();
  return { label: relativeAgeTr(ms), stale: ms > STALE_AFTER_MS };
}

/**
 * Which slice of history the ledger is reporting.
 *
 * The backend scopes /v1/trades to the eval window by default (entries after
 * EVAL_START_DATE), the same cutoff the scorecard uses — before that the two
 * cards on this screen measured different books. Rows dropped by the cutoff
 * have to be named on a money screen: a record that silently shrank from 30
 * trades to 4 reads as a bug, or as cherry-picking.
 */
export function evalWindowNote(
  response:
    | Pick<TradesResponse, 'window' | 'eval_start_utc' | 'excluded_pre_eval'>
    | null
    | undefined,
): string | null {
  if (!response || response.window !== 'eval') return null;
  const excluded = response.excluded_pre_eval ?? 0;
  if (excluded <= 0) return null;
  const since = (response.eval_start_utc ?? '').slice(0, 10);
  const when = since ? ` (${since})` : '';
  return `Eval penceresi${when}: temiz kitap öncesi ${excluded} işlem hariç.`;
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

/* -------------------------------------------------------------------------- *
 * Exit attribution
 *
 * `stats` above blends every exit path into one expectancy. On this account
 * most of the realized ledger is the 2026-06-24 flatten that cleaned up the
 * accumulation bug — an operator action. Reading that number as "how well does
 * the agent exit?" scores a cleanup as strategy performance, which is exactly
 * the mistake the backend's `strategy` bucket exists to stop. These helpers
 * keep the two claims separate in the UI, and keep the narrower claim's sample
 * size attached to it.
 * -------------------------------------------------------------------------- */

/** TR copy for an exit class. Unknown classes fall through to the raw value. */
export function exitClassLabelTr(exitClass: string | null | undefined): string {
  switch (exitClass) {
    case 'take_profit':
      return 'Kâr al';
    case 'stop':
      return 'Koruyucu stop';
    case 'decision_sell':
      return 'Ajan satış kararı';
    case 'flatten':
      return 'Flatten (ajan dışı)';
    case 'unknown':
      return 'Bilinmiyor (emir silinmiş)';
    case 'strategy':
      return 'Stratejinin kendi çıkışları';
    default:
      return (exitClass ?? '').trim() || EM_DASH;
  }
}

/** Render order: the agent's own exits first, then what happened to it. */
const CLASS_ORDER = ['take_profit', 'stop', 'decision_sell', 'flatten', 'unknown'];

/**
 * The exit split, ordered for display and stripped of empty paths.
 *
 * Buckets with zero trades are dropped rather than rendered as flat rows: an
 * exit path the book has never taken is not a result, and a column of zeros
 * reads as one.
 */
export function exitBreakdown(
  response: Pick<TradesResponse, 'by_exit'> | null | undefined,
): ExitBucket[] {
  const rows = response?.by_exit;
  if (!rows?.length) return [];
  return rows
    .filter((b) => b.trades > 0)
    .slice()
    .sort((a, b) => {
      const ia = CLASS_ORDER.indexOf(a.exit_class);
      const ib = CLASS_ORDER.indexOf(b.exit_class);
      // Classes the app does not know about sort last, but still render.
      return (ia < 0 ? CLASS_ORDER.length : ia) - (ib < 0 ? CLASS_ORDER.length : ib);
    });
}

export type StrategyReadingStatus = 'unavailable' | 'none' | 'ok';

export interface StrategyReading {
  /**
   * 'unavailable' — this backend does not report the split yet (pre-attribution
   * deployment). 'none' — it reports one, but no returned row is attributed to
   * a strategy exit. 'ok' — there is a bucket to show.
   *
   * The first two must NOT render alike: "we cannot tell" and "the agent has
   * closed nothing itself" are different claims about the same book.
   */
  status: StrategyReadingStatus;
  bucket: ExitBucket | null;
  /** One-line TR explanation of what the reading is worth. */
  note: string;
}

/**
 * What the app may honestly say about the agent's own exit record.
 *
 * `strategy === undefined` is the undeployed-backend case and is reported as
 * 'unavailable'; `strategy === null` is the deployed-but-nothing-attributed
 * case and is reported as 'none'.
 */
export function strategyReading(
  response:
    | Pick<TradesResponse, 'strategy' | 'unattributed' | 'by_exit'>
    | null
    | undefined,
): StrategyReading {
  if (!response || response.strategy === undefined) {
    return {
      status: 'unavailable',
      bucket: null,
      note: 'Çıkış kırılımı bu sürümde yok — aşağıdaki beklenti tüm çıkış yollarının karışımı.',
    };
  }
  const bucket = response.strategy;
  if (!bucket || bucket.trades === 0) {
    return {
      status: 'none',
      bucket: null,
      note: 'Stratejinin kendi kapattığı işlem yok — gerçekleşen P&L tamamen ajan dışı çıkışlardan.',
    };
  }
  const note =
    bucket.trades < MIN_SAMPLE
      ? `${bucket.trades} işlem — istatistiksel olarak anlamlı değil (≥${MIN_SAMPLE} gerekir).`
      : `${bucket.trades} işlem üzerinden.`;
  return { status: 'ok', bucket, note };
}

/**
 * Warning for a partially attributed ledger. Non-zero `unattributed` means the
 * split does not add up to `stats`, so the breakdown must say so instead of
 * letting the rows read as the complete record.
 */
export function attributionNote(
  response: Pick<TradesResponse, 'unattributed'> | null | undefined,
): string | null {
  const n = response?.unattributed ?? 0;
  if (n <= 0) return null;
  return `${n} işlem sınıflandırılamadı — kırılım toplam ile örtüşmüyor.`;
}
