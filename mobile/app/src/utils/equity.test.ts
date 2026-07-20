import { describe, it, expect } from '@jest/globals';

import type { Bar, EquityPoint } from '@/api/types';
import {
  verdictTheme,
  equityScale,
  barHeight,
  worstDrawdown,
  ddIntensity,
  rebaseSpy,
  spyReturnPct,
  alphaPct,
  combinedScale,
} from './equity';

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

describe('ddIntensity', () => {
  it('is 0 at no drawdown', () => {
    expect(ddIntensity(0, -5)).toBe(0);
  });

  it('is 1 at the series worst', () => {
    expect(ddIntensity(-5, -5)).toBe(1);
  });

  it('scales proportionally between', () => {
    expect(ddIntensity(-2.5, -5)).toBe(0.5);
  });

  it('guards a flat series (worst >= 0) to 0', () => {
    expect(ddIntensity(0, 0)).toBe(0);
    expect(ddIntensity(-1, 0)).toBe(0);
  });

  it('clamps a positive/over-range value into [0, 1]', () => {
    expect(ddIntensity(0.3, -5)).toBe(0);
    expect(ddIntensity(-6, -5)).toBe(1);
  });
});

const bar = (t: string, c: number): Bar => ({ t, o: c, h: c, l: c, c, v: 0 });
const eq = (date: string, equity: number): EquityPoint => ({ date, equity, return_pct: 0, drawdown_pct: 0 });

describe('rebaseSpy', () => {
  const pts = [eq('2026-02-02', 100000), eq('2026-02-03', 101000), eq('2026-02-04', 102000)];

  it('rebases closes onto the portfolio start equity', () => {
    const spy = rebaseSpy([bar('2026-02-02', 500), bar('2026-02-03', 505), bar('2026-02-04', 510)], pts);
    expect(spy).toHaveLength(3);
    expect(spy[0]!.value).toBe(100000); // anchor → start equity
    expect(spy[1]!.value).toBeCloseTo(100000 * (505 / 500));
    expect(spy[2]!.value).toBeCloseTo(100000 * (510 / 500));
  });

  it('trims padded bars outside the equity window', () => {
    const spy = rebaseSpy(
      [bar('2026-01-01', 480), bar('2026-02-02', 500), bar('2026-02-04', 510), bar('2026-03-01', 520)],
      pts,
    );
    expect(spy.map((p) => p.date)).toEqual(['2026-02-02', '2026-02-04']);
    expect(spy[0]!.value).toBe(100000);
  });

  it('returns [] on empty inputs or a non-positive anchor', () => {
    expect(rebaseSpy([], pts)).toEqual([]);
    expect(rebaseSpy([bar('2026-02-02', 500)], [])).toEqual([]);
    expect(rebaseSpy([bar('2026-02-02', 0)], pts)).toEqual([]);
  });
});

describe('spyReturnPct', () => {
  it('computes total return over the rebased series', () => {
    expect(spyReturnPct([{ date: 'a', value: 100000 }, { date: 'b', value: 102000 }])).toBeCloseTo(2);
  });

  it('is null on empty or zero-base series', () => {
    expect(spyReturnPct([])).toBeNull();
    expect(spyReturnPct([{ date: 'a', value: 0 }, { date: 'b', value: 5 }])).toBeNull();
  });
});

describe('alphaPct', () => {
  it('is portfolio minus benchmark', () => {
    expect(alphaPct(6.0, 1.4)).toBeCloseTo(4.6);
    expect(alphaPct(1.0, 3.0)).toBeCloseTo(-2.0);
  });

  it('is null when the benchmark is unavailable', () => {
    expect(alphaPct(6.0, null)).toBeNull();
  });
});

describe('combinedScale', () => {
  const pts = [eq('2026-02-02', 100000), eq('2026-02-03', 101000)];

  it('falls back to the equity-only scale with no overlay', () => {
    expect(combinedScale(pts, [])).toEqual(equityScale(pts));
  });

  it('widens min/max to include the benchmark series', () => {
    const s = combinedScale(pts, [{ date: 'a', value: 99000 }, { date: 'b', value: 103000 }]);
    expect(s.min).toBe(99000);
    expect(s.max).toBe(103000);
    expect(s.span).toBe(4000);
  });
});
