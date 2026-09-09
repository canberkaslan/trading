/**
 * Archivo, and the type scale that goes with it.
 *
 * Two things this module exists to prevent.
 *
 * First: on Android `fontWeight` does not pick between separately registered
 * faces. Each weight is its own family, so a screen that sets only
 * `fontWeight: '800'` renders in the system font's bold — which is exactly what
 * the whole app was doing: 105 `fontWeight` declarations and not one
 * `fontFamily`. Every style that wants Archivo has to name the family, so
 * `font(w)` returns both and is the only correct way to ask for a weight.
 *
 * Second: the handoff's ramp is 400/600/800 — three steps, no more. The app had
 * drifted to a fourth (700) at 25 call sites. Only three faces are loaded, so
 * `font()` snaps anything else onto the ramp rather than silently falling back
 * to the system font at that weight, which would look like a rendering bug
 * rather than the design decision it is.
 */

import type { TextStyle } from 'react-native';

export const FONT_FAMILIES = {
  400: 'Archivo_400Regular',
  600: 'Archivo_600SemiBold',
  800: 'Archivo_800ExtraBold',
} as const;

export type RampWeight = keyof typeof FONT_FAMILIES;

/** Everything off the ramp lands on the nearest step the design actually has. */
function snap(weight: number): RampWeight {
  if (weight >= 700) return 800;
  if (weight >= 500) return 600;
  return 400;
}

/**
 * The only way to ask for a weight. Returns `fontFamily` AND `fontWeight` —
 * the family is what Android honours, the weight is what iOS and web use to
 * pick a synthetic fallback if the face has not loaded yet.
 */
export function font(weight: number): Pick<TextStyle, 'fontFamily' | 'fontWeight'> {
  const w = snap(weight);
  return { fontFamily: FONT_FAMILIES[w], fontWeight: String(w) as TextStyle['fontWeight'] };
}

/**
 * Tabular figures. The handoff asks for `font-feature-settings: "tnum"` on
 * numbers so a changing price does not shift the columns beside it; Archivo
 * ships the feature. Spread onto every style that renders a figure.
 */
export const TABULAR = { fontVariant: ['tabular-nums'] } as const satisfies TextStyle;

/**
 * The mobile type scale, verbatim from the handoff:
 * hero 44 · h2 24 · section 15 · body 13 · helper 11 · kicker 11 uppercase.
 * Sizes only where the design fixes them; colour stays with the screen.
 */
export const TYPE = {
  hero: { fontSize: 44, letterSpacing: -1, ...font(800), ...TABULAR },
  h2: { fontSize: 24, ...font(800) },
  section: { fontSize: 15, ...font(800) },
  body: { fontSize: 13, ...font(400) },
  bodyStrong: { fontSize: 13, ...font(600) },
  helper: { fontSize: 11, ...font(400) },
  /** 11px uppercase, .1em tracking. The colour is accent-700 wherever it is used. */
  kicker: { fontSize: 11, letterSpacing: 1.1, textTransform: 'uppercase', ...font(600) },
} as const satisfies Record<string, TextStyle>;
