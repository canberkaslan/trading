/**
 * Apply a waiting OTA on the launch that finds it, not the one after.
 *
 * expo-updates' default (`checkAutomatically: ON_LOAD`,
 * `fallbackToCacheTimeout: 0`) starts the app on whatever bundle it already
 * has and downloads a newer one in the background for the NEXT cold start. So
 * every fix reached a phone one launch late: after #97 a tester installed
 * build 12 from TestFlight, opened it, met the very sign-in failure #97 fixed —
 * build 12's own bundle predates it — and reported the fix as not working.
 * Changing those two settings would change the native config, and with it the
 * fingerprint, cutting every installed binary off from OTA; this does the same
 * from JS, which an OTA can ship.
 *
 * Only within a short window after launch: a reload throws away whatever is on
 * screen, which in the first seconds is nothing and minutes later may be a
 * half-typed order. An update found later still applies on the next cold
 * start, exactly as before. Every failure is swallowed for the same reason —
 * this is an accelerator, the default path is still underneath it.
 */

/** How long after launch a freshly downloaded update may still restart the app. */
export const RELOAD_WINDOW_MS = 10_000;

/** The part of `expo-updates` this needs; the module itself satisfies it. */
export interface UpdatesApi {
  readonly isEnabled: boolean;
  checkForUpdateAsync(): Promise<{ isAvailable: boolean }>;
  fetchUpdateAsync(): Promise<{ isNew: boolean }>;
  reloadAsync(): Promise<void>;
}

export type LaunchUpdateOutcome = 'disabled' | 'current' | 'reloaded' | 'next-launch' | 'failed';

export async function applyUpdateOnLaunch(
  api: UpdatesApi,
  now: () => number = Date.now,
): Promise<LaunchUpdateOutcome> {
  // False in dev clients, on web, and whenever expo-updates could not start.
  if (!api.isEnabled) return 'disabled';
  const started = now();
  try {
    const { isAvailable } = await api.checkForUpdateAsync();
    if (!isAvailable) return 'current';
    // `isNew` is false when the server's update is the one already running,
    // which is what makes this safe to run again after its own reload.
    const { isNew } = await api.fetchUpdateAsync();
    if (!isNew) return 'current';
    if (now() - started > RELOAD_WINDOW_MS) return 'next-launch';
    await api.reloadAsync();
    return 'reloaded';
  } catch {
    // Offline, server error, storage error: the download expo-updates started
    // on its own still lands for the next launch.
    return 'failed';
  }
}
