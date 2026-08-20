import { describe, it, expect } from '@jest/globals';

import { formatUsd, formatPct, parseUtc, relativeAgeTr } from './format';

describe('relativeAgeTr', () => {
  it('walks the TR age ladder', () => {
    expect(relativeAgeTr(30_000)).toBe('az önce');
    expect(relativeAgeTr(12 * 60_000)).toBe('12 dk önce');
    expect(relativeAgeTr(3 * 60 * 60_000)).toBe('3 sa önce');
    expect(relativeAgeTr(9 * 24 * 60 * 60_000)).toBe('9 gün önce');
  });

  it('does not render a negative age when the device clock runs ahead', () => {
    expect(relativeAgeTr(-2 * 60 * 60_000)).toBe('az önce');
  });

  it('renders a non-finite age as an em dash', () => {
    expect(relativeAgeTr(NaN)).toBe('—');
  });
});

describe('parseUtc', () => {
  it('reads a naive backend timestamp as UTC, not device-local', () => {
    expect(parseUtc('2026-08-11T23:30:04.470376')?.toISOString()).toBe(
      '2026-08-11T23:30:04.470Z',
    );
  });

  it('leaves an explicit zone alone', () => {
    expect(parseUtc('2026-08-11T23:30:04Z')?.toISOString()).toBe('2026-08-11T23:30:04.000Z');
    expect(parseUtc('2026-08-12T02:30:04+03:00')?.toISOString()).toBe('2026-08-11T23:30:04.000Z');
  });

  it('returns null for missing/unparseable input instead of an Invalid Date', () => {
    expect(parseUtc(null)).toBeNull();
    expect(parseUtc('')).toBeNull();
    expect(parseUtc('not-a-date')).toBeNull();
  });
});

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
