import { describe, it, expect } from '@jest/globals';

import {
  sectorLabelTr,
  sectorAllocation,
  topWeightTone,
  diversificationLabel,
} from './concentration';
import type { Position } from '@/api/types';

function pos(over: Partial<Position>): Position {
  return {
    ticker: 'X',
    market: 'US',
    quantity: 10,
    avg_entry_price: 100,
    current_price: 100,
    unrealized_pnl: 0,
    unrealized_pnl_pct: 0,
    stop_loss: 0,
    sector: 'Information Technology',
    opened_at_utc: '2026-07-01T00:00:00Z',
    ...over,
  };
}

describe('sectorLabelTr', () => {
  it('maps known GICS sectors to short TR labels', () => {
    expect(sectorLabelTr('Information Technology')).toBe('Teknoloji');
    expect(sectorLabelTr('Financials')).toBe('Finans');
    expect(sectorLabelTr('Health Care')).toBe('Sağlık');
  });

  it('falls back to "Bilinmiyor" for null/empty', () => {
    expect(sectorLabelTr(null)).toBe('Bilinmiyor');
    expect(sectorLabelTr(undefined)).toBe('Bilinmiyor');
    expect(sectorLabelTr('')).toBe('Bilinmiyor');
  });

  it('passes through an unmapped sector verbatim', () => {
    expect(sectorLabelTr('Space Mining')).toBe('Space Mining');
  });
});

describe('sectorAllocation', () => {
  it('groups by sector, weights by market value, sorts heaviest first', () => {
    const positions = [
      pos({ ticker: 'AAPL', quantity: 10, current_price: 300, sector: 'Information Technology' }), // 3000
      pos({ ticker: 'MSFT', quantity: 10, current_price: 200, sector: 'Information Technology' }), // 2000
      pos({ ticker: 'JPM', quantity: 10, current_price: 400, sector: 'Financials' }), // 4000
    ];
    const slices = sectorAllocation(positions, 10000);
    expect(slices).toHaveLength(2);
    // Teknoloji 5000 (AAPL+MSFT) outweighs Finans 4000 (JPM).
    expect(slices[0]!.label).toBe('Teknoloji');
    expect(slices[0]!.value).toBe(5000);
    expect(slices[0]!.weightPct).toBeCloseTo(50);
    expect(slices[1]!.label).toBe('Finans');
    expect(slices[1]!.weightPct).toBeCloseTo(40);
  });

  it('collapses null-sector positions into Bilinmiyor', () => {
    const slices = sectorAllocation(
      [pos({ quantity: 5, current_price: 100, sector: null })],
      1000,
    );
    expect(slices[0]!.label).toBe('Bilinmiyor');
    expect(slices[0]!.weightPct).toBeCloseTo(50);
  });

  it('returns [] on no positions or non-positive equity', () => {
    expect(sectorAllocation([], 1000)).toEqual([]);
    expect(sectorAllocation([pos({})], 0)).toEqual([]);
    expect(sectorAllocation([pos({})], -5)).toEqual([]);
  });

  it('skips positions with non-finite or non-positive value', () => {
    const slices = sectorAllocation(
      [
        pos({ ticker: 'A', quantity: 10, current_price: 100, sector: 'Energy' }),
        pos({ ticker: 'B', quantity: 0, current_price: 100, sector: 'Materials' }),
        pos({ ticker: 'C', quantity: 10, current_price: NaN, sector: 'Utilities' }),
      ],
      2000,
    );
    expect(slices).toHaveLength(1);
    expect(slices[0]!.label).toBe('Enerji');
  });
});

describe('topWeightTone', () => {
  it('is calm at or below the cap', () => {
    expect(topWeightTone(8)).toBe('up');
    expect(topWeightTone(10)).toBe('up');
  });

  it('warns on a small breach', () => {
    expect(topWeightTone(11)).toBe('warning');
    expect(topWeightTone(14.9)).toBe('warning');
  });

  it('reads a large breach (>=1.5x cap) as down', () => {
    expect(topWeightTone(15)).toBe('down');
    expect(topWeightTone(30)).toBe('down');
  });

  it('respects a custom cap', () => {
    expect(topWeightTone(6, 5)).toBe('warning');
    expect(topWeightTone(4, 5)).toBe('up');
  });
});

describe('diversificationLabel', () => {
  it('bins effective_n into concentrated / moderate / diversified', () => {
    expect(diversificationLabel(3)).toEqual({ label: 'Yoğunlaşmış', tone: 'down' });
    expect(diversificationLabel(6)).toEqual({ label: 'Orta', tone: 'warning' });
    expect(diversificationLabel(8.82)).toEqual({ label: 'Dağıtılmış', tone: 'up' });
  });

  it('renders an em dash for a non-positive effective_n', () => {
    expect(diversificationLabel(0).label).toBe('—');
    expect(diversificationLabel(NaN).label).toBe('—');
  });
});
