import { describe, it, expect } from '@jest/globals';

import { toCaps } from './caps';

describe('toCaps', () => {
  it('uses Turkish casing for Turkish text', () => {
    expect(toCaps('Giriş', 'tr')).toBe('GİRİŞ');
    expect(toCaps('Portföy yöneticisi gerekçesi', 'tr')).toBe('PORTFÖY YÖNETİCİSİ GEREKÇESİ');
    expect(toCaps('Çıkış yolu', 'tr')).toBe('ÇIKIŞ YOLU');
    expect(toCaps('diğer', 'tr-TR')).toBe('DİĞER');
  });

  it('uses English casing for English text, whatever the device', () => {
    expect(toCaps('Underweight', 'en')).toBe('UNDERWEIGHT');
    expect(toCaps('Entry', 'en-US')).toBe('ENTRY');
  });

  it('never produces a dotted capital I from English input cased as English', () => {
    expect(toCaps('Live — real money', 'en')).not.toMatch(/İ/);
  });
});
