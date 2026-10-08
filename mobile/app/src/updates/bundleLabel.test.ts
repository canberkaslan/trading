import { describe, it, expect } from '@jest/globals';

import { bundleLabel } from './bundleLabel';

const at = new Date(2026, 9, 8, 9, 20);

describe('the running-bundle footer', () => {
  it('names a downloaded update by the id prefix `eas update:list` shows', () => {
    expect(
      bundleLabel({
        isEnabled: true,
        isEmbeddedLaunch: false,
        updateId: '01a11a2c-0740-76dd-bbe5-f865fda020b5',
        createdAt: at,
      }),
    ).toBe('Paket: OTA 01a11a2c · 08.10 09:20');
  });

  it('says when the binary is still running its own bundle', () => {
    expect(
      bundleLabel({ isEnabled: true, isEmbeddedLaunch: true, updateId: 'x', createdAt: at }),
    ).toBe('Paket: gömülü · 08.10 09:20');
  });

  it('survives a missing or invalid time', () => {
    const base = { isEnabled: true, isEmbeddedLaunch: false, updateId: 'abcdef0123' };
    expect(bundleLabel({ ...base, createdAt: null })).toBe('Paket: OTA abcdef01');
    expect(bundleLabel({ ...base, createdAt: new Date(NaN) })).toBe('Paket: OTA abcdef01');
  });

  it('tells a binary with OTA switched off apart from a dev client', () => {
    expect(
      bundleLabel({ isEnabled: false, isEmbeddedLaunch: true, updateId: null, createdAt: null }),
    ).toBe('Paket: OTA kapalı');
    expect(
      bundleLabel({ isEnabled: true, isEmbeddedLaunch: true, updateId: null, createdAt: null }),
    ).toBe('Paket: geliştirme');
  });
});
