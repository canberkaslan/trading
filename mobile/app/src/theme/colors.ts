export const colors = {
  background: '#0a0a0a',
  surface: '#171717',
  surfaceElevated: '#262626',

  textPrimary: '#ffffff',
  textSecondary: '#a3a3a3',
  textMuted: '#666666',

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
