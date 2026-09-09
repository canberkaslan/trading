/**
 * Two palettes, one shape.
 *
 * `dark` is what has shipped so far. `modernist` is the light system from
 * `design_handoff_trader_ui/` — flat, square-cornered, one red accent.
 *
 * They are deliberately the same shape so a screen can take a palette as a
 * value rather than importing one. `colors` stays a direct export of `dark`,
 * so the existing call sites keep working while screens migrate one at a time.
 */

export const dark = {
  background: '#0a0a0a',
  surface: '#171717',
  surfaceElevated: '#262626',

  textPrimary: '#ffffff',
  textSecondary: '#a3a3a3',
  // WCAG AA (4.5:1) on all app backgrounds — #666 was 3.4:1 (fail).
  // #949494 → 5.0:1 on the lightest surface (#262626), 6.5:1 on #0a0a0a,
  // still visibly dimmer than textSecondary. Guarded by utils/contrast.test.ts.
  textMuted: '#949494',

  // P&L colors — US/TR convention (green=up, red=down)
  up: '#22c55e',
  down: '#ef4444',

  // `down` as TEXT. red-500 reads fine on the page ground (5.4:1) but only
  // reaches 4.2:1 on surfaceElevated, where P&L rows are actually drawn — so
  // small loss figures get red-400 and the fill keeps red-500.
  downText: '#f87171',

  // Colorblind-safe variant (blue/orange)
  upCB: '#3b82f6',
  downCB: '#f97316',

  accent: '#a855f7',
  // Deeper red for surfaces that carry WHITE text. colors.down is tuned to be
  // read AS text on a dark ground; inverted it only reaches 3.76:1, so the LIVE
  // mode chip needs its own fill rather than borrowing the P&L red.
  dangerDeep: '#dc2626',
  warning: '#f59e0b',
  danger: '#ef4444',

  divider: '#262626',
  // Dark has no light to cast; the sheet reads by its border and fill instead.
  shadowColor: '#000000',
} as const;

/**
 * Modernist — the light system from the design handoff.
 *
 * Two things about it are not interchangeable with `dark` and are worth stating
 * rather than leaving to be discovered: every radius is 0, and P&L is not
 * green/red. Gain is ink, loss is the accent — colour marks the exception
 * rather than both directions, so a mostly-flat book reads as mostly quiet.
 * `upAlt` carries the conventional green for anyone who wants it back.
 */
export const modernist = {
  background: '#f3f2f2',
  surface: '#eae9e9',
  surfaceElevated: '#f8f4f4',

  textPrimary: '#201e1d',
  textSecondary: '#605d5d',
  // neutral-500, 3.4:1 on the page ground — below AA, so it is for rules and
  // disabled marks, never for text. Copy that needs to recede uses
  // textSecondary (neutral-700, 6.9:1).
  textMuted: '#9b9797',

  // Accounting P&L: gain is ink, loss is the accent.
  up: '#201e1d',
  down: '#ec3013',
  upCB: '#0b62c4',
  downCB: '#ec3013',

  // The loss colour as TEXT. `down` is the base accent — a 3.6:1 fill, which is
  // right for a candle body or a chart line and wrong for a 14px figure. This
  // is the same accent-700 the handoff designates for small red type.
  downText: '#ae1800',

  accent: '#ec3013',
  // The base accent is 3.6:1 on the page ground — fine as a fill or a rule,
  // not as small text. accent-700 is what small red type uses.
  warning: '#ae1800',
  danger: '#ec3013',
  // accent-600: darker than the base accent, but still only 4.25:1 under the
  // page ground — see liveStrip.
  dangerDeep: '#dd2b0f',

  // The LIVE strip is the one full-bleed accent fill in the system, and it
  // carries text. The handoff specifies the base accent for it, which puts the
  // page ground at 3.76:1 on top — the same failure, to two decimals, that the
  // dark palette had on its own LIVE chip. accent-700 is the ramp step the
  // handoff already designates for legible red, and it clears at 6.41:1 while
  // staying unmistakably the accent. A deliberate deviation from the spec:
  // this strip's entire job is to be read.
  liveStrip: '#ae1800',

  // Ink at 40% alpha. 2px between sections, 1px between rows.
  divider: 'rgba(32,30,29,0.4)',

  // The one elevation the system has, and only dialogs and sheets may use it
  // (`0 12px 32px rgba(45,43,43,.22)`). Everything else is flat, so a surface
  // that lifts is unambiguously a thing that took over the screen.
  shadowColor: '#2d2b2b',

  neutral100: '#f8f4f4',
  neutral200: '#eae7e7',
  neutral300: '#d7d3d3',
  neutral400: '#bab6b6',
  neutral500: '#9b9797',
  neutral700: '#605d5d',
  neutral800: '#444141',
  neutral900: '#2d2b2b',

  accent100: '#fff2ef',
  accent200: '#ffe0d9',
  accent300: '#ffc4b8',
  accent500: '#ff563c',
  accent600: '#dd2b0f',
  accent700: '#ae1800',
  accent800: '#7c1405',
  // The alternate P&L green from the handoff, for anyone who wants the
  // conventional palette instead of the accounting one. Same fill-vs-text split
  // as the accent: the handoff value is 3.94:1 on the page ground and 3.63:1 on
  // the surface, so it marks and fills, and `upAltText` is what a green WORD
  // uses. Darkened by two ramp steps rather than re-hued.
  upAlt: '#118a4e',
  upAltText: '#0e703f',
} as const;

/** The palette screens import today. */
export const colors = dark;

export type Colors = typeof dark;
export type ThemeName = 'dark' | 'modernist';

export const themes = { dark, modernist } as const;
