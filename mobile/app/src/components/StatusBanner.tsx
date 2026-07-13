/**
 * Global trading-mode + connection banner, rendered above every tab.
 *
 * Trust signal for go-live: the paper->live flip is a one-line env change
 * on the box with zero visual difference in the app otherwise. This strip
 * makes the active mode — and a degraded broker/DB — impossible to miss.
 *
 * Rules the design encodes:
 * - The strip renders from the FIRST frame (never null) so the header
 *   height is stable — no layout jump when the readiness query settles.
 * - Mode is tri-state: PAPER / LIVE only when the backend actually said
 *   so; with no data the chip shows "MOD ?" — never assert PAPER while
 *   offline (after go-live that would be a dangerous lie).
 * - One failed poll is "yeniden deneniyor", not "offline": red only after
 *   2+ consecutive failures (~60s at the 30s interval).
 */

import { View, Text, StyleSheet } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { useReadiness } from '@/api/hooks';
import { colors } from '@/theme/colors';

export function StatusBanner() {
  const insets = useSafeAreaInsets();
  const { data, isError, failureCount } = useReadiness();

  const mode = data?.trading_mode; // 'paper' | 'live' | undefined
  const live = mode === 'live';
  const hardOffline = isError && failureCount >= 2;

  let statusText: string;
  let statusColor: string;
  if (hardOffline) {
    statusText = '● backend offline — veriler güncel değil';
    statusColor = colors.down;
  } else if (isError) {
    statusText = '● bağlantı yeniden deneniyor…';
    statusColor = colors.warning;
  } else if (!data) {
    statusText = '● bağlanıyor…';
    statusColor = colors.textMuted;
  } else if (data.status !== 'ok') {
    const broken = [!data.alpaca && 'broker', !data.db && 'db'].filter(Boolean).join(' + ');
    statusText = `● degraded: ${broken || 'bilinmiyor'}`;
    statusColor = colors.warning;
  } else {
    statusText = '● bağlı';
    statusColor = colors.up;
  }

  const degradedBg = (data && data.status !== 'ok') || hardOffline;

  return (
    <View
      style={[
        styles.strip,
        { paddingTop: insets.top },
        live ? styles.stripLive : null,
        !live && degradedBg ? styles.stripDegraded : null,
      ]}
      accessibilityRole="header"
      accessibilityLabel={`İşlem modu ${live ? 'live gerçek para' : mode === 'paper' ? 'paper' : 'bilinmiyor'}, ${statusText}`}
    >
      <View style={styles.row}>
        <View
          style={[
            styles.modeChip,
            live ? styles.modeChipLive : mode === 'paper' ? styles.modeChipPaper : styles.modeChipUnknown,
          ]}
        >
          <Text
            style={[
              styles.modeText,
              live ? styles.modeTextLive : mode === 'paper' ? styles.modeTextPaper : styles.modeTextUnknown,
            ]}
          >
            {live ? 'LIVE — GERÇEK PARA' : mode === 'paper' ? 'PAPER' : 'MOD ?'}
          </Text>
        </View>
        <Text style={[styles.statusText, { color: statusColor }]} numberOfLines={1}>
          {statusText}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  strip: {
    backgroundColor: colors.background,
    paddingHorizontal: 16,
    paddingBottom: 6,
  },
  stripLive: { backgroundColor: '#450a0a' },
  stripDegraded: { backgroundColor: '#451a03' },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 24 },
  modeChip: { borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3 },
  modeChipPaper: { backgroundColor: 'rgba(245, 158, 11, 0.15)' },
  modeChipLive: { backgroundColor: colors.down },
  modeChipUnknown: { backgroundColor: colors.surfaceElevated },
  modeText: { fontSize: 11, fontWeight: '800', letterSpacing: 0.5 },
  modeTextPaper: { color: colors.warning },
  modeTextLive: { color: colors.textPrimary },
  modeTextUnknown: { color: colors.textMuted },
  statusText: { fontSize: 11, fontWeight: '600', flexShrink: 1 },
});
