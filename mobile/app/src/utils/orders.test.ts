import { describe, it, expect } from '@jest/globals';

import {
  orderStatusMeta,
  fillSummary,
  formatOrderDate,
  isCancellable,
  rejectionReasonTr,
} from './orders';

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

describe('isCancellable', () => {
  it('allows cancel for live broker statuses', () => {
    for (const s of ['new', 'accepted', 'pending_new', 'held', 'partially_filled', 'ACCEPTED']) {
      expect(isCancellable(s, 'bkr-1')).toBe(true);
    }
  });

  it('refuses terminal statuses', () => {
    for (const s of ['filled', 'canceled', 'cancelled', 'expired', 'rejected', 'done_for_day']) {
      expect(isCancellable(s, 'bkr-1')).toBe(false);
    }
  });

  it('refuses an in-flight cancel so the button cannot be double-tapped', () => {
    expect(isCancellable('pending_cancel', 'bkr-1')).toBe(false);
  });

  it('refuses an order that never reached the broker (that is /reject)', () => {
    expect(isCancellable('new', null)).toBe(false);
    expect(isCancellable('new', '')).toBe(false);
  });

  it('refuses an unknown or missing status rather than offering a doomed action', () => {
    expect(isCancellable('some_new_alpaca_status', 'bkr-1')).toBe(false);
    expect(isCancellable(null, 'bkr-1')).toBe(false);
    expect(isCancellable('', 'bkr-1')).toBe(false);
  });
});

describe('rejectionReasonTr', () => {
  it('translates the key and keeps the numbers verbatim', () => {
    expect(rejectionReasonTr('position_pct=12.40% exceeds 10%')).toBe(
      'Tek isim limiti (12.40% exceeds 10%)',
    );
    expect(rejectionReasonTr('kill_switch=PAUSE_NEW')).toBe('Kill switch devrede (PAUSE_NEW)');
  });

  it('translates bare keys with no detail', () => {
    expect(rejectionReasonTr('risk_layer_rejected')).toBe('Risk katmanı reddetti');
    expect(rejectionReasonTr('pattern_day_trader_active')).toBe('PDT kısıtı aktif');
  });

  it('handles colon-separated executor refusals', () => {
    expect(rejectionReasonTr('stale_decision: age=30.2h exceeds 24.0h')).toBe(
      'Karar bayatlamış (age=30.2h exceeds 24.0h)',
    );
  });

  it('handles a space-separated key', () => {
    expect(rejectionReasonTr('non-actionable rating=Hold')).toBe(
      'Aksiyon gerektirmeyen karar (rating=Hold)',
    );
  });

  it('does not double-wrap a detail the backend already parenthesized', () => {
    expect(rejectionReasonTr('trimmed_to_zero_by_cash_cap (spendable=$0.00)')).toBe(
      'Harcanabilir nakit kalmadı (spendable=$0.00)',
    );
    expect(
      rejectionReasonTr(
        'trimmed_to_zero_by_portfolio_caps (AAPL at 10.4% of equity, cap 10.0%, headroom=$0.00)',
      ),
    ).toBe('Portföy limitleri emri sıfıra indirdi (AAPL at 10.4% of equity, cap 10.0%, headroom=$0.00)');
  });

  it('keeps the cap detail that says the name is saturated distinct from a sub-share trim', () => {
    expect(
      rejectionReasonTr(
        'trimmed_to_zero_by_portfolio_caps (AAPL headroom=$210.00, cap 10.0%, below 1 share @ $309.35)',
      ),
    ).toContain('below 1 share @ $309.35');
  });

  it('leaves two separate parenthesized asides alone', () => {
    // Stripping the first and last paren here would splice unrelated fragments.
    expect(rejectionReasonTr('kill_switch=(a) and (b)')).toBe('Kill switch devrede ((a) and (b))');
  });

  it('passes an unknown reason through untouched rather than hiding it', () => {
    expect(rejectionReasonTr('brand_new_guard=7')).toBe('brand_new_guard=7');
  });

  it('returns an empty string for null/blank input', () => {
    expect(rejectionReasonTr(null)).toBe('');
    expect(rejectionReasonTr('   ')).toBe('');
  });
});
