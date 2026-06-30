import { useState, useEffect, useRef } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  Pressable,
  ScrollView,
  ActivityIndicator,
  Keyboard,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';

import { useStartAnalysis, useAnalysisJob } from '@/api/hooks';
import { colors } from '@/theme/colors';
import type { Rating } from '@/api/types';

const RATING_COLOR: Record<Rating, string> = {
  Buy: colors.up,
  Overweight: '#86efac',
  Hold: colors.textSecondary,
  Underweight: '#fda4af',
  Sell: colors.down,
};

const STATUS_LABEL: Record<string, string> = {
  queued: 'Sıraya alındı…',
  running: 'Ajanlar tartışıyor… (~5-10 dk)',
  done: 'Hazır',
  error: 'Hata',
};

export default function AskScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ ticker?: string }>();
  const [ticker, setTicker] = useState('');
  const [jobId, setJobId] = useState<string | null>(null);
  const start = useStartAnalysis();
  const { data: job } = useAnalysisJob(jobId);
  const lastDeepLink = useRef<string | null>(null);

  const busy =
    start.isPending || job?.status === 'queued' || job?.status === 'running';

  const runAnalysis = (raw: string) => {
    const t = raw.trim().toUpperCase();
    if (!t || busy) return;
    Keyboard.dismiss();
    setTicker(t);
    start.mutate(t, { onSuccess: (j) => setJobId(j.job_id) });
  };

  const onAnalyze = () => runAnalysis(ticker);

  // Deep-link from Portfolio/Agents: /(tabs)/ask?ticker=NVDA auto-runs once.
  useEffect(() => {
    const dl = params.ticker;
    if (dl && dl !== lastDeepLink.current) {
      lastDeepLink.current = dl;
      setTicker(dl.toUpperCase());
      runAnalysis(dl);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.ticker]);

  const decision = job?.status === 'done' ? job.decision : null;

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
        <Text style={styles.heading}>Sor</Text>
        <Text style={styles.subheading}>
          Bir hisse gir — 7 ajanlı pipeline analiz eder. Sadece analiz, emir göndermez.
        </Text>

        <View style={styles.inputRow}>
          <TextInput
            style={styles.input}
            value={ticker}
            onChangeText={setTicker}
            placeholder="AAPL"
            placeholderTextColor={colors.textMuted}
            autoCapitalize="characters"
            autoCorrect={false}
            maxLength={6}
            returnKeyType="search"
            onSubmitEditing={onAnalyze}
            editable={!busy}
          />
          <Pressable
            style={[styles.btn, busy && styles.btnDisabled]}
            onPress={onAnalyze}
            disabled={busy}
          >
            {busy ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={styles.btnText}>Analiz</Text>
            )}
          </Pressable>
        </View>

        {start.isError ? (
          <Text style={styles.err}>İstek başarısız — backend'e ulaşılamadı.</Text>
        ) : null}

        {!job && !decision && !busy && !start.isError ? (
          <View style={styles.empty}>
            <Text style={styles.emptyIcon}>📊</Text>
            <Text style={styles.emptyTitle}>Henüz analiz yok</Text>
            <Text style={styles.emptyText}>
              Yukarıya bir hisse kodu yaz ya da aşağıdan birini seç. 7 ajanlı pipeline
              ~5-10 dk içinde Buy/Hold/Sell kararı üretir.
            </Text>
            <View style={styles.chips}>
              {['AAPL', 'NVDA', 'MSFT', 'GOOGL', 'AMZN'].map((t) => (
                <Pressable key={t} style={styles.chip} onPress={() => runAnalysis(t)}>
                  <Text style={styles.chipText}>{t}</Text>
                </Pressable>
              ))}
            </View>
          </View>
        ) : null}

        {job && !decision ? (
          <View style={styles.statusCard}>
            {busy ? <ActivityIndicator color={colors.accent} /> : null}
            <Text style={styles.statusText}>
              {job.ticker}: {STATUS_LABEL[job.status] ?? job.status}
            </Text>
            {job.status === 'error' ? (
              <Text style={styles.err}>{job.error ?? 'bilinmeyen hata'}</Text>
            ) : null}
          </View>
        ) : null}

        {decision ? (
          <Pressable
            style={styles.card}
            onPress={() => router.push(`/trade/${decision.ticker}` as never)}
          >
            <View style={styles.row}>
              <Text style={styles.ticker}>{decision.ticker}</Text>
              <Text style={[styles.rating, { color: RATING_COLOR[decision.rating] }]}>
                {decision.rating}
              </Text>
            </View>
            <View style={[styles.row, { marginTop: 10 }]}>
              <Metric label="Entry" value={decision.entry_price} />
              <Metric label="Stop" value={decision.stop_loss} />
              <Metric label="Hedef" value={decision.price_target} />
            </View>
            {decision.time_horizon ? (
              <Text style={styles.horizon}>Vade: {decision.time_horizon}</Text>
            ) : null}

            {decision.final_decision_text ? (
              <Text style={styles.rationale} numberOfLines={6}>
                {decision.final_decision_text}
              </Text>
            ) : null}

            {decision.reasoning?.length ? (
              <View style={styles.agentsBox}>
                <Text style={styles.agentsTitle}>Ajan gerekçeleri</Text>
                {decision.reasoning.slice(0, 4).map((r, i) => (
                  <Text key={i} style={styles.agentLine} numberOfLines={2}>
                    <Text style={styles.agentName}>{r.agent}: </Text>
                    {r.summary}
                  </Text>
                ))}
              </View>
            ) : null}

            <Text style={styles.tapHint}>Detay için dokun →</Text>
          </Pressable>
        ) : null}
      </ScrollView>
    </SafeAreaView>
  );
}

