import { Redirect } from 'expo-router';

import { isConfigured as isFirebaseConfigured, currentUser } from '@/auth/firebase';

/**
 * Root entry, and the only auth gate the app has.
 *
 * It used to redirect straight to the portfolio tab unconditionally, which was
 * correct while the shared dev token was the whole security model — the login
 * screen existed but nothing ever routed to it. That becomes a trap the moment
 * the server starts requiring Firebase: every screen 401s, and the one screen
 * that could fix it is unreachable without typing the URL by hand.
 *
 * So the gate exists only when Firebase does. With no Firebase project
 * configured the old behaviour is kept exactly — this deployment still runs on
 * the shared bearer, and locking people out of a working app for an identity
 * system that has not been set up would be the worse failure.
 *
 * `currentUser()` is read synchronously and may be null on a cold start before
 * persistence has rehydrated, which would bounce a signed-in user to the login
 * screen. That is why the login screen keeps its device-unlock path: it is a
 * one-tap return, not a re-authentication. Gating on a slower "definitely
 * signed out" signal would mean showing the wrong screen first and moving it
 * under the user, which is worse than one extra tap.
 */
export default function Index() {
  if (isFirebaseConfigured() && currentUser() === null) {
    return <Redirect href="/(auth)/login" />;
  }
  return <Redirect href="/(tabs)/portfolio" />;
}
