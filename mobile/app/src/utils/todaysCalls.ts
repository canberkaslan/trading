/**
 * The day's recommendations, pulled out of the decision list.
 *
 * The Agents tab showed "son 25 karar" — a page, mixing today's run with
 * whatever came before it. The question an operator actually opens the app
 * with is narrower: what did the agents say TODAY. That was answerable only by
 * reading timestamps down a list.
 *
 * Grouped client-side because the data is already there. Asking the server for
 * a second, nearly identical list would be a round trip to re-sort rows the
 * app is holding.
 *
 * "Today" is the operator's local day, not UTC. The run fires at 22:30 UTC,
 * which is well inside the same calendar day in Istanbul — but a viewer in
 * another timezone should see their own day, and a UTC boundary would put a
 * morning's decisions under "yesterday" for no reason the reader could see.
 */

export interface DecisionLike {
  decision_id: string;
  ticker: string;
  rating: string;
  timestamp_utc: string;
}

export interface TodaysCalls<T> {
  /** Every decision stamped with the local calendar day of `now`. */
  items: T[];
  /** Counts per rating, for the summary line. */
  byRating: Record<string, number>;
  /** Ratings present, ordered most-actionable first. */
  ratings: string[];
}

/** Most actionable first, so a Sell never hides under a row of Holds. */
const RATING_ORDER = ['Sell', 'Buy', 'Underweight', 'Overweight', 'Hold'];

function sameLocalDay(iso: string, now: Date): boolean {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return false;
  return (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  );
}

export function todaysCalls<T extends DecisionLike>(
  decisions: readonly T[] | undefined,
  now: Date = new Date(),
): TodaysCalls<T> {
  const items = (decisions ?? []).filter((d) => sameLocalDay(d.timestamp_utc, now));

  const byRating: Record<string, number> = {};
  for (const d of items) byRating[d.rating] = (byRating[d.rating] ?? 0) + 1;

  const ratings = Object.keys(byRating).sort((a, b) => {
    const ia = RATING_ORDER.indexOf(a);
    const ib = RATING_ORDER.indexOf(b);
    // An unknown rating sorts last rather than first: it is not more urgent
    // for being unrecognised, and putting it at the top would be a louder
    // claim than the app can back up.
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  });

  return { items, byRating, ratings };
}

/** "3 Hold · 1 Buy" — or null when today has produced nothing yet. */
export function summaryLine(calls: TodaysCalls<DecisionLike>): string | null {
  if (calls.items.length === 0) return null;
  return calls.ratings.map((r) => `${calls.byRating[r]} ${r}`).join(' · ');
}
