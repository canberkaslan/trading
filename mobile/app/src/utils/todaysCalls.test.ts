import { describe, it, expect } from '@jest/globals';

import { todaysCalls, summaryLine, type DecisionLike } from './todaysCalls';

const NOW = new Date('2026-09-14T20:00:00');

const d = (id: string, ticker: string, rating: string, iso: string): DecisionLike => ({
  decision_id: id,
  ticker,
  rating,
  timestamp_utc: iso,
});

describe("today's calls", () => {
  it('keeps only decisions from the local calendar day', () => {
    // Built from local Date objects rather than literal Z strings: a UTC
    // timestamp near midnight lands on a different calendar day depending on
    // the reader's offset, which is the whole point of grouping locally and
    // would otherwise make this test pass or fail by timezone.
    const rows = [
      d('1', 'AAPL', 'Hold', new Date('2026-09-14T09:00:00').toISOString()),
      d('2', 'NVDA', 'Buy', new Date('2026-09-13T09:00:00').toISOString()),
    ];
    const out = todaysCalls(rows, NOW);
    expect(out.items.map((x) => x.ticker)).toEqual(['AAPL']);
  });

  it('uses the local day, not UTC', () => {
    // The run fires at 22:30 UTC. A viewer whose local day already rolled over
    // should see those under their own date, not the UTC one.
    const local = new Date('2026-09-14T23:00:00');
    const rows = [d('1', 'AAPL', 'Hold', new Date('2026-09-14T21:00:00').toISOString())];
    expect(todaysCalls(rows, local).items).toHaveLength(1);
  });

  it('counts per rating', () => {
    const rows = [
      d('1', 'AAPL', 'Hold', new Date('2026-09-14T09:00:00').toISOString()),
      d('2', 'MSFT', 'Hold', new Date('2026-09-14T09:05:00').toISOString()),
      d('3', 'NVDA', 'Buy', new Date('2026-09-14T09:10:00').toISOString()),
    ];
    expect(todaysCalls(rows, NOW).byRating).toEqual({ Hold: 2, Buy: 1 });
  });

  it('puts the actionable ratings first', () => {
    // A Sell must not hide under a row of Holds.
    const rows = [
      d('1', 'AAPL', 'Hold', new Date('2026-09-14T09:00:00').toISOString()),
      d('2', 'NVDA', 'Sell', new Date('2026-09-14T09:05:00').toISOString()),
      d('3', 'MSFT', 'Buy', new Date('2026-09-14T09:06:00').toISOString()),
    ];
    expect(todaysCalls(rows, NOW).ratings).toEqual(['Sell', 'Buy', 'Hold']);
  });

  it('sorts an unrecognised rating last, not first', () => {
    // It is not more urgent for being unknown, and leading with it would be a
    // louder claim than the app can back up.
    const rows = [
      d('1', 'AAPL', 'Weird', new Date('2026-09-14T09:00:00').toISOString()),
      d('2', 'NVDA', 'Sell', new Date('2026-09-14T09:05:00').toISOString()),
    ];
    expect(todaysCalls(rows, NOW).ratings).toEqual(['Sell', 'Weird']);
  });

  it('survives missing and malformed input', () => {
    expect(todaysCalls(undefined, NOW).items).toEqual([]);
    expect(todaysCalls([d('1', 'X', 'Hold', 'not a date')], NOW).items).toEqual([]);
  });
});

describe('summary line', () => {
  it('reads as a sentence', () => {
    const rows = [
      d('1', 'AAPL', 'Hold', new Date('2026-09-14T09:00:00').toISOString()),
      d('2', 'MSFT', 'Hold', new Date('2026-09-14T09:05:00').toISOString()),
      d('3', 'NVDA', 'Buy', new Date('2026-09-14T09:10:00').toISOString()),
    ];
    expect(summaryLine(todaysCalls(rows, NOW))).toBe('1 Buy · 2 Hold');
  });

  it('is null when today has produced nothing', () => {
    // Null, not "0 karar": the run happens once a day, and before it fires
    // "nothing yet" and "nothing decided" are different facts.
    expect(summaryLine(todaysCalls([], NOW))).toBeNull();
  });
});
