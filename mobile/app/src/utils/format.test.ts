import { describe, it, expect } from '@jest/globals';

import { formatUsd, formatPct } from './format';

describe('formatUsd', () => {
  it('formats with two decimals and a dollar sign', () => {
    expect(formatUsd(1234.5)).toBe('$1,234.50');
  });

  it('renders null/undefined/NaN as an em dash', () => {
    expect(formatUsd(null)).toBe('—');
    expect(formatUsd(undefined)).toBe('—');
    expect(formatUsd(NaN)).toBe('—');
  });

  it('keeps the sign on the outside for negatives', () => {
    expect(formatUsd(-42.1)).toBe('-$42.10');
  });

  it('adds a + prefix for positive values when signed', () => {
    expect(formatUsd(42.1, { signed: true })).toBe('+$42.10');
    expect(formatUsd(-42.1, { signed: true })).toBe('-$42.10');
    expect(formatUsd(0, { signed: true })).toBe('+$0.00');
  });
});

describe('formatPct', () => {
  it('scales a fraction to a percentage', () => {
    expect(formatPct(0.0754)).toBe('7.54%');
  });

  it('renders null/NaN as an em dash', () => {
    expect(formatPct(null)).toBe('—');
    expect(formatPct(NaN)).toBe('—');
  });

  it('prefixes + only for positive signed values', () => {
    expect(formatPct(0.05, { signed: true })).toBe('+5.00%');
    expect(formatPct(-0.05, { signed: true })).toBe('-5.00%');
    expect(formatPct(0, { signed: true })).toBe('0.00%');
  });
});
