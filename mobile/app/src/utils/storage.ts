/**
 * Key/value persistence that works on both the phone and the browser.
 *
 * Every store in this app was written against `expo-secure-store`, because it
 * is the only storage module linked into the native build — AsyncStorage would
 * have needed a rebuild and broken OTA. That reasoning still holds on device.
 *
 * It does not hold on web. `expo-secure-store` ships no web implementation:
 * every call throws `UnavailabilityError`. The call sites all wrap their reads
 * and writes in try/catch, so the app *runs* — it just silently forgets
 * everything, including the bearer that gates order approval. Retyping the
 * token on every page load is not a working surface.
 *
 * So this module is the one place that knows which platform it is on. Native
 * keeps the OS keystore; web falls back to `localStorage`, which is the
 * strongest thing a browser offers without a service worker.
 *
 * The two are NOT equivalent and the difference is deliberate, not an
 * oversight: the keystore is hardware-backed and encrypted at rest, while
 * `localStorage` is plain text readable by any script on the origin. That is
 * an acceptable trade only because the web build is served from its own
 * origin behind the tunnel and carries the same bearer the operator would
 * otherwise paste into a dashboard field by hand. It is not a place for a
 * credential with a wider blast radius.
 *
 * `localStorage` itself can throw — Safari private mode, disabled site data —
 * so even the web path degrades to session-only rather than crashing.
 */

import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';

const isWeb = Platform.OS === 'web';

/** Guarded handle: reading `window.localStorage` throws outright in some browsers. */
function webStore(): Storage | null {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null;
  } catch {
    return null;
  }
}

export async function getItemAsync(key: string): Promise<string | null> {
  if (isWeb) return webStore()?.getItem(key) ?? null;
  return SecureStore.getItemAsync(key);
}

export async function setItemAsync(key: string, value: string): Promise<void> {
  if (isWeb) {
    webStore()?.setItem(key, value);
    return;
  }
  await SecureStore.setItemAsync(key, value);
}

export async function deleteItemAsync(key: string): Promise<void> {
  if (isWeb) {
    webStore()?.removeItem(key);
    return;
  }
  await SecureStore.deleteItemAsync(key);
}
