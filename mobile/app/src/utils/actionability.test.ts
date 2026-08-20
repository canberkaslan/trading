import { describe, expect, it } from '@jest/globals';

import {
  actionabilityVerdictMeta,
  inertiaNote,
  lastSubmitLabel,
  submitRate,
  submitRatioLabel,
  topReasons,
  verdictQualifier,
  type ActionabilityReportLike,
} from './actionability';

const NOW = new Date('2026-08-20T09:00:00Z');

function report(over: Partial<ActionabilityReportLike> = {}): ActionabilityReportLike {
  return {
    verdict: 'inert',
    window_days: 30,
    orders: 234,
    submitted: 6,
    refused: 228,
    by_reason: {
      'non-actionable rating=Hold': 130,
      trimmed_to_zero_by_portfolio_caps: 93,
      'non-actionable rating=Underweight': 2,
      trimmed_to_zero_by_cash_cap: 1,
    },
    dominant_reason: 'non-actionable rating=Hold',
    inert_run_days: 8,
    run_days: 27,
    inert_threshold_run_days: 3,
    last_submitted_at_utc: '2026-08-11T23:30:04.470376',
    ...over,
  };
}

describe('actionabilityVerdictMeta', () => {
  it('maps the three known verdicts to TR labels and tones', () => {
    expect(actionabilityVerdictMeta('active')).toEqual({ label: 'Aktif', tone: 'up' });
    expect(actionabilityVerdictMeta('inert')).toEqual({ label: 'Donmuş', tone: 'warning' });
    expect(actionabilityVerdictMeta('idle')).toEqual({ label: 'Veri yok', tone: 'muted' });
  });

  it('renders an unknown verdict verbatim rather than flattening it', () => {
    expect(actionabilityVerdictMeta('degraded')).toEqual({ label: 'degraded', tone: 'muted' });
    expect(actionabilityVerdictMeta(null).label).toBe('—');
  });
});

describe('submitRate', () => {
  it('is the broker-acked share of produced orders', () => {
    expect(submitRate({ orders: 234, submitted: 6 })).toBeCloseTo(0.0256, 4);
  });

  it('is null with no orders — 0/0 is "nothing attempted", not "0% submitted"', () => {
    expect(submitRate({ orders: 0, submitted: 0 })).toBeNull();
    expect(submitRate(null)).toBeNull();
    expect(submitRatioLabel({ orders: 0, submitted: 0 })).toBe('—');
  });

  it('labels the raw counts so the denominator stays visible', () => {
    expect(submitRatioLabel({ orders: 234, submitted: 6 })).toBe('6 / 234');
  });
});

describe('topReasons', () => {
  it('sorts by count desc and translates known keys, numbers kept verbatim', () => {
    const rows = topReasons(report().by_reason);
    expect(rows.map((r) => r.count)).toEqual([130, 93, 2, 1]);
    expect(rows[0]?.label).toBe('Aksiyon gerektirmeyen karar (rating=Hold)');
    expect(rows[1]?.label).toBe('Portföy limitleri emri sıfıra indirdi');
    expect(rows[3]?.label).toBe('Harcanabilir nakit kalmadı');
  });

  it('scales bars against the top reason, not `refused` — reasons stack past 100%', () => {
    const rows = topReasons({ a: 10, b: 5 });
    expect(rows[0]?.share).toBe(1);
    expect(rows[1]?.share).toBe(0.5);
    // Counts summing past the refused total must still produce <= 1 shares.
    expect(topReasons({ a: 200, b: 200 }).every((r) => r.share <= 1)).toBe(true);
  });

  it('breaks ties on the name so the list does not reshuffle between polls', () => {
    expect(topReasons({ zebra: 5, alpha: 5 }).map((r) => r.reason)).toEqual(['alpha', 'zebra']);
  });

  it('honours the limit and drops empty/zero buckets', () => {
    expect(topReasons({ a: 3, b: 2, c: 1 }, 2)).toHaveLength(2);
    expect(topReasons({ a: 0, b: 4 }).map((r) => r.reason)).toEqual(['b']);
    expect(topReasons({})).toEqual([]);
    expect(topReasons(null)).toEqual([]);
  });
});

describe('lastSubmitLabel', () => {
  it('reads a naive backend timestamp as UTC, not device-local', () => {
    // 2026-08-11T23:30Z -> 2026-08-20T09:00Z is 8 days. Parsed as local time in
    // UTC+3 it would be 8 days + 3h, which still rounds to 8 — so assert the
    // boundary case where the offset actually flips the answer.
    expect(lastSubmitLabel(report().last_submitted_at_utc, NOW)).toBe('8 gün önce');
    expect(lastSubmitLabel('2026-08-20T06:30:00', new Date('2026-08-20T08:00:00Z'))).toBe(
      '1 sa önce',
    );
  });

  it('says "hiç" when nothing was submitted in the window', () => {
    expect(lastSubmitLabel(null, NOW)).toBe('hiç');
  });

  it('says "bilinmiyor" on an unparseable timestamp instead of "az önce"', () => {
    expect(lastSubmitLabel('not-a-date', NOW)).toBe('bilinmiyor');
  });
});

describe('inertiaNote', () => {
  it('names the run-day count and what the scorecard is actually measuring', () => {
    const note = inertiaNote(report()) ?? '';
    expect(note).toContain('8 çalışma günüdür');
    expect(note).toContain('eşik 3');
    expect(note).toContain('değerlemesini');
  });

  it('blames a missing run, not the strategy, when the window is empty', () => {
    const note = inertiaNote(report({ verdict: 'idle', orders: 0, submitted: 0, refused: 0 })) ?? '';
    expect(note).toContain('günlük çalışma');
    expect(note).not.toContain('Donmuş');
  });

  it('is silent while the book is still acting', () => {
    expect(inertiaNote(report({ verdict: 'active', inert_run_days: 0 }))).toBeNull();
    expect(inertiaNote(null)).toBeNull();
  });
});

describe('verdictQualifier', () => {
  it('qualifies the eval badge only while the book is frozen', () => {
    expect(verdictQualifier({ verdict: 'inert', inert_run_days: 8 })).toBe('donmuş kitap · 8g');
    expect(verdictQualifier({ verdict: 'active', inert_run_days: 0 })).toBeNull();
    expect(verdictQualifier({ verdict: 'idle', inert_run_days: 0 })).toBeNull();
    expect(verdictQualifier(null)).toBeNull();
  });
});
