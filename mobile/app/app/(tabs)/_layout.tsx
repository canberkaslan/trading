import { View } from 'react-native';
import { Tabs } from 'expo-router';
import { useTranslation } from 'react-i18next';

import { StatusBanner } from '@/components/StatusBanner';
import { colors } from '@/theme/colors';

export default function TabLayout() {
  const { t } = useTranslation();
  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <StatusBanner />
      {/* Seven text-only tabs is tight on a narrow phone, so the labels come
          from i18n — the Turkish ones are short enough to fit without the bar
          truncating them. */}
      <Tabs screenOptions={{ headerShown: false, tabBarLabelStyle: { fontSize: 10 } }}>
        <Tabs.Screen name="portfolio" options={{ title: t('tabs.portfolio') }} />
        <Tabs.Screen name="ask" options={{ title: t('tabs.ask') }} />
        <Tabs.Screen name="orders" options={{ title: t('tabs.orders') }} />
        <Tabs.Screen name="agents" options={{ title: t('tabs.agents') }} />
        <Tabs.Screen name="charts" options={{ title: t('tabs.charts') }} />
        <Tabs.Screen name="learn" options={{ title: t('tabs.learn') }} />
        <Tabs.Screen name="settings" options={{ title: t('tabs.settings') }} />
      </Tabs>
    </View>
  );
}
