/**
 * Position-level rules that are not obvious from the wire shape.
 */

import type { Position } from '@/api/types';

/**
 * The stop protecting a position, or null when this endpoint cannot say.
 *
 * `Position.stop_loss` is 0.0 whenever the backend cannot vouch for a stop:
 * the real stop is a bracket leg on the order, and the snapshot route only
 * fills a price in when every held share is behind a live stop (older
 * backends send 0.0 for every position).
 *
 * So a raw `formatUsd(p.stop_loss)` renders "$0.00", which on a money screen
 * does not read as "unknown": it reads as a stop sitting at zero, i.e. a
 * position with no protection that the operator believes is protected. Null is
 * the honest answer, and `formatUsd` draws it as an em dash.
 *
 * Kept as a function rather than a `> 0` check at each call site because the
 * two screens that render it had already diverged — one guarded, one did not.
 */
export function positionStop(p: Pick<Position, 'stop_loss'>): number | null {
  return p.stop_loss > 0 ? p.stop_loss : null;
}

const DAY_MS = 86_400_000;

/**
 * Whole days a position has been held, or null when the backend cannot say.
 *
 * `opened_at_utc` comes from the fill ledger and is null whenever the ledger's
 * lots do not add up to the broker quantity — an age we would have to guess
 * at. A timestamp in the future (clock skew between the box and the phone) is
 * clamped to 0 rather than rendered as a negative age.
 */
export function holdingDays(
  p: Pick<Position, 'opened_at_utc'>,
  nowMs: number,
): number | null {
  if (!p.opened_at_utc) return null;
  const opened = Date.parse(p.opened_at_utc);
  if (Number.isNaN(opened)) return null;
  return Math.max(0, Math.floor((nowMs - opened) / DAY_MS));
}

/** "bugün" for a position opened today, "12 gün" otherwise; null passes through. */
export function holdingLabel(days: number | null): string | null {
  if (days == null) return null;
  return days === 0 ? 'bugün açıldı' : `${days} gün`;
}
