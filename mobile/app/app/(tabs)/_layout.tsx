import { View, Text, Pressable, StyleSheet } from 'react-native';
import { Tabs } from 'expo-router';
import { useTranslation } from 'react-i18next';
import Svg, { Path } from 'react-native-svg';

import { StatusBanner } from '@/components/StatusBanner';
import { usePendingOrders } from '@/api/hooks';
import { useTheme } from '@/theme/useTheme';
import { badgeLabel } from '@/utils/inbox';

/**
 * Five tabs, not seven.
 *
 * Text-only labels at 390px gave each of seven tabs about 55px, which truncated
 * the longer ones. The handoff resolves it by promotion rather than shrinking:
 * the four screens the operator acts on stay in the bar, and the reference
 * screens (Grafik, Öğren, Ayarlar, Bildirimler) move behind "Diğer". Their
 * routes are unchanged — `href: null` keeps them navigable from anywhere,
 * it only takes them out of the bar.
 */

function MenuIcon({ color }: { color: string }) {
  return (
    <Svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2.5} strokeLinecap="round">
      <Path d="M4 12h16" />
      <Path d="M4 6h16" />
      <Path d="M4 18h16" />
    </Svg>
  );
}

/**
 * The bar itself. Rendered by hand rather than through `tabBarIcon` because the
 * active marker is a 3px rule along the top EDGE of the tab, which the default
 * bar cannot express — it insets its content.
 */
/**
 * The four action tabs plus More, in bar order. An explicit list rather than a
 * filter on `href: null`: expo-router only strips those routes when the DEFAULT
 * tab bar renders the state, so a custom bar still receives every registered
 * screen and would otherwise draw all eight.
 */
const BAR_TABS = ['portfolio', 'ask', 'orders', 'agents', 'more'] as const;

function ModernistTabBar({ state, descriptors, navigation }: any) {
  const t = useTheme();
  const { data: pending } = usePendingOrders();
  const pendingBadge = badgeLabel(pending?.length ?? 0);

  return (
    <View style={[styles.bar, { backgroundColor: t.background, borderTopColor: t.divider }]}>
      {state.routes.map((route: { key: string; name: string }, index: number) => {
        if (!BAR_TABS.includes(route.name as (typeof BAR_TABS)[number])) return null;
        const { options } = descriptors[route.key];

        const focused = state.index === index;
        const label = options.title ?? route.name;
        const colour = focused ? t.accent : t.textSecondary;
        const badge = route.name === 'orders' ? pendingBadge : null;

        return (
          <Pressable
            key={route.key}
            onPress={() => {
              const event = navigation.emit({ type: 'tabPress', target: route.key, canPreventDefault: true });
              if (!focused && !event.defaultPrevented) navigation.navigate(route.name);
            }}
            style={[styles.tab, { borderTopColor: focused ? t.accent : 'transparent' }]}
            accessibilityRole="button"
            accessibilityState={{ selected: focused }}
            accessibilityLabel={label}
          >
            {route.name === 'more' ? (
              <MenuIcon color={colour} />
            ) : (
              <View style={[styles.marker, { backgroundColor: colour }]} />
            )}
            <Text style={[styles.label, { color: colour }]}>{label}</Text>
            {badge ? (
              <View style={[styles.badge, { backgroundColor: t.accent }]}>
                <Text style={[styles.badgeText, { color: t.background }]}>{badge}</Text>
              </View>
            ) : null}
          </Pressable>
        );
      })}
    </View>
  );
}

export default function TabLayout() {
  const { t: tr } = useTranslation();
  const theme = useTheme();

  return (
    <View style={{ flex: 1, backgroundColor: theme.background }}>
      <StatusBanner />
      <Tabs screenOptions={{ headerShown: false }} tabBar={(props) => <ModernistTabBar {...props} />}>
        <Tabs.Screen name="portfolio" options={{ title: tr('tabs.portfolio') }} />
        <Tabs.Screen name="ask" options={{ title: tr('tabs.ask') }} />
        <Tabs.Screen name="orders" options={{ title: tr('tabs.orders') }} />
        <Tabs.Screen name="agents" options={{ title: tr('tabs.agents') }} />
        <Tabs.Screen name="more" options={{ title: tr('tabs.more') }} />
        {/* Reachable by route, not shown in the bar. */}
        <Tabs.Screen name="charts" options={{ title: tr('tabs.charts'), href: null }} />
        <Tabs.Screen name="learn" options={{ title: tr('tabs.learn'), href: null }} />
        <Tabs.Screen name="settings" options={{ title: tr('tabs.settings'), href: null }} />
      </Tabs>
    </View>
  );
}

const styles = StyleSheet.create({
  bar: { flexDirection: 'row', borderTopWidth: 2, paddingBottom: 20 },
  // The 3px marker sits on the tab's own top edge and overlaps the bar's 2px
  // rule, so the active tab reads as continuous with the screen above it.
  tab: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'flex-start',
    gap: 4,
    paddingTop: 10,
    paddingBottom: 8,
    minHeight: 56,
    borderTopWidth: 3,
    marginTop: -2,
  },
  marker: { width: 8, height: 8 },
  label: { fontSize: 10, fontWeight: '700', letterSpacing: 0.4 },
  badge: { position: 'absolute', top: 8, right: '22%', paddingHorizontal: 4, paddingVertical: 1 },
  badgeText: { fontSize: 9, fontWeight: '800' },
});
