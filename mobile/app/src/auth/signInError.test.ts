import { describe, it, expect, jest, afterEach } from '@jest/globals';

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
    // auth/internal-error logs its cause in dev; that is console, not UI.
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    for (const code of [
      'auth/user-not-found',
      'auth/wrong-password',
      'auth/invalid-credential',
      'auth/invalid-email',
      'auth/user-disabled',
      'auth/too-many-requests',
      'auth/network-request-failed',
      'auth/email-already-in-use',
      'auth/internal-error',
      'auth/some-future-code',
    ]) {
      expect(signInErrorTr({ code })).not.toContain('auth/');
    }
    warn.mockRestore();
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

describe('the codes that used to fall to the generic message', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('an existing address on sign-up points at the sign-in tab', () => {
    const msg = signInErrorTr({ code: 'auth/email-already-in-use' });
    expect(msg).toContain('zaten bir hesap var');
    expect(msg).not.toBe(signInErrorTr({ code: 'auth/some-future-code' }));
  });

  it('an internal error is not reported as bad credentials', () => {
    // What every native sign-in returned while the keystore write was failing.
    jest.spyOn(console, 'warn').mockImplementation(() => {});
    const msg = signInErrorTr({ code: 'auth/internal-error' });
    expect(msg).not.toBe(signInErrorTr({ code: 'auth/wrong-password' }));
    expect(msg).not.toBe(signInErrorTr({ code: 'auth/some-future-code' }));
  });

  it('logs the underlying cause of an internal error in dev', () => {
    // Firebase's message for it says nothing; the cause is in customData.
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const cause = new Error('Invalid key provided to SecureStore');
    signInErrorTr({ code: 'auth/internal-error', customData: { originalError: cause } });
    expect(warn).toHaveBeenCalledWith(expect.any(String), cause);
  });

  it('does not log for the ordinary codes', () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    signInErrorTr({ code: 'auth/wrong-password' });
    expect(warn).not.toHaveBeenCalled();
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
