import { describe, it, expect, beforeAll } from '@jest/globals';
import { createInstance, type i18n as I18n } from 'i18next';

import en from './en.json';
import tr from './tr.json';
import { formatHorizon, parseHorizon } from './horizon';

let inst: I18n;
beforeAll(async () => {
  inst = createInstance();
  await inst.init({
    resources: { en: { translation: en }, tr: { translation: tr } },
    lng: 'en',
    fallbackLng: 'en',
    interpolation: { escapeValue: false },
  });
});
const tIn = (lng: string) => inst.getFixedT(lng) as unknown as (k: string, o?: Record<string, unknown>) => string;

describe('parseHorizon', () => {
  it('parses the shapes the PM model writes', () => {
    expect(parseHorizon('6-12 months')).toEqual({ kind: 'range', from: 6, to: 12, unit: 'month' });
    expect(parseHorizon('3–6 Months')).toEqual({ kind: 'range', from: 3, to: 6, unit: 'month' });
    expect(parseHorizon('1 to 2 years')).toEqual({ kind: 'range', from: 1, to: 2, unit: 'year' });
    expect(parseHorizon('2 weeks')).toEqual({ kind: 'range', from: 2, to: null, unit: 'week' });
    expect(parseHorizon('1 month.')).toEqual({ kind: 'range', from: 1, to: null, unit: 'month' });
    expect(parseHorizon('short-term')).toEqual({ kind: 'term', term: 'short' });
    expect(parseHorizon('Long term')).toEqual({ kind: 'term', term: 'long' });
    expect(parseHorizon('mid-term')).toEqual({ kind: 'term', term: 'medium' });
  });

  it('returns null for anything else', () => {
    expect(parseHorizon(null)).toBeNull();
    expect(parseHorizon('')).toBeNull();
    expect(parseHorizon('until earnings')).toBeNull();
  });
});

describe('formatHorizon', () => {
  it('renders in Turkish on a Turkish UI (no "6-12 months" in TR)', () => {
    expect(formatHorizon('6-12 months', tIn('tr'))).toBe('6–12 ay');
    expect(formatHorizon('1 month', tIn('tr'))).toBe('1 ay');
    expect(formatHorizon('short-term', tIn('tr'))).toBe('Kısa vade');
  });

  it('renders in English on an English UI, with plurals', () => {
    expect(formatHorizon('6-12 months', tIn('en'))).toBe('6–12 months');
    expect(formatHorizon('1 month', tIn('en'))).toBe('1 month');
    expect(formatHorizon('2 weeks', tIn('en'))).toBe('2 weeks');
  });

  it('shows unparseable text as written and a dash when absent', () => {
    expect(formatHorizon('until earnings', tIn('tr'))).toBe('until earnings');
    expect(formatHorizon(null, tIn('tr'))).toBe('—');
    expect(formatHorizon('  ', tIn('tr'))).toBe('—');
  });
});
