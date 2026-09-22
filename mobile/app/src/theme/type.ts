/**
 * The type ramp, and which family renders it.
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

import type { ThemeName } from './colors';
/** The active palette's name, readable outside React. */
function currentThemeName(): ThemeName {
  // Imported lazily through the store's own getter so `type.ts` does not
  // depend on React having rendered.
  return themeNameGetter();
}

let themeNameGetter: () => ThemeName = () => 'modernist';

/** Wired once at startup by the theme store; see useTheme.ts. */
export function bindThemeName(getter: () => ThemeName): void {
  themeNameGetter = getter;
}


/**
 * One ramp, two families. Modernist is Archivo; Aurora is Plus Jakarta Sans.
 * `dark` predates both handoffs and keeps Archivo so migrating a screen is a
 * palette change and not also a typeface change.
 */
const RAMPS = {
  modernist: {
    400: 'Archivo_400Regular',
    600: 'Archivo_600SemiBold',
    800: 'Archivo_800ExtraBold',
  },
  dark: {
    400: 'Archivo_400Regular',
    600: 'Archivo_600SemiBold',
    800: 'Archivo_800ExtraBold',
  },
  aurora: {
    400: 'PlusJakartaSans_400Regular',
    600: 'PlusJakartaSans_600SemiBold',
    800: 'PlusJakartaSans_800ExtraBold',
  },
} as const satisfies Record<ThemeName, Record<400 | 600 | 800, string>>;

/** Kept as a named export: the Modernist ramp is what `dark` also uses. */
export const FONT_FAMILIES = RAMPS.modernist;

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
  // Read at CALL time, not at module load. Every call site is inside a
  // `makeStyles(t)` factory that already re-runs under `useMemo` when the
  // palette changes, so resolving here is what makes the family follow it.
  // Freezing the family at import is the bug this replaced: the palette
  // switched and the typeface did not.
  return {
    fontFamily: RAMPS[currentThemeName()][w],
    fontWeight: String(w) as TextStyle['fontWeight'],
  };
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
/**
 * The scale, verbatim from the handoffs. A getter per entry rather than a
 * plain object: `TYPE.body` is read inside style factories at render, so the
 * family resolves against the palette that is actually active. A frozen object
 * would bake in whichever one happened to be selected when the module loaded.
 */
const SCALE = {
  hero: () => ({ fontSize: 44, letterSpacing: -1, ...font(800), ...TABULAR }),
  h2: () => ({ fontSize: 24, ...font(800) }),
  section: () => ({ fontSize: 15, ...font(800) }),
  body: () => ({ fontSize: 13, ...font(400) }),
  bodyStrong: () => ({ fontSize: 13, ...font(600) }),
  helper: () => ({ fontSize: 11, ...font(400) }),
  /** 11px uppercase, .1em tracking. The colour is accent-700 wherever it is used. */
  kicker: () => ({ fontSize: 11, letterSpacing: 1.1, textTransform: 'uppercase', ...font(600) }),
} satisfies Record<string, () => TextStyle>;

export const TYPE = new Proxy({} as Record<keyof typeof SCALE, TextStyle>, {
  get: (_t, key: string) => SCALE[key as keyof typeof SCALE]?.(),
  has: (_t, key: string) => key in SCALE,
  ownKeys: () => Reflect.ownKeys(SCALE),
  getOwnPropertyDescriptor: () => ({ enumerable: true, configurable: true }),
});
