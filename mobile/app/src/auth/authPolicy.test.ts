import { describe, it, expect } from '@jest/globals';

import { resolveAuthMode, SECURITY_LEVEL } from './authPolicy';

describe('resolveAuthMode', () => {
  it('uses biometric when hardware present and enrolled at biometric level', () => {
    expect(resolveAuthMode(true, SECURITY_LEVEL.BIOMETRIC_STRONG)).toBe('biometric');
    expect(resolveAuthMode(true, SECURITY_LEVEL.BIOMETRIC_WEAK)).toBe('biometric');
  });

  it('falls back to passcode when biometric hardware absent but device has a passcode', () => {
    expect(resolveAuthMode(false, SECURITY_LEVEL.SECRET)).toBe('passcode');
  });

  it('falls back to passcode when hardware present but only a passcode is enrolled', () => {
    // e.g. phone with Face ID hardware but user only set a PIN — must NOT lock them out
    expect(resolveAuthMode(true, SECURITY_LEVEL.SECRET)).toBe('passcode');
  });

  it('returns none only when the device has no lock at all', () => {
    expect(resolveAuthMode(false, SECURITY_LEVEL.NONE)).toBe('none');
    expect(resolveAuthMode(true, SECURITY_LEVEL.NONE)).toBe('none');
  });
});
