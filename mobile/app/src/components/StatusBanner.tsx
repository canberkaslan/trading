/**
 * Global trading-mode + connection strip, rendered above every tab.
 *
 * Trust signal for go-live: the paper->live flip is a one-line env change on
 * the box with zero visual difference in the app otherwise. This strip makes
 * the active mode — and a degraded broker/DB — impossible to miss.
 *
 * Rules the design encodes, unchanged by the Aurora restyle:
 * - The strip renders from the FIRST frame (never null) so the header height
 *   is stable — no layout jump when the readiness query settles.
 * - Mode is tri-state: PAPER / LIVE only when the backend actually said so;
 *   with no data the chip shows "MOD ?" — never assert PAPER while offline
 *   (after go-live that would be a dangerous lie).
 * - One failed poll is "yeniden deneniyor", not "offline": red only after 2+
 *   consecutive failures (~60s at the 30s interval).
 * - The last-run stamp beside the connection dot is best-effort: the strip must
 *   not depend on it, so an absent or still-loading actionability query simply
 *   renders the dot alone.
 *
 * Aurora adds the prototype's brand block — a round mark, the wordmark, and the
 * mode chip as a pill rather than a stamp. The mark is the app's only route
 * back to Bugün, because that screen is not one of the five tabs (see
 * `app/(tabs)/_layout.tsx`).
 */

import { View, Text, StyleSheet, Pressable } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useMemo } from 'react';
import Svg, { Path } from 'react-native-svg';

import { useActionability, useReadiness } from '@/api/hooks';
import { useUnreadCount } from '@/stores/notifications';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { badgeLabel } from '@/utils/inbox';
import { lastSubmitLabel } from '@/utils/actionability';
import { isAuthError } from '@/utils/apiError';
import { BlinkSquare } from './BlinkSquare';
import { font, TABULAR } from '@/theme/type';

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
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const { data, isError, failureCount } = useReadiness();
  const { data: flow, error: flowError } = useActionability();
  const unread = useUnreadCount();
  const badge = badgeLabel(unread);

  const mode = data?.trading_mode; // 'paper' | 'live' | undefined
  const live = mode === 'live';
  const hardOffline = isError && failureCount >= 2;

  let statusText: string;
  let statusColor: string;
  if (hardOffline) {
    statusText = 'backend offline — veriler güncel değil';
    statusColor = t.danger;
  } else if (isError) {
    statusText = 'bağlantı yeniden deneniyor…';
    statusColor = t.warning;
  } else if (!data) {
    statusText = 'bağlanıyor…';
    statusColor = t.textSecondary;
  } else if (isAuthError(flowError)) {
    // /readyz needs no bearer, so on a pure auth failure it answers 200 and the
    // strip said "● bağlı" while every screen under it said the server could
    // not be reached. Both were describing different things and the operator
    // had to reconcile them. The strip already polls an AUTHENTICATED endpoint
    // for the last-run stamp, so it can simply report what that one saw: the
    // backend is up and refusing us.
    statusText = 'sunucu token’ı gerekli — veriler okunamıyor';
    statusColor = t.danger;
  } else if (data.status !== 'ok') {
    const broken = [!data.alpaca && 'broker', !data.db && 'db'].filter(Boolean).join(' + ');
    statusText = `degraded: ${broken || 'bilinmiyor'}`;
    statusColor = t.warning;
  } else {
    // Reuse the actionability label rather than reimplementing relative time —
    // it already renders "17 sa önce" / "hiç" from the same clock the flow card
    // uses, so the strip cannot disagree with the screen below it.
    const run = flow ? lastSubmitLabel(flow.last_submitted_at_utc, new Date()) : null;
    statusText = run ? `bağlı · son koşu ${run}` : 'bağlı';
    // Healthy is the quiet state in every palette: secondary ink, with colour
    // reserved for degraded and offline. (In Modernist `up` IS the ink colour,
    // so tinting the connected dot with it would just be body text.)
    statusColor = t.textSecondary;
  }

  const degraded = (data && data.status !== 'ok') || hardOffline;
  // On LIVE the whole strip inverts: the palette's dedicated LIVE fill (a ramp
  // step darker than `danger`, so the ink on it clears AA) and page-ground ink.
  const stripBg = live ? (t.liveStrip ?? t.accent) : degraded ? t.surface : t.background;
  // The ink is the same in both variants: `liveStrip` is chosen precisely so
  // `textPrimary` clears AA on it (5.72:1), which is why LIVE inverts the
  // GROUND and not the type.
  const stripFg = t.textPrimary;
  const markBg = live ? stripFg : (t.brand ?? t.accent);
  const markFg = live ? stripBg : t.background;

  return (
    <View
      style={[styles.strip, { paddingTop: insets.top, backgroundColor: stripBg, borderBottomColor: t.line ?? t.divider }]}
      accessibilityRole="header"
      accessibilityLabel={`İşlem modu ${live ? 'live gerçek para' : mode === 'paper' ? 'paper' : 'bilinmiyor'}, ${statusText}`}
    >
      <View style={styles.row}>
        <Pressable
          style={styles.brand}
          onPress={() => router.push('/(tabs)' as never)}
          accessibilityRole="button"
          accessibilityLabel="Bugün ekranı"
          hitSlop={{ top: 8, bottom: 8, left: 8, right: 4 }}
        >
          <View style={[styles.mark, { backgroundColor: markBg }]}>
            <Text style={[styles.markText, { color: markFg }]}>T</Text>
          </View>
          <Text style={[styles.wordmark, { color: stripFg }]}>Trader</Text>
        </Pressable>

        <View
          style={[
            styles.modeChip,
            live
              ? { borderColor: stripFg, backgroundColor: 'transparent' }
              : { borderColor: 'transparent', backgroundColor: t.surface2 ?? t.surface },
          ]}
        >
          {live ? <BlinkSquare size={6} color={stripFg} /> : null}
          <Text style={[styles.modeText, { color: live ? stripFg : (t.ink2 ?? t.textSecondary) }]}>
            {live ? 'LIVE — GERÇEK PARA' : mode === 'paper' ? 'PAPER' : 'MOD ?'}
          </Text>
        </View>

        <View style={styles.spacer} />

        <View style={styles.status}>
          <View style={[styles.dot, { backgroundColor: live ? stripFg : statusColor }]} />
          <Text style={[styles.statusText, { color: live ? stripFg : statusColor }]} numberOfLines={1}>
            {statusText}
          </Text>
        </View>

        <Pressable
          style={styles.bell}
          hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
          onPress={() => router.push('/notifications' as never)}
          accessibilityRole="button"
          accessibilityLabel={unread > 0 ? `Bildirimler, ${unread} okunmamış` : 'Bildirimler'}
        >
          <BellIcon color={stripFg} />
          {badge ? (
            <View style={[styles.badge, { backgroundColor: live ? stripFg : t.dangerDeep }]}>
              <Text style={[styles.badgeText, { color: live ? stripBg : stripFg }]}>{badge}</Text>
            </View>
          ) : null}
        </Pressable>
      </View>
    </View>
  );
}

