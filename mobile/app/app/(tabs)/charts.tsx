import { useMemo, useState } from 'react';
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
import { useRouter } from 'expo-router';

import { usePrices } from '@/api/hooks';
import { useTheme } from '@/theme/useTheme';
import { MIN_TOUCH_TARGET } from '@/utils/a11y';

const RANGES = [
  { label: '1A', days: 30 },
  { label: '3A', days: 90 },
  { label: '6A', days: 180 },
];

const CHART_H = 220;

// Chart is rendered with plain RN Views (no native chart lib) so it ships via
// OTA on any installed build — no APK rebuild needed.
export default function ChartsScreen() {
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);
  const { width } = useWindowDimensions();
  const router = useRouter();
  const chartW = width - 48;
  const [input, setInput] = useState('AAPL');
  const [ticker, setTicker] = useState('AAPL');
  const [days, setDays] = useState(90);
  const [mode, setMode] = useState<'area' | 'candle'>('area');
  const { data, isLoading, isError, refetch, isRefetching } = usePrices(ticker, days);

  // Memoized so the `?? []` fallback does not hand a fresh array identity to
  // the min/max useMemo below on every render.
  const bars = useMemo(() => data?.bars ?? [], [data?.bars]);
  const up = (data?.change_pct ?? 0) >= 0;
  // In Modernist this reads as ink for a rising series, accent for a falling
  // one — the same accounting scheme the P&L figures use.
  const lineColor = up ? theme.up : theme.down;
  // The line is a graphic and may use the 3.6:1 fill; the % is 14px text.
  const changeColor = up ? theme.up : theme.downText ?? theme.down;

  const { min, max } = useMemo(() => {
    if (!bars.length) return { min: 0, max: 0 };
    return { min: Math.min(...bars.map((b) => b.l)), max: Math.max(...bars.map((b) => b.h)) };
  }, [bars]);
  const span = max - min || 1;
  const slot = bars.length ? chartW / bars.length : chartW;
  const yTop = (v: number) => CHART_H - ((v - min) / span) * CHART_H; // px from top

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
            <View style={{ alignItems: 'flex-end' }}>
              <Text style={styles.price}>${data.last.toFixed(2)}</Text>
              <Text style={[styles.change, { color: changeColor }]}>
                {up ? '▲' : '▼'} {Math.abs(data.change_pct ?? 0).toFixed(2)}%
              </Text>
            </View>
          ) : null}
        </View>

        <View style={styles.modeRow}>
          {(['area', 'candle'] as const).map((m) => (
            <Pressable
              key={m}
              style={[styles.modeChip, mode === m && styles.modeChipActive]}
              onPress={() => setMode(m)}
              accessibilityRole="button"
              accessibilityLabel={m === 'area' ? 'Alan grafiği' : 'Mum grafiği'}
              accessibilityState={{ selected: mode === m }}
            >
              <Text style={[styles.modeText, mode === m && styles.modeTextActive]}>
                {m === 'area' ? 'Alan' : 'Mum'}
              </Text>
            </Pressable>
          ))}
        </View>

        <View style={[styles.chartBox, { width: chartW, height: CHART_H }]}>
          {isLoading ? (
            <ActivityIndicator color={theme.textPrimary} />
          ) : isError ? (
            <Text style={styles.err}>Fiyat alınamadı — ticker geçerli mi?</Text>
          ) : !bars.length ? (
            <Text style={styles.err}>Veri yok</Text>
          ) : mode === 'candle' ? (
            <View style={styles.candleWrap}>
              {bars.map((b, i) => {
                const cUp = b.c >= b.o;
                const col = cUp ? theme.up : theme.down;
                const wickTop = yTop(b.h);
                const bodyTop = yTop(Math.max(b.o, b.c));
                const bodyBot = yTop(Math.min(b.o, b.c));
                const bw = Math.max(1, slot * 0.6);
                return (
                  <View key={i} style={{ width: slot, height: CHART_H }}>
                    <View
                      style={{
                        position: 'absolute',
                        left: slot / 2 - 0.5,
                        top: wickTop,
                        width: 1,
                        height: Math.max(1, yTop(b.l) - wickTop),
                        backgroundColor: col,
                      }}
                    />
                    <View
                      style={{
                        position: 'absolute',
                        left: (slot - bw) / 2,
                        top: bodyTop,
                        width: bw,
                        height: Math.max(1, bodyBot - bodyTop),
                        backgroundColor: col,
                      }}
                    />
                  </View>
                );
              })}
            </View>
          ) : (
            <View style={styles.areaWrap}>
              {bars.map((b, i) => {
                const h = Math.max(2, ((b.c - min) / span) * CHART_H);
                return (
                  <View key={i} style={{ width: slot, alignItems: 'center' }}>
                    <View
                      style={{
                        width: Math.max(1, slot * 0.85),
                        height: h,
                        backgroundColor: lineColor,
                        opacity: 0.55,
                      }}
                    />
                  </View>
                );
              })}
            </View>
          )}
        </View>

        {bars.length ? (
          <View style={styles.minmax}>
            <Text style={styles.muted}>Düşük ${min.toFixed(2)}</Text>
            <Text style={styles.muted}>{bars.length} gün</Text>
            <Text style={styles.muted}>Yüksek ${max.toFixed(2)}</Text>
          </View>
        ) : null}

        <View style={styles.ranges}>
          {RANGES.map((r) => (
            <Pressable
              key={r.days}
              style={[styles.rangeChip, days === r.days && styles.rangeChipActive]}
              onPress={() => setDays(r.days)}
              accessibilityRole="button"
              accessibilityLabel={`${r.label} aralık`}
              accessibilityState={{ selected: days === r.days }}
            >
              <Text style={[styles.rangeText, days === r.days && styles.rangeTextActive]}>{r.label}</Text>
            </Pressable>
          ))}
        </View>

        <Pressable
          style={styles.analyzeBtn}
          onPress={() => router.push(`/(tabs)/ask?ticker=${ticker}` as never)}
          accessibilityRole="button"
          accessibilityLabel={`${ticker} analiz et`}
        >
          <Text style={styles.analyzeBtnText}>🤖 {ticker} analiz et</Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.background },
    scroll: { paddingHorizontal: 16, paddingTop: 20, paddingBottom: 24, gap: 16 },
    inputRow: { flexDirection: 'row' },
    input: {
      flex: 1,
      backgroundColor: t.surfaceElevated,
      color: t.textPrimary,
      borderWidth: 1,
      borderColor: t.textPrimary,
      paddingHorizontal: 14,
      paddingVertical: 11,
      fontSize: 17,
      fontWeight: '700',
      letterSpacing: 2,
    },
    go: {
      backgroundColor: t.textPrimary,
      paddingHorizontal: 20,
      justifyContent: 'center',
      alignItems: 'center',
      minHeight: MIN_TOUCH_TARGET,
      marginLeft: -1,
    },
    goText: { color: t.background, fontWeight: '800', fontSize: 15 },
    header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-end' },
    ticker: { color: t.textPrimary, fontSize: 26, fontWeight: '800' },
    price: { color: t.textPrimary, fontSize: 24, fontWeight: '800' },
    change: { fontSize: 14, fontWeight: '700', marginTop: 2 },
    // One continuous outlined strip, borders collapsed — same control as Orders.
    modeRow: { flexDirection: 'row', alignSelf: 'flex-start' },
    modeChip: {
      paddingHorizontal: 18,
      paddingVertical: 6,
      borderWidth: 1,
      borderColor: t.textPrimary,
      marginRight: -1,
      minHeight: MIN_TOUCH_TARGET,
      alignItems: 'center',
      justifyContent: 'center',
    },
    modeChipActive: { backgroundColor: t.textPrimary },
    modeText: { color: t.textPrimary, fontSize: 13, fontWeight: '700' },
    modeTextActive: { color: t.background },
    chartBox: { justifyContent: 'center', alignItems: 'center', borderBottomWidth: 2, borderBottomColor: t.textPrimary },
    areaWrap: { flexDirection: 'row', alignItems: 'flex-end', height: CHART_H, width: '100%' },
    candleWrap: { flexDirection: 'row', height: CHART_H, width: '100%' },
    minmax: { flexDirection: 'row', justifyContent: 'space-between' },
    muted: { color: t.textSecondary, fontSize: 12 },
    ranges: { flexDirection: 'row', justifyContent: 'center' },
    rangeChip: {
      paddingHorizontal: 24,
      paddingVertical: 8,
      borderWidth: 1,
      borderColor: t.textPrimary,
      marginRight: -1,
      minHeight: MIN_TOUCH_TARGET,
      alignItems: 'center',
      justifyContent: 'center',
    },
    rangeChipActive: { backgroundColor: t.textPrimary },
    rangeText: { color: t.textPrimary, fontWeight: '700' },
    rangeTextActive: { color: t.background },
    err: { color: t.accent700 ?? t.danger, fontSize: 14, textAlign: 'center' },
    analyzeBtn: {
      borderWidth: 1,
      borderColor: t.textPrimary,
      paddingVertical: 14,
      minHeight: MIN_TOUCH_TARGET,
      alignItems: 'center',
      justifyContent: 'center',
    },
    analyzeBtnText: { color: t.textPrimary, fontSize: 15, fontWeight: '700' },
  });
