import { describe, it, expect } from '@jest/globals';

import {
  statusOf,
  isAuthError,
  authErrorKind,
  isPartialFlatten,
  PARTIAL_FLATTEN_TR,
} from './apiError';

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

describe('a partial flatten is not a connection problem', () => {
  const partial = Object.assign(
    new Error('Request failed with status code 502: POST https://h/v1/orders/kill-switch'),
    { name: 'HTTPError', response: { status: 502 }, message: 'kill switch armed, but flatten was PARTIAL: 2/5 submitted' },
  );

  it('recognises the server saying PARTIAL', () => {
    // The positions that did NOT close have already had their stop legs
    // cancelled. Calling this "could not reach the server" sends the operator
    // to check their wifi while money sits unprotected.
    expect(isPartialFlatten(partial)).toBe(true);
  });

  it('is not confused by an ordinary 502', () => {
    const plain = Object.assign(new Error('bad gateway'), {
      response: { status: 502 },
      message: 'broker refused cancel: timeout',
    });
    expect(isPartialFlatten(plain)).toBe(false);
  });

  it('is not triggered by other statuses', () => {
    const notFound = Object.assign(new Error('x'), {
      response: { status: 404 },
      message: 'flatten was PARTIAL',
    });
    expect(isPartialFlatten(notFound)).toBe(false);
  });

  it('survives a transport error with no response at all', () => {
    expect(isPartialFlatten(new Error('Network request failed'))).toBe(false);
    expect(isPartialFlatten(null)).toBe(false);
  });

  it('the message names the actual danger, not a generic failure', () => {
    expect(PARTIAL_FLATTEN_TR).toContain('stop koruması kaldırıldı');
    expect(PARTIAL_FLATTEN_TR).not.toContain('sunucuya ulaşılamadı');
  });
});
