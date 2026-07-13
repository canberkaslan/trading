import { View } from 'react-native';
import { Tabs } from 'expo-router';

import { StatusBanner } from '@/components/StatusBanner';
import { colors } from '@/theme/colors';

export default function TabLayout() {
  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <StatusBanner />
      <Tabs screenOptions={{ headerShown: false }}>
        <Tabs.Screen name="portfolio" options={{ title: 'Portfolio' }} />
        <Tabs.Screen name="ask" options={{ title: 'Sor' }} />
        <Tabs.Screen name="orders" options={{ title: 'Pending' }} />
        <Tabs.Screen name="agents" options={{ title: 'Agents' }} />
        <Tabs.Screen name="charts" options={{ title: 'Charts' }} />
        <Tabs.Screen name="settings" options={{ title: 'Settings' }} />
      </Tabs>
    </View>
  );
}
