export const colors = {
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

  // Colorblind-safe variant (blue/orange)
  upCB: '#3b82f6',
  downCB: '#f97316',

  accent: '#a855f7',
  warning: '#f59e0b',
  danger: '#ef4444',
} as const;

export type Colors = typeof colors;
