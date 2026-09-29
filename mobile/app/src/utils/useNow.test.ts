import { describe, it, expect, jest, afterEach } from '@jest/globals';

import { createClock } from './useNow';

afterEach(() => {
  jest.useRealTimers();
});

describe('createClock', () => {
  it('reads the time at creation and holds it between ticks', () => {
    let t = 1_000;
    const clock = createClock(null, () => t);
    t = 5_000;
    expect(clock.getSnapshot()).toBe(1_000);
    expect(clock.getSnapshot()).toBe(1_000);
  });

  it('catches up on subscribe and advances on every tick', () => {
    jest.useFakeTimers();
    let t = 1_000;
    const clock = createClock(1_000, () => t);
    const onChange = jest.fn();
    t = 2_000;
    const unsubscribe = clock.subscribe(onChange);
    expect(clock.getSnapshot()).toBe(2_000);

    t = 3_000;
    jest.advanceTimersByTime(1_000);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(clock.getSnapshot()).toBe(3_000);

    unsubscribe();
    t = 9_000;
    jest.advanceTimersByTime(5_000);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(clock.getSnapshot()).toBe(3_000);
  });

  it('a stopped clock never schedules a timer', () => {
    jest.useFakeTimers();
    const clock = createClock(null, () => 1);
    const onChange = jest.fn();
    clock.subscribe(onChange)();
    jest.advanceTimersByTime(60_000);
    expect(onChange).not.toHaveBeenCalled();
  });
});
