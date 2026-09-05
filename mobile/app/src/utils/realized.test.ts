import { describe, it, expect } from '@jest/globals';

import {
  MIN_SAMPLE,
  unrealizedTotal,
  pnlTone,
  sampleTone,
  pnlSplit,
  formatWinRate,
  formatProfitFactor,
  reconcileFreshness,
  realizedCaveat,
  evalWindowNote,
  exitClassLabelTr,
  exitBreakdown,
  strategyReading,
  attributionNote,
} from './realized';
import type { ExitBucket, Position, TradeStats, TradesResponse } from '@/api/types';

function pos(unrealized: number): Position {
  return {
    ticker: 'X',
    market: 'US',
    quantity: 1,
    avg_entry_price: 100,
    current_price: 100,
    unrealized_pnl: unrealized,
    unrealized_pnl_pct: 0,
    stop_loss: 0,
    sector: 'Information Technology',
    opened_at_utc: '2026-07-01T00:00:00Z',
  };
}

function stats(over: Partial<TradeStats> = {}): TradeStats {
  return {
    trades: 30,
    wins: 8,
    losses: 22,
    scratches: 0,
    win_rate: 8 / 30,
    gross_profit: 148,
    gross_loss: 680,
    net_pnl: -531.93,
    avg_win: 18,
    avg_loss: 31,
    profit_factor: 0.22,
    expectancy: -17.73,
    avg_holding_days: 12,
    best_trade: 47,
    worst_trade: -94,
    ...over,
  };
}

describe('unrealizedTotal', () => {
  it('sums open-position P&L', () => {
    expect(unrealizedTotal([pos(100), pos(-40), pos(0.5)])).toBeCloseTo(60.5, 6);
  });

  it('is zero for an empty or missing book', () => {
    expect(unrealizedTotal([])).toBe(0);
    expect(unrealizedTotal(null)).toBe(0);
    expect(unrealizedTotal(undefined)).toBe(0);
  });

  it('skips non-finite values instead of poisoning the sum with NaN', () => {
    expect(unrealizedTotal([pos(100), pos(Number.NaN)])).toBe(100);
  });
});

describe('pnlTone', () => {
  it('maps sign to tone, flat to neutral', () => {
    expect(pnlTone(1)).toBe('up');
    expect(pnlTone(-1)).toBe('down');
    expect(pnlTone(0)).toBe('neutral');
  });

  it('is neutral for null/NaN rather than green', () => {
    expect(pnlTone(null)).toBe('neutral');
    expect(pnlTone(undefined)).toBe('neutral');
    expect(pnlTone(Number.NaN)).toBe('neutral');
  });
});

describe('sampleTone', () => {
  it('refuses to color a statistic under the minimum sample', () => {
    expect(sampleTone(500, MIN_SAMPLE - 1)).toBe('neutral');
    expect(sampleTone(-500, 3)).toBe('neutral');
  });

  it('colors normally once the sample is large enough', () => {
    expect(sampleTone(500, MIN_SAMPLE)).toBe('up');
    expect(sampleTone(-500, MIN_SAMPLE + 10)).toBe('down');
  });

  it('treats a null value (undefined profit factor) as neutral', () => {
    expect(sampleTone(null, 100)).toBe('neutral');
  });
});

describe('formatWinRate', () => {
  it('renders a fraction as a one-decimal percent', () => {
    expect(formatWinRate(stats())).toBe('26.7%');
    expect(formatWinRate(stats({ trades: 4, win_rate: 1 }))).toBe('100.0%');
  });

  it('renders an em dash with no closed trades', () => {
    expect(formatWinRate(stats({ trades: 0, win_rate: 0 }))).toBe('—');
    expect(formatWinRate(null)).toBe('—');
  });
});

describe('formatProfitFactor', () => {
  it('renders two decimals', () => {
    expect(formatProfitFactor(0.22)).toBe('0.22');
    expect(formatProfitFactor(3)).toBe('3.00');
  });

  it('renders an em dash when undefined (no losses yet) — never infinity', () => {
    expect(formatProfitFactor(null)).toBe('—');
    expect(formatProfitFactor(Number.POSITIVE_INFINITY)).toBe('—');
  });
});

describe('pnlSplit', () => {
  it('denominates the share by magnitude, not by the net total', () => {
    // The live case: realized is negative while unrealized carries everything.
    // A net-denominated share would read as a negative percentage.
    const split = pnlSplit(-531.93, 9489);
    expect(split.total).toBeCloseTo(8957.07, 2);
    expect(split.realizedShare).toBeGreaterThan(0);
    expect(split.realizedShare).toBeLessThan(0.06);
  });

  it('is fully realized when nothing is open', () => {
    expect(pnlSplit(400, 0).realizedShare).toBe(1);
  });

  it('does not divide by zero on a flat book', () => {
    expect(pnlSplit(0, 0).realizedShare).toBe(0);
  });
});

