import { describe, it, expect } from '@jest/globals';

import type { EquityPoint } from '@/api/types';
import { verdictTheme, equityScale, barHeight, worstDrawdown } from './equity';

const palette = { up: '#22c55e', down: '#ef4444', warning: '#f59e0b' };

const pt = (equity: number, drawdown_pct = 0): EquityPoint => ({
  date: '2026-01-01',
  equity,
  return_pct: 0,
  drawdown_pct,
});

describe('verdictTheme', () => {
  it('maps GO to the up color', () => {
    expect(verdictTheme('GO', palette)).toEqual({ color: palette.up, emoji: '✓' });
  });

  it('maps NO-GO to the down color', () => {
    expect(verdictTheme('NO-GO', palette)).toEqual({ color: palette.down, emoji: '✕' });
  });

  it('falls back to warning for TOO EARLY / null / unknown', () => {
    expect(verdictTheme('TOO EARLY', palette).color).toBe(palette.warning);
    expect(verdictTheme(null, palette).color).toBe(palette.warning);
    expect(verdictTheme('WAT', palette).color).toBe(palette.warning);
  });
});

describe('equityScale', () => {
  it('returns a guarded span for an empty series', () => {
    expect(equityScale([])).toEqual({ min: 0, max: 0, span: 1 });
  });

  it('never returns a zero span for a flat series', () => {
    expect(equityScale([pt(100), pt(100)]).span).toBe(1);
  });

  it('finds the true min/max/span', () => {
    expect(equityScale([pt(100), pt(150), pt(90)])).toEqual({ min: 90, max: 150, span: 60 });
  });
});

describe('barHeight', () => {
  const scale = { min: 90, max: 150, span: 60 };

  it('floors the minimum bar at 2px so it stays visible', () => {
    expect(barHeight(90, scale, 120)).toBe(2);
  });

  it('scales the maximum to the full height', () => {
    expect(barHeight(150, scale, 120)).toBe(120);
  });

  it('scales a mid value proportionally', () => {
    expect(barHeight(120, scale, 120)).toBe(60);
  });
});

describe('worstDrawdown', () => {
  it('returns 0 for an empty or all-flat series', () => {
    expect(worstDrawdown([])).toBe(0);
    expect(worstDrawdown([pt(100), pt(101)])).toBe(0);
  });

  it('returns the most negative drawdown', () => {
    expect(worstDrawdown([pt(100, -0.5), pt(99, -1.2), pt(101, -0.3)])).toBe(-1.2);
  });
});
