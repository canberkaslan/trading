import { Stack, useRouter } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect } from 'react';
import { I18nextProvider } from 'react-i18next';

import i18n from '@/i18n';
import { registerTapHandler, setupNotifications } from '@/notifications';

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

  useEffect(() => {
    void setupNotifications();
    const unsub = registerTapHandler((path) => router.push(path as never));
    return () => unsub();
    // We deliberately re-subscribe only on mount; navigation ref is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <StatusBar style="auto" />
        <Stack screenOptions={{ headerShown: false }}>
          <Stack.Screen name="(auth)" />
          <Stack.Screen name="(tabs)" />
        </Stack>
      </I18nextProvider>
    </QueryClientProvider>
  );
}
