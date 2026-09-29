import { describe, it, expect } from '@jest/globals';

import { dayChange, windowChange } from './priceChange';

const bars = [{ c: 100 }, { c: 110 }, { c: 120 }, { c: 114 }];

describe('dayChange', () => {
  it('compares the last two closes, not the window', () => {
    expect(dayChange(bars)).toBeCloseTo(114 / 120 - 1, 10);
    expect(dayChange(bars)).not.toBeCloseTo(windowChange(bars) ?? 0, 4);
  });

  it('is unknown with fewer than two bars or a non-positive prior close', () => {
    expect(dayChange(undefined)).toBeNull();
    expect(dayChange([{ c: 5 }])).toBeNull();
    expect(dayChange([{ c: 0 }, { c: 5 }])).toBeNull();
  });
});

describe('windowChange', () => {
  it('is last close over first close', () => {
    expect(windowChange(bars)).toBeCloseTo(0.14, 10);
  });

  it('is unknown without two bars', () => {
    expect(windowChange([])).toBeNull();
  });
});