describe('reconcileFreshness', () => {
  const now = new Date('2026-08-10T12:00:00Z');

  it('labels recent runs and marks them fresh', () => {
    expect(reconcileFreshness('2026-08-10T11:59:30Z', now)).toEqual({ label: 'az önce', stale: false });
    expect(reconcileFreshness('2026-08-10T11:35:00Z', now)).toEqual({ label: '25 dk önce', stale: false });
    expect(reconcileFreshness('2026-08-10T10:00:00Z', now)).toEqual({ label: '2 sa önce', stale: false });
  });

  it('marks the ledger stale once the hourly timer has clearly missed', () => {
    expect(reconcileFreshness('2026-08-10T04:00:00Z', now)).toEqual({ label: '8 sa önce', stale: true });
    expect(reconcileFreshness('2026-08-08T12:00:00Z', now)).toEqual({ label: '2 gün önce', stale: true });
  });

  it('treats a never-run reconcile as stale, not as fresh zero', () => {
    expect(reconcileFreshness(null, now)).toEqual({ label: 'hiç çalışmadı', stale: true });
    expect(reconcileFreshness(undefined, now)).toEqual({ label: 'hiç çalışmadı', stale: true });
  });

  it('parses a naive backend timestamp as UTC (not as device-local time)', () => {
    // The API serializes datetimes without a trailing Z; read locally that
    // would shift the age by the device offset and flip the stale flag.
    expect(reconcileFreshness('2026-08-10T10:00:00', now).label).toBe('2 sa önce');
  });
});

describe('realizedCaveat', () => {
  it('says so when nothing has closed', () => {
    expect(realizedCaveat(stats({ trades: 0, net_pnl: 0 }), 500)).toMatch(/Henüz kapanan işlem yok/);
    expect(realizedCaveat(null, 500)).toMatch(/Henüz kapanan işlem yok/);
  });

  it('flags a sample too small to read anything into', () => {
    expect(realizedCaveat(stats({ trades: 5, net_pnl: 90 }), 500)).toMatch(/anlamlı değil/);
  });

  it('names the realized/unrealized disagreement on a big-enough sample', () => {
    expect(realizedCaveat(stats(), 9489)).toMatch(/açık pozisyonların değerlemesinden/);
  });

  it('stays quiet when realized and unrealized agree', () => {
    expect(realizedCaveat(stats({ net_pnl: 1200 }), 9489)).toBeNull();
  });
});

describe('evalWindowNote', () => {
  function resp(over: Partial<TradesResponse> = {}): TradesResponse {
    return {
      trades: [],
      stats: stats(),
      reconciled_at_utc: '2026-08-16T06:00:00',
      window: 'eval',
      eval_start_utc: '2026-06-24T00:00:00Z',
      excluded_pre_eval: 26,
      ...over,
    } as TradesResponse;
  }

  it('names the cutoff and how many trades it hides', () => {
    // The live case: the record shrinks from 30 trades to 4 when the pre-clean-book
    // flatten is excluded. Unexplained, that shrinkage reads as cherry-picking.
    expect(evalWindowNote(resp())).toBe('Eval penceresi (2026-06-24): temiz kitap öncesi 26 işlem hariç.');
  });

  it('stays quiet when the cutoff hides nothing', () => {
    expect(evalWindowNote(resp({ excluded_pre_eval: 0 }))).toBeNull();
  });

  it('stays quiet on the all-time view — nothing is being filtered there', () => {
    expect(evalWindowNote(resp({ window: 'all_time' }))).toBeNull();
  });

  it('still names the exclusion when the API sends no cutoff date', () => {
    expect(evalWindowNote(resp({ eval_start_utc: null }))).toBe('Eval penceresi: temiz kitap öncesi 26 işlem hariç.');
  });

  it('is null-safe for a screen that renders before the fetch lands', () => {
    expect(evalWindowNote(null)).toBeNull();
    expect(evalWindowNote(undefined)).toBeNull();
  });
});

describe('exitClassLabelTr', () => {
  it('names every class the backend can emit', () => {
    expect(exitClassLabelTr('take_profit')).toBe('Kâr al');
    expect(exitClassLabelTr('stop')).toBe('Koruyucu stop');
    expect(exitClassLabelTr('decision_sell')).toBe('Ajan satış kararı');
    expect(exitClassLabelTr('flatten')).toBe('Flatten (ajan dışı)');
    expect(exitClassLabelTr('unknown')).toBe('Bilinmiyor (emir silinmiş)');
    expect(exitClassLabelTr('strategy')).toBe('Stratejinin kendi çıkışları');
  });

  it('renders a class it has no copy for instead of dropping the row', () => {
    // A backend that grows a new exit class must not make its trades vanish
    // from a money screen just because the app has not shipped copy for it.
    expect(exitClassLabelTr('assignment')).toBe('assignment');
    expect(exitClassLabelTr(null)).toBe('—');
    expect(exitClassLabelTr('  ')).toBe('—');
  });
});

