/**
 * Grafik — the prototype's CHARTS screen, on Aurora.
 *
 * The prototype's order, top to bottom:
 *   1. a scrolling row of ticker pills (held names ∪ watchlist);
 *   2. the ticker header — symbol left, price and both changes right;
 *   3. ONE card holding the two segmented controls, the plot and the
 *      date/high/low footer. The chart is no longer a bare rectangle sitting on
 *      a rule: in Aurora it lives inside the same `--surface` card as its own
 *      controls, which is what makes the controls read as belonging to the plot
 *      rather than to the page;
 *   4. a two-up: the position in this name, and the last decision on it;
 *   5. the action row — watch, and analyse.
 *
 * The chart MATHS are untouched: the same `RANGE_DAYS`, the same lo/hi/slot/y
 * projection, the same 60%-of-slot body and the same closed area path that were
 * here before. Only the paint changed — the area is `--brandSoft` under a
 * `--brand` line, and the candles keep the hollow-rising / filled-falling split
 * they already had, because that split is the mark's meaning and not a style.
 *
 * Every tone still comes from `pnlTone`, every figure still from
 * `utils/format`, and the rating chip still from `ratingVariant`. This screen
 * decides nothing; it draws.
 */

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
import { useTranslation } from 'react-i18next';
import Svg, { G, Line, Path, Rect } from 'react-native-svg';

import { usePortfolio, useDecisions, usePrices } from '@/api/hooks';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { ratingVariant } from '@/theme/rating';
import { hitSlopFor, MIN_TOUCH_TARGET } from '@/utils/a11y';
import { font, TABULAR, TYPE } from '@/theme/type';
import { positionStop } from '@/utils/positions';
import { formatUsd, formatPct, parseUtc, relativeAgeTr } from '@/utils/format';
import { isAuthError } from '@/utils/apiError';
import { pnlTone, type Tone } from '@/utils/realized';
import { Seg, type SegOption } from '@/components/Seg';
import { Card } from '@/components/Card';
import { Tag } from '@/components/Tag';
import { useWatchStore, WATCH_CAP } from '@/stores/watchlist';
import { toast } from '@/stores/toast';

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

const RANGE_SUB: Record<RangeKey, string> = {
  '1A': 'Günlük barlar · son 1 ay',
  '3A': 'Günlük barlar · son 3 ay',
  '6A': 'Günlük barlar · son 6 ay',
};

/** Mobile chart height from the handoff (300 on web, 220 here). */
const CHART_H = 220;
/**
 * Room for the 2px area line's own stroke, so a series high or low is not
 * sliced in half by the edge of the box. The candle wicks get the same slack.
 */
const PAD = 2;

/** The floating tab bar's height plus air; the shell's bar is not flush. */
const TAB_BAR_CLEARANCE = 72;

/** The prototype caps its pill row at ten names. */
const MAX_CHIPS = 10;

/**
 * Hermes ships a trimmed ICU on some builds, where `toLocaleDateString('tr-TR')`
 * silently falls back to English — the same reason Bugün writes its months out.
 * Twelve strings are cheaper than an axis that reads "Sep".
 */
const TR_MONTHS_SHORT = [
  'Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz',
  'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara',
] as const;

/** `2026-09-18` → `18 Eyl`. The axis ends, nothing more. */
function barDateTr(iso: string | null | undefined): string {
  const d = parseUtc(iso);
  if (!d) return '—';
  return `${d.getUTCDate()} ${TR_MONTHS_SHORT[d.getUTCMonth()]}`;
}

/**
 * When the decision was taken, on the app's one freshness ladder rather than a
 * second date format invented here.
 */
function decisionAge(iso: string): string {
  const d = parseUtc(iso);
  return d ? relativeAgeTr(Date.now() - d.getTime()) : '—';
}

