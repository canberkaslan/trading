import { describe, it, expect } from '@jest/globals';

import { makeInviteGate } from './inviteCode';

describe('davet kodu kapisi', () => {
  it('kod yapilandirilmamissa kayit KAPALI ve hicbir kod gecmez', () => {
    // Fail-closed. Unutulmus bir ortam degiskeni kapiyi acmamali.
    for (const missing of [undefined, null, '', '   ']) {
      const g = makeInviteGate(missing);
      expect(g.enabled).toBe(false);
      expect(g.isValid('')).toBe(false);
      expect(g.isValid('herhangi')).toBe(false);
    }
  });

  it('kod yapilandirilmissa kayit acilir', () => {
    const g = makeInviteGate('TRADER-2026');
    expect(g.enabled).toBe(true);
    expect(g.isValid('TRADER-2026')).toBe(true);
  });

  it('bosluk kirpilir ve buyuk/kucuk harf onemsizdir', () => {
    // Kod elde, muhtemelen telefonda yazilacak; "dogru yazdim ama kabul
    // etmedi" en can sikici basarisizlik bicimi.
    const g = makeInviteGate('  TRADER-2026  ');
    expect(g.isValid('trader-2026')).toBe(true);
    expect(g.isValid('  Trader-2026  ')).toBe(true);
  });

  it('yanlis kod gecmez', () => {
    const g = makeInviteGate('TRADER-2026');
    for (const wrong of ['TRADER-2025', '', 'TRADER', 'TRADER-2026X', ' ']) {
      expect(g.isValid(wrong)).toBe(false);
    }
  });

  it('bos kod, bos beklenen kodla ESLESMEZ', () => {
    // En sinsi hata bicimi: iki tarafi da bos birakip esitlik yakalamak.
    expect(makeInviteGate('').isValid('')).toBe(false);
    expect(makeInviteGate(undefined).isValid('')).toBe(false);
  });
});
