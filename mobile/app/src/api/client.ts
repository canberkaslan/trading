import ky, { type KyInstance } from 'ky';
import Constants from 'expo-constants';
import { readApiToken } from '@/stores/apiToken';

const API_URL = (Constants.expoConfig?.extra?.apiUrl ?? 'http://localhost:8000') as string;

/*
 * The bearer comes from the device keystore only — see stores/apiToken.
 *
 * It used to fall back to `extra.devApiToken` from the Expo config, and that
 * fallback is gone rather than deprecated: Expo republishes `extra` verbatim in
 * the OTA update manifest, which needs no authentication to fetch. A token that
 * gates order approval and the kill switch cannot ship inside the bundle.
 */

export const apiClient: KyInstance = ky.create({
  prefixUrl: API_URL,
  timeout: 10_000,
  retry: { limit: 2, methods: ['get'] },
  hooks: {
    beforeRequest: [
      async (request) => {
        const bearer = await readApiToken();
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
