import { View, Text, StyleSheet, useWindowDimensions } from 'react-native';

import type { EquityHistory, PriceSeries } from '@/api/types';
import { colors } from '@/theme/colors';
import { formatUsd, formatPct } from '@/utils/format';
import {
  barHeight,
  worstDrawdown,
  ddIntensity,
  rebaseSpy,
  spyReturnPct,
  alphaPct,
  combinedScale,
} from '@/utils/equity';

const CHART_H = 120;
const RIBBON_H = 8;
const SPY_DOT = 3;

/**
 * Home equity curve — drawn with plain RN Views (no native chart lib) so it
 * ships OTA on any installed build, mirroring the Charts-tab price chart.
 * Bars are tinted by cumulative return sign; the worst drawdown is annotated
 * below. An optional SPY series (`spy`) is rebased onto the portfolio's start
 * equity and overlaid as dotted markers, with an α (excess-return) chip in the
 * header. Read-only, off the trading path.
 */
export function EquityChart({ history, spy }: { history: EquityHistory; spy?: PriceSeries }) {
  const { width } = useWindowDimensions();
  const chartW = width - 48;
  const pts = history.points;
  const spyPts = spy ? rebaseSpy(spy.bars, pts) : [];
  const scale = combinedScale(pts, spyPts);
  const slot = pts.length ? chartW / pts.length : chartW;
  const up = history.total_return_pct >= 0;
  const barColor = up ? colors.up : colors.down;
  const maxDd = worstDrawdown(pts);
  const spyByDate = new Map(spyPts.map((p) => [p.date, p.value]));
  const alpha = alphaPct(history.total_return_pct, spyReturnPct(spyPts));

  if (!pts.length) return null;

  return (
    <View style={styles.wrap}>
      <View style={styles.headerRow}>
        <Text style={styles.label}>{history.days} gün</Text>
        <View style={styles.headerRight}>
          {alpha != null ? (
            <View style={[styles.alphaChip, { borderColor: alpha >= 0 ? colors.up : colors.down }]}>
              <Text style={[styles.alphaText, { color: alpha >= 0 ? colors.up : colors.down }]}>
                α {formatPct(alpha / 100, { signed: true })}
              </Text>
            </View>
          ) : null}
          <Text style={[styles.return, { color: barColor }]}>
            {formatPct(history.total_return_pct / 100, { signed: true })}
          </Text>
        </View>
      </View>

      <View style={[styles.chartBox, { width: chartW, height: CHART_H }]}>
        {pts.map((p) => {
          const spyVal = spyByDate.get(p.date);
          return (
            <View key={p.date} style={{ width: slot, height: CHART_H, justifyContent: 'flex-end', alignItems: 'center' }}>
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
              {spyVal != null ? (
                <View
                  style={{
                    position: 'absolute',
                    bottom: barHeight(spyVal, scale, CHART_H) - SPY_DOT / 2,
                    width: SPY_DOT,
                    height: SPY_DOT,
                    borderRadius: SPY_DOT / 2,
                    backgroundColor: colors.textSecondary,
                  }}
                />
              ) : null}
            </View>
          );
        })}
      </View>

      {spyPts.length ? (
        <View style={styles.legendRow}>
          <View style={[styles.legendDot, { backgroundColor: barColor, opacity: 0.6 }]} />
          <Text style={styles.legendText}>Portföy</Text>
          <View style={[styles.legendDot, { backgroundColor: colors.textSecondary, marginLeft: 12 }]} />
          <Text style={styles.legendText}>SPY</Text>
        </View>
      ) : null}

      {/* Drawdown ribbon — each cell tinted by its depth relative to the worst
          point, so underwater stretches read at a glance under the curve. */}
      <View style={[styles.ribbon, { width: chartW, height: RIBBON_H }]}>
        {pts.map((p) => (
          <View
            key={p.date}
            style={{
              width: slot,
              height: RIBBON_H,
              backgroundColor: colors.down,
              opacity: 0.12 + 0.68 * ddIntensity(p.drawdown_pct, maxDd),
            }}
          />
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
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 },
  headerRight: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  label: { color: colors.textSecondary, fontSize: 13, fontWeight: '600' },
  return: { fontSize: 15, fontWeight: '700' },
  alphaChip: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 8, paddingVertical: 2 },
  alphaText: { fontSize: 12, fontWeight: '700' },
  legendRow: { flexDirection: 'row', alignItems: 'center', marginTop: 6 },
  legendDot: { width: 8, height: 8, borderRadius: 4, marginRight: 4 },
  legendText: { color: colors.textMuted, fontSize: 11 },
  chartBox: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    height: CHART_H,
    borderBottomWidth: 1,
    borderBottomColor: colors.surfaceElevated,
  },
  ribbon: { flexDirection: 'row', marginTop: 3, borderRadius: 2, overflow: 'hidden' },
  footRow: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 6 },
  muted: { color: colors.textMuted, fontSize: 12 },
});