function bucket(over: Partial<ExitBucket> = {}): ExitBucket {
  return {
    exit_class: 'stop',
    label: 'protective stop',
    trades: 4,
    wins: 1,
    losses: 3,
    win_rate: 0.25,
    net_pnl: -120,
    gross_profit: 40,
    gross_loss: 160,
    avg_pnl: -30,
    avg_holding_days: 2.5,
    ...over,
  };
}

describe('exitBreakdown', () => {
  it("orders the agent's own exits ahead of what happened to it", () => {
    const rows = exitBreakdown({
      by_exit: [
        bucket({ exit_class: 'flatten' }),
        bucket({ exit_class: 'stop' }),
        bucket({ exit_class: 'take_profit' }),
        bucket({ exit_class: 'unknown' }),
        bucket({ exit_class: 'decision_sell' }),
      ],
    } as TradesResponse);
    expect(rows.map((r) => r.exit_class)).toEqual([
      'take_profit',
      'stop',
      'decision_sell',
      'flatten',
      'unknown',
    ]);
  });

  it('drops empty paths — a never-taken exit is not a result', () => {
    const rows = exitBreakdown({
      by_exit: [bucket({ exit_class: 'take_profit', trades: 0 }), bucket({ exit_class: 'stop' })],
    } as TradesResponse);
    expect(rows.map((r) => r.exit_class)).toEqual(['stop']);
  });

  it('sorts an unrecognised class last but keeps it', () => {
    const rows = exitBreakdown({
      by_exit: [bucket({ exit_class: 'assignment' }), bucket({ exit_class: 'flatten' })],
    } as TradesResponse);
    expect(rows.map((r) => r.exit_class)).toEqual(['flatten', 'assignment']);
  });

  it('does not mutate the response array it was handed', () => {
    const by_exit = [bucket({ exit_class: 'flatten' }), bucket({ exit_class: 'take_profit' })];
    exitBreakdown({ by_exit } as TradesResponse);
    expect(by_exit.map((r) => r.exit_class)).toEqual(['flatten', 'take_profit']);
  });

  it('is empty when the backend sends no split at all', () => {
    expect(exitBreakdown({} as TradesResponse)).toEqual([]);
    expect(exitBreakdown(null)).toEqual([]);
  });
});

describe('strategyReading', () => {
  it("reports 'unavailable' when the backend predates attribution", () => {
    // The live case while the box is dark: the deployed API answers without
    // `strategy`, and the card must say the expectancy below is a blend —
    // not claim the agent has closed nothing.
    const r = strategyReading({} as TradesResponse);
    expect(r.status).toBe('unavailable');
    expect(r.bucket).toBeNull();
    expect(r.note).toContain('karışımı');
  });

  it("reports 'none' — a different claim — when nothing is attributed to the strategy", () => {
    const r = strategyReading({ strategy: null, unattributed: 0 } as TradesResponse);
    expect(r.status).toBe('none');
    expect(r.note).toContain('ajan dışı');
  });

  it('treats a zero-trade bucket as nothing attributed', () => {
    const r = strategyReading({ strategy: bucket({ exit_class: 'strategy', trades: 0 }) } as TradesResponse);
    expect(r.status).toBe('none');
  });

  it('attaches the sample size and refuses to call a small one meaningful', () => {
    const r = strategyReading({
      strategy: bucket({ exit_class: 'strategy', trades: 8 }),
    } as TradesResponse);
    expect(r.status).toBe('ok');
    expect(r.bucket?.trades).toBe(8);
    expect(r.note).toBe(`8 işlem — istatistiksel olarak anlamlı değil (≥${MIN_SAMPLE} gerekir).`);
  });

  it('drops the caveat once the sample clears the floor', () => {
    const r = strategyReading({
      strategy: bucket({ exit_class: 'strategy', trades: MIN_SAMPLE }),
    } as TradesResponse);
    expect(r.note).toBe(`${MIN_SAMPLE} işlem üzerinden.`);
  });

  it('is null-safe before the fetch lands', () => {
    expect(strategyReading(null).status).toBe('unavailable');
    expect(strategyReading(undefined).status).toBe('unavailable');
  });
});

describe('attributionNote', () => {
  it('says so when the split does not add up to the blended stats', () => {
    expect(attributionNote({ unattributed: 3 } as TradesResponse)).toBe(
      '3 işlem sınıflandırılamadı — kırılım toplam ile örtüşmüyor.',
    );
  });

  it('stays quiet on a fully attributed ledger, and when the field is absent', () => {
    expect(attributionNote({ unattributed: 0 } as TradesResponse)).toBeNull();
    expect(attributionNote({} as TradesResponse)).toBeNull();
    expect(attributionNote(null)).toBeNull();
  });
});
