import { describe, it, expect, jest, afterEach } from '@jest/globals';

import { settlesWithin } from './settlesWithin';

afterEach(() => {
  jest.useRealTimers();
});

describe('settlesWithin', () => {
  it('is true for a promise that is already resolved', async () => {
    await expect(settlesWithin(Promise.resolve(), 1_000)).resolves.toBe(true);
  });

  it('is true when the promise resolves before the timer', async () => {
    jest.useFakeTimers();
    let done!: () => void;
    const pending = new Promise<void>((r) => {
      done = r;
    });
    const result = settlesWithin(pending, 3_000);
    jest.advanceTimersByTime(1_000);
    done();
    await expect(result).resolves.toBe(true);
  });

  it('is false when the timer wins', async () => {
    // Firebase's session restore reloads the user over the network; on a bad
    // connection it can take longer than the caller is willing to wait.
    jest.useFakeTimers();
    const result = settlesWithin(new Promise<void>(() => {}), 3_000);
    jest.advanceTimersByTime(3_000);
    await expect(result).resolves.toBe(false);
  });

  it('counts a rejection as settled and does not reject itself', async () => {
    // The outcome is read from currentUser() afterwards; a failed restore has
    // finished, it just finished with nobody signed in.
    await expect(settlesWithin(Promise.reject(new Error('boom')), 1_000)).resolves.toBe(true);
  });

  it('leaves no timer behind once the promise settles', async () => {
    jest.useFakeTimers();
    await settlesWithin(Promise.resolve(), 3_000);
    expect(jest.getTimerCount()).toBe(0);
  });
});
