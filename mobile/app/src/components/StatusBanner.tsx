/**
 * Global trading-mode + connection strip, rendered above every tab.
 *
 * Trust signal for go-live: the paper->live flip is a one-line env change on
 * the box with zero visual difference in the app otherwise. This strip makes
 * the active mode — and a degraded broker/DB — impossible to miss.
 *
 * Rules the design encodes, unchanged by the Modernist restyle:
 * - The strip renders from the FIRST frame (never null) so the header height
 *   is stable — no layout jump when the readiness query settles.
 * - Mode is tri-state: PAPER / LIVE only when the backend actually said so;
 *   with no data the chip shows "MOD ?" — never assert PAPER while offline
 *   (after go-live that would be a dangerous lie).
 * - One failed poll is "yeniden deneniyor", not "offline": red only after 2+
 *   consecutive failures (~60s at the 30s interval).
 *
 * Modernist adds the last-run stamp beside the connection dot. It is
 * best-effort: the strip must not depend on it, so an absent or still-loading
 * actionability query simply renders the dot alone.
 */

import { View, Text, StyleSheet, Pressable } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import Svg, { Path } from 'react-native-svg';

import { useActionability, useReadiness } from '@/api/hooks';
import { useUnreadCount } from '@/stores/notifications';
import { useTheme } from '@/theme/useTheme';
import { badgeLabel } from '@/utils/inbox';
import { lastSubmitLabel } from '@/utils/actionability';

/** Lucide `bell`, traced rather than shipped as a font so it inherits colour. */
function BellIcon({ color }: { color: string }) {
  return (
    <Svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round">
      <Path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
      <Path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
    </Svg>
  );
}

export function StatusBanner() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const t = useTheme();
  const { data, isError, failureCount } = useReadiness();
  const { data: flow } = useActionability();
  const unread = useUnreadCount();
  const badge = badgeLabel(unread);

  const mode = data?.trading_mode; // 'paper' | 'live' | undefined
  const live = mode === 'live';
  const hardOffline = isError && failureCount >= 2;

  let statusText: string;
  let statusColor: string;
  if (hardOffline) {
    statusText = '● backend offline — veriler güncel değil';
    statusColor = t.danger;
  } else if (isError) {
    statusText = '● bağlantı yeniden deneniyor…';
    statusColor = t.warning;
  } else if (!data) {
    statusText = '● bağlanıyor…';
    statusColor = t.textSecondary;
  } else if (data.status !== 'ok') {
    const broken = [!data.alpaca && 'broker', !data.db && 'db'].filter(Boolean).join(' + ');
    statusText = `● degraded: ${broken || 'bilinmiyor'}`;
    statusColor = t.warning;
  } else {
    // Reuse the actionability label rather than reimplementing relative time —
    // it already renders "17 sa önce" / "hiç" from the same clock the flow card
    // uses, so the strip cannot disagree with the screen below it.
    const run = flow ? lastSubmitLabel(flow.last_submitted_at_utc, new Date()) : null;
    statusText = run ? `● bağlı · son koşu ${run}` : '● bağlı';
    // In Modernist `up` IS the ink colour, so tinting the connected dot with it
    // would just be body text. Healthy is the quiet state in both palettes:
    // secondary ink, with colour reserved for degraded and offline.
    statusColor = t.textSecondary;
  }

  const degraded = (data && data.status !== 'ok') || hardOffline;
  // On LIVE the whole strip inverts: accent fill, page-ground ink.
  const stripBg = live ? (t.liveStrip ?? t.accent) : degraded ? t.surface : t.background;
  const stripFg = live ? t.background : t.textPrimary;

  return (
    <View
      style={[styles.strip, { paddingTop: insets.top, backgroundColor: stripBg, borderBottomColor: t.divider }]}
      accessibilityRole="header"
      accessibilityLabel={`İşlem modu ${live ? 'live gerçek para' : mode === 'paper' ? 'paper' : 'bilinmiyor'}, ${statusText}`}
    >
      <View style={styles.row}>
        <View style={[styles.modeChip, { borderColor: stripFg }]}>
          <Text style={[styles.modeText, { color: stripFg }]}>
            {live ? 'LIVE — GERÇEK PARA' : mode === 'paper' ? 'PAPER' : 'MOD ?'}
          </Text>
        </View>
        <Text
          style={[styles.statusText, { color: live ? stripFg : statusColor }]}
          numberOfLines={1}
        >
          {statusText}
        </Text>
        <Pressable
          style={styles.bell}
          hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
          onPress={() => router.push('/notifications' as never)}
          accessibilityRole="button"
          accessibilityLabel={unread > 0 ? `Bildirimler, ${unread} okunmamış` : 'Bildirimler'}
        >
          <BellIcon color={stripFg} />
          {badge ? (
            <View style={[styles.badge, { backgroundColor: live ? t.background : t.accent }]}>
              <Text style={[styles.badgeText, { color: live ? t.accent : t.background }]}>{badge}</Text>
            </View>
          ) : null}
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  strip: { paddingHorizontal: 16, paddingBottom: 8, borderBottomWidth: 2 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 32 },
  // Square, outlined in the strip's own ink — the chip reads as a stamp rather
  // than a pill, and inverts with the strip on LIVE without a second rule.
  modeChip: { borderWidth: 1, paddingHorizontal: 8, paddingVertical: 3 },
  modeText: { fontSize: 11, fontWeight: '800', letterSpacing: 0.88 },
  statusText: { fontSize: 11, fontWeight: '600', flex: 1 },
  bell: { width: 44, height: 32, alignItems: 'center', justifyContent: 'center', marginRight: -12 },
  badge: { position: 'absolute', top: 0, right: 6, paddingHorizontal: 4, paddingVertical: 1 },
  badgeText: { fontSize: 9, fontWeight: '800' },
});
