/**
 * Whether `promise` settles — resolves OR rejects — within `ms`.
 *
 * For waits whose outcome is read from somewhere else afterwards. The
 * device-unlock path waits for Firebase to restore a persisted session and
 * then reads `currentUser()`; what it needs from the wait is only which of two
 * things to say when nobody is signed in: "no session on this device" (the
 * restore finished) or "could not tell yet" (the timer won).
 *
 * Never rejects, and clears its timer either way, so a settled wait leaves no
 * pending timeout behind it.
 */
export function settlesWithin(promise: Promise<unknown>, ms: number): Promise<boolean> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), ms);
    const settle = () => {
      clearTimeout(timer);
      resolve(true);
    };
    promise.then(settle, settle);
  });
}
