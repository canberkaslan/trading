import { useState, useEffect, useRef, useMemo } from 'react';
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
import { useTranslation } from 'react-i18next';

import { useStartAnalysis, useAnalysisJob } from '@/api/hooks';
import { useTheme } from '@/theme/useTheme';
import { ratingChip } from '@/theme/rating';
import { MIN_TOUCH_TARGET } from '@/utils/a11y';
import { font, TABULAR } from '@/theme/type';

const STATUS_LABEL: Record<string, string> = {
  queued: 'Sıraya alındı…',
  running: 'Ajanlar tartışıyor… (~5-10 dk)',
  done: 'Hazır',
  error: 'Hata',
};

export default function AskScreen() {
  const { t } = useTranslation();
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);
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
            placeholderTextColor={theme.textSecondary}
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
            accessibilityRole="button"
            accessibilityLabel="Girilen sembolü analiz et"
            accessibilityState={{ disabled: busy, busy }}
          >
            {busy ? (
              <ActivityIndicator color={theme.background} />
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
                <Pressable
                  key={t}
                  style={styles.chip}
                  onPress={() => runAnalysis(t)}
                  accessibilityRole="button"
                  accessibilityLabel={`${t} analiz et`}
                >
                  <Text style={styles.chipText}>{t}</Text>
                </Pressable>
              ))}
            </View>
          </View>
        ) : null}

        {job && !decision ? (
          <View style={styles.statusCard}>
            {busy ? <ActivityIndicator color={theme.textPrimary} /> : null}
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
            accessibilityRole="button"
            accessibilityLabel={`${decision.ticker} kararının tam detayı`}
          >
            <View style={styles.row}>
              <Text style={styles.ticker}>{decision.ticker}</Text>
              <View style={[styles.ratingChip, ratingChip(theme, decision.rating)]}>
                <Text style={[styles.rating, { color: ratingChip(theme, decision.rating).color }]}>
                  {decision.rating}
                </Text>
              </View>
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
        <Text style={styles.disclaimer}>{t('disclaimer.short')}</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

function Metric({ label, value }: { label: string; value: number | null }) {
  const styles = makeStyles(useTheme());
  return (
    <View style={styles.metric}>
      <Text style={styles.metricLabel}>{label}</Text>
      <Text style={styles.metricValue}>{value != null ? `$${value}` : '—'}</Text>
    </View>
  );
}

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.background },
    scroll: { paddingHorizontal: 16, paddingTop: 20, paddingBottom: 24, gap: 0 },
    heading: { color: t.textPrimary, fontSize: 24, ...font(800) },
    subheading: { color: t.textSecondary, fontSize: 13, marginBottom: 16 },
    // Square field + square ink button, sharing one baseline height.
    inputRow: { flexDirection: 'row', marginTop: 4 },
    input: {
      flex: 1,
      backgroundColor: t.surfaceElevated,
      color: t.textPrimary,
      borderWidth: 1,
      borderColor: t.textPrimary,
      paddingHorizontal: 14,
      paddingVertical: 13,
      fontSize: 18,
      ...font(800),
      letterSpacing: 2,
    },
    btn: {
      backgroundColor: t.textPrimary,
      paddingHorizontal: 22,
      justifyContent: 'center',
      alignItems: 'center',
      minWidth: 96,
      minHeight: MIN_TOUCH_TARGET,
      marginLeft: -1,
    },
    btnDisabled: { opacity: 0.45 },
    btnText: { color: t.background, fontSize: 15, ...font(800), letterSpacing: 0.5 },
    statusCard: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 12,
      paddingVertical: 14,
      borderBottomWidth: 1,
      borderBottomColor: t.divider,
    },
    statusText: { color: t.textSecondary, fontSize: 14, flexShrink: 1 },
    // A ruled block, not a floating card: 2px above, hairline rows within.
    card: { marginTop: 20, paddingTop: 16, borderTopWidth: 2, borderTopColor: t.divider },
    row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
    ticker: { color: t.textPrimary, fontSize: 22, ...font(800) },
    ratingChip: { paddingHorizontal: 8, paddingVertical: 3, borderWidth: 1, borderColor: 'transparent' },
    rating: { fontSize: 13, ...font(800), letterSpacing: 0.6 },
    metric: { alignItems: 'flex-start' },
    metricLabel: { color: t.textSecondary, fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.8 },
    metricValue: { color: t.textPrimary, fontSize: 16, ...font(800), marginTop: 3, ...TABULAR },
    horizon: { color: t.textSecondary, fontSize: 12, marginTop: 10 },
    rationale: { color: t.textSecondary, fontSize: 13, lineHeight: 19, marginTop: 12 },
    agentsBox: { marginTop: 14, gap: 6, borderTopWidth: 1, borderTopColor: t.divider, paddingTop: 12 },
    agentsTitle: { color: t.textSecondary, fontSize: 10, textTransform: 'uppercase', letterSpacing: 1, ...font(800) },
    agentLine: { color: t.textSecondary, fontSize: 12, lineHeight: 17 },
    agentName: { color: t.textPrimary, ...font(800) },
    tapHint: { color: t.accent700 ?? t.accent, fontSize: 12, marginTop: 12, textAlign: 'right', ...font(600) },
    err: { color: t.accent700 ?? t.danger, fontSize: 13, marginTop: 4 },
    empty: { alignItems: 'center', paddingVertical: 32, gap: 10 },
    emptyIcon: { fontSize: 40 },
    emptyTitle: { color: t.textPrimary, fontSize: 17, ...font(800) },
    emptyText: { color: t.textSecondary, fontSize: 13, lineHeight: 19, textAlign: 'center', paddingHorizontal: 8 },
    chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, justifyContent: 'center', marginTop: 8 },
    chip: {
      paddingHorizontal: 18,
      paddingVertical: 8,
      borderWidth: 1,
      borderColor: t.textPrimary,
      minHeight: MIN_TOUCH_TARGET,
      alignItems: 'center',
      justifyContent: 'center',
    },
    chipText: { color: t.textPrimary, fontSize: 14, ...font(800), letterSpacing: 1 },
    disclaimer: { color: t.textSecondary, fontSize: 11, paddingVertical: 20, textAlign: 'center' },
  });
