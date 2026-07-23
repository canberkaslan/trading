import { View, Text, StyleSheet, ScrollView, RefreshControl, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useState, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'expo-router';

import { usePendingOrders, useOrders } from '@/api/hooks';
import { colors } from '@/theme/colors';
import { ErrorState } from '@/components/ErrorState';
import { EmptyState } from '@/components/EmptyState';
import { formatUsd } from '@/utils/format';
import { orderStatusMeta, fillSummary, formatOrderDate, type OrderTone } from '@/utils/orders';

type Tab = 'pending' | 'history';

const TONE_COLOR: Record<OrderTone, string> = {
  up: colors.up,
  down: colors.down,
  warning: colors.warning,
  muted: colors.textMuted,
};

export default function OrdersScreen() {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>('pending');
  const pending = usePendingOrders();
  const history = useOrders();
  const active = tab === 'pending' ? pending : history;
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
        <Text style={styles.heading}>Emirler</Text>

        <View style={styles.segment}>
          <SegmentButton label="Onay Bekleyen" active={tab === 'pending'} onPress={() => setTab('pending')} />
          <SegmentButton label="Geçmiş" active={tab === 'history'} onPress={() => setTab('history')} />
        </View>

        <Text style={styles.subheading}>
          {active.isFetching
            ? 'Yenileniyor…'
            : tab === 'pending'
              ? '--hold ile tutulan, kararınızı bekleyen emirler'
              : "Broker'a gönderilen son emirler ve durumları"}
        </Text>

        {active.isLoading ? (
          <Text style={styles.muted}>Yükleniyor…</Text>
        ) : active.isError ? (
          <ErrorState onRetry={active.refetch} />
        ) : !active.data || active.data.length === 0 ? (
          tab === 'pending' ? (
            <EmptyState title="Onay bekleyen emir yok" hint="Günlük çalışma bir emri onaya düşürdüğünde burada görünür." />
          ) : (
            <EmptyState title="Henüz emir geçmişi yok" hint="Broker'a bir emir gönderildiğinde burada listelenir." />
          )
        ) : tab === 'pending' ? (
          active.data.map((o) => {
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
                <Text style={styles.tapHint}>İncele &amp; onayla →</Text>
              </Pressable>
            );
          })
        ) : (
          active.data.map((o) => {
            const sideColor = o.side === 'BUY' ? colors.up : colors.down;
            const meta = orderStatusMeta(o.broker_status);
            return (
              <View key={o.order_id} style={styles.card}>
                <View style={styles.row}>
                  <Text style={styles.ticker}>{o.ticker}</Text>
                  <Text style={[styles.side, { color: sideColor }]}>{o.side} {o.quantity}</Text>
                </View>
                <View style={styles.row}>
                  <Text style={[styles.status, { color: TONE_COLOR[meta.tone] }]}>{meta.label}</Text>
                  <Text style={styles.muted}>{fillSummary(o.filled_qty, o.quantity)}</Text>
                </View>
                <Text style={styles.muted}>
                  {o.avg_fill_price != null ? `Ort. ${formatUsd(o.avg_fill_price)} • ` : ''}
                  {formatOrderDate(o.submitted_at_utc)}
                </Text>
              </View>
            );
          })
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

function SegmentButton({ label, active, onPress }: { label: string; active: boolean; onPress: () => void }) {
  return (
    <Pressable
      style={[styles.segBtn, active && styles.segBtnActive]}
      onPress={onPress}
      accessibilityRole="button"
      accessibilityState={{ selected: active }}
    >
      <Text style={[styles.segLabel, active && styles.segLabelActive]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  scroll: { padding: 24, gap: 12 },
  heading: { color: colors.textPrimary, fontSize: 28, fontWeight: '700' },
  subheading: { color: colors.textSecondary, fontSize: 13, marginBottom: 16 },
  segment: { flexDirection: 'row', gap: 8, marginTop: 12 },
  segBtn: { flex: 1, paddingVertical: 10, borderRadius: 10, backgroundColor: colors.surface, alignItems: 'center' },
  segBtnActive: { backgroundColor: colors.surfaceElevated },
  segLabel: { color: colors.textMuted, fontSize: 14, fontWeight: '600' },
  segLabelActive: { color: colors.textPrimary },
  card: { padding: 16, backgroundColor: colors.surface, borderRadius: 12 },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  ticker: { color: colors.textPrimary, fontSize: 18, fontWeight: '600' },
  side: { fontSize: 15, fontWeight: '700' },
  status: { fontSize: 14, fontWeight: '700', marginTop: 6 },
  muted: { color: colors.textMuted, fontSize: 12, marginTop: 6 },
  tapHint: { color: colors.accent, fontSize: 12, marginTop: 8 },
});
