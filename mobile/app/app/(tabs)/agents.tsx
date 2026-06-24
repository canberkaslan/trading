import { View, Text, StyleSheet, ScrollView, RefreshControl, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useState, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'expo-router';

import { useDecisions } from '@/api/hooks';
import { colors } from '@/theme/colors';
import type { Rating } from '@/api/types';

const RATING_COLOR: Record<Rating, string> = {
  Buy: colors.up,
  Overweight: '#86efac',
  Hold: colors.textSecondary,
  Underweight: '#fda4af',
  Sell: colors.down,
};

function modelBadge(model: string): { label: string; color: string } {
  if (model.includes('opus')) return { label: 'Opus', color: colors.accent };
  if (model.includes('sonnet')) return { label: 'Sonnet', color: '#3b82f6' };
  if (model.includes('haiku')) return { label: 'Haiku', color: colors.textMuted };
  return { label: model.slice(0, 8), color: colors.textMuted };
}

function formatTs(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString();
}

export default function AgentsScreen() {
  const router = useRouter();
  const { data, isLoading, isError, isFetching } = useDecisions({ limit: 25 });
  const queryClient = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const toggle = (id: string) => setExpanded((e) => ({ ...e, [id]: !e[id] }));

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await queryClient.invalidateQueries({ queryKey: ['agents'] });
    setRefreshing(false);
  }, [queryClient]);

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#fff" />}
      >
        <Text style={styles.heading}>Decisions</Text>
        <Text style={styles.subheading}>
          {isFetching ? 'Refreshing…' : 'Latest LLM agent calls (DB-backed)'}
        </Text>

        {isLoading ? (
          <Text style={styles.muted}>Loading…</Text>
        ) : isError ? (
          <Text style={styles.err}>Backend unreachable</Text>
        ) : !data || data.length === 0 ? (
          <View style={styles.empty}>
            <Text style={styles.muted}>No decisions yet.</Text>
            <Text style={styles.muted}>
              Run: ./.venv/bin/python -m scripts.trade --ticker AAPL --use-cached
            </Text>
          </View>
        ) : (
          data.map((d) => {
            const isOpen = !!expanded[d.decision_id];
            return (
              <Pressable key={d.decision_id} style={styles.card} onPress={() => toggle(d.decision_id)}>
                <View style={styles.row}>
                  <Text style={styles.ticker}>{d.ticker}</Text>
                  <Text style={[styles.rating, { color: RATING_COLOR[d.rating] }]}>{d.rating}</Text>
                </View>
                <View style={[styles.row, { marginTop: 8 }]}>
                  <Text style={styles.muted}>Entry ${d.entry_price ?? '—'}</Text>
                  <Text style={styles.muted}>Stop ${d.stop_loss ?? '—'}</Text>
                  <Text style={styles.muted}>PT ${d.price_target ?? '—'}</Text>
                </View>
                <View style={[styles.row, { marginTop: 6 }]}>
                  <Text style={[styles.muted, { fontSize: 11 }]}>
                    {formatTs(d.timestamp_utc)} • {d.time_horizon ?? 'no horizon'}
                  </Text>
                  <Text style={styles.expandHint}>
                    {d.reasoning?.length ? `${isOpen ? '▲' : '▼'} ${d.reasoning.length} ajan` : ''}
                  </Text>
                </View>

                {isOpen && d.reasoning?.length ? (
                  <View style={styles.reasoningBox}>
                    {d.reasoning.map((r, i) => {
                      const badge = modelBadge(r.model);
                      return (
                        <View key={i} style={styles.reasoning}>
                          <View style={styles.reasoningHead}>
                            <Text style={styles.agentName}>{r.agent}</Text>
                            <Text style={[styles.badge, { color: badge.color, borderColor: badge.color }]}>
                              {badge.label}
                            </Text>
                          </View>
                          <Text style={styles.reasoningText}>{r.summary}</Text>
                        </View>
                      );
                    })}
                    <Pressable onPress={() => router.push(`/trade/${d.ticker}` as never)}>
                      <Text style={styles.detailLink}>Tam detay →</Text>
                    </Pressable>
                  </View>
                ) : null}
              </Pressable>
            );
          })
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  scroll: { padding: 24, gap: 12 },
  heading: { color: colors.textPrimary, fontSize: 28, fontWeight: '700' },
  subheading: { color: colors.textSecondary, fontSize: 13, marginBottom: 16 },
  card: { padding: 16, backgroundColor: colors.surface, borderRadius: 12 },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  ticker: { color: colors.textPrimary, fontSize: 18, fontWeight: '600' },
  rating: { fontSize: 15, fontWeight: '700' },
  expandHint: { color: colors.accent, fontSize: 11, fontWeight: '600' },
  reasoningBox: { marginTop: 12, gap: 12, borderTopWidth: 1, borderTopColor: colors.surfaceElevated, paddingTop: 12 },
  reasoning: { gap: 4 },
  reasoningHead: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  agentName: { color: colors.textPrimary, fontSize: 13, fontWeight: '600' },
  badge: { fontSize: 10, fontWeight: '700', borderWidth: 1, borderRadius: 6, paddingHorizontal: 6, paddingVertical: 1, overflow: 'hidden' },
  reasoningText: { color: colors.textSecondary, fontSize: 12, lineHeight: 17 },
  detailLink: { color: colors.accent, fontSize: 13, fontWeight: '600', marginTop: 4 },
  muted: { color: colors.textMuted, fontSize: 12 },
  err: { color: colors.danger, fontSize: 14 },
  empty: { padding: 24, backgroundColor: colors.surface, borderRadius: 12, gap: 6 },
});
