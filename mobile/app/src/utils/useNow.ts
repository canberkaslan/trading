import { useMemo, useSyncExternalStore } from 'react';

/**
 * The wall clock as React state.
 *
 * Relative-age labels ("3 dk önce") and elapsed counters used to call
 * `Date.now()` in render. That works today only because nothing memoises
 * render: once React Compiler is on, a label computed from `Date.now()` and a
 * stable timestamp is cached and stops advancing. Reading the clock through a
 * store makes the time an input React knows about, so it is re-read on every
 * tick instead of whenever something else happens to re-render.
 */

export interface Clock {
  subscribe: (onChange: () => void) => () => void;
  getSnapshot: () => number;
}

/** `intervalMs: null` is a stopped clock: it keeps the time it was created at. */
export function createClock(intervalMs: number | null, read: () => number = Date.now): Clock {
  let now = read();
  return {
    subscribe(onChange) {
      if (intervalMs == null) return () => {};
      // Catch up on subscribe: React re-reads the snapshot right after it
      // subscribes and re-renders if the time moved since render.
      now = read();
      const id = setInterval(() => {
        now = read();
        onChange();
      }, intervalMs);
      return () => clearInterval(id);
    },
    getSnapshot: () => now,
  };
}

/**
 * Minute-granular age labels only need a tick well inside a minute; 15 s keeps
 * them at most one step behind without re-rendering every second.
 */
export const AGE_TICK_MS = 15_000;

export function useNow(intervalMs: number | null = AGE_TICK_MS): number {
  const clock = useMemo(() => createClock(intervalMs), [intervalMs]);
  return useSyncExternalStore(clock.subscribe, clock.getSnapshot, clock.getSnapshot);
}
