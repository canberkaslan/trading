/**
 * Price-move helpers for a daily-bar series — pure, unit-tested.
 *
 * `/v1/prices` answers `change_pct` over the WHOLE window (last close over first
 * close), not the session. Grafik printed that figure as the day's move and then
 * printed the same number again as "Dönem", so the "daily" % changed whenever
 * 1A/3A/6A was toggled.
 */

interface Close {
  c: number;
}

/**
 * Today's move, as a fraction, from the last two closes. Fewer than two bars
 * (or a non-positive prior close) is null, not zero: nothing to compare is not
 * "unchanged".
 */
export function dayChange(bars: readonly Close[] | undefined): number | null {
  if (!bars || bars.length < 2) return null;
  const prev = bars[bars.length - 2];
  const last = bars[bars.length - 1];
  if (!prev || !last || !(prev.c > 0)) return null;
  return last.c / prev.c - 1;
}

/** The move across the window on screen, as a fraction: last close over first. */
export function windowChange(bars: readonly Close[] | undefined): number | null {
  if (!bars || bars.length < 2) return null;
  const first = bars[0];
  const last = bars[bars.length - 1];
  if (!first || !last || !(first.c > 0)) return null;
  return last.c / first.c - 1;
}
