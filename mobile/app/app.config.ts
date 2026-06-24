import type { ExpoConfig, ConfigContext } from 'expo/config';

export default ({ config }: ConfigContext): ExpoConfig => ({
  ...config,
  name: 'Trading',
  slug: 'trading',
  scheme: 'trading',
  version: '0.1.0',
  orientation: 'portrait',
  userInterfaceStyle: 'automatic',
  icon: './assets/icon.png',
  splash: {
    image: './assets/splash.png',
    resizeMode: 'contain',
    backgroundColor: '#0a0a0a',
  },

  // OTA updates — JS-only fixes ship instantly via `eas update --channel
  // preview`, no 30-min rebuild. runtimeVersion ties a build to compatible
  // JS bundles; bump it only on native changes.
  runtimeVersion: { policy: 'appVersion' },
  updates: {
    url: 'https://u.expo.dev/c6b1f8ea-1c38-4bd8-b091-c6bacf17bbaa',
  },

  ios: {
    bundleIdentifier: 'co.canberkaslan.trading',
    supportsTablet: true,
    buildNumber: '1',
    infoPlist: {
      NSFaceIDUsageDescription: 'Trading uses Face ID to unlock your account and approve trades.',
      ITSAppUsesNonExemptEncryption: false,
    },
  },

  android: {
    package: 'co.canberkaslan.trading',
    versionCode: 1,
    adaptiveIcon: {
      foregroundImage: './assets/adaptive-icon.png',
      backgroundColor: '#000000',
    },
    permissions: ['USE_BIOMETRIC', 'USE_FINGERPRINT', 'POST_NOTIFICATIONS'],
  },

  plugins: [
    'expo-router',
    'expo-secure-store',
    'expo-local-authentication',
    'expo-notifications',
    [
      // Allow cleartext HTTP on Android so the app can reach the
      // plain-HTTP backend (http://167.233.102.179:8000) during the
      // paper-trading personal-use phase. Switch to HTTPS + remove this
      // once the backend is fronted by TLS (Caddy/tunnel).
      'expo-build-properties',
      {
        android: { usesCleartextTraffic: true },
      },
    ],
  ],

  experiments: {
    typedRoutes: true,
  },

  extra: {
    // Fallback points at the live paper-trading backend (not localhost) so a
    // bundle built without EXPO_PUBLIC_API_URL still reaches the box. Public
    // IP, safe to commit.
    apiUrl: process.env.EXPO_PUBLIC_API_URL ?? 'http://167.233.102.179:8000',
    wsUrl: process.env.EXPO_PUBLIC_WS_URL ?? 'ws://167.233.102.179:8000/ws',
    devApiToken: process.env.EXPO_PUBLIC_DEV_API_TOKEN ?? '',
    cognitoPoolId: process.env.EXPO_PUBLIC_COGNITO_POOL_ID,
    cognitoClientId: process.env.EXPO_PUBLIC_COGNITO_CLIENT_ID,
    sentryDsn: process.env.EXPO_PUBLIC_SENTRY_DSN,
    posthogKey: process.env.EXPO_PUBLIC_POSTHOG_KEY,
    eas: {
      projectId: process.env.EAS_PROJECT_ID ?? 'c6b1f8ea-1c38-4bd8-b091-c6bacf17bbaa',
    },
  },
});
