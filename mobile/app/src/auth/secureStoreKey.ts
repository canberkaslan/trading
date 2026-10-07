/**
 * A Firebase persistence key, made fit for `expo-secure-store`.
 *
 * SecureStore accepts only letters, digits, '.', '-' and '_' in a key and
 * throws on anything else. Firebase names its persisted session
 * `firebase:authUser:<apiKey>:[DEFAULT]`, so on native every sign-in reached
 * the server, came back with a user, and then failed while Firebase wrote that
 * user to storage. Firebase wraps that failure as `auth/internal-error`, which
 * the login screen maps to its catch-all 'Giriş yapılamadı'. The web build
 * never saw this: it persists to the browser, not through this adapter.
 *
 * Every character outside [A-Za-z0-9.-] — '_' included — is written as '_'
 * followed by its UTF-16 code unit in four hex digits. The width is fixed, so
 * no two keys can map to the same one, and the output only ever uses the
 * characters SecureStore allows.
 */
export function secureStoreKey(key: string): string {
  return key.replace(
    /[^A-Za-z0-9.-]/g,
    (ch) => `_${ch.charCodeAt(0).toString(16).padStart(4, '0')}`,
  );
}
