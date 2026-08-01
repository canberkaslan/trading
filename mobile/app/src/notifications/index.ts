/**
 * Expo Push wiring.
 *
 * Permission is NOT requested at startup any more. A cold-start OS prompt with
 * no context is the classic way to get permanently denied — and a denial is
 * sticky, which on this app means never seeing an approval request again. The
 * prompt now happens where the value is obvious: the Settings row, or the first
 * time an order is actually waiting for the user. Startup only *syncs* the push
 * token when permission was already granted (silent, no prompt).
 *
 * Deep-link targets come from `@/utils/inbox.routeForType` so the tap handler
 * and the in-app inbox can never drift apart.
 */

import * as Notifications from 'expo-notifications';
import * as Device from 'expo-device';
import { Platform } from 'react-native';

import { api } from '@/api/endpoints';
import { routeForType, toInboxItem, type InboxItem } from '@/utils/inbox';

export type PushPermission = 'granted' | 'denied' | 'undetermined' | 'unsupported';

let tokenRegistered = false;

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

/** Current OS permission, without prompting. */
export async function getPermissionStatus(): Promise<PushPermission> {
  if (!Device.isDevice) return 'unsupported';
  try {
    const { status, canAskAgain } = await Notifications.getPermissionsAsync();
    if (status === 'granted') return 'granted';
    if (status === 'undetermined' || canAskAgain) return 'undetermined';
    return 'denied';
  } catch {
    return 'unsupported';
  }
}

async function registerToken(): Promise<string | null> {
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
    tokenRegistered = true;
  } catch {
    // Backend unreachable — fine in dev; user can retry from Settings.
  }
  return token;
}

/**
 * Contextual opt-in: prompts for permission (if it can) and registers the push
 * token. Call from a user-initiated moment, never from app startup.
 */
export async function requestAndRegisterPush(): Promise<string | null> {
  await ensureAndroidChannel();
  if (!Device.isDevice) {
    // Push doesn't work reliably on simulators.
    return null;
  }

  const { status: existing } = await Notifications.getPermissionsAsync();
  let status = existing;
  if (existing !== 'granted') {
    const res = await Notifications.requestPermissionsAsync();
    status = res.status;
  }
  if (status !== 'granted') return null;

  return registerToken();
}

/**
 * Startup path: keep the backend's token fresh when permission already exists.
 * Silent — never prompts, never surfaces an error.
 */
export async function syncPushTokenIfGranted(): Promise<void> {
  if (tokenRegistered) return;
  await ensureAndroidChannel();
  if ((await getPermissionStatus()) !== 'granted') return;
  await registerToken();
}

/** Clear the springboard badge — called when the inbox is opened. */
export async function clearBadge(): Promise<void> {
  try {
    await Notifications.setBadgeCountAsync(0);
  } catch {
    // Unsupported platform — nothing to clear.
  }
}

function toItem(notification: Notifications.Notification): InboxItem {
  const content = notification.request.content;
  const receivedMs = typeof notification.date === 'number' ? notification.date : Date.now();
  return toInboxItem({
    id: notification.request.identifier,
    title: content.title,
    body: content.body,
    data: content.data as Record<string, unknown> | undefined,
    receivedAt: new Date(receivedMs).toISOString(),
  });
}

/**
 * Foreground deliveries — the banner is transient, the inbox row is not.
 */
export function registerReceivedHandler(onItem: (item: InboxItem) => void): () => void {
  const sub = Notifications.addNotificationReceivedListener((notification) => {
    onItem(toItem(notification));
  });
  return () => sub.remove();
}

/**
 * Hook a router into incoming notification taps. Call from root layout.
 * Every tapped notification is also filed in the inbox (already read — the
 * user just saw it) so history is complete whether or not the app was open.
 */
export function registerTapHandler(
  navigate: (path: string) => void,
  onItem?: (item: InboxItem) => void,
): () => void {
  const sub = Notifications.addNotificationResponseReceivedListener((response) => {
    const item = toItem(response.notification);
    onItem?.({ ...item, read: true });
    const data = response.notification.request.content.data as Record<string, unknown> | undefined;
    const route = routeForType(data);
    if (route) navigate(route);
  });
  return () => sub.remove();
}
