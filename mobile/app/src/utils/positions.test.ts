import { describe, it, expect } from '@jest/globals';

import { holdingDays, holdingLabel, positionStop } from './positions';
import { formatUsd } from './format';

describe('positionStop', () => {
  it('is null for the backend placeholder', () => {
    // agent/api/routes/portfolio.py sends stop_loss=0.0 when it cannot vouch for a stop:
    // "broker-side leg lives on order, not position".
    expect(positionStop({ stop_loss: 0 })).toBeNull();
  });

  it('renders as an em dash, never as a stop sitting at zero', () => {
    expect(formatUsd(positionStop({ stop_loss: 0 }))).toBe('—');
    expect(formatUsd(0)).toBe('$0.00'); // what the unguarded call used to print
  });

  it('passes a real stop through', () => {
    expect(positionStop({ stop_loss: 286.77 })).toBe(286.77);
    expect(formatUsd(positionStop({ stop_loss: 286.77 }))).toBe('$286.77');
  });

  it('treats a negative as absent rather than rendering it', () => {
    expect(positionStop({ stop_loss: -1 })).toBeNull();
  });
});

describe('holdingDays', () => {
  const now = Date.parse('2026-10-08T06:07:06Z');

  it('counts whole days since the ledger open', () => {
    expect(holdingDays({ opened_at_utc: '2026-06-25T13:31:16.404163Z' }, now)).toBe(104);
    expect(holdingDays({ opened_at_utc: '2026-10-07T06:07:07Z' }, now)).toBe(0);
    expect(holdingDays({ opened_at_utc: '2026-10-07T06:07:06Z' }, now)).toBe(1);
  });

  it('is null when the backend cannot vouch for the open', () => {
    expect(holdingDays({ opened_at_utc: null }, now)).toBeNull();
    expect(holdingDays({ opened_at_utc: 'not-a-date' }, now)).toBeNull();
  });

  it('clamps a future open (clock skew) to zero', () => {
    expect(holdingDays({ opened_at_utc: '2026-10-08T07:00:00Z' }, now)).toBe(0);
  });
});

describe('holdingLabel', () => {
  it('reads today, days, or nothing', () => {
    expect(holdingLabel(0)).toBe('bugün açıldı');
    expect(holdingLabel(12)).toBe('12 gün');
    expect(holdingLabel(null)).toBeNull();
  });
});
