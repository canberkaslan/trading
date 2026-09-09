import { View, Text, StyleSheet, ScrollView, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';
import Svg, { Path } from 'react-native-svg';

import { useUnreadCount } from '@/stores/notifications';
import { useTheme } from '@/theme/useTheme';
import { badgeLabel } from '@/utils/inbox';
import { MIN_TOUCH_TARGET } from '@/utils/a11y';

/**
 * The screens the tab bar no longer carries.
 *
 * A plain list rather than a sheet: these are destinations, not actions, and a
 * list can carry the unread count next to Bildirimler without a second surface.
 */

function Chevron({ color }: { color: string }) {
  return (
    <Svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <Path d="m9 18 6-6-6-6" />
    </Svg>
  );
}

export default function MoreScreen() {
  const { t: tr } = useTranslation();
  const router = useRouter();
  const t = useTheme();
  const unread = useUnreadCount();

  const items: { route: string; label: string; hint: string; badge?: string | null }[] = [
    { route: '/(tabs)/charts', label: tr('tabs.charts'), hint: 'Fiyat grafiği ve sembol arama' },
    { route: '/(tabs)/learn', label: tr('tabs.learn'), hint: 'Paneldeki sayıların ne anlama geldiği' },
    { route: '/notifications', label: tr('tabs.notifications'), hint: 'Onay istekleri ve uyarılar', badge: badgeLabel(unread) },
    { route: '/(tabs)/settings', label: tr('tabs.settings'), hint: 'Hesap, mod, risk ve bildirimler' },
  ];

  return (
    <SafeAreaView style={[styles.container, { backgroundColor: t.background }]} edges={['top']}>
      <ScrollView contentContainerStyle={styles.scroll}>
        <Text style={[styles.kicker, { color: t.accent700 ?? t.accent }]}>{tr('tabs.more').toUpperCase()}</Text>

        <View style={[styles.list, { borderTopColor: t.divider }]}>
          {items.map((item) => (
            <Pressable
              key={item.route}
              style={[styles.row, { borderBottomColor: t.divider }]}
              onPress={() => router.push(item.route as never)}
              accessibilityRole="button"
              accessibilityLabel={item.label}
            >
              <View style={styles.rowText}>
                <View style={styles.rowHead}>
                  <Text style={[styles.label, { color: t.textPrimary }]}>{item.label}</Text>
                  {item.badge ? (
                    <View style={[styles.badge, { backgroundColor: t.accent }]}>
                      <Text style={[styles.badgeText, { color: t.background }]}>{item.badge}</Text>
                    </View>
                  ) : null}
                </View>
                <Text style={[styles.hint, { color: t.textSecondary }]}>{item.hint}</Text>
              </View>
              <Chevron color={t.textSecondary} />
            </Pressable>
          ))}
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  scroll: { paddingHorizontal: 16, paddingTop: 20, paddingBottom: 24 },
  kicker: { fontSize: 11, fontWeight: '600', letterSpacing: 1.1, marginBottom: 16 },
  list: { borderTopWidth: 2 },
  row: {
    minHeight: MIN_TOUCH_TARGET,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingVertical: 14,
    borderBottomWidth: 1,
  },
  rowText: { flex: 1, gap: 2 },
  rowHead: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  label: { fontSize: 15, fontWeight: '800' },
  hint: { fontSize: 12 },
  badge: { paddingHorizontal: 4, paddingVertical: 1 },
  badgeText: { fontSize: 9, fontWeight: '800' },
});