type Palette = ReturnType<typeof useTheme>;

// Colour is applied inline per element here (the LIVE variant inverts the whole
// strip), so the factory needs only the shape.
const makeStyles = (_t: Palette, sh: Shape) =>
  StyleSheet.create({
    strip: { paddingHorizontal: sh.space[3], paddingBottom: sh.space[1], borderBottomWidth: sh.hairline },
    row: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1], minHeight: 40 },
    brand: { flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 40 },
    mark: { width: 28, height: 28, borderRadius: sh.radiusPill, alignItems: 'center', justifyContent: 'center' },
    markText: { fontSize: 13, ...font(800) },
    wordmark: { fontSize: 15, ...font(800), letterSpacing: -0.15 },
    modeChip: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 5,
      borderWidth: sh.hairline,
      borderRadius: sh.radiusPill,
      paddingHorizontal: 8,
      paddingVertical: 3,
    },
    modeText: { fontSize: 10, ...font(800), letterSpacing: 0.9 },
    spacer: { flex: 1 },
    status: { flexDirection: 'row', alignItems: 'center', gap: 5, flexShrink: 1 },
    dot: { width: 7, height: 7, borderRadius: sh.radiusPill },
    statusText: { fontSize: 11, ...font(600), ...TABULAR, flexShrink: 1 },
    bell: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center', marginRight: -10 },
    badge: {
      position: 'absolute',
      top: 4,
      right: 4,
      minWidth: 16,
      height: 16,
      paddingHorizontal: 4,
      borderRadius: sh.radiusPill,
      alignItems: 'center',
      justifyContent: 'center',
    },
    badgeText: { fontSize: 9, ...font(800), ...TABULAR },
  });
