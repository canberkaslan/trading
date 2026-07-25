import { View, Text, StyleSheet, ScrollView, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useLocalSearchParams, useRouter } from 'expo-router';

import { useDecisions } from '@/api/hooks';
import { colors } from '@/theme/colors';
import { formatUsd, formatPct } from '@/utils/format';
import {
  formatTokens,
  formatLatency,
  debateEntries,
  debateRoleLabel,
} from '@/utils/decision';

function modelBadge(model: string): { label: string; color: string } {
  if (model.includes('opus')) return { label: 'Opus', color: colors.accent };
  if (model.includes('sonnet')) return { label: 'Sonnet', color: '#3b82f6' };
  if (model.includes('haiku')) return { label: 'Haiku', color: colors.textMuted };
  return { label: model.slice(0, 8) || 'model', color: colors.textMuted };
}

export default function TradeApproveScreen() {
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
        <Pressable onPress={() => router.back()} style={styles.back}>
          <Text style={styles.backText}>← Geri</Text>
        </Pressable>

        <Text style={styles.title}>{ticker}</Text>

        {isLoading || !decision ? (
          <Text style={styles.muted}>{isLoading ? 'Yükleniyor…' : 'Karar bulunamadı.'}</Text>
        ) : (
          <>
            <View style={styles.headlineCard}>
              <Text style={styles.headlineLabel}>Son karar</Text>
              <Text style={styles.headlineValue}>{decision.rating}</Text>
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
                  const badge = modelBadge(r.model);
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
      </ScrollView>
    </SafeAreaView>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <View style={statStyles.stat}>
      <Text style={statStyles.label}>{label}</Text>
      <Text style={statStyles.value}>{value}</Text>
    </View>
  );
}

const statStyles = StyleSheet.create({
  stat: { flex: 1 },
  label: { color: colors.textMuted, fontSize: 11 },
  value: { color: colors.textPrimary, fontSize: 15, fontWeight: '600', marginTop: 2 },
});

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  back: { marginBottom: 12 },
  backText: { color: colors.accent, fontSize: 14 },
  title: { color: colors.textPrimary, fontSize: 32, fontWeight: '700', marginBottom: 16 },
  headlineCard: { backgroundColor: colors.surface, borderRadius: 12, padding: 16, gap: 12 },
  headlineLabel: { color: colors.textMuted, fontSize: 12 },
  headlineValue: { color: colors.textPrimary, fontSize: 24, fontWeight: '700' },
  row: { flexDirection: 'row', gap: 12, marginTop: 8 },
  muted: { color: colors.textMuted, fontSize: 12 },
  note: { color: colors.textMuted, fontSize: 12, lineHeight: 18, marginTop: 16 },
  sectionHead: {
    flexDirection: 'row',
    alignItems: 'baseline',
    justifyContent: 'space-between',
    marginTop: 24,
    marginBottom: 8,
  },
  section: { color: colors.textPrimary, fontSize: 16, fontWeight: '600', marginTop: 24, marginBottom: 8 },
  sectionInline: { marginTop: 0, marginBottom: 0 },
  sectionMeta: { color: colors.textMuted, fontSize: 11 },
  agentCard: { backgroundColor: colors.surface, borderRadius: 10, padding: 12, gap: 6, marginBottom: 8 },
  agentHead: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  agentName: { color: colors.textPrimary, fontSize: 13, fontWeight: '600' },
  badge: {
    fontSize: 10,
    fontWeight: '700',
    borderWidth: 1,
    borderRadius: 6,
    paddingHorizontal: 6,
    paddingVertical: 1,
    overflow: 'hidden',
  },
  agentBody: { color: colors.textSecondary, fontSize: 12, lineHeight: 17 },
  agentMeta: { color: colors.textMuted, fontSize: 10 },
  debateCard: { backgroundColor: colors.surface, borderRadius: 10, padding: 12, gap: 4, marginBottom: 8 },
  debateRole: { color: colors.textPrimary, fontSize: 12, fontWeight: '700' },
  body: { color: colors.textSecondary, fontSize: 13, lineHeight: 20 },
});
