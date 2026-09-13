import { describe, it, expect } from '@jest/globals';

import { signInErrorTr } from './signInError';

describe('sign-in errors do not leak whether an account exists', () => {
  it('gives the SAME message for a missing user and a wrong password', () => {
    // Distinguishing them turns the login form into an account-existence
    // oracle: anyone could enumerate which family members have accounts.
    const notFound = signInErrorTr({ code: 'auth/user-not-found' });
    const wrongPw = signInErrorTr({ code: 'auth/wrong-password' });
    expect(notFound).toBe(wrongPw);
  });

  it('treats the collapsed modern code the same way', () => {
    // Newer Firebase returns auth/invalid-credential for both, for this exact
    // reason. All three must agree or the older codes reintroduce the oracle.
    expect(signInErrorTr({ code: 'auth/invalid-credential' })).toBe(
      signInErrorTr({ code: 'auth/user-not-found' }),
    );
  });

  it('never echoes the raw Firebase code to the user', () => {
    for (const code of [
      'auth/user-not-found',
      'auth/wrong-password',
      'auth/invalid-credential',
      'auth/invalid-email',
      'auth/user-disabled',
      'auth/too-many-requests',
      'auth/network-request-failed',
      'auth/some-future-code',
    ]) {
      expect(signInErrorTr({ code })).not.toContain('auth/');
    }
  });
});

describe('the actionable cases are distinguished', () => {
  it('a malformed address is its own message, since the user can fix it', () => {
    expect(signInErrorTr({ code: 'auth/invalid-email' })).not.toBe(
      signInErrorTr({ code: 'auth/wrong-password' }),
    );
  });

  it('a disabled account says so rather than blaming the password', () => {
    expect(signInErrorTr({ code: 'auth/user-disabled' })).toContain('devre dışı');
  });

  it('rate limiting tells the user to wait, not to re-check credentials', () => {
    expect(signInErrorTr({ code: 'auth/too-many-requests' })).toContain('bekle');
  });

  it('a network failure is not reported as bad credentials', () => {
    // The one that used to send people to re-type a correct password.
    const net = signInErrorTr({ code: 'auth/network-request-failed' });
    expect(net).toContain('ulaşılamadı');
    expect(net).not.toBe(signInErrorTr({ code: 'auth/wrong-password' }));
  });
});

describe('malformed errors still produce something sayable', () => {
  it.each([null, undefined, 'a string', 42, {}, { code: null }, new Error('boom')])(
    'handles %p without throwing',
    (value) => {
      expect(typeof signInErrorTr(value)).toBe('string');
      expect(signInErrorTr(value).length).toBeGreaterThan(0);
    },
  );
});
