import { useEffect, useMemo, useRef, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  Pressable,
  ActivityIndicator,
  useWindowDimensions,
  Keyboard,
  ScrollView,
  RefreshControl,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useLocalSearchParams, useRouter } from 'expo-router';
import Svg, { G, Line, Path, Rect } from 'react-native-svg';

import { usePortfolio, useDecisions, usePrices } from '@/api/hooks';
import { useTheme } from '@/theme/useTheme';
import { ratingChip } from '@/theme/rating';
import { MIN_TOUCH_TARGET } from '@/utils/a11y';
import { font, TABULAR, TYPE } from '@/theme/type';
import { positionStop } from '@/utils/positions';
import { formatUsd, formatPct, parseUtc, relativeAgeTr } from '@/utils/format';
import { pnlTone, type Tone } from '@/utils/realized';
import { Seg, type SegOption } from '@/components/Seg';

type RangeKey = '1A' | '3A' | '6A';
type Mode = 'area' | 'candle';

const RANGE_DAYS: Record<RangeKey, number> = { '1A': 30, '3A': 90, '6A': 180 };

const RANGE_OPTIONS: readonly SegOption<RangeKey>[] = [
  { value: '1A', label: '1A', accessibilityLabel: 'Son 1 ay' },
  { value: '3A', label: '3A', accessibilityLabel: 'Son 3 ay' },
  { value: '6A', label: '6A', accessibilityLabel: 'Son 6 ay' },
];

const MODE_OPTIONS: readonly SegOption<Mode>[] = [
  { value: 'area', label: 'Alan', accessibilityLabel: 'Alan grafiği' },
  { value: 'candle', label: 'Mum', accessibilityLabel: 'Mum grafiği' },
];

/** Mobile chart height from the handoff (300 on web, 220 here). */
const CHART_H = 220;
/**
 * Room for the 2px area line's own stroke, so a series high or low is not
 * sliced in half by the edge of the box. The candle wicks get the same slack.
 */
const PAD = 2;

/**
 * Screen 5 — Grafik.
 *
 * The chart is react-native-svg (already a dependency, so still OTA-safe):
 * one Path per series rather than a stack of Views, which is what lets a rising
 * candle be a HOLLOW ink outline. That hollow/filled split is the whole point
 * of the mark — the accent is reserved for the down direction here exactly as
 * it is for a loss in the P&L, so a mostly-rising tape reads as mostly quiet
 * ink and every red body is an exception worth looking at.
 */
