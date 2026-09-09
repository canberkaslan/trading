import { View, Text, StyleSheet, ScrollView, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import { useLocalSearchParams, useRouter } from 'expo-router';

import { useMemo } from 'react';

import { useDecisions } from '@/api/hooks';
import { useTheme } from '@/theme/useTheme';
import { ratingChip, modelBadge } from '@/theme/rating';
import { formatUsd, formatPct } from '@/utils/format';
import {
  formatTokens,
  formatLatency,
  debateEntries,
  debateRoleLabel,
} from '@/utils/decision';
import { hitSlopFor } from '@/utils/a11y';
import { font, TABULAR } from '@/theme/type';


export default function TradeApproveScreen() {
  const { t } = useTranslation();
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);
  const { ticker } = useLocalSearchParams<{ ticker: string }>();
  const router = useRouter();
  const { data, isLoading } = useDecisions({ ticker, limit: 1 });

  const decision = data?.[0];
  const debate = debateEntries(decision?.debate_transcript);
  const totalTokens = decision?.reasoning?.reduce(
    (sum, r) => sum + (r.tokens_in ?? 0) + (r.tokens_out ?? 0),
    0,
  );

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView contentContainerStyle={{ padding: 24 }}>
        <Pressable
          onPress={() => router.back()}
          style={styles.back}
          hitSlop={hitSlopFor(18)}
          accessibilityRole="button"
          accessibilityLabel="Geri dön"
        >
          <Text style={styles.backText}>← Geri</Text>
        </Pressable>

        <Text style={styles.title}>{ticker}</Text>

        {isLoading || !decision ? (
          <Text style={styles.muted}>{isLoading ? 'Yükleniyor…' : 'Karar bulunamadı.'}</Text>
        ) : (
          <>
            <View style={styles.headlineCard}>
              <Text style={styles.headlineLabel}>Son karar</Text>
              {/* The rating used to render as plain body ink here, so the one
                  screen that shows the FULL reasoning was also the one that
                  dropped the buy/hold/sell encoding. Same chip as everywhere. */}
              <View style={styles.headlineRow}>
                <View style={[styles.ratingChip, ratingChip(theme, decision.rating)]}>
                  <Text style={[styles.headlineValue, { color: ratingChip(theme, decision.rating).color }]}>
                    {decision.rating}
                  </Text>
                </View>
              </View>
              <View style={styles.row}>
                <Stat label="Giriş" value={formatUsd(decision.entry_price)} />
                <Stat label="Stop" value={formatUsd(decision.stop_loss)} />
                <Stat label="Kâr al" value={formatUsd(decision.take_profit)} />
                <Stat label="Hedef" value={formatUsd(decision.price_target)} />
              </View>
              <Text style={styles.muted}>Vade: {decision.time_horizon ?? '—'}</Text>
              <Text style={styles.muted}>
                Boyut: {formatPct(decision.suggested_size_pct)} portföyün
              </Text>
            </View>

            <Text style={styles.note}>
              Bu sadece son karar detayıdır. Onay/red işlemi, emir bekleyen listeye
              düştüğünde Emirler sekmesinden yapılır.
            </Text>

            {decision.reasoning?.length ? (
              <>
                <View style={styles.sectionHead}>
                  <Text style={[styles.section, styles.sectionInline]}>Ajan analizleri</Text>
                  {totalTokens ? (
                    <Text style={styles.sectionMeta}>~{formatTokens(totalTokens)} token</Text>
                  ) : null}
                </View>
                {decision.reasoning.map((r, i) => {
                  const badge = modelBadge(theme, r.model);
                  return (
                    <View key={i} style={styles.agentCard}>
                      <View style={styles.agentHead}>
                        <Text style={styles.agentName}>{r.agent}</Text>
                        <Text style={[styles.badge, { color: badge.color, borderColor: badge.color }]}>
                          {badge.label}
                        </Text>
                      </View>
                      <Text style={styles.agentBody}>{r.summary}</Text>
                      <Text style={styles.agentMeta}>
                        {formatTokens(r.tokens_in)}↓ / {formatTokens(r.tokens_out)}↑ token ·{' '}
                        {formatLatency(r.latency_ms)}
                      </Text>
                    </View>
                  );
                })}
              </>
            ) : null}

            {debate.length ? (
              <>
                <Text style={styles.section}>Tartışma</Text>
                {debate.map((entry) => (
                  <View key={entry.role} style={styles.debateCard}>
                    <Text style={styles.debateRole}>{debateRoleLabel(entry.role)}</Text>
                    <Text style={styles.body}>{entry.text}</Text>
                  </View>
                ))}
              </>
            ) : null}

            <Text style={styles.section}>Portföy yöneticisi çıktısı</Text>
            <Text style={styles.body}>{decision.final_decision_text ?? '(PM metni yok)'}</Text>
          </>
        )}
        <Text style={styles.disclaimer}>{t('disclaimer.short')}</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  const statStyles = makeStatStyles(useTheme());
  return (
    <View style={statStyles.stat}>
      <Text style={statStyles.label}>{label}</Text>
      <Text style={statStyles.value}>{value}</Text>
    </View>
  );
}

