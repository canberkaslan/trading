/**
 * How a rating is drawn, in one place.
 *
 * It was duplicated byte-for-byte across agents.tsx and ask.tsx, with
 * `#86efac` and `#fda4af` written as inline literals that appear in neither
 * palette — so a retune meant editing two files and hoping. A third screen
 * (trade/[ticker].tsx) rendered the rating in plain body ink and dropped the
 * encoding entirely.
 *
 * Modernist also changes the treatment: five hues collapse to three FILLED
 * buckets, because on a light ground a tinted word is much weaker than a
 * filled chip, and the distinction that matters to the operator is
 * buy / hold / sell rather than five shades of conviction.
 */

import type { Rating } from '@/api/types';

export interface RatingChip {
  background: string;
  color: string;
  /** Set when the chip is outlined rather than filled. */
  borderColor?: string;
}

/**
 * Structural, not `Record<string, string>`: under `noUncheckedIndexedAccess` an
 * index read is `string | undefined`, so every `??` fallback here would still be
 * possibly-undefined. Naming the keys makes the required ones required and the
 * ramp steps genuinely optional — which is the truth: `dark` has no ramp.
 */
interface Palette {
  background: string;
  accent700?: string;
  surface: string;
  textPrimary: string;
  textSecondary: string;
  accent: string;
  danger: string;
  accent100?: string;
  accent800?: string;
  neutral100?: string;
  neutral800?: string;
}

export function ratingChip(t: Palette, rating: Rating | string): RatingChip {
  switch (rating) {
    case 'Buy':
    case 'Overweight':
      // Ink fill: the strongest mark the palette has, and not the accent —
      // the accent means loss here.
      return { background: t.textPrimary, color: t.background };
    case 'Underweight':
    case 'Sell':
      return { background: t.accent100 ?? t.surface, color: t.accent800 ?? t.danger };
    case 'Hold':
      return { background: t.neutral100 ?? t.surface, color: t.neutral800 ?? t.textSecondary };
    default:
      // An unrecognised rating must not silently borrow Sell's styling — the
      // web dashboard's own fallthrough does exactly that, so API drift paints
      // an unknown value red. Outline it instead: visibly not one of the five.
      return { background: 'transparent', color: t.textSecondary, borderColor: t.textSecondary };
  }
}

/**
 * The same decision as `ratingChip`, expressed as a `<Tag>` variant so a screen
 * can render `<Tag label={rating} variant={ratingVariant(rating)} />` instead of
 * hand-rolling a chip. Five screens were doing the latter.
 *
 * Stated as a switch rather than derived by matching `ratingChip`'s colours back
 * against the variants: a reverse lookup silently degrades to the fallback the
 * moment two variants happen to share a fill, and it re-runs per row per render.
 * The pairing is pinned by a test instead.
 */
export type RatingVariant = 'ink' | 'accent' | 'neutral' | 'outlineMuted';

export function ratingVariant(rating: Rating | string): RatingVariant {
  switch (rating) {
    case 'Buy':
    case 'Overweight':
      return 'ink';
    case 'Underweight':
    case 'Sell':
      return 'accent';
    case 'Hold':
      return 'neutral';
    default:
      // Muted, not the accent outline: the accent outline is the system's
      // warning mark, so painting an unrecognised rating with it would turn a
      // schema change into an alarm.
      return 'outlineMuted';
  }
}

/** The five ratings in conviction order, for legends and pickers. */
export const RATINGS: Rating[] = ['Buy', 'Overweight', 'Hold', 'Underweight', 'Sell'];

/**
 * How a model is stamped on an agent's analysis.
 *
 * Same story as the rating above: agents.tsx and trade/[ticker].tsx each had
 * their own copy, both hard-coding `#3b82f6` for Sonnet — a literal that is
 * also the dark palette's unused colourblind `up` token, so the two meanings
 * would have moved together by accident. The badge is an OUTLINE in Modernist,
 * never a fill: the filled chip on these screens means the rating.
 */
export function modelBadge(t: Palette, model: string): { label: string; color: string } {
  if (model.includes('opus')) return { label: 'Opus', color: t.accent700 ?? t.accent };
  if (model.includes('sonnet')) return { label: 'Sonnet', color: t.neutral800 ?? t.textSecondary };
  if (model.includes('haiku')) return { label: 'Haiku', color: t.textSecondary };
  return { label: model.slice(0, 8) || 'model', color: t.textSecondary };
}
