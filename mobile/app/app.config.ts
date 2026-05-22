import type { ExpoConfig, ConfigContext } from 'expo/config';

export default ({ config }: ConfigContext): ExpoConfig => ({
  ...config,
  name: 'Trading',
  slug: 'trading',
  scheme: 'trading',
  version: '0.1.0',
  orientation: 'portrait',
  userInterfaceStyle: 'automatic',

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
  ],

  experiments: {
    typedRoutes: true,
  },

  extra: {
    apiUrl: process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000',
    wsUrl: process.env.EXPO_PUBLIC_WS_URL ?? 'ws://localhost:8000/ws',
    cognitoPoolId: process.env.EXPO_PUBLIC_COGNITO_POOL_ID,
    cognitoClientId: process.env.EXPO_PUBLIC_COGNITO_CLIENT_ID,
    sentryDsn: process.env.EXPO_PUBLIC_SENTRY_DSN,
    posthogKey: process.env.EXPO_PUBLIC_POSTHOG_KEY,
    eas: {
      projectId: process.env.EAS_PROJECT_ID,
    },
  },
});
