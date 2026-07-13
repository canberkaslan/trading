/**
 * Global trading-mode + connection banner, rendered above every tab.
 *
 * Trust signal for go-live: the paper->live flip is a one-line env change
 * on the box with zero visual difference in the app otherwise. This strip
 * makes the active mode — and a degraded broker/DB — impossible to miss.
 */

import { View, Text, StyleSheet } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { useReadiness } from '@/api/hooks';
import { colors } from '@/theme/colors';

export function StatusBanner() {
  const insets = useSafeAreaInsets();
  const { data, isError, isLoading } = useReadiness();

  // First load: render nothing — screens keep their own SafeArea behavior.
  if (isLoading && !data && !isError) return null;

  const live = data?.trading_mode === 'live';
  const degraded = isError || !data || data.status !== 'ok';

  let statusText = '● bağlı';
  let statusColor: string = colors.up;
  if (isError || !data) {
    statusText = '● backend offline — veriler güncel değil';
    statusColor = colors.down;
  } else if (data.status !== 'ok') {
    const broken = [!data.alpaca && 'broker', !data.db && 'db'].filter(Boolean).join(' + ');
    statusText = `● degraded: ${broken || 'bilinmiyor'}`;
    statusColor = colors.warning;
  }

  return (
    <View
      style={[
        styles.strip,
        { paddingTop: insets.top },
        live ? styles.stripLive : null,
        !live && degraded ? styles.stripDegraded : null,
      ]}
      accessibilityRole="header"
      accessibilityLabel={`İşlem modu ${live ? 'live gerçek para' : 'paper'}, ${statusText}`}
    >
      <View style={styles.row}>
        <View style={[styles.modeChip, live ? styles.modeChipLive : styles.modeChipPaper]}>
          <Text style={[styles.modeText, live ? styles.modeTextLive : styles.modeTextPaper]}>
            {live ? 'LIVE — GERÇEK PARA' : 'PAPER'}
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
  modeText: { fontSize: 11, fontWeight: '800', letterSpacing: 0.5 },
  modeTextPaper: { color: colors.warning },
  modeTextLive: { color: colors.textPrimary },
  statusText: { fontSize: 11, fontWeight: '600', flexShrink: 1 },
});
