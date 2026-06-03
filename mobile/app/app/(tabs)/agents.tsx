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

function formatTs(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString();
}

export default function AgentsScreen() {
  const router = useRouter();
  const { data, isLoading, isError, refetch, isFetching } = useDecisions({ limit: 25 });
  const queryClient = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);

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
          data.map((d) => (
            <Pressable
              key={d.decision_id}
              style={styles.card}
              onPress={() => router.push(`/trade/${d.ticker}` as never)}
            >
              <View style={styles.row}>
                <Text style={styles.ticker}>{d.ticker}</Text>
                <Text style={[styles.rating, { color: RATING_COLOR[d.rating] }]}>{d.rating}</Text>
              </View>
              <View style={[styles.row, { marginTop: 8 }]}>
                <Text style={styles.muted}>Entry ${d.entry_price ?? '—'}</Text>
                <Text style={styles.muted}>Stop ${d.stop_loss ?? '—'}</Text>
                <Text style={styles.muted}>PT ${d.price_target ?? '—'}</Text>
              </View>
              <Text style={[styles.muted, { marginTop: 6, fontSize: 11 }]}>
                {formatTs(d.timestamp_utc)} • {d.time_horizon ?? 'no horizon'}
              </Text>
            </Pressable>
          ))
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
  muted: { color: colors.textMuted, fontSize: 12 },
  err: { color: colors.danger, fontSize: 14 },
  empty: { padding: 24, backgroundColor: colors.surface, borderRadius: 12, gap: 6 },
});