export default function ChartsScreen() {
  const { t: tr } = useTranslation();
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const { width } = useWindowDimensions();
  const router = useRouter();
  const params = useLocalSearchParams<{ ticker?: string }>();
  // Screen padding, then the card's own — the plot is inside the card now.
  const chartW = width - sh.space[3] * 4;
  const [input, setInput] = useState('AAPL');
  const [ticker, setTicker] = useState('AAPL');
  const [range, setRange] = useState<RangeKey>('3A');
  const [mode, setMode] = useState<Mode>('candle');
  const days = RANGE_DAYS[range];
  const { data, isLoading, isError, error, refetch, isRefetching } = usePrices(ticker, days);
  // The last decision and the open position for THIS name — the block under
  // the chart. Both are already-cached queries on any screen that showed them.
  const { data: decisions } = useDecisions({ ticker, limit: 1 });
  const { data: portfolio } = usePortfolio();

  // The pill row is the handoff's `chartTickers`: what is held, then what is
  // watched. The store is the watchlist screen's own — same rules, same cap.
  const watchTickers = useWatchStore((s) => s.tickers);
  const hydrateWatch = useWatchStore((s) => s.hydrate);
  const addWatch = useWatchStore((s) => s.add);
  useEffect(() => {
    void hydrateWatch();
  }, [hydrateWatch]);

  const lastDeepLink = useRef<string | null>(null);
  // Deep link from İzleme/Portföy: /(tabs)/charts?ticker=NVDA.
  useEffect(() => {
    const dl = params.ticker;
    if (dl && dl !== lastDeepLink.current) {
      lastDeepLink.current = dl;
      const next = dl.toUpperCase();
      setTicker(next);
      setInput(next);
    }
  }, [params.ticker]);

  const positions = portfolio?.positions;
  const chips = useMemo(() => {
    const out: string[] = [];
    const seen = new Set<string>();
    // The selected name always has a pill, so the active state is visible even
    // for a symbol that is neither held nor watched (a deep link, or typing).
    for (const s of [ticker, ...(positions ?? []).map((p) => p.ticker), ...watchTickers]) {
      if (!s || seen.has(s)) continue;
      seen.add(s);
      out.push(s);
      if (out.length >= MAX_CHIPS) break;
    }
    return out;
  }, [ticker, positions, watchTickers]);

  // Memoized so the `?? []` fallback does not hand a fresh array identity to
  // the geometry useMemo below on every render.
  const bars = useMemo(() => data?.bars ?? [], [data?.bars]);
  const changePct = data?.change_pct ?? null;
  /**
   * Sign -> tone is `pnlTone`'s job — the one rule the position P&L below, the
   * İzleme row and the realized card all read from — so this screen only picks
   * WHICH palette that tone resolves against. The two differ on purpose: the
   * candle bodies may take the graphic fill, while the % beside the price is
   * 13px text and takes the text value. Under Aurora the two are the same
   * colour (measured at 5.60:1 on every ground), and the split stays so the
   * screen is still correct under a palette where they are not.
   */
  const fillTone = useMemo<Record<Tone, string>>(
    () => ({ up: t.up, down: t.down, neutral: t.textPrimary }),
    [t],
  );
  const textTone = useMemo<Record<Tone, string>>(
    () => ({ up: t.up, down: t.downText ?? t.down, neutral: t.textPrimary }),
    [t],
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

  /**
   * The prototype's second figure: how the name moved across the WINDOW on
   * screen, as opposed to `change_pct`, which is the session. Derived from the
   * bars already drawn — last close over first close — so it can never disagree
   * with the plot beside it.
   */
  const windowPct = useMemo(() => {
    if (bars.length < 2) return null;
    const first = bars[0]?.c;
    const last = data?.last ?? bars[bars.length - 1]?.c;
    if (first == null || last == null || first === 0) return null;
    return last / first - 1;
  }, [bars, data?.last]);
  const windowTone = pnlTone(windowPct);

  // Guarded by ticker rather than trusting position 0: a deployment that
  // ignores the ?ticker filter would otherwise show another name's call here.
  const decision = decisions?.find((d) => d.ticker === ticker) ?? null;
  const position = portfolio?.positions.find((p) => p.ticker === ticker) ?? null;

  const onSubmit = () => {
    const next = input.trim().toUpperCase();
    if (next) {
      Keyboard.dismiss();
      setTicker(next);
    }
  };

  const onWatch = () => {
    const { outcome, ticker: added } = addWatch(ticker);
    switch (outcome) {
      case 'added':
        toast(`${added} izleme listesine eklendi`);
        break;
      case 'duplicate':
        toast(`${added} zaten listede`);
        break;
      case 'invalid':
        toast('Geçerli bir sembol gir — örn. TSLA');
        break;
      case 'full':
        toast(`Liste dolu — en fazla ${WATCH_CAP} sembol`);
        break;
    }
  };

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <ScrollView
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        refreshControl={
          <RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={t.textSecondary} />
        }
      >
        {/* The pill row. Bleeds to both edges so a long list scrolls out of the
            page rather than stopping inside a 16px gutter. */}
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          style={styles.chipStrip}
          contentContainerStyle={styles.chipRow}
          keyboardShouldPersistTaps="handled"
        >
          {chips.map((sym) => {
            const active = sym === ticker;
            return (
              <Pressable
                key={sym}
                onPress={() => {
                  setTicker(sym);
                  setInput(sym);
                }}
                hitSlop={hitSlopFor(34)}
                style={({ pressed }) => [
                  styles.chip,
                  active && styles.chipActive,
                  pressed && !active && styles.chipPressed,
                ]}
                accessibilityRole="button"
                accessibilityState={{ selected: active }}
                accessibilityLabel={`${sym} grafiğini göster`}
              >
                <Text style={[styles.chipText, active && styles.chipTextActive]}>{sym}</Text>
              </Pressable>
            );
          })}
        </ScrollView>

        <View style={styles.inputRow}>
          <TextInput
            style={styles.input}
            value={input}
            onChangeText={setInput}
            placeholder="AAPL"
            placeholderTextColor={t.ink3 ?? t.textMuted}
            autoCapitalize="characters"
            autoCorrect={false}
            maxLength={6}
            returnKeyType="search"
            onSubmitEditing={onSubmit}
            accessibilityLabel="Sembol"
          />
          <Pressable
            style={({ pressed }) => [styles.go, pressed && styles.pressedDim]}
            onPress={onSubmit}
            accessibilityRole="button"
            accessibilityLabel="Girilen sembolün grafiğini göster"
          >
            <Text style={styles.goText}>Göster</Text>
          </Pressable>
        </View>

        <View style={styles.header}>
          <View style={styles.headerLeft}>
            <Text style={styles.ticker} accessibilityRole="header" numberOfLines={1}>
              {ticker}
            </Text>
            <Text style={styles.tickerSub}>{RANGE_SUB[range]}</Text>
          </View>
          {data?.last != null ? (
            <View style={styles.headerRight}>
              <Text style={styles.price}>{formatUsd(data.last)}</Text>
              <Text style={styles.changeLine}>
                <Text style={{ color: textTone[changeTone] }}>
                  {formatPct(changePct == null ? null : changePct / 100, { signed: true })}
                </Text>
                {windowPct != null ? (
                  <>
                    <Text style={styles.changeSep}> · Dönem </Text>
                    <Text style={{ color: textTone[windowTone] }}>
                      {formatPct(windowPct, { signed: true })}
                    </Text>
                  </>
                ) : null}
              </Text>
            </View>
          ) : null}
        </View>

        {/* One card: controls, plot and axis footer, as the prototype draws it. */}
        <Card style={styles.chartCard}>
          <View style={styles.controls}>
            <Seg options={MODE_OPTIONS} value={mode} onChange={setMode} />
            <Seg options={RANGE_OPTIONS} value={range} onChange={setRange} />
          </View>

          <View style={[styles.chartBox, { width: chartW, height: CHART_H }]}>
            {isLoading ? (
              <ActivityIndicator color={t.textSecondary} />
            ) : isError ? (
              // Not the user's ticker. "ticker geçerli mi?" blamed their input
              // for what is usually a refused bearer, sending them to re-type a
              // symbol that was correct all along. isAuthError tells them apart.
              <Text style={styles.err}>
                {isAuthError(error)
                  ? 'Fiyat alınamadı — sunucu token’ı gerekli.'
                  : 'Fiyat alınamadı — bağlantını kontrol et.'}
              </Text>
            ) : !geom ? (
              <Text style={styles.muted}>Veri yok</Text>
            ) : (
              <Svg width={chartW} height={CHART_H}>
                {/* The prototype's dashed mid-rule: something for a flat tape
                    to sit against, so "went nowhere" still reads as a shape. */}
                <Line
                  x1={0}
                  x2={chartW}
                  y1={CHART_H / 2}
                  y2={CHART_H / 2}
                  stroke={t.line ?? t.divider}
                  strokeWidth={sh.hairline}
                  strokeDasharray="3 4"
                />
                {mode === 'candle' ? (
                  geom.candles.map((c) => (
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
                        // Rising is a hollow outline, falling is a solid body:
                        // the direction is legible from the fill alone, before
                        // the colour is read at all.
                        fill={c.rising ? 'none' : fillTone.down}
                        stroke={c.rising ? fillTone.up : fillTone.down}
                        strokeWidth={1}
                      />
                    </G>
                  ))
                ) : (
                  <>
                    <Path d={geom.areaPath} fill={t.brandSoft ?? t.surfaceElevated} />
                    <Path
                      d={geom.linePath}
                      fill="none"
                      stroke={t.brand ?? t.accent}
                      strokeWidth={2}
                      strokeLinejoin="round"
                      strokeLinecap="round"
                    />
                  </>
                )}
              </Svg>
            )}
          </View>

          {geom ? (
            <View style={styles.axis}>
              <Text style={styles.axisText}>{barDateTr(bars[0]?.t)}</Text>
              <Text style={styles.axisText} numberOfLines={1}>
                Yüksek {formatUsd(geom.hi)} · Düşük {formatUsd(geom.lo)}
              </Text>
              <Text style={styles.axisText}>{barDateTr(bars[bars.length - 1]?.t)}</Text>
            </View>
          ) : null}
        </Card>

        {/* The two-up. Both halves always render: "Pozisyon yok" and "Karar
            yok" are answers, and a missing card would read as a missing fetch. */}
        <View style={styles.grid}>
          <Card style={styles.gridCard}>
            <Text style={styles.cellLabel}>Pozisyon</Text>
            {position ? (
              <Text style={styles.cellValue}>
                {position.quantity} lot · ort. {formatUsd(position.avg_entry_price)}
                {'\n'}
                <Text style={{ color: textTone[pnlTone(position.unrealized_pnl)] }}>
                  {formatUsd(position.unrealized_pnl, { signed: true })}
                </Text>
                <Text style={styles.cellMuted}> · stop {formatUsd(positionStop(position))}</Text>
              </Text>
            ) : (
              <Text style={styles.cellMuted}>Pozisyon yok</Text>
            )}
          </Card>

          <Card style={styles.gridCard}>
            <Text style={styles.cellLabel}>Son karar</Text>
            {decision ? (
              <View style={styles.cellStack}>
                <Tag label={decision.rating} variant={ratingVariant(decision.rating)} size="sm" caps />
                <Text style={styles.cellMuted}>
                  {decisionAge(decision.timestamp_utc)} · hedef {formatUsd(decision.price_target)}
                </Text>
              </View>
            ) : (
              <Text style={styles.cellMuted}>Karar yok</Text>
            )}
          </Card>
        </View>

        {decision?.final_decision_text ? (
          <Card>
            <Text style={styles.cellLabel}>Gerekçe</Text>
            <Text style={styles.decisionText}>{decision.final_decision_text}</Text>
            <Text style={styles.disclaimer}>{tr('disclaimer.short')}</Text>
          </Card>
        ) : null}

        <View style={styles.actions}>
          <Pressable
            style={({ pressed }) => [styles.watchBtn, pressed && styles.pressedDim]}
            onPress={onWatch}
            accessibilityRole="button"
            accessibilityLabel={`${ticker} sembolünü izleme listesine ekle`}
          >
            <Text style={styles.watchBtnText}>+ İzle</Text>
          </Pressable>
          <Pressable
            style={({ pressed }) => [styles.analyzeBtn, pressed && styles.pressedDim]}
            onPress={() => router.push(`/(tabs)/ask?ticker=${ticker}` as never)}
            accessibilityRole="button"
            accessibilityLabel={`${ticker} analiz et`}
          >
            <Text style={styles.analyzeBtnText}>{ticker} analiz et →</Text>
          </Pressable>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    screen: { flex: 1, backgroundColor: t.background },
    content: {
      paddingHorizontal: sh.space[3],
      paddingTop: sh.space[1],
      paddingBottom: TAB_BAR_CLEARANCE,
      gap: sh.space[2],
    },

    // Bleed the strip past the page gutter, then pad it back inside.
    chipStrip: { marginHorizontal: -sh.space[3] },
    chipRow: { paddingHorizontal: sh.space[3], gap: sh.space[0] + 2, paddingVertical: sh.space[0] / 2 },
    chip: {
      height: 34,
      paddingHorizontal: sh.space[2] + 2,
      borderRadius: sh.radiusPill,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
      alignItems: 'center',
      justifyContent: 'center',
    },
    chipActive: { backgroundColor: t.textPrimary, borderColor: t.textPrimary },
    chipPressed: { borderColor: t.brand ?? t.accent },
    chipText: { fontSize: 12, ...font(600), color: t.textPrimary },
    chipTextActive: { color: t.background },

    inputRow: { flexDirection: 'row', gap: sh.space[1] },
    input: {
      flex: 1,
      minHeight: MIN_TOUCH_TARGET,
      backgroundColor: t.surface,
      color: t.textPrimary,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
      borderRadius: sh.radius,
      paddingHorizontal: sh.space[2] + 2,
      paddingVertical: sh.space[1] + 2,
      fontSize: 14,
      ...font(800),
      letterSpacing: 1.3,
    },
    go: {
      backgroundColor: t.textPrimary,
      borderRadius: sh.radius,
      paddingHorizontal: sh.space[3] + 2,
      justifyContent: 'center',
      alignItems: 'center',
      minHeight: MIN_TOUCH_TARGET,
    },
    goText: { color: t.background, ...font(800), fontSize: 14 },
    pressedDim: { opacity: 0.85 },

    header: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'flex-end',
      gap: sh.space[1],
      marginTop: sh.space[0],
    },
    headerLeft: { flexShrink: 1 },
    headerRight: { alignItems: 'flex-end' },
    ticker: { ...TYPE.h2, fontSize: 32, letterSpacing: -0.9, color: t.textPrimary, ...TABULAR },
    tickerSub: { ...TYPE.helper, fontSize: 12, color: t.ink2 ?? t.textSecondary, marginTop: 2 },
    price: { color: t.textPrimary, fontSize: 24, letterSpacing: -0.5, ...font(800), ...TABULAR },
    changeLine: { fontSize: 13, ...font(600), ...TABULAR, marginTop: 2, color: t.textPrimary },
    changeSep: { color: t.ink3 ?? t.textMuted, ...font(400) },

    chartCard: { paddingVertical: sh.space[2] },
    // Mode left, range right, on one line above the plot.
    controls: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
    chartBox: { justifyContent: 'center', alignItems: 'center', marginTop: sh.space[2] },
    axis: { flexDirection: 'row', justifyContent: 'space-between', gap: sh.space[1], marginTop: sh.space[1] },
    axisText: { fontSize: 10, ...font(400), ...TABULAR, color: t.ink3 ?? t.textMuted },

    grid: { flexDirection: 'row', gap: sh.space[1] },
    gridCard: { flex: 1 },
    cellLabel: { ...TYPE.kicker, color: t.ink3 ?? t.textMuted },
    cellValue: {
      fontSize: 12,
      ...font(600),
      ...TABULAR,
      lineHeight: 17,
      color: t.textPrimary,
      marginTop: sh.space[0],
    },
    cellMuted: { fontSize: 12, ...font(400), color: t.ink2 ?? t.textSecondary, marginTop: sh.space[0] },
    cellStack: { alignItems: 'flex-start', gap: 2, marginTop: sh.space[0] },

    decisionText: { ...TYPE.body, lineHeight: 20, color: t.textPrimary, marginTop: sh.space[0] },
    disclaimer: { ...TYPE.helper, color: t.ink3 ?? t.textMuted, lineHeight: 16, marginTop: sh.space[1] },

    muted: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, textAlign: 'center' },
    // TYPE.body rather than a bare fontSize: without font() this renders in the
    // system face, which on Android is a different typeface, not a lighter one.
    err: { ...TYPE.body, color: t.downText ?? t.danger, textAlign: 'center', paddingHorizontal: sh.space[2] },

    actions: { flexDirection: 'row', gap: sh.space[1], marginTop: sh.space[0] },
    watchBtn: {
      flex: 1,
      minHeight: 48,
      borderRadius: sh.radius,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
      backgroundColor: t.surface,
      alignItems: 'center',
      justifyContent: 'center',
    },
    watchBtnText: { fontSize: 14, ...font(600), color: t.textPrimary },
    // Primary = ink fill, as on the approve screen and in Sheet. The prototype
    // draws .btn-primary in `--ink`, which under Aurora IS the light ink on the
    // dark ground — the same inversion, read the other way round.
    analyzeBtn: {
      flex: 2,
      minHeight: 48,
      borderRadius: sh.radius,
      backgroundColor: t.textPrimary,
      alignItems: 'center',
      justifyContent: 'center',
      paddingHorizontal: sh.space[2],
    },
    analyzeBtnText: { color: t.background, fontSize: 14, ...font(800) },
  });
