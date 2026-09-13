/**
 * The backend bearer, stored per device.
 *
 * It used to ride in `app.config.ts` under `extra.devApiToken`, which Expo
 * republishes verbatim in the OTA update manifest — an unauthenticated GET on
 * u.expo.dev returns it in the clear. A token that gates order approval,
 * rejection, cancellation and the kill switch cannot live in a file the update
 * server hands to anyone who asks.
 *
 * So it moves here: typed once per device, kept in the OS keystore, and never
 * part of any bundle. The cost is one setup step per phone, which is the right
 * trade for a credential that can flatten a book.
 *
 * A storage failure is not fatal — the app degrades to unauthenticated and the
 * screens show their error states, which is the honest outcome. Silently
 * pretending to be signed in would be worse.
 */

import { create } from 'zustand';
import * as SecureStore from 'expo-secure-store';

/** Deliberately not `cognito_id_token`: this is a static bearer, not a JWT. */
const STORAGE_KEY = 'api_bearer_v1';

interface TokenState {
  token: string | null;
  /** False until storage has been read once, so the UI can avoid a flash. */
  hydrated: boolean;
  hydrate: () => Promise<void>;
  set: (token: string) => Promise<void>;
  clear: () => Promise<void>;
}

export const useApiTokenStore = create<TokenState>((set) => ({
  token: null,
  hydrated: false,
  hydrate: async () => {
    try {
      set({ token: await SecureStore.getItemAsync(STORAGE_KEY), hydrated: true });
    } catch {
      set({ hydrated: true });
    }
  },
  set: async (token) => {
    const trimmed = token.trim();
    set({ token: trimmed || null });
    try {
      if (trimmed) await SecureStore.setItemAsync(STORAGE_KEY, trimmed);
      else await SecureStore.deleteItemAsync(STORAGE_KEY);
    } catch {
      // Session-only. The screen reports what it can see.
    }
  },
  clear: async () => {
    set({ token: null });
    try {
      await SecureStore.deleteItemAsync(STORAGE_KEY);
    } catch {
      /* nothing to clear */
    }
  },
}));

/**
 * Read the bearer outside React — the ky `beforeRequest` hook runs in no
 * component. Falls back to SecureStore directly when the store has not
 * hydrated yet, so the very first request after a cold start still carries it.
 */
export async function readApiToken(): Promise<string | null> {
  const inMemory = useApiTokenStore.getState().token;
  if (inMemory) return inMemory;
  try {
    return await SecureStore.getItemAsync(STORAGE_KEY);
  } catch {
    return null;
  }
}
