import { describe, it, expect } from '@jest/globals';

import {
  MIN_TOUCH_TARGET,
  hitSlopFor,
  sideLabelTr,
  orderActionLabel,
  killSwitchLabel,
} from './a11y';

describe('hitSlopFor', () => {
  it('pads a short control up to the 44pt minimum', () => {
    expect(hitSlopFor(20)).toEqual({ top: 12, bottom: 12, left: 12, right: 12 });
    // 20 + 12 + 12 = 44
    expect(20 + hitSlopFor(20).top + hitSlopFor(20).bottom).toBe(MIN_TOUCH_TARGET);
  });

  it('rounds up on odd gaps so the target never lands under 44', () => {
    const slop = hitSlopFor(21);
    expect(slop.top).toBe(12);
    expect(21 + slop.top + slop.bottom).toBeGreaterThanOrEqual(MIN_TOUCH_TARGET);
  });

  it('adds nothing when the control already clears the minimum', () => {
    expect(hitSlopFor(44)).toEqual({ top: 0, bottom: 0, left: 0, right: 0 });
    expect(hitSlopFor(60)).toEqual({ top: 0, bottom: 0, left: 0, right: 0 });
  });

  it('is safe on nonsense heights', () => {
    expect(hitSlopFor(0)).toEqual({ top: 0, bottom: 0, left: 0, right: 0 });
    expect(hitSlopFor(-10)).toEqual({ top: 0, bottom: 0, left: 0, right: 0 });
    expect(hitSlopFor(NaN)).toEqual({ top: 0, bottom: 0, left: 0, right: 0 });
  });
});

describe('sideLabelTr', () => {
  it('maps broker sides to Turkish', () => {
    expect(sideLabelTr('BUY')).toBe('al');
    expect(sideLabelTr('SELL')).toBe('sat');
    expect(sideLabelTr('buy')).toBe('al');
  });

  it('falls back to the raw side instead of throwing', () => {
    expect(sideLabelTr('SHORT')).toBe('short');
    expect(sideLabelTr(null)).toBe('');
    expect(sideLabelTr(undefined)).toBe('');
  });
});

describe('orderActionLabel', () => {
  const order = { ticker: 'AAPL', side: 'BUY', quantity: 10 };

  it('names ticker, side and size for every action', () => {
    expect(orderActionLabel(order, 'review')).toBe('AAPL, al 10 lot, incele ve onayla');
    expect(orderActionLabel(order, 'approve')).toBe('AAPL, al 10 lot, emri onayla');
    expect(orderActionLabel(order, 'reject')).toBe('AAPL, al 10 lot, emri reddet');
  });

  it('handles sells', () => {
    expect(orderActionLabel({ ticker: 'NVDA', side: 'SELL', quantity: 3 }, 'approve')).toBe(
      'NVDA, sat 3 lot, emri onayla',
    );
  });

  it('does not render NaN quantities', () => {
    expect(orderActionLabel({ ticker: 'MSFT', side: 'BUY', quantity: NaN }, 'approve')).toBe(
      'MSFT, al 0 lot, emri onayla',
    );
  });
});

describe('killSwitchLabel', () => {
  it('expands the terse chip text', () => {
    expect(killSwitchLabel('RUN')).toBe('Normal işlem (RUN)');
    expect(killSwitchLabel('PAUSE_NEW')).toBe('Yeni giriş durdur (PAUSE)');
    expect(killSwitchLabel('FLATTEN_ALL')).toBe('Tüm pozisyonları kapat (FLATTEN)');
  });

  it('falls back to the raw state for unknown values', () => {
    expect(killSwitchLabel('WAT')).toBe('WAT');
  });
});
