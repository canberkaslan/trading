import { describe, it, expect } from '@jest/globals';

import {
  formatTokens,
  formatLatency,
  debateEntries,
  debateRoleLabel,
  decisionRoute,
  measured,
  sumMeasured,
  decisionTokenTotal,
  decisionLatencyTotal,
  agentMetaLine,
} from './decision';

describe('formatTokens', () => {
  it('leaves sub-1k counts as integers', () => {
    expect(formatTokens(0)).toBe('0');
    expect(formatTokens(980)).toBe('980');
    expect(formatTokens(999)).toBe('999');
  });

  it('compacts thousands with one decimal', () => {
    expect(formatTokens(1234)).toBe('1.2k');
    expect(formatTokens(1000)).toBe('1.0k');
    expect(formatTokens(25800)).toBe('25.8k');
  });

  it('rounds fractional sub-1k inputs', () => {
    expect(formatTokens(12.6)).toBe('13');
  });

  it('em-dashes null/NaN/negative', () => {
    expect(formatTokens(null)).toBe('—');
    expect(formatTokens(undefined)).toBe('—');
    expect(formatTokens(NaN)).toBe('—');
    expect(formatTokens(-5)).toBe('—');
  });
});

describe('formatLatency', () => {
  it('keeps sub-second in ms', () => {
    expect(formatLatency(0)).toBe('0 ms');
    expect(formatLatency(850)).toBe('850 ms');
    expect(formatLatency(999)).toBe('999 ms');
  });

  it('flips >=1s to seconds', () => {
    expect(formatLatency(1000)).toBe('1.0 sn');
    expect(formatLatency(4200)).toBe('4.2 sn');
    expect(formatLatency(12500)).toBe('12.5 sn');
  });

  it('em-dashes null/NaN/negative', () => {
    expect(formatLatency(null)).toBe('—');
    expect(formatLatency(undefined)).toBe('—');
    expect(formatLatency(NaN)).toBe('—');
    expect(formatLatency(-1)).toBe('—');
  });
});

describe('debateEntries', () => {
  it('returns [] for null/undefined/empty', () => {
    expect(debateEntries(null)).toEqual([]);
    expect(debateEntries(undefined)).toEqual([]);
    expect(debateEntries({})).toEqual([]);
  });

  it('drops empty / whitespace-only entries', () => {
    expect(debateEntries({ bull: '  ', bear: 'sell it' })).toEqual([
      { role: 'bear', text: 'sell it' },
    ]);
  });

  it('trims text', () => {
    expect(debateEntries({ bull: '  buy  ' })).toEqual([{ role: 'bull', text: 'buy' }]);
  });

  it('orders known roles by the debate flow', () => {
    const out = debateEntries({
      portfolio_manager: 'pm',
      bull: 'b',
      trader: 't',
      bear: 'be',
    });
    expect(out.map((e) => e.role)).toEqual(['bull', 'bear', 'trader', 'portfolio_manager']);
  });

  it('keeps unknown roles after known ones', () => {
    const out = debateEntries({ mystery: 'x', bull: 'b' });
    expect(out.map((e) => e.role)).toEqual(['bull', 'mystery']);
  });
});

describe('debateRoleLabel', () => {
  it('title-cases underscore keys', () => {
    expect(debateRoleLabel('bull_researcher')).toBe('Bull Researcher');
    expect(debateRoleLabel('trader')).toBe('Trader');
    expect(debateRoleLabel('risk_manager')).toBe('Risk Manager');
  });
});

describe('decisionRoute', () => {
  it('carries the decision id so the detail opens THAT decision', () => {
    expect(decisionRoute('NVDA', 'dec-17')).toBe('/trade/NVDA?decisionId=dec-17');
  });

  it('falls back to the ticker alone when no id is known', () => {
    expect(decisionRoute('NVDA')).toBe('/trade/NVDA');
    expect(decisionRoute('NVDA', null)).toBe('/trade/NVDA');
  });

  it('encodes both parts', () => {
    expect(decisionRoute('BF.B', 'a/b c')).toBe('/trade/BF.B?decisionId=a%2Fb%20c');
  });
});

describe('measured counters', () => {
  const unmetered = { tokens_in: 0, tokens_out: 0, latency_ms: 0 };

  it('reads a pipeline 0 as not measured', () => {
    expect(measured(0)).toBeNull();
    expect(measured(null)).toBeNull();
    expect(measured(1200)).toBe(1200);
    expect(sumMeasured([0, 0, null])).toBeNull();
    expect(sumMeasured([0, 5, 7])).toBe(12);
  });

  it('prefers the decision-level metered total over per-agent zeros', () => {
    expect(decisionTokenTotal({ tokens_in: 4000, tokens_out: 500, reasoning: [unmetered] })).toBe(4500);
    expect(decisionTokenTotal({ tokens_in: null, tokens_out: null, reasoning: [unmetered] })).toBeNull();
    expect(decisionTokenTotal({ reasoning: [{ tokens_in: 10, tokens_out: 5, latency_ms: 0 }] })).toBe(15);
  });

  it('has no latency total when no agent was timed', () => {
    expect(decisionLatencyTotal({ reasoning: [unmetered, unmetered] })).toBeNull();
    expect(decisionLatencyTotal({ reasoning: [{ latency_ms: 800 }, unmetered] })).toBe(800);
  });

  it('drops the per-agent meta line instead of printing zeros', () => {
    expect(agentMetaLine(unmetered)).toBeNull();
    expect(agentMetaLine({ tokens_in: 1200, tokens_out: 0, latency_ms: 4200 })).toBe(
      '1.2k↓ / —↑ token · 4.2 sn',
    );
  });
});
