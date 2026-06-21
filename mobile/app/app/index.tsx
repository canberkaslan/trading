import { Redirect } from 'expo-router';

/**
 * Root entry. The app has no auth gate yet (dev token), so send the user
 * straight to the portfolio tab. Once Cognito auth (5h) is wired, this
 * redirects to /(auth)/login when there's no session.
 */
export default function Index() {
  return <Redirect href="/(tabs)/portfolio" />;
}
