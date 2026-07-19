/**
 * Pure helpers for the home equity curve + GO/NO-GO verdict badge.
 *
 * Kept free of React/RN imports so the geometry and verdict→theme mapping are
 * unit-testable (jest) without a renderer. The chart itself is drawn with plain
 * RN Views (see EquityChart.tsx) so it ships over-the-air with no native chart
 * lib — same constraint as the price chart on the Charts tab.
 */

import type { EquityPoint } from '@/api/types';

export type Verdict = 'GO' | 'NO-GO' | 'TOO EARLY';

export interface VerdictTheme {
  /** Hex color for the badge background/border. */
  color: string;
  /** Leading glyph. */
  emoji: string;
}

/**
 * Map an eval verdict to a badge color + glyph. Falls back to the "too early"
 * neutral treatment for anything unexpected so a new backend verdict string
 * never renders an empty badge.
 */
export function verdictTheme(
  verdict: string | null | undefined,
  palette: { up: string; down: string; warning: string },
): VerdictTheme {
  switch (verdict) {
    case 'GO':
      return { color: palette.up, emoji: '✓' };
    case 'NO-GO':
      return { color: palette.down, emoji: '✕' };
    default:
      return { color: palette.warning, emoji: '…' };
  }
}

export interface EquityScale {
  min: number;
  max: number;
  span: number;
}

/**
 * Min/max equity across the series with a non-zero span guard so downstream
 * division never blows up on a flat or single-point curve.
 */
export function equityScale(points: EquityPoint[]): EquityScale {
  const first = points[0];
  if (!first) return { min: 0, max: 0, span: 1 };
  let min = first.equity;
  let max = first.equity;
  for (const p of points) {
    if (p.equity < min) min = p.equity;
    if (p.equity > max) max = p.equity;
  }
  return { min, max, span: max - min || 1 };
}

/**
 * Height in px for an equity bar within a chart of `height` px. Bars are
 * floored at 2px so even the series minimum stays visible.
 */
export function barHeight(equity: number, scale: EquityScale, height: number): number {
  return Math.max(2, ((equity - scale.min) / scale.span) * height);
}

/** Worst (most negative) drawdown_pct in the series, as a negative number. */
export function worstDrawdown(points: EquityPoint[]): number {
  let dd = 0;
  for (const p of points) {
    if (p.drawdown_pct < dd) dd = p.drawdown_pct;
  }
  return dd;
}

/** Selectable lookback windows for the home equity curve. Each value is a
 * period string the backend forwards straight to Alpaca's portfolio_history;
 * the EVAL_START_DATE cutoff trims anything before the eval window. */
export const PERIODS = ['1M', '3M', '6M'] as const;
export type Period = (typeof PERIODS)[number];

/**
 * Relative depth (0..1) of a point's drawdown against the series worst, for
 * shading the drawdown ribbon. 0 at no drawdown, 1 at the worst point. Guards
 * a flat series (worst === 0) to 0 so the ribbon renders empty, not NaN.
 * `worst` is expected negative (as from {@link worstDrawdown}); the ratio is
 * clamped to [0, 1] so a stray positive value never over/under-shades.
 */
export function ddIntensity(drawdown_pct: number, worst: number): number {
  if (worst >= 0) return 0;
  const r = drawdown_pct / worst;
  return r <= 0 ? 0 : r > 1 ? 1 : r;
}
