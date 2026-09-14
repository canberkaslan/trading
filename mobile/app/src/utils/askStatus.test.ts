import { describe, it, expect } from '@jest/globals';

import { elapsedTr, statusLine, PHASE_TR } from './askStatus';

const T0 = Date.parse('2026-09-14T13:21:08Z');

describe('elapsed time is the part that proves the job is alive', () => {
  it('counts seconds under a minute', () => {
    expect(elapsedTr('2026-09-14T13:21:08Z', T0 + 42_000)).toBe('42 sn');
  });

  it('counts minutes and seconds past one', () => {
    expect(elapsedTr('2026-09-14T13:21:08Z', T0 + 282_000)).toBe('4 dk 42 sn');
  });

  it('is empty rather than wrong when the start is unknown', () => {
    expect(elapsedTr(undefined, T0)).toBe('');
    expect(elapsedTr('not a date', T0)).toBe('');
  });

  it('does not render a negative age from a clock skew', () => {
    // The server stamps created_utc; a phone a few seconds behind must not
    // show "-3 sn", which reads as broken.
    expect(elapsedTr('2026-09-14T13:21:08Z', T0 - 3_000)).toBe('');
  });
});

describe('the running line says which of the eighteen steps is going', () => {
  it('translates a known graph node', () => {
    const s = statusLine('MSFT', 'running', 'Research Manager', '2026-09-14T13:21:08Z', T0 + 60_000);
    expect(s).toContain('araştırma sentezi');
    expect(s).toContain('1 dk 0 sn');
    expect(s).toContain('MSFT');
  });

  it('shows an unknown node verbatim rather than hiding it', () => {
    // A node we have no word for is still more informative than silence.
    const s = statusLine('MSFT', 'running', 'Some New Node', '2026-09-14T13:21:08Z', T0 + 1000);
    expect(s).toContain('Some New Node');
  });

  it('falls back to the generic line before the first node reports', () => {
    const s = statusLine('MSFT', 'running', null, '2026-09-14T13:21:08Z', T0 + 5_000);
    expect(s).toContain('ajanlar tartışıyor');
    expect(s).toContain('5 sn');
  });

  it('a queued job says queued, not a stage', () => {
    expect(statusLine('MSFT', 'queued', null, undefined, T0)).toContain('sıraya alındı');
  });
});

describe('the stated estimate matches what was measured', () => {
  it('every graph node the pipeline emits has a phrase', () => {
    // A node without one shows its English name mid-sentence in a Turkish UI.
    const emitted = [
      'Market Analyst', 'Sentiment Analyst', 'News Analyst', 'Fundamentals Analyst',
      'Bull Researcher', 'Bear Researcher', 'Research Manager', 'Trader',
      'Aggressive Analyst', 'Neutral Analyst', 'Conservative Analyst', 'Portfolio Manager',
    ];
    for (const node of emitted) expect(PHASE_TR[node]).toBeTruthy();
  });
});
