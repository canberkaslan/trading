/**
 * Position-level rules that are not obvious from the wire shape.
 */

import type { Position } from '@/api/types';

/**
 * The stop protecting a position, or null when this endpoint cannot say.
 *
 * `Position.stop_loss` is not a stop. The snapshot route hardcodes it to 0.0
 * with the note "broker-side leg lives on order, not position" — the real stop
 * is a bracket leg on the order, which the positions payload does not carry.
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
