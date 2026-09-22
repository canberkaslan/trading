import { View, Text, Pressable, StyleSheet } from 'react-native';
import { Tabs } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import { useMemo } from 'react';
import Svg, { Path } from 'react-native-svg';

import { StatusBanner } from '@/components/StatusBanner';
import { usePendingOrders } from '@/api/hooks';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { badgeLabel } from '@/utils/inbox';
import { Toast } from '@/components/Toast';
import { font, TABULAR } from '@/theme/type';

/**
 * Five tabs, not seven.
 *
 * Text-only labels at 390px gave each of seven tabs about 55px, which truncated
 * the longer ones. The handoff resolves it by promotion rather than shrinking:
 * the four screens the operator acts on stay in the bar, and the reference
 * screens (Grafik, Öğren, Ayarlar, Bildirimler) move behind "Diğer". Their
 * routes are unchanged — `href: null` keeps them navigable from anywhere,
 * it only takes them out of the bar.
 *
 * Aurora changes the bar itself rather than its contents. It is no longer a
 * ruled strip along the screen's bottom edge: it is a floating pill that sits
 * ON the page with air under it, the selected tab marked by an ink-filled pill
 * of its own rather than by a rule. Two consequences the rest of the app has to
 * know about:
 *  - the bar's dock is transparent, so it reads as the page ground continuing
 *    under the pill; a screen's scroll content should still end with air so its
 *    last row does not butt against the pill's shadow;
 *  - the toast can no longer dock to the screen edge, so the shell tells it how
 *    far up to sit.
 */

