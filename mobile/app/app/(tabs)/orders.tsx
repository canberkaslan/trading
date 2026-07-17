import { View, Text, StyleSheet, ScrollView, RefreshControl, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useState, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'expo-router';

import { usePendingOrders } from '@/api/hooks';
import { colors } from '@/theme/colors';
import { ErrorState } from '@/components/ErrorState';
import { EmptyState } from '@/components/EmptyState';
import { formatUsd } from '@/utils/format';

export default function OrdersScreen() {
  const router = useRouter();
  const { data, isLoading, isError, isFetching, refetch } = usePendingOrders();
  const qc = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await qc.invalidateQueries({ queryKey: ['orders'] });
    setRefreshing(false);
  }, [qc]);

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#fff" />}
      >
        <Text style={styles.heading}>Pending Approval</Text>
        <Text style={styles.subheading}>
          {isFetching ? 'Refreshing…' : 'Orders held by --hold awaiting your decision'}
        </Text>

        {isLoading ? (
          <Text style={styles.muted}>Loading…</Text>
        ) : isError ? (
          <ErrorState onRetry={refetch} />
        ) : !data || data.length === 0 ? (
          <EmptyState title="Onay bekleyen emir yok" hint="Günlük çalışma bir emri onaya düşürdüğünde burada görünür." />
        ) : (
          data.map((o) => {
            const sideColor = o.side === 'BUY' ? colors.up : colors.down;
            return (
              <Pressable
                key={o.order_id}
                style={styles.card}
                onPress={() => router.push(`/approve/${o.order_id}` as never)}
              >
                <View style={styles.row}>
                  <Text style={styles.ticker}>{o.ticker}</Text>
                  <Text style={[styles.side, { color: sideColor }]}>{o.side} {o.quantity}</Text>
                </View>
                <Text style={styles.muted}>Stop {formatUsd(o.stop_loss)} • {o.order_type}</Text>
                <Text style={styles.tapHint}>Tap to review &amp; approve →</Text>
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
  side: { fontSize: 15, fontWeight: '700' },
  muted: { color: colors.textMuted, fontSize: 12, marginTop: 6 },
  tapHint: { color: colors.accent, fontSize: 12, marginTop: 8 },
});