export default function ChartsScreen() {
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);
  const { width } = useWindowDimensions();
  const router = useRouter();
  const params = useLocalSearchParams<{ ticker?: string }>();
  const chartW = width - 32; // screen padding is 16 per side
  const [input, setInput] = useState('AAPL');
  const [ticker, setTicker] = useState('AAPL');
  const [range, setRange] = useState<RangeKey>('3A');
  const [mode, setMode] = useState<Mode>('candle');
  const days = RANGE_DAYS[range];
  const { data, isLoading, isError, refetch, isRefetching } = usePrices(ticker, days);
  // The last decision and the open position for THIS name — the block under
  // the chart. Both are already-cached queries on any screen that showed them.
  const { data: decisions } = useDecisions({ ticker, limit: 1 });
  const { data: portfolio } = usePortfolio();

  const lastDeepLink = useRef<string | null>(null);
  // Deep link from İzleme/Portföy: /(tabs)/charts?ticker=NVDA.
  useEffect(() => {
    const dl = params.ticker;
    if (dl && dl !== lastDeepLink.current) {
      lastDeepLink.current = dl;
      const t = dl.toUpperCase();
      setTicker(t);
      setInput(t);
    }
  }, [params.ticker]);

  // Memoized so the `?? []` fallback does not hand a fresh array identity to
  // the geometry useMemo below on every render.
  const bars = useMemo(() => data?.bars ?? [], [data?.bars]);
  const changePct = data?.change_pct ?? null;
  /**
   * Sign -> tone is `pnlTone`'s job — the one rule the position P&L below, the
   * İzleme row and the realized card all read from — so this screen only picks
   * WHICH palette that tone resolves against. The two differ on purpose: the
   * candle bodies and the area line are graphics and may take the 3.6:1 accent
   * fill, while the % beside the price is 13px text and needs accent-700. A
   * flat or missing change is body ink, not a direction.
   */
  const fillTone = useMemo<Record<Tone, string>>(
    () => ({ up: theme.up, down: theme.down, neutral: theme.textPrimary }),
    [theme],
  );
  const textTone = useMemo<Record<Tone, string>>(
    () => ({ up: theme.up, down: theme.downText ?? theme.down, neutral: theme.textPrimary }),
    [theme],
  );
  const changeTone = pnlTone(changePct);

  const geom = useMemo(() => {
    if (!bars.length) return null;
    const lo = Math.min(...bars.map((b) => b.l));
    const hi = Math.max(...bars.map((b) => b.h));
    const span = hi - lo || 1;
    const slot = chartW / bars.length;
    const usable = CHART_H - PAD * 2;
    const y = (v: number) => PAD + (1 - (v - lo) / span) * usable;

    const candles = bars.map((b, i) => {
      const rising = b.c >= b.o;
      const top = y(Math.max(b.o, b.c));
      return {
        key: `${b.t}-${i}`,
        rising,
        // 60% of the slot, centred — the same proportion as the prototype.
        x: i * slot + slot * 0.2,
        w: Math.max(1, slot * 0.6),
        y: top,
        h: Math.max(1, y(Math.min(b.o, b.c)) - top),
        wx: i * slot + slot / 2,
        y1: y(b.h),
        y2: y(b.l),
      };
    });

    const line = bars
      .map((b, i) => `${i ? 'L' : 'M'}${(i * slot + slot / 2).toFixed(1)},${y(b.c).toFixed(1)}`)
      .join(' ');

    return {
      lo,
      hi,
      candles,
      linePath: line,
      // Closed down to the baseline so the area reads as a filled region.
      areaPath: `${line} L${chartW.toFixed(1)},${CHART_H} L0,${CHART_H} Z`,
    };
  }, [bars, chartW]);

  // Guarded by ticker rather than trusting position 0: a deployment that
  // ignores the ?ticker filter would otherwise show another name's call here.
  const decision = decisions?.find((d) => d.ticker === ticker) ?? null;
  const position = portfolio?.positions.find((p) => p.ticker === ticker) ?? null;
  // Resolved once: `ratingChip` is the source of truth for the buy/hold/sell
  // buckets, and its keys are the plain shape (`background`/`color`), NOT RN
  // style props — they have to be mapped, never spread onto a View.
  const chip = decision ? ratingChip(theme, decision.rating) : null;

  const onSubmit = () => {
    const t = input.trim().toUpperCase();
    if (t) {
      Keyboard.dismiss();
      setTicker(t);
    }
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView
        contentContainerStyle={styles.scroll}
        keyboardShouldPersistTaps="handled"
        refreshControl={
          <RefreshControl
            refreshing={isRefetching}
            onRefresh={refetch}
            tintColor={theme.textPrimary}
            colors={[theme.textPrimary]}
          />
        }
      >
        <View style={styles.inputRow}>
          <TextInput
            style={styles.input}
            value={input}
            onChangeText={setInput}
            placeholder="AAPL"
            placeholderTextColor={theme.textSecondary}
            autoCapitalize="characters"
            autoCorrect={false}
            maxLength={6}
            returnKeyType="search"
            onSubmitEditing={onSubmit}
          />
          <Pressable
            style={styles.go}
            onPress={onSubmit}
            accessibilityRole="button"
            accessibilityLabel="Girilen sembolün grafiğini göster"
          >
            <Text style={styles.goText}>Göster</Text>
          </Pressable>
        </View>

        <View style={styles.header}>
          <Text style={styles.ticker}>{ticker}</Text>
          {data?.last != null ? (
            <View style={styles.headerRight}>
              <Text style={styles.price}>{formatUsd(data.last)}</Text>
              <Text style={[styles.change, { color: textTone[changeTone] }]}>
                {formatPct(changePct == null ? null : changePct / 100, { signed: true })}
              </Text>
            </View>
          ) : null}
        </View>

        {/* One row: mode left, range right — both the shared .seg control. */}
        <View style={styles.controls}>
          <Seg options={MODE_OPTIONS} value={mode} onChange={setMode} />
          <Seg options={RANGE_OPTIONS} value={range} onChange={setRange} />
        </View>

        <View style={[styles.chartBox, { width: chartW, height: CHART_H }]}>
          {isLoading ? (
            <ActivityIndicator color={theme.textPrimary} />
          ) : isError ? (
            <Text style={styles.err}>Fiyat alınamadı — ticker geçerli mi?</Text>
          ) : !geom ? (
            <Text style={styles.err}>Veri yok</Text>
          ) : mode === 'candle' ? (
            <Svg width={chartW} height={CHART_H}>
              {geom.candles.map((c) => (
                <G key={c.key}>
                  <Line
                    x1={c.wx}
                    x2={c.wx}
                    y1={c.y1}
                    y2={c.y2}
                    stroke={c.rising ? fillTone.up : fillTone.down}
                    strokeWidth={1}
                  />
                  <Rect
                    x={c.x}
                    y={c.y}
                    width={c.w}
                    height={c.h}
                    // Rising is a hollow ink outline, falling is a solid accent
                    // body: the direction is legible from the fill alone.
                    fill={c.rising ? 'none' : fillTone.down}
                    stroke={c.rising ? fillTone.up : fillTone.down}
                    strokeWidth={1}
                  />
                </G>
              ))}
            </Svg>
          ) : (
            <Svg width={chartW} height={CHART_H}>
              <Path d={geom.areaPath} fill={theme.surface} />
              <Path
                d={geom.linePath}
                fill="none"
                stroke={fillTone[changeTone]}
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
            </Svg>
          )}
        </View>

        {geom ? (
          <View style={styles.minmax}>
            <Text style={styles.muted}>Düşük {formatUsd(geom.lo)}</Text>
            <Text style={styles.muted}>{bars.length} gün</Text>
            <Text style={styles.muted}>Yüksek {formatUsd(geom.hi)}</Text>
          </View>
        ) : null}

        {decision && chip ? (
          <View style={styles.decisionBlock}>
            <View style={styles.decisionHead}>
              <View
                style={[
                  styles.ratingChip,
                  { backgroundColor: chip.background, borderColor: chip.borderColor ?? 'transparent' },
                ]}
              >
                <Text style={[styles.rating, { color: chip.color }]}>{decision.rating}</Text>
              </View>
              <Text style={styles.decisionMeta}>
                {decisionAge(decision.timestamp_utc)} · hedef {formatUsd(decision.price_target)}
              </Text>
            </View>
            {decision.final_decision_text ? (
              <Text style={styles.decisionText}>{decision.final_decision_text}</Text>
            ) : null}
          </View>
        ) : null}

        {position ? (
          <Text style={styles.position}>
            Pozisyon:{' '}
            <Text style={styles.positionStrong}>
              {position.quantity} @ {formatUsd(position.avg_entry_price)}
            </Text>
            {' · '}
            <Text
              style={[styles.positionStrong, { color: textTone[pnlTone(position.unrealized_pnl)] }]}
            >
              {formatUsd(position.unrealized_pnl, { signed: true })}
            </Text>
            {' · stop '}
            {formatUsd(positionStop(position))}
          </Text>
        ) : null}

        <Pressable
          style={styles.analyzeBtn}
          onPress={() => router.push(`/(tabs)/ask?ticker=${ticker}` as never)}
          accessibilityRole="button"
          accessibilityLabel={`${ticker} analiz et`}
        >
          <Text style={styles.analyzeBtnText}>{ticker} analiz et →</Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

/**
 * When the decision was taken, on the app's one freshness ladder rather than a
 * second date format invented here.
 */
function decisionAge(iso: string): string {
  const d = parseUtc(iso);
  return d ? relativeAgeTr(Date.now() - d.getTime()) : '—';
}

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.background },
    scroll: { paddingHorizontal: 16, paddingTop: 20, paddingBottom: 24, gap: 14 },
    inputRow: { flexDirection: 'row' },
    input: {
      flex: 1,
      backgroundColor: t.surfaceElevated,
      color: t.textPrimary,
      borderWidth: 1,
      borderColor: t.textPrimary,
      paddingHorizontal: 14,
      paddingVertical: 11,
      fontSize: 16,
      ...font(800),
      letterSpacing: 1.3,
    },
    go: {
      backgroundColor: t.textPrimary,
      paddingHorizontal: 20,
      justifyContent: 'center',
      alignItems: 'center',
      minHeight: MIN_TOUCH_TARGET,
      marginLeft: -1,
    },
    goText: { color: t.background, ...font(800), fontSize: 15 },
    header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-end' },
    headerRight: { alignItems: 'flex-end' },
    ticker: { color: t.textPrimary, ...TYPE.h2, ...TABULAR },
    price: { color: t.textPrimary, fontSize: 20, ...font(800), ...TABULAR },
    change: { fontSize: 13, ...font(800), marginTop: 2, ...TABULAR },
    // Mode left, range right, on one line above the chart.
    controls: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
    // Section rule: the chart sits on a 2px divider, nothing else frames it.
    chartBox: {
      justifyContent: 'center',
      alignItems: 'center',
      borderBottomWidth: 2,
      borderBottomColor: t.divider,
    },
    minmax: { flexDirection: 'row', justifyContent: 'space-between' },
    muted: { color: t.textSecondary, ...TYPE.helper, ...TABULAR },
    decisionBlock: { borderTopWidth: 2, borderTopColor: t.divider, paddingTop: 12 },
    decisionHead: { flexDirection: 'row', alignItems: 'center', gap: 10 },
    // `.tag` metrics; the colours come from `ratingChip` at the call site.
    ratingChip: { paddingHorizontal: 8, paddingVertical: 3, borderWidth: 1 },
    rating: { fontSize: 11, letterSpacing: 0.22, ...font(600) },
    decisionMeta: { color: t.textSecondary, fontSize: 12, ...font(400) },
    decisionText: { color: t.textPrimary, ...TYPE.body, lineHeight: 20, marginTop: 8 },
    position: { color: t.textSecondary, fontSize: 12, ...font(400), ...TABULAR },
    positionStrong: { color: t.textPrimary, ...font(800) },
    // TYPE.body rather than a bare fontSize: without font() this renders in the
    // system face, which on Android is a different typeface, not a lighter one.
    err: { color: t.accent700 ?? t.danger, ...TYPE.body, textAlign: 'center' },
    // Primary = ink fill, as on the approve screen and in Sheet. The handoff
    // draws .btn-primary in the accent, but the accent under ground-coloured
    // text is 3.76:1 — the LIVE strip is the one place the system spends that.
    analyzeBtn: {
      backgroundColor: t.textPrimary,
      paddingVertical: 14,
      paddingHorizontal: 16,
      minHeight: MIN_TOUCH_TARGET,
      justifyContent: 'center',
    },
    analyzeBtnText: { color: t.background, fontSize: 15, ...font(800) },
  });