/** Lucide paths, verbatim from the prototype's ICONS map. */
const ICONS: Record<string, readonly [string, string]> = {
  index: [
    'M12 3v1M12 20v1M4.2 4.2l.7.7M19.1 19.1l.7.7M3 12h1M20 12h1M4.2 19.8l.7-.7M19.1 4.9l.7-.7',
    'M12 8a4 4 0 1 0 0 8 4 4 0 1 0 0-8z',
  ],
  portfolio: ['M21.21 15.89A10 10 0 1 1 8 2.83', 'M22 12A10 10 0 0 0 12 2v10z'],
  ask: ['M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z', 'M8 9h8M8 13h5'],
  orders: [
    'M22 12h-6l-2 3h-4l-2-3H2',
    'M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z',
  ],
  agents: [
    'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75',
    'M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  ],
  more: ['M4 7h16M4 12h16M4 17h16', ''],
};

function TabIcon({ name, color, active }: { name: string; color: string; active: boolean }) {
  const paths = ICONS[name] ?? ICONS.more;
  return (
    <Svg
      width={20}
      height={20}
      viewBox="0 0 24 24"
      fill="none"
      stroke={color}
      strokeWidth={active ? 2.2 : 1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <Path d={paths?.[0] ?? ''} />
      {paths?.[1] ? <Path d={paths[1]} /> : null}
    </Svg>
  );
}

/**
 * The four action tabs plus More, in bar order. An explicit list rather than a
 * filter on `href: null`: expo-router only strips those routes when the DEFAULT
 * tab bar renders the state, so a custom bar still receives every registered
 * screen and would otherwise draw all nine — Bugün included, which is reached
 * from the brand mark in the status strip instead.
 */
const BAR_TABS = ['portfolio', 'ask', 'orders', 'agents', 'more'] as const;

/**
 * Pill (48) + its 6pt inset on both sides. Not exported: a route module's
 * exports are the router's namespace. A screen that needs to leave room under
 * its scroll content states its own clearance (~72 with air).
 */
const TAB_PILL_HEIGHT = 60;

function AuroraTabBar({ state, descriptors, navigation }: any) {
  const t = useTheme();
  const sh = useShape();
  const insets = useSafeAreaInsets();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const { data: pending } = usePendingOrders();
  const pendingBadge = badgeLabel(pending?.length ?? 0);

  return (
    <View style={[styles.dock, { paddingBottom: Math.max(insets.bottom, sh.space[1]) }]} pointerEvents="box-none">
      <View style={styles.bar}>
        {state.routes.map((route: { key: string; name: string }, index: number) => {
          if (!BAR_TABS.includes(route.name as (typeof BAR_TABS)[number])) return null;
          const { options } = descriptors[route.key];

          const focused = state.index === index;
          const label = options.title ?? route.name;
          const colour = focused ? t.background : (t.ink3 ?? t.textMuted);
          const badge = route.name === 'orders' ? pendingBadge : null;

          return (
            <Pressable
              key={route.key}
              onPress={() => {
                const event = navigation.emit({ type: 'tabPress', target: route.key, canPreventDefault: true });
                if (!focused && !event.defaultPrevented) navigation.navigate(route.name);
              }}
              style={[styles.tab, focused && { backgroundColor: t.textPrimary }]}
              accessibilityRole="tab"
              accessibilityState={{ selected: focused }}
              accessibilityLabel={
                badge && route.name === 'orders' ? `${label}, ${pending?.length ?? 0} onay bekliyor` : label
              }
            >
              <TabIcon name={route.name} color={colour} active={focused} />
              <Text style={[styles.label, { color: colour }]} numberOfLines={1}>
                {label}
              </Text>
              {badge ? (
                <View style={[styles.badge, { backgroundColor: t.dangerDeep }]}>
                  <Text style={[styles.badgeText, { color: t.textPrimary }]}>{badge}</Text>
                </View>
              ) : null}
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

export default function TabLayout() {
  const { t: tr } = useTranslation();
  const theme = useTheme();
  const insets = useSafeAreaInsets();

  return (
    <View style={{ flex: 1, backgroundColor: theme.background }}>
      <StatusBanner />
      <Tabs screenOptions={{ headerShown: false }} tabBar={(props) => <AuroraTabBar {...props} />}>
        <Tabs.Screen name="portfolio" options={{ title: tr('tabs.portfolio') }} />
        <Tabs.Screen name="ask" options={{ title: tr('tabs.ask') }} />
        <Tabs.Screen name="orders" options={{ title: tr('tabs.orders') }} />
        <Tabs.Screen name="agents" options={{ title: tr('tabs.agents') }} />
        <Tabs.Screen name="more" options={{ title: tr('tabs.more') }} />
        {/* Reachable by route, not shown in the bar. Bugün is among them: the
            brief fixes the bar at five, and the brand mark in the status strip
            is what opens it. */}
        <Tabs.Screen name="index" options={{ title: 'Bugün', href: null }} />
        <Tabs.Screen name="charts" options={{ title: tr('tabs.charts'), href: null }} />
        <Tabs.Screen name="watchlist" options={{ title: tr('tabs.watchlist'), href: null }} />
        <Tabs.Screen name="risk" options={{ title: tr('tabs.risk'), href: null }} />
        <Tabs.Screen name="learn" options={{ title: tr('tabs.learn'), href: null }} />
        <Tabs.Screen name="settings" options={{ title: tr('tabs.settings'), href: null }} />
      </Tabs>
      {/* One toast for the whole shell: the outcomes it reports (a rejected
          order, a cancelled one) usually land as the screen that triggered them
          is being popped, so it cannot belong to a screen. It clears the
          floating bar rather than docking to the screen edge. */}
      <Toast bottomOffset={TAB_PILL_HEIGHT + Math.max(insets.bottom, 8)} />
    </View>
  );
}

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    dock: { paddingHorizontal: sh.space[3], paddingTop: sh.space[1] },
    bar: {
      flexDirection: 'row',
      backgroundColor: t.surface,
      borderRadius: sh.radiusPill,
      borderWidth: sh.hairline,
      borderColor: t.line ?? t.divider,
      padding: 6,
      // The system's other elevation, and the reason the bar reads as floating
      // over the page rather than as its bottom edge.
      shadowColor: t.shadowColor,
      shadowOpacity: 0.28,
      shadowRadius: 30,
      shadowOffset: { width: 0, height: 10 },
      elevation: 10,
    },
    tab: {
      flex: 1,
      alignItems: 'center',
      justifyContent: 'center',
      gap: 2,
      paddingTop: 7,
      paddingBottom: 6,
      minHeight: 48,
      borderRadius: sh.radiusPill,
    },
    label: { fontSize: 10, ...font(800), letterSpacing: 0.1 },
    badge: {
      position: 'absolute',
      top: 2,
      right: '22%',
      minWidth: 16,
      height: 16,
      paddingHorizontal: 4,
      borderRadius: sh.radiusPill,
      alignItems: 'center',
      justifyContent: 'center',
    },
    badgeText: { fontSize: 9, ...font(800), ...TABULAR },
  });
