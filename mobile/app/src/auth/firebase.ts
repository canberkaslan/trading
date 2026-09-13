/**
 * Firebase sign-in for this app.
 *
 * Until now there was no sign-in at all: `login.tsx` made zero network calls,
 * so any address containing "@" plus four characters got in, and the only real
 * gate was one shared bearer typed into Settings. With eight or nine family
 * members that means no identity, no audit trail, and no way to remove one
 * person without rotating everyone.
 *
 * Three decisions are worth stating, because each had a wrong-looking easy
 * option.
 *
 * **Persistence without a new native module.** The documented Expo recipe
 * reaches for `@react-native-async-storage/async-storage`, which is a native
 * dependency — it would need a rebuild and break OTA, which is exactly the
 * reasoning that put every other store in this app on `expo-secure-store`.
 * `getReactNativePersistence` only needs something shaped like AsyncStorage,
 * so it is handed an adapter over the storage module already in the build.
 * Nothing new is linked.
 *
 * **Firebase is optional, not assumed.** There is no Firebase project yet, and
 * the app works today on the shared bearer. So an absent config is a supported
 * state: `isConfigured()` is false, every call fails cleanly, and the existing
 * path keeps working. A half-migrated auth system that bricks the working one
 * is worse than no migration.
 *
 * **The web config is not a secret.** Firebase API keys identify a project;
 * they do not authorise anything. Access is decided by the ID token the server
 * verifies and by Firebase's own rules, so shipping this config in the bundle
 * is correct and not the mistake that `extra.devApiToken` was — that WAS a
 * credential, and it is why nothing secret rides in `extra` any more.
 */

import Constants from 'expo-constants';
import { Platform } from 'react-native';
import { initializeApp, getApps, getApp, type FirebaseApp } from 'firebase/app';
import {
  browserLocalPersistence,
  createUserWithEmailAndPassword,
  getAuth,
  initializeAuth,
  onAuthStateChanged,
  signInWithEmailAndPassword,
  signOut as fbSignOut,
  type Auth,
  type User,
} from 'firebase/auth';

import * as storage from '@/utils/storage';

/** The public web config, from app.config.ts `extra.firebase`. */
type FirebaseConfig = {
  apiKey: string;
  authDomain: string;
  projectId: string;
  appId: string;
};

function config(): FirebaseConfig | null {
  const raw = (Constants.expoConfig?.extra as Record<string, unknown> | undefined)?.firebase;
  if (!raw || typeof raw !== 'object') return null;
  const c = raw as Partial<FirebaseConfig>;
  // All four are required to reach Firebase at all; a partial config would
  // fail later, deeper, and with a worse message.
  if (!c.apiKey || !c.authDomain || !c.projectId || !c.appId) return null;
  return c as FirebaseConfig;
}

export function isConfigured(): boolean {
  return config() !== null;
}

/**
 * AsyncStorage's shape over the storage module already in the build.
 *
 * Note the size caveat: on native this lands in the OS keystore, and
 * SecureStore warns past 2 KB. Firebase's persisted auth state is normally
 * well under that, but a write that fails leaves the session in memory only —
 * the user is signed in until the app restarts, which degrades to today's
 * behaviour rather than breaking.
 */
const persistenceAdapter = {
  getItem: (key: string) => storage.getItemAsync(key),
  setItem: (key: string, value: string) => storage.setItemAsync(key, value),
  removeItem: (key: string) => storage.deleteItemAsync(key),
};

let cachedAuth: Auth | null = null;

function app(cfg: FirebaseConfig): FirebaseApp {
  return getApps().length ? getApp() : initializeApp(cfg);
}

/** The Auth instance, or null when Firebase is not configured. */
export function auth(): Auth | null {
  if (cachedAuth) return cachedAuth;
  const cfg = config();
  if (!cfg) return null;

  const instance = app(cfg);
  try {
    if (Platform.OS === 'web') {
      cachedAuth = initializeAuth(instance, { persistence: browserLocalPersistence });
    } else {
      // Only present in the react-native build of firebase/auth, which Metro
      // resolves via the package's "react-native" export condition; the
      // browser type definitions this file compiles against do not know it
      // exists. A static import would therefore fail to type-check AND be
      // evaluated on web, where the symbol is genuinely absent — so the
      // require is deliberate and platform-guarded, not laziness.
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { getReactNativePersistence } = require('firebase/auth') as {
        getReactNativePersistence?: (s: typeof persistenceAdapter) => unknown;
      };
      cachedAuth = getReactNativePersistence
        ? initializeAuth(instance, {
            persistence: getReactNativePersistence(persistenceAdapter) as never,
          })
        : getAuth(instance);
    }
  } catch {
    // initializeAuth throws if it already ran for this app — a fast refresh, a
    // second import. The instance still exists, so ask for it rather than
    // leaving the app signed out.
    cachedAuth = getAuth(instance);
  }
  return cachedAuth;
}

export class NotConfiguredError extends Error {
  constructor() {
    super('Firebase is not configured');
    this.name = 'NotConfiguredError';
  }
}

function require_(): Auth {
  const a = auth();
  if (!a) throw new NotConfiguredError();
  return a;
}

export async function signIn(email: string, password: string): Promise<User> {
  const { user } = await signInWithEmailAndPassword(require_(), email.trim(), password);
  return user;
}

export async function signUp(email: string, password: string): Promise<User> {
  const { user } = await createUserWithEmailAndPassword(require_(), email.trim(), password);
  return user;
}

export async function signOut(): Promise<void> {
  const a = auth();
  if (a) await fbSignOut(a);
}

/**
 * A currently-valid ID token, or null when nobody is signed in.
 *
 * Firebase ID tokens expire after an hour. `getIdToken()` refreshes
 * automatically when the cached one is close to expiry, so this is called per
 * request rather than cached by us — a token we cached ourselves would be the
 * one thing guaranteed to go stale.
 */
export async function getIdToken(): Promise<string | null> {
  const a = auth();
  const user = a?.currentUser;
  if (!user) return null;
  try {
    return await user.getIdToken();
  } catch {
    // Refresh failed (offline, revoked). Reporting null lets the caller fall
    // back rather than sending a token the server will refuse anyway.
    return null;
  }
}

export function currentUser(): User | null {
  return auth()?.currentUser ?? null;
}

/** Subscribe to sign-in state. Returns an unsubscribe, a no-op when unconfigured. */
export function onAuthChange(fn: (user: User | null) => void): () => void {
  const a = auth();
  if (!a) {
    fn(null);
    return () => {};
  }
  return onAuthStateChanged(a, fn);
}
