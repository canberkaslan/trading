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
import { colors } from '@/theme/colors';

const RANGES = [
  { label: '1A', days: 30 },
  { label: '3A', days: 90 },
  { label: '6A', days: 180 },
];

const CHART_H = 220;

// Chart is rendered with plain RN Views (no native chart lib) so it ships via
// OTA on any installed build — no APK rebuild needed.
export default function ChartsScreen() {
  const { width } = useWindowDimensions();
  const router = useRouter();
  const chartW = width - 48;
  const [input, setInput] = useState('AAPL');
  const [ticker, setTicker] = useState('AAPL');
  const [days, setDays] = useState(90);
  const [mode, setMode] = useState<'area' | 'candle'>('area');
  const { data, isLoading, isError, refetch, isRefetching } = usePrices(ticker, days);

  const bars = data?.bars ?? [];
  const up = (data?.change_pct ?? 0) >= 0;
  const lineColor = up ? colors.up : colors.down;

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
            tintColor={colors.accent}
            colors={[colors.accent]}
          />
        }
      >
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
          {(['area', 'candle'] as const).map((m) => (
            <Pressable
              key={m}
              style={[styles.modeChip, mode === m && styles.modeChipActive]}
              onPress={() => setMode(m)}
            >
              <Text style={[styles.modeText, mode === m && styles.modeTextActive]}>
                {m === 'area' ? 'Alan' : 'Mum'}
              </Text>
            </Pressable>
          ))}
        </View>

        <View style={[styles.chartBox, { width: chartW, height: CHART_H }]}>
          {isLoading ? (
            <ActivityIndicator color={colors.accent} />
          ) : isError ? (
            <Text style={styles.err}>Fiyat alınamadı — ticker geçerli mi?</Text>
          ) : !bars.length ? (
            <Text style={styles.err}>Veri yok</Text>
          ) : mode === 'candle' ? (
            <View style={styles.candleWrap}>
              {bars.map((b, i) => {
                const cUp = b.c >= b.o;
                const col = cUp ? colors.up : colors.down;
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
                        opacity: 0.5,
                        borderTopLeftRadius: 1,
                        borderTopRightRadius: 1,
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
      </ScrollView>
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
  chartBox: { justifyContent: 'center', alignItems: 'center', borderBottomWidth: 1, borderBottomColor: colors.surfaceElevated },
  areaWrap: { flexDirection: 'row', alignItems: 'flex-end', height: CHART_H, width: '100%' },
  candleWrap: { flexDirection: 'row', height: CHART_H, width: '100%' },
  minmax: { flexDirection: 'row', justifyContent: 'space-between' },
  muted: { color: colors.textMuted, fontSize: 12 },
  ranges: { flexDirection: 'row', gap: 10, justifyContent: 'center' },
  rangeChip: { paddingHorizontal: 22, paddingVertical: 8, borderRadius: 20, backgroundColor: colors.surface },
  rangeChipActive: { backgroundColor: colors.accent },
  rangeText: { color: colors.textSecondary, fontWeight: '600' },
  rangeTextActive: { color: '#fff' },
  err: { color: colors.danger, fontSize: 14, textAlign: 'center' },
  analyzeBtn: { backgroundColor: colors.surfaceElevated, borderRadius: 12, paddingVertical: 14, alignItems: 'center' },
  analyzeBtnText: { color: colors.textPrimary, fontSize: 15, fontWeight: '600' },
});
