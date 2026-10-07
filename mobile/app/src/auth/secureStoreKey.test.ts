import { describe, it, expect } from '@jest/globals';

import { secureStoreKey } from './secureStoreKey';

// expo-secure-store's own check (build/SecureStore.js, isValidKey).
const SECURE_STORE_KEY = /^[\w.-]+$/;

describe('Firebase persistence keys made fit for SecureStore', () => {
  it('turns the key Firebase signs in with into one SecureStore accepts', () => {
    // The key that made every native sign-in fail after the server said yes.
    const firebaseKey = 'firebase:authUser:AIzaSyExampleKey_123-abc:[DEFAULT]';
    expect(SECURE_STORE_KEY.test(firebaseKey)).toBe(false);
    expect(SECURE_STORE_KEY.test(secureStoreKey(firebaseKey))).toBe(true);
  });

  it('accepts every key Firebase persists, not only the user', () => {
    for (const key of [
      'firebase:authUser:k:[DEFAULT]',
      'firebase:redirectUser:k:[DEFAULT]',
      'firebase:persistence:k:[DEFAULT]',
      '__sak',
    ]) {
      expect(SECURE_STORE_KEY.test(secureStoreKey(key))).toBe(true);
    }
  });

  it('never maps two different keys to the same one', () => {
    const keys = [
      'a:b',
      'a_b',
      'a_003ab',
      'a.b',
      'a-b',
      'firebase:authUser:k:[DEFAULT]',
      'firebase_authUser_k_DEFAULT',
      'é',
      'Ā0',
      '\u0010' + '0',
    ];
    const mapped = keys.map(secureStoreKey);
    expect(new Set(mapped).size).toBe(keys.length);
  });

  it('is stable, so a session written by one launch is read by the next', () => {
    const key = 'firebase:authUser:k:[DEFAULT]';
    expect(secureStoreKey(key)).toBe(secureStoreKey(key));
  });

  it('leaves letters, digits, dots and hyphens as they are', () => {
    expect(secureStoreKey('abc.DEF-123')).toBe('abc.DEF-123');
  });
});
