import ky, { type KyInstance } from 'ky';
import Constants from 'expo-constants';
import * as SecureStore from 'expo-secure-store';

const API_URL = (Constants.expoConfig?.extra?.apiUrl ?? 'http://localhost:8000') as string;

/**
 * Phase 5 dev: bearer token from either expo-secure-store (Cognito JWT once
 * 5h lands) or a hardcoded EXPO_PUBLIC_DEV_API_TOKEN env var. Backend's
 * DEV_API_TOKEN env var must match for the bearer to be accepted; both empty
 * means auth is disabled (single-user local dev).
 */
const DEV_TOKEN = (Constants.expoConfig?.extra?.devApiToken ?? '') as string;

export const apiClient: KyInstance = ky.create({
  prefixUrl: API_URL,
  timeout: 10_000,
  retry: { limit: 2, methods: ['get'] },
  hooks: {
    beforeRequest: [
      async (request) => {
        let token: string | null = null;
        try {
          token = await SecureStore.getItemAsync('cognito_id_token');
        } catch {
          // SecureStore can fail on web; fall through to dev token
        }
        const bearer = token || DEV_TOKEN;
        if (bearer) {
          request.headers.set('Authorization', `Bearer ${bearer}`);
        }
      },
    ],
    beforeError: [
      async (error) => {
        if (error.response?.status === 426) {
          // TODO: navigate to upgrade screen (server-driven version gating)
        }
        return error;
      },
    ],
  },
});
