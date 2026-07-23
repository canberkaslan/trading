import { describe, it, expect } from '@jest/globals';

import { orderStatusMeta, fillSummary, formatOrderDate } from './orders';

describe('orderStatusMeta', () => {
  it('maps filled to an up tone', () => {
    expect(orderStatusMeta('filled')).toEqual({ label: 'Dolduruldu', tone: 'up' });
  });

  it('maps partially_filled to a warning tone', () => {
    expect(orderStatusMeta('partially_filled')).toEqual({ label: 'Kısmi doldu', tone: 'warning' });
  });

  it('treats live broker states as warning', () => {
    for (const s of ['new', 'accepted', 'pending_new', 'held']) {
      expect(orderStatusMeta(s).tone).toBe('warning');
    }
  });

  it('maps rejected/suspended/stopped to a down tone', () => {
    for (const s of ['rejected', 'suspended', 'stopped']) {
      expect(orderStatusMeta(s)).toEqual({ label: 'Reddedildi', tone: 'down' });
    }
  });

  it('maps canceled/expired to muted', () => {
    expect(orderStatusMeta('canceled').tone).toBe('muted');
    expect(orderStatusMeta('expired').tone).toBe('muted');
  });

  it('is case-insensitive', () => {
    expect(orderStatusMeta('FILLED').label).toBe('Dolduruldu');
  });

  it('falls back to the raw status for unknown values', () => {
    expect(orderStatusMeta('weird_status')).toEqual({ label: 'weird_status', tone: 'muted' });
  });

  it('handles null/empty (DB-only or broker unreachable)', () => {
    expect(orderStatusMeta(null)).toEqual({ label: 'Broker durumu yok', tone: 'muted' });
    expect(orderStatusMeta('')).toEqual({ label: 'Broker durumu yok', tone: 'muted' });
  });
});

describe('fillSummary', () => {
  it('renders filled/quantity', () => {
    expect(fillSummary(12, 33)).toBe('12/33 lot');
    expect(fillSummary(33, 33)).toBe('33/33 lot');
  });

  it('clamps null/NaN/negative filled to 0', () => {
    expect(fillSummary(null, 33)).toBe('0/33 lot');
    expect(fillSummary(NaN, 33)).toBe('0/33 lot');
    expect(fillSummary(-5, 33)).toBe('0/33 lot');
  });

  it('floors fractional fills', () => {
    expect(fillSummary(12.9, 33)).toBe('12/33 lot');
  });
});

describe('formatOrderDate', () => {
  it('formats an ISO timestamp in UTC with a TR month', () => {
    expect(formatOrderDate('2026-07-23T09:08:42.741Z')).toBe('23 Tem 09:08');
  });

  it('zero-pads hours and minutes', () => {
    expect(formatOrderDate('2026-01-05T04:03:00Z')).toBe('5 Oca 04:03');
  });

  it('returns an em dash for null/invalid input', () => {
    expect(formatOrderDate(null)).toBe('—');
    expect(formatOrderDate('not-a-date')).toBe('—');
  });
});