type Palette = ReturnType<typeof useTheme>;

const makeStatStyles = (t: Palette) =>
  StyleSheet.create({
    stat: { flex: 1 },
    label: { color: t.textSecondary, fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.8 },
    value: { color: t.textPrimary, fontSize: 15, ...font(800), marginTop: 3, ...TABULAR },
  });

const makeStyles = (t: Palette) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.background },
    back: { marginBottom: 12 },
    backText: { color: t.textPrimary, fontSize: 14, ...font(800) },
    title: { color: t.textPrimary, fontSize: 32, ...font(800), marginBottom: 16 },
    // Ruled block rather than a filled card: 2px rule above, hairlines within.
    headlineCard: { borderTopWidth: 2, borderTopColor: t.divider, paddingTop: 14, gap: 10 },
    headlineLabel: { color: t.textSecondary, fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.8 },
    headlineRow: { flexDirection: 'row' },
    ratingChip: { paddingHorizontal: 10, paddingVertical: 4, borderWidth: 1, borderColor: 'transparent' },
    headlineValue: { fontSize: 20, ...font(800), letterSpacing: 0.5 },
    row: { flexDirection: 'row', gap: 12, marginTop: 8 },
    muted: { color: t.textSecondary, fontSize: 12 },
    note: { color: t.textSecondary, fontSize: 12, lineHeight: 18, marginTop: 16 },
    // The rule belongs to the ROW, not to the label: inside a row a Text sizes
    // to its content, so the underline stopped at the last letter while every
    // other section rule on the screen spanned the column.
    sectionHead: {
      flexDirection: 'row',
      alignItems: 'baseline',
      justifyContent: 'space-between',
      marginTop: 24,
      marginBottom: 8,
      borderBottomWidth: 2,
      borderBottomColor: t.divider,
      paddingBottom: 6,
    },
    section: {
      color: t.textPrimary,
      fontSize: 11,
      ...font(800),
      textTransform: 'uppercase',
      letterSpacing: 1,
      marginTop: 24,
      marginBottom: 8,
      borderBottomWidth: 2,
      borderBottomColor: t.divider,
      paddingBottom: 6,
    },
    sectionInline: { marginTop: 0, marginBottom: 0, borderBottomWidth: 0, paddingBottom: 0 },
    sectionMeta: { color: t.textSecondary, fontSize: 11 },
    agentCard: { paddingVertical: 12, gap: 6, borderBottomWidth: 1, borderBottomColor: t.divider },
    agentHead: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    agentName: { color: t.textPrimary, fontSize: 13, ...font(800) },
    badge: {
      fontSize: 10,
      ...font(800),
      borderWidth: 1,
      paddingHorizontal: 6,
      paddingVertical: 1,
      overflow: 'hidden',
    },
    agentBody: { color: t.textSecondary, fontSize: 12, lineHeight: 17 },
    agentMeta: { color: t.textSecondary, fontSize: 10 },
    debateCard: { paddingVertical: 12, gap: 4, borderBottomWidth: 1, borderBottomColor: t.divider },
    debateRole: { color: t.textPrimary, fontSize: 12, ...font(800) },
    body: { color: t.textSecondary, fontSize: 13, lineHeight: 20 },
    disclaimer: { color: t.textSecondary, fontSize: 11, paddingVertical: 20, textAlign: 'center' },
  });
