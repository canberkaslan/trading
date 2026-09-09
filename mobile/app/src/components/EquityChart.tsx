import { useMemo } from 'react';
import { View, Text, StyleSheet, useWindowDimensions } from 'react-native';
import Svg, { Path, Circle } from 'react-native-svg';

import type { EquityHistory, PriceSeries } from '@/api/types';
import { useTheme } from '@/theme/useTheme';
import { formatUsd, formatPct } from '@/utils/format';
import {
  worstDrawdown,
  ddIntensity,
  rebaseSpy,
  spyReturnPct,
  alphaPct,
  combinedScale,
} from '@/utils/equity';

const CHART_H = 220;
const RIBBON_H = 8;
/** Room for the curve's own stroke so a peak or trough is not clipped. */
const PAD = 3;

/**
 * Home equity curve — drawn with plain RN Views (no native chart lib) so it
 * ships OTA on any installed build, mirroring the Charts-tab price chart.
 * Bars are tinted by cumulative return sign; the worst drawdown is annotated
 * below. An optional SPY series (`spy`) is rebased onto the portfolio's start
 * equity and overlaid as dotted markers, with an α (excess-return) chip in the
 * header. Read-only, off the trading path.
 */
export function EquityChart({ history, spy }: { history: EquityHistory; spy?: PriceSeries }) {
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);
  const { width } = useWindowDimensions();
  const chartW = width - 48;
  const pts = history.points;
  // Memoized for the same reason as the paths below: a fresh array each render
  // would defeat every memo downstream of it.
  const spyPts = useMemo(() => (spy ? rebaseSpy(spy.bars, pts) : []), [spy, pts]);
  const scale = combinedScale(pts, spyPts);
  const slot = pts.length ? chartW / pts.length : chartW;
  const up = history.total_return_pct >= 0;
  // The equity line is drawn in body ink (see the Path below), so the only
  // thing the direction still colours is the % figure beside it — which is
  // 15px text and therefore takes the legible loss colour, not the fill.
  const returnColor = up ? theme.up : theme.downText ?? theme.down;
  const maxDd = worstDrawdown(pts);
  const alpha = alphaPct(history.total_return_pct, spyReturnPct(spyPts));

  // Paths are derived from the same `combinedScale` the bars used, so the
  // curve and the drawdown ribbon below it stay on one vertical scale.
  const { equityPath, spyPath, endPoint } = useMemo(() => {
    const usable = CHART_H - PAD * 2;
    const x = (i: number) => (pts.length > 1 ? (i / (pts.length - 1)) * chartW : chartW / 2);
    const y = (v: number) => PAD + (1 - (v - scale.min) / scale.span) * usable;
    const line = (vals: { v: number; i: number }[]) =>
      vals.map((p, n) => `${n ? 'L' : 'M'}${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`).join('');

    // Built inside the memo: as a component-body `new Map(...)` its identity
    // changed every render, so the memo never actually memoized.
    const spyByDate = new Map(spyPts.map((sp) => [sp.date, sp.value]));
    const eq = pts.map((p, i) => ({ v: p.equity, i }));
    const spyByIndex = pts
      .map((p, i) => ({ v: spyByDate.get(p.date), i }))
      .filter((p): p is { v: number; i: number } => p.v != null);

    const last = eq[eq.length - 1];
    return {
      equityPath: eq.length ? line(eq) : '',
      spyPath: spyByIndex.length > 1 ? line(spyByIndex) : '',
      endPoint: last ? { x: x(last.i), y: y(last.v) } : null,
    };
  }, [pts, spyPts, scale, chartW]);

  if (!pts.length) return null;

  return (
    <View style={styles.wrap}>
      <View style={styles.headerRow}>
        <Text style={styles.label}>{history.days} gün</Text>
        <View style={styles.headerRight}>
          {alpha != null ? (
            <View style={[styles.alphaChip, { borderColor: alpha >= 0 ? theme.up : theme.down }]}>
              <Text style={[styles.alphaText, { color: alpha >= 0 ? theme.up : theme.downText ?? theme.down }]}>
                α {formatPct(alpha / 100, { signed: true })}
              </Text>
            </View>
          ) : null}
          <Text style={[styles.return, { color: returnColor }]}>
            {formatPct(history.total_return_pct / 100, { signed: true })}
          </Text>
        </View>
      </View>

      <View style={[styles.chartBox, { width: chartW, height: CHART_H }]}>
        <Svg width={chartW} height={CHART_H}>
          {/* SPY first so the portfolio curve reads on top of it. */}
          {spyPath ? <Path d={spyPath} fill="none" stroke={theme.neutral500 ?? theme.textSecondary} strokeWidth={1.5} /> : null}
          <Path
            d={equityPath}
            fill="none"
            stroke={theme.textPrimary}
            strokeWidth={2.2}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
          {/* The end of the curve, so the latest value has a fixed anchor. */}
          {endPoint ? <Circle cx={endPoint.x} cy={endPoint.y} r={3} fill={theme.textPrimary} /> : null}
        </Svg>
      </View>

      {spyPts.length ? (
        <View style={styles.legendRow}>
          <View style={[styles.legendDot, { backgroundColor: theme.textPrimary }]} />
          <Text style={styles.legendText}>Portföy</Text>
          <View style={[styles.legendDot, { backgroundColor: theme.neutral500 ?? theme.textSecondary, marginLeft: 12 }]} />
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
              backgroundColor: theme.down,
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

const makeStyles = (t: ReturnType<typeof useTheme>) =>
  StyleSheet.create({
  wrap: { paddingHorizontal: 24, marginTop: 8 },
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 },
  headerRight: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  label: { color: t.textSecondary, fontSize: 13, fontWeight: '600' },
  return: { fontSize: 15, fontWeight: '700' },
  alphaChip: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 8, paddingVertical: 2 },
  alphaText: { fontSize: 12, fontWeight: '700' },
  legendRow: { flexDirection: 'row', alignItems: 'center', marginTop: 6 },
  legendDot: { width: 8, height: 8, borderRadius: 4, marginRight: 4 },
  legendText: { color: t.textSecondary, fontSize: 11 },
  chartBox: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    height: CHART_H,
    borderBottomWidth: 1,
    borderBottomColor: t.divider,
  },
  ribbon: { flexDirection: 'row', marginTop: 3, borderRadius: 2, overflow: 'hidden' },
  footRow: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 6 },
  muted: { color: t.textSecondary, fontSize: 12 },
});
