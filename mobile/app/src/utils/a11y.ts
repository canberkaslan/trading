/**
 * Accessibility helpers for money actions.
 *
 * Two problems this fixes on the approve/reject/kill-switch path:
 *  - Touch targets smaller than the 44pt platform minimum (a mis-tap on this
 *    screen submits or kills a real order).
 *  - Pressables with no `accessibilityLabel`, so VoiceOver/TalkBack announce
 *    only the visible glyph ("←", "RUN") with no idea what it does or to which
 *    order it applies.
 *
 * Pure functions only — no React, no RN imports — so they stay unit-testable.
 */

/** iOS HIG / Material minimum tappable size, in points. */
export const MIN_TOUCH_TARGET = 44;

export type HitSlop = { top: number; bottom: number; left: number; right: number };

const NO_SLOP: HitSlop = { top: 0, bottom: 0, left: 0, right: 0 };

/**
 * Expand a control that renders shorter than {@link MIN_TOUCH_TARGET} up to the
 * minimum via hitSlop, without changing its visual height. Returns zero padding
 * for controls that are already large enough (or for a nonsense height).
 */
export function hitSlopFor(height: number): HitSlop {
  if (!Number.isFinite(height) || height <= 0) return NO_SLOP;
  const missing = MIN_TOUCH_TARGET - height;
  if (missing <= 0) return NO_SLOP;
  const pad = Math.ceil(missing / 2);
  return { top: pad, bottom: pad, left: pad, right: pad };
}

/** Turkish side word for screen readers ("BUY" -> "al"). */
export function sideLabelTr(side: string | null | undefined): string {
  const s = (side ?? '').trim().toUpperCase();
  if (s === 'BUY') return 'al';
  if (s === 'SELL') return 'sat';
  return s.toLowerCase();
}

export type OrderA11yInput = {
  ticker: string;
  side: string;
  quantity: number;
};

export type OrderAction = 'review' | 'approve' | 'reject';

/**
 * Spoken label for an order action. Always names the ticker, side and size, so
 * the action is unambiguous even when the visual context isn't read out.
 */
export function orderActionLabel(order: OrderA11yInput, action: OrderAction): string {
  const side = sideLabelTr(order.side);
  const qty = Number.isFinite(order.quantity) ? order.quantity : 0;
  const subject = `${order.ticker}, ${side} ${qty} lot`;
  switch (action) {
    case 'review':
      return `${subject}, incele ve onayla`;
    case 'approve':
      return `${subject}, emri onayla`;
    case 'reject':
      return `${subject}, emri reddet`;
  }
}

const KILL_LABELS: Record<string, string> = {
  RUN: 'Normal işlem (RUN)',
  PAUSE_NEW: 'Yeni giriş durdur (PAUSE)',
  FLATTEN_ALL: 'Tüm pozisyonları kapat (FLATTEN)',
};

/** Spoken label for a kill-switch chip; the visible chip text is just "RUN". */
export function killSwitchLabel(state: string): string {
  return KILL_LABELS[state] ?? state;
}
