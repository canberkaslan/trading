/**
 * Diğer — every screen the five-tab bar cannot carry.
 *
 * The prototype draws this as the MENU SHEET: a three-column grid of tiles,
 * each a `--paper` rectangle with a 20px brand-stroked Lucide glyph, a 13px
 * label under it, and a badge pinned to the top-right corner when the
 * destination is waiting on you. Twelve keys, in the prototype's own order —
 * market · charts · watchlist · risk · journal · backtest · audit · learn ·
 * notifications · run · queue · settings.
 *
 * Two deliberate departures from the prototype, both forced by the target:
 *
 *  - It is a tab screen here, not a bottom sheet over one. The sheet's grab
 *    handle and its "⌘K ile ara" hint are dropped: there is no sheet to drag
 *    and no keyboard to press. The hint's job — telling you this is the index
 *    of everything — moves into the subtitle.
 *  - The tile ground is `surface`, not `paper`. In the prototype the sheet is
 *    white and the tiles are the page grey, so the tiles are the darker thing.
 *    Under Aurora the page ground is already the darkest surface in the
 *    system, so `paper` tiles on a `background` page would be a 2-value
 *    difference and effectively invisible; the tile keeps its identity by
 *    being the raised thing instead. That is exactly what `<Card>` is.
 *
 * The per-item hint line the previous list showed has no room in a 74pt tile,
 * so it survives as `accessibilityHint`: a sighted user reads the label, a
 * screen-reader user still hears what the destination is for. Nothing else
 * about this screen decides anything — it is a router and the unread badge.
 */

import { View, Text, StyleSheet, ScrollView } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useMemo } from 'react';
import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';
import Svg, { Path } from 'react-native-svg';

import { useUnreadCount } from '@/stores/notifications';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { TYPE, TABULAR, font } from '@/theme/type';
import { Card } from '@/components/Card';
import { badgeLabel } from '@/utils/inbox';
import { MIN_TOUCH_TARGET } from '@/utils/a11y';

/** The floating tab bar's height plus air, as on every other tab screen. */
const TAB_BAR_CLEARANCE = 72;

/** The prototype's tile: `min-height:74px`, comfortably over the 44pt floor. */
const TILE_HEIGHT = Math.max(74, MIN_TOUCH_TARGET);

/**
 * The Lucide pairs, copied verbatim from the prototype's `ICONS` map so the
 * menu and the tab bar draw the same glyph for the same destination. Two paths
 * because that is how the prototype splits them; `icon2` is empty for `run`.
 */
const ICONS: Record<string, readonly [string, string]> = {
  market: ['M22 7 13.5 15.5 8.5 10.5 2 17', 'M16 7h6v6'],
  charts: ['M3 3v18h18', 'M7 14l4-5 4 3 5-7'],
  watchlist: ['M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7z', 'M12 9a3 3 0 1 0 0 6 3 3 0 1 0 0-6z'],
  risk: [
    'M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z',
    'M12 8v4M12 16h.01',
  ],
  journal: ['M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20', 'M8 7h8M8 11h6'],
  backtest: ['M10 2v7.31M14 9.3V1.99M8.5 2h7M14 9.3a6.5 6.5 0 1 1-4 0', 'M5.52 16h12.96'],
  audit: ['M9 12l2 2 4-4', 'M21 12c0 5-4 9-9 9s-9-4-9-9 4-9 9-9 9 4 9 9z'],
  learn: ['M4 19.5A2.5 2.5 0 0 1 6.5 17H20', 'M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z'],
  notifications: ['M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9', 'M10.3 21a1.94 1.94 0 0 0 3.4 0'],
  run: ['M6 3 20 12 6 21V3z', ''],
  queue: ['M9 11l3 3L22 4', 'M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11'],
  settings: [
    'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
    'M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z',
  ],
};

interface MenuItem {
  key: keyof typeof ICONS | string;
  route: string;
  label: string;
  /** Spoken, not drawn — the tile has no room for it. */
  hint: string;
  badge?: string | null;
}

function TileIcon({ name, color }: { name: string; color: string }) {
  const paths = ICONS[name];
  if (!paths) return null;
  return (
    <Svg
      width={20}
      height={20}
      viewBox="0 0 24 24"
      fill="none"
      stroke={color}
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <Path d={paths[0]} />
      {paths[1] ? <Path d={paths[1]} /> : null}
    </Svg>
  );
}

