import { View, Text, StyleSheet, useWindowDimensions } from 'react-native';

import type { EquityHistory } from '@/api/types';
import { colors } from '@/theme/colors';
import { formatUsd, formatPct } from '@/utils/format';
import { equityScale, barHeight, worstDrawdown } from '@/utils/equity';

const CHART_H = 120;

/**
 * Home equity curve — drawn with plain RN Views (no native chart lib) so it
 * ships OTA on any installed build, mirroring the Charts-tab price chart.
 * Bars are tinted by cumulative return sign; the worst drawdown is annotated
 * below. Read-only, off the trading path.
 */
export function EquityChart({ history }: { history: EquityHistory }) {
  const { width } = useWindowDimensions();
  const chartW = width - 48;
  const pts = history.points;
  const scale = equityScale(pts);
  const slot = pts.length ? chartW / pts.length : chartW;
  const up = history.total_return_pct >= 0;
  const barColor = up ? colors.up : colors.down;
  const maxDd = worstDrawdown(pts);

  if (!pts.length) return null;

  return (
    <View style={styles.wrap}>
      <View style={styles.headerRow}>
        <Text style={styles.label}>{history.days} gün</Text>
        <Text style={[styles.return, { color: barColor }]}>
          {formatPct(history.total_return_pct / 100, { signed: true })}
        </Text>
      </View>

      <View style={[styles.chartBox, { width: chartW, height: CHART_H }]}>
        {pts.map((p) => (
          <View key={p.date} style={{ width: slot, alignItems: 'center' }}>
            <View
              style={{
                width: Math.max(1, slot * 0.85),
                height: barHeight(p.equity, scale, CHART_H),
                backgroundColor: barColor,
                opacity: 0.45,
                borderTopLeftRadius: 1,
                borderTopRightRadius: 1,
              }}
            />
          </View>
        ))}
      </View>

      <View style={styles.footRow}>
        <Text style={styles.muted}>{formatUsd(history.start_equity)}</Text>
        <Text style={styles.muted}>
          Max DD {formatPct(maxDd / 100)}
        </Text>
        <Text style={styles.muted}>{formatUsd(history.end_equity)}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { paddingHorizontal: 24, marginTop: 8 },
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 },
  label: { color: colors.textSecondary, fontSize: 13, fontWeight: '600' },
  return: { fontSize: 15, fontWeight: '700' },
  chartBox: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    height: CHART_H,
    borderBottomWidth: 1,
    borderBottomColor: colors.surfaceElevated,
  },
  footRow: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 6 },
  muted: { color: colors.textMuted, fontSize: 12 },
});