function Metric({ label, value }: { label: string; value: number | null }) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricLabel}>{label}</Text>
      <Text style={styles.metricValue}>{value != null ? `$${value}` : '—'}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  scroll: { padding: 24, gap: 12 },
  heading: { color: colors.textPrimary, fontSize: 28, fontWeight: '700' },
  subheading: { color: colors.textSecondary, fontSize: 13, marginBottom: 8 },
  inputRow: { flexDirection: 'row', gap: 10, marginTop: 4 },
  input: {
    flex: 1,
    backgroundColor: colors.surface,
    color: colors.textPrimary,
    borderRadius: 12,
    paddingHorizontal: 16,
    paddingVertical: 14,
    fontSize: 18,
    fontWeight: '600',
    letterSpacing: 2,
  },
  btn: {
    backgroundColor: colors.accent,
    borderRadius: 12,
    paddingHorizontal: 22,
    justifyContent: 'center',
    minWidth: 96,
    alignItems: 'center',
  },
  btnDisabled: { opacity: 0.5 },
  btnText: { color: '#fff', fontSize: 16, fontWeight: '700' },
  statusCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    padding: 16,
    backgroundColor: colors.surface,
    borderRadius: 12,
    marginTop: 4,
  },
  statusText: { color: colors.textSecondary, fontSize: 14, flexShrink: 1 },
  card: { padding: 18, backgroundColor: colors.surface, borderRadius: 12, gap: 4 },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  ticker: { color: colors.textPrimary, fontSize: 22, fontWeight: '700' },
  rating: { fontSize: 16, fontWeight: '700' },
  metric: { alignItems: 'flex-start' },
  metricLabel: { color: colors.textMuted, fontSize: 11 },
  metricValue: { color: colors.textPrimary, fontSize: 15, fontWeight: '600', marginTop: 2 },
  horizon: { color: colors.textMuted, fontSize: 12, marginTop: 8 },
  rationale: { color: colors.textSecondary, fontSize: 13, lineHeight: 19, marginTop: 12 },
  agentsBox: { marginTop: 14, gap: 6, borderTopWidth: 1, borderTopColor: colors.surfaceElevated, paddingTop: 12 },
  agentsTitle: { color: colors.textMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: 1 },
  agentLine: { color: colors.textSecondary, fontSize: 12, lineHeight: 17 },
  agentName: { color: colors.accent, fontWeight: '600' },
  tapHint: { color: colors.textMuted, fontSize: 11, marginTop: 12, textAlign: 'right' },
  err: { color: colors.danger, fontSize: 13, marginTop: 4 },
  empty: { alignItems: 'center', paddingVertical: 32, gap: 10 },
  emptyIcon: { fontSize: 40 },
  emptyTitle: { color: colors.textPrimary, fontSize: 17, fontWeight: '700' },
  emptyText: { color: colors.textSecondary, fontSize: 13, lineHeight: 19, textAlign: 'center', paddingHorizontal: 8 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, justifyContent: 'center', marginTop: 8 },
  chip: { paddingHorizontal: 18, paddingVertical: 8, borderRadius: 18, backgroundColor: colors.surfaceElevated },
  chipText: { color: colors.textPrimary, fontSize: 14, fontWeight: '600', letterSpacing: 1 },
});
