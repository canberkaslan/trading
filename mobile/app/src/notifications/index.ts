/**
 * Expo Push wiring.
 *
 * - Requests notification permission once.
 * - Gets the Expo push token (works in dev with Expo Go; in standalone
 *   builds requires an Apple Developer / FCM project tied to the app).
 * - Registers the token with the backend so it appears in
 *   /v1/notifications/register.
 *
 * Call `setupNotifications()` exactly once at app startup (root layout).
 */

import * as Notifications from 'expo-notifications';
import * as Device from 'expo-device';
import { Platform } from 'react-native';

import { api } from '@/api/endpoints';

let registered = false;

// Foreground behavior: show the banner + play sound + bump badge.
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: true,
  }),
});

async function ensureAndroidChannel(): Promise<void> {
  if (Platform.OS !== 'android') return;
  await Notifications.setNotificationChannelAsync('default', {
    name: 'Trade alerts',
    importance: Notifications.AndroidImportance.HIGH,
    vibrationPattern: [0, 250, 250, 250],
    lightColor: '#22c55e',
  });
}

export async function setupNotifications(): Promise<string | null> {
  if (registered) return null;
  registered = true;

  await ensureAndroidChannel();

  if (!Device.isDevice) {
    // Notifications don't work on simulators reliably; skip silently.
    return null;
  }

  const { status: existing } = await Notifications.getPermissionsAsync();
  let status = existing;
  if (existing !== 'granted') {
    const res = await Notifications.requestPermissionsAsync();
    status = res.status;
  }
  if (status !== 'granted') {
    return null;
  }

  let token: string;
  try {
    const result = await Notifications.getExpoPushTokenAsync();
    token = result.data;
  } catch {
    return null;
  }

  const platform: 'ios' | 'android' | 'web' =
    Platform.OS === 'ios' ? 'ios' : Platform.OS === 'android' ? 'android' : 'web';

  try {
    await api.registerPushToken(token, platform);
  } catch {
    // Backend unreachable — fine in dev; user can retry from Settings.
  }

  return token;
}

/**
 * Hook a router into incoming notification taps. Call from root layout.
 * data.type drives the deep-link target:
 *   - 'decision_pending' -> /approve/{order_id}
 *   - 'order_submitted'  -> /(tabs)/orders
 *   - 'order_filled'     -> /(tabs)/portfolio
 *   - 'order_rejected'   -> /(tabs)/orders
 */
export function registerTapHandler(navigate: (path: string) => void): () => void {
  const sub = Notifications.addNotificationResponseReceivedListener((response) => {
    const data = response.notification.request.content.data as Record<string, string> | undefined;
    if (!data) return;
    switch (data.type) {
      case 'decision_pending':
        if (data.order_id) navigate(`/approve/${data.order_id}`);
        break;
      case 'order_submitted':
      case 'order_rejected':
        navigate('/(tabs)/orders');
        break;
      case 'order_filled':
        navigate('/(tabs)/portfolio');
        break;
    }
  });
  return () => sub.remove();
}
