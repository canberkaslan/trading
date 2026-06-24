import { useMemo, useState, Fragment } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  Pressable,
  ActivityIndicator,
  useWindowDimensions,
  Keyboard,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import Svg, { Path, Line, Circle, Rect, Defs, LinearGradient, Stop } from 'react-native-svg';

import { usePrices } from '@/api/hooks';
import { colors } from '@/theme/colors';
import type { Bar } from '@/api/types';

const RANGES = [
  { label: '1A', days: 30 },
  { label: '3A', days: 90 },
  { label: '6A', days: 180 },
];

const CHART_H = 220;

function buildPaths(bars: Bar[], width: number, height: number) {
  const closes = bars.map((b) => b.c);
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const span = max - min || 1;
  const dx = bars.length > 1 ? width / (bars.length - 1) : width;
  const y = (c: number) => height - ((c - min) / span) * height;
  const pts = closes.map((c, i) => [i * dx, y(c)] as const);
  const line = pts.map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(1)},${py.toFixed(1)}`).join(' ');
  const area = `${line} L${((bars.length - 1) * dx).toFixed(1)},${height} L0,${height} Z`;
  return { line, area, min, max, lastPt: pts[pts.length - 1] };
}

type Candle = { x: number; w: number; wickTop: number; wickBot: number; bodyTop: number; bodyH: number; up: boolean };

function buildCandles(bars: Bar[], width: number, height: number): { candles: Candle[]; min: number; max: number } {
  const lows = bars.map((b) => b.l);
  const highs = bars.map((b) => b.h);
  const min = Math.min(...lows);
  const max = Math.max(...highs);
  const span = max - min || 1;
  const slot = width / bars.length;
  const w = Math.max(1, slot * 0.6);
  const y = (v: number) => height - ((v - min) / span) * height;
  const candles = bars.map((b, i) => {
    const up = b.c >= b.o;
    const bodyTop = y(Math.max(b.o, b.c));
    const bodyBot = y(Math.min(b.o, b.c));
    return {
      x: i * slot + (slot - w) / 2,
      w,
      wickTop: y(b.h),
      wickBot: y(b.l),
      bodyTop,
      bodyH: Math.max(1, bodyBot - bodyTop),
      up,
    };
  });
  return { candles, min, max };
}

export default function ChartsScreen() {
  const { width } = useWindowDimensions();
  const router = useRouter();
  const chartW = width - 48;
  const [input, setInput] = useState('AAPL');
  const [ticker, setTicker] = useState('AAPL');
  const [days, setDays] = useState(90);
  const [mode, setMode] = useState<'line' | 'candle'>('line');
  const { data, isLoading, isError } = usePrices(ticker, days);

  const up = (data?.change_pct ?? 0) >= 0;
  const lineColor = up ? colors.up : colors.down;

  const paths = useMemo(() => {
    if (!data?.bars?.length) return null;
    return buildPaths(data.bars, chartW, CHART_H);
  }, [data, chartW]);

  const candleData = useMemo(() => {
    if (mode !== 'candle' || !data?.bars?.length) return null;
    return buildCandles(data.bars, chartW, CHART_H);
  }, [mode, data, chartW]);

  const onSubmit = () => {
    const t = input.trim().toUpperCase();
    if (t) {
      Keyboard.dismiss();
      setTicker(t);
    }
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.scroll}>
        <View style={styles.inputRow}>
          <TextInput
            style={styles.input}
            value={input}
            onChangeText={setInput}
            placeholder="AAPL"
            placeholderTextColor={colors.textMuted}
            autoCapitalize="characters"
            autoCorrect={false}
            maxLength={6}
            returnKeyType="search"
            onSubmitEditing={onSubmit}
          />
          <Pressable style={styles.go} onPress={onSubmit}>
            <Text style={styles.goText}>Göster</Text>
          </Pressable>
        </View>

        <View style={styles.header}>
          <Text style={styles.ticker}>{ticker}</Text>
          {data?.last != null ? (
            <View style={{ alignItems: 'flex-end' }}>
              <Text style={styles.price}>${data.last.toFixed(2)}</Text>
              <Text style={[styles.change, { color: lineColor }]}>
                {up ? '▲' : '▼'} {Math.abs(data.change_pct ?? 0).toFixed(2)}%
              </Text>
            </View>
          ) : null}
        </View>

        <View style={styles.modeRow}>
          {(['line', 'candle'] as const).map((m) => (
            <Pressable
              key={m}
              style={[styles.modeChip, mode === m && styles.modeChipActive]}
              onPress={() => setMode(m)}
            >
              <Text style={[styles.modeText, mode === m && styles.modeTextActive]}>
                {m === 'line' ? 'Çizgi' : 'Mum'}
              </Text>
            </Pressable>
          ))}
        </View>

        <View style={styles.chartBox}>
          {isLoading ? (
            <ActivityIndicator color={colors.accent} style={{ marginTop: 90 }} />
          ) : isError ? (
            <Text style={styles.err}>Fiyat alınamadı — ticker geçerli mi?</Text>
          ) : mode === 'candle' && candleData ? (
            <Svg width={chartW} height={CHART_H}>
              {candleData.candles.map((c, i) => {
                const col = c.up ? colors.up : colors.down;
                return (
                  <Fragment key={i}>
                    <Line x1={c.x + c.w / 2} y1={c.wickTop} x2={c.x + c.w / 2} y2={c.wickBot} stroke={col} strokeWidth={1} />
                    <Rect x={c.x} y={c.bodyTop} width={c.w} height={c.bodyH} fill={col} />
                  </Fragment>
                );
              })}
              <Line x1={0} y1={CHART_H - 1} x2={chartW} y2={CHART_H - 1} stroke={colors.surfaceElevated} strokeWidth={1} />
            </Svg>
          ) : paths ? (
            <Svg width={chartW} height={CHART_H}>
              <Defs>
                <LinearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
                  <Stop offset="0" stopColor={lineColor} stopOpacity={0.25} />
                  <Stop offset="1" stopColor={lineColor} stopOpacity={0} />
                </LinearGradient>
              </Defs>
              <Path d={paths.area} fill="url(#fill)" />
              <Path d={paths.line} stroke={lineColor} strokeWidth={2} fill="none" />
              {paths.lastPt ? (
                <Circle cx={paths.lastPt[0]} cy={paths.lastPt[1]} r={4} fill={lineColor} />
              ) : null}
              <Line x1={0} y1={CHART_H - 1} x2={chartW} y2={CHART_H - 1} stroke={colors.surfaceElevated} strokeWidth={1} />
            </Svg>
          ) : (
            <Text style={styles.err}>Veri yok</Text>
          )}
        </View>

        {paths ? (
          <View style={styles.minmax}>
            <Text style={styles.muted}>Düşük ${paths.min.toFixed(2)}</Text>
            <Text style={styles.muted}>{data?.bars.length} gün</Text>
            <Text style={styles.muted}>Yüksek ${paths.max.toFixed(2)}</Text>
          </View>
        ) : null}

        <View style={styles.ranges}>
          {RANGES.map((r) => (
            <Pressable
              key={r.days}
              style={[styles.rangeChip, days === r.days && styles.rangeChipActive]}
              onPress={() => setDays(r.days)}
            >
              <Text style={[styles.rangeText, days === r.days && styles.rangeTextActive]}>{r.label}</Text>
            </Pressable>
          ))}
        </View>

        <Pressable
          style={styles.analyzeBtn}
          onPress={() => router.push(`/(tabs)/ask?ticker=${ticker}` as never)}
        >
          <Text style={styles.analyzeBtnText}>🤖 {ticker} analiz et</Text>
        </Pressable>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  scroll: { padding: 24, gap: 16 },
  inputRow: { flexDirection: 'row', gap: 10 },
  input: {
    flex: 1,
    backgroundColor: colors.surface,
    color: colors.textPrimary,
    borderRadius: 12,
    paddingHorizontal: 16,
    paddingVertical: 12,
    fontSize: 17,
    fontWeight: '600',
    letterSpacing: 2,
  },
  go: { backgroundColor: colors.accent, borderRadius: 12, paddingHorizontal: 20, justifyContent: 'center' },
  goText: { color: '#fff', fontWeight: '700', fontSize: 15 },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-end' },
  ticker: { color: colors.textPrimary, fontSize: 26, fontWeight: '700' },
  price: { color: colors.textPrimary, fontSize: 22, fontWeight: '700' },
  change: { fontSize: 14, fontWeight: '600', marginTop: 2 },
  modeRow: { flexDirection: 'row', gap: 8, alignSelf: 'flex-start' },
  modeChip: { paddingHorizontal: 16, paddingVertical: 6, borderRadius: 16, backgroundColor: colors.surface },
  modeChipActive: { backgroundColor: colors.surfaceElevated },
  modeText: { color: colors.textMuted, fontSize: 13, fontWeight: '600' },
  modeTextActive: { color: colors.textPrimary },
  chartBox: { minHeight: CHART_H, justifyContent: 'center' },
  minmax: { flexDirection: 'row', justifyContent: 'space-between' },
  muted: { color: colors.textMuted, fontSize: 12 },
  ranges: { flexDirection: 'row', gap: 10, justifyContent: 'center' },
  rangeChip: { paddingHorizontal: 22, paddingVertical: 8, borderRadius: 20, backgroundColor: colors.surface },
  rangeChipActive: { backgroundColor: colors.accent },
  rangeText: { color: colors.textSecondary, fontWeight: '600' },
  rangeTextActive: { color: '#fff' },
  err: { color: colors.danger, fontSize: 14, textAlign: 'center', marginTop: 90 },
  analyzeBtn: { backgroundColor: colors.surfaceElevated, borderRadius: 12, paddingVertical: 14, alignItems: 'center', marginTop: 4 },
  analyzeBtnText: { color: colors.textPrimary, fontSize: 15, fontWeight: '600' },
});
