import { describe, it, expect } from '@jest/globals';

import { statusOf, isAuthError, authErrorKind } from './apiError';

/** The shape ky throws: an Error carrying the Response. */
const httpError = (status: number) =>
  Object.assign(
    new Error(`Request failed with status code ${status}: GET https://trader.fusapp.com/v1/x`),
    { name: 'HTTPError', response: { status } },
  );

describe('statusOf', () => {
  it('reads the status off a ky HTTPError', () => {
    expect(statusOf(httpError(401))).toBe(401);
    expect(statusOf(httpError(503))).toBe(503);
  });

  it('returns null for everything that carries no response', () => {
    // A timeout or a DNS failure is a plain Error — no response at all. That
    // is the case that must keep the connection message.
    expect(statusOf(new Error('Network request failed'))).toBeNull();
    expect(statusOf(new TypeError('Failed to fetch'))).toBeNull();
    expect(statusOf(null)).toBeNull();
    expect(statusOf(undefined)).toBeNull();
    expect(statusOf('401')).toBeNull();
    expect(statusOf({ response: null })).toBeNull();
    expect(statusOf({ response: { status: '401' } })).toBeNull();
  });
});

describe('isAuthError', () => {
  it('is true for the statuses that mean "your credential was refused"', () => {
    expect(isAuthError(httpError(401))).toBe(true);
    expect(isAuthError(httpError(403))).toBe(true);
  });

  it('is false for every other failure', () => {
    // 426 has its own handling in the client; it is not an auth problem.
    for (const s of [400, 404, 408, 426, 429, 500, 502, 503, 504]) {
      expect(isAuthError(httpError(s))).toBe(false);
    }
    expect(isAuthError(new Error('Network request failed'))).toBe(false);
    expect(isAuthError(undefined)).toBe(false);
  });

  it('does not depend on instanceof, so a duplicated ky cannot defeat it', () => {
    // Two copies of ky in one bundle produce two HTTPError classes. A check
    // written as `err instanceof HTTPError` would return false for the other
    // copy and silently fall back to the connection message — the exact bug
    // this module removes. A duck-typed object must therefore classify.
    expect(isAuthError({ response: { status: 401 } })).toBe(true);
  });
});

describe('authErrorKind', () => {
  it('says the token is missing when none is stored', () => {
    expect(authErrorKind(httpError(401), false)).toBe('missing');
  });

  it('says the token is invalid when one is stored and still refused', () => {
    expect(authErrorKind(httpError(401), true)).toBe('invalid');
  });

  it('is null for non-auth failures regardless of stored token', () => {
    expect(authErrorKind(httpError(503), false)).toBeNull();
    expect(authErrorKind(httpError(503), true)).toBeNull();
    expect(authErrorKind(new Error('offline'), true)).toBeNull();
  });
});