export default function MoreScreen() {
  const { t: tr } = useTranslation();
  const router = useRouter();
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const unread = useUnreadCount();

  /*
   * The prototype's twelve keys in its own order. Labels come from i18n
   * wherever the app already has a Turkish word for the destination — the six
   * screens the old list carried — and from the prototype's `L.menu` for the
   * six that are new. Routes are paths, not components: /market, /queue,
   * /run, /journal, /backtest and /audit are being written in parallel.
   */
  const items: MenuItem[] = [
    { key: 'market', route: '/market', label: 'Piyasa tahtası', hint: 'Semboller, fiyatlar ve gün içi hareket' },
    { key: 'charts', route: '/(tabs)/charts', label: tr('tabs.charts'), hint: 'Fiyat grafiği ve sembol arama' },
    { key: 'watchlist', route: '/(tabs)/watchlist', label: tr('tabs.watchlist'), hint: 'Takip edilen semboller ve son kararları' },
    { key: 'risk', route: '/(tabs)/risk', label: tr('tabs.risk'), hint: 'Kill switch, devre kesiciler ve limitler' },
    { key: 'journal', route: '/journal', label: 'İşlem günlüğü', hint: 'Kapanan işlemler ve gerçekleşen kâr/zarar' },
    { key: 'backtest', route: '/backtest', label: 'Backtest lab', hint: 'Strateji denemeleri ve geçmiş sonuçları' },
    { key: 'audit', route: '/audit', label: 'Denetim kaydı', hint: 'Kim neyi ne zaman onayladı' },
    { key: 'learn', route: '/(tabs)/learn', label: tr('tabs.learn'), hint: 'Paneldeki sayıların ne anlama geldiği' },
    {
      key: 'notifications',
      route: '/notifications',
      label: tr('tabs.notifications'),
      hint: 'Onay istekleri ve uyarılar',
      badge: badgeLabel(unread),
    },
    { key: 'run', route: '/run', label: 'Canlı koşu', hint: 'Günlük koşunun adımları ve durumu' },
    { key: 'queue', route: '/queue', label: 'Onay kuyruğu', hint: 'Onay bekleyen emirlerin tamamı' },
    { key: 'settings', route: '/(tabs)/settings', label: tr('tabs.settings'), hint: 'Hesap, mod, risk ve bildirimler' },
  ];

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.kicker}>{tr('tabs.more').toUpperCase()}</Text>
        <Text style={styles.title} accessibilityRole="header">
          Tüm ekranlar
        </Text>
        <Text style={styles.subtitle}>
          Sekme çubuğuna sığmayan her şey burada: piyasa, günlük, denetim ve ayarlar.
        </Text>

        <View style={styles.grid}>
          {items.map((item) => (
            <Card
              key={item.route}
              padded={false}
              style={styles.tile}
              onPress={() => router.push(item.route as never)}
              accessibilityLabel={
                item.badge ? `${item.label}, ${item.badge} okunmamış` : item.label
              }
              accessibilityHint={item.hint}
            >
              <TileIcon name={item.key} color={t.brand ?? t.accent} />
              <Text style={styles.label} numberOfLines={2}>
                {item.label}
              </Text>
              {item.badge ? (
                <View style={styles.badge}>
                  <Text style={styles.badgeText} numberOfLines={1}>
                    {item.badge}
                  </Text>
                </View>
              ) : null}
            </Card>
          ))}
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    screen: { flex: 1, backgroundColor: t.background },
    content: { paddingHorizontal: sh.space[3], paddingTop: sh.space[1], paddingBottom: TAB_BAR_CLEARANCE },

    kicker: { ...TYPE.kicker, color: t.ink3 ?? t.textMuted },
    title: { ...TYPE.h2, fontSize: 22, letterSpacing: -0.44, color: t.textPrimary, marginTop: sh.space[0] },
    subtitle: { ...TYPE.helper, fontSize: 12, color: t.ink2 ?? t.textSecondary, marginBottom: sh.space[3] },

    /*
     * The prototype's `grid-template-columns:repeat(3,1fr);gap:8px`. RN has no
     * grid, so: wrap a row, give every tile a 30% basis and let `flexGrow`
     * eat the remainder. Three baskets of 30% plus two 8pt gaps always leave
     * room for exactly three and never four, at any phone width.
     */
    grid: { flexDirection: 'row', flexWrap: 'wrap', gap: sh.space[1] },
    tile: {
      flexGrow: 1,
      flexBasis: '30%',
      minHeight: TILE_HEIGHT,
      padding: sh.space[2],
      justifyContent: 'space-between',
      gap: sh.space[1],
    },
    label: { ...TYPE.bodyStrong, lineHeight: 16, color: t.textPrimary },

    /*
     * The prototype fills the badge with `--down` and writes `#fff` on it.
     * Under Aurora `down` is a rose light enough that white on it fails
     * contrast, so this takes the same pairing the tab bar's pending badge
     * settled on: the deep danger ground with primary ink.
     */
    badge: {
      position: 'absolute',
      top: sh.space[1],
      right: sh.space[1],
      minWidth: 16,
      height: 16,
      paddingHorizontal: 4,
      borderRadius: sh.radiusPill,
      backgroundColor: t.dangerDeep ?? t.danger,
      alignItems: 'center',
      justifyContent: 'center',
    },
    badgeText: { ...TYPE.helper, fontSize: 10, ...font(600), ...TABULAR, color: t.textPrimary },
  });
