import ky, { type KyInstance } from 'ky';
import Constants from 'expo-constants';
import * as SecureStore from 'expo-secure-store';

const API_URL = (Constants.expoConfig?.extra?.apiUrl ?? 'http://localhost:8000') as string;

export const apiClient: KyInstance = ky.create({
  prefixUrl: API_URL,
  timeout: 10_000,
  retry: { limit: 2, methods: ['get'] },
  hooks: {
    beforeRequest: [
      async (request) => {
        const token = await SecureStore.getItemAsync('cognito_id_token');
        if (token) {
          request.headers.set('Authorization', `Bearer ${token}`);
        }
        request.headers.set('Accept', 'application/vnd.trading.v1+json');
      },
    ],
    beforeError: [
      async (error) => {
        // 426 = force-upgrade required (server-driven version gating)
        if (error.response?.status === 426) {
          // TODO: navigate to upgrade screen
        }
        return error;
      },
    ],
  },
});
