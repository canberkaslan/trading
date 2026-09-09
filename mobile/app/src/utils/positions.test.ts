import { describe, it, expect } from '@jest/globals';

import { positionStop } from './positions';
import { formatUsd } from './format';

describe('positionStop', () => {
  it('is null for the backend placeholder', () => {
    // agent/api/routes/portfolio.py sends stop_loss=0.0 for every position:
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
