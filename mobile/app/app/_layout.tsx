import { Stack, useRouter } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect } from 'react';
import { I18nextProvider } from 'react-i18next';
import { useFonts } from 'expo-font';

import { FONT_ASSETS } from '@/theme/fonts';

import i18n, { hydrateLanguage } from '@/i18n';
import { hydrateTheme, useThemeName } from '@/theme/useTheme';
import { themeScheme } from '@/theme/colors';
import { hydrateWatchlist } from '@/stores/watchlist';
import { registerReceivedHandler, registerTapHandler, syncPushTokenIfGranted } from '@/notifications';
import { useInboxStore } from '@/stores/notifications';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 2,
      staleTime: 30_000,
    },
  },
});

export default function RootLayout() {
  const router = useRouter();
  const [fontsLoaded, fontError] = useFonts(FONT_ASSETS);
  // The status-bar icons follow the palette, not the phone. `style="auto"`
  // resolves against the system appearance, so on a phone in light mode
  // Android drew dark icons on Aurora's near-black ground, where the clock and
  // battery disappeared on every screen without its own StatusBar.
  const scheme = themeScheme[useThemeName()];

  useEffect(() => {
    const { hydrate, push } = useInboxStore.getState();
    void hydrate();
    void hydrateTheme();
    void hydrateLanguage();
    void hydrateWatchlist();
    // Startup never prompts — permission is asked contextually (Settings row,
    // or the first pending order). This only refreshes an existing grant.
    void syncPushTokenIfGranted();
    const unsubTap = registerTapHandler((path) => router.push(path as never), push);
    const unsubReceived = registerReceivedHandler(push);
    return () => {
      unsubTap();
      unsubReceived();
    };
    // We deliberately re-subscribe only on mount; navigation ref is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /*
   * Hold the first paint until Archivo is in memory, otherwise every heading
   * renders in the system face and then reflows to a different width at once —
   * which reads as a broken load rather than a font swap. The faces are bundled
   * assets, so this is a frame or two.
   *
   * Deliberately NOT expo-splash-screen: that module is absent from the
   * installed native build, so an OTA that called it would throw on launch and
   * take the app down. `useFonts` only needs expo-font, which ships with the
   * Expo SDK and is already linked.
   *
   * `fontError` falls through on purpose — a font that fails to decode must
   * cost the typeface, never the app.
   */
  if (!fontsLoaded && !fontError) return null;

  return (
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <StatusBar style={scheme === 'dark' ? 'light' : 'dark'} />
        <Stack screenOptions={{ headerShown: false }}>
          {/* `(auth)` has no _layout, so its only route is `(auth)/login`;
              naming the group logged "No route named (auth)" on every launch. */}
          <Stack.Screen name="(auth)/login" />
          <Stack.Screen name="(tabs)" />
        </Stack>
      </I18nextProvider>
    </QueryClientProvider>
  );
}
