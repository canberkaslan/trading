import { View, Text, StyleSheet, ScrollView, RefreshControl, Pressable, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useState, useCallback, useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'expo-router';

import { usePendingOrders, useOrders, useCancelOrder } from '@/api/hooks';
import type { OrderListItem } from '@/api/types';
import { getPermissionStatus, requestAndRegisterPush } from '@/notifications';
import { colors } from '@/theme/colors';
import { ErrorState } from '@/components/ErrorState';
import { EmptyState } from '@/components/EmptyState';
import { formatUsd } from '@/utils/format';
import {
  orderStatusMeta,
  fillSummary,
  formatOrderDate,
  isCancellable,
  rejectionReasonTr,
  type OrderTone,
} from '@/utils/orders';
import { MIN_TOUCH_TARGET, orderActionLabel } from '@/utils/a11y';

type Tab = 'pending' | 'history';

/** Ask at most once per app session, and only with a real order on screen. */
let pushPromptShown = false;

/**
 * Contextual push opt-in: the OS prompt lands when there is actually an order
 * waiting, so the ask is self-explanatory. A cold-start prompt gets denied, and
 * a denial is permanent — which would mean never being told about an approval
 * again.
 */
async function maybeAskForPush(): Promise<void> {
  if (pushPromptShown) return;
  pushPromptShown = true;
  if ((await getPermissionStatus()) !== 'undetermined') return;
  Alert.alert(
    'Bu emirden haberdar ol',
    'Bir emir onayını beklerken telefonuna bildirim gönderelim mi? Sadece onay bekleyen ve gerçekleşen emirler için.',
    [
      { text: 'Şimdi değil', style: 'cancel' },
      { text: 'Bildirim aç', onPress: () => void requestAndRegisterPush() },
    ],
  );
}

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

  const pendingCount = pending.data?.length ?? 0;
  useEffect(() => {
    if (pendingCount > 0) void maybeAskForPush();
  }, [pendingCount]);

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
                accessibilityRole="button"
                accessibilityLabel={orderActionLabel(o, 'review')}
                accessibilityHint={`Stop ${formatUsd(o.stop_loss)}, ${o.order_type}`}
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
          active.data.map((o) => <HistoryCard key={o.order_id} order={o} />)
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

/**
 * One broker-submitted order. Read-only except for the cancel action, which is
 * only rendered while the broker can still act on it (see `isCancellable`) and
 * is confirm-gated — a mis-tap here pulls a live order off the book.
 */
function HistoryCard({ order: o }: { order: OrderListItem }) {
  const cancel = useCancelOrder();
  const sideColor = o.side === 'BUY' ? colors.up : colors.down;
  const meta = orderStatusMeta(o.broker_status);
  const cancellable = isCancellable(o.broker_status, o.broker_order_id);
  const reasons = o.rejection_reasons ?? [];

  const onCancel = useCallback(() => {
    Alert.alert(
      'Emri iptal et',
      `${o.ticker} ${o.side} ${o.quantity} lot emri broker'dan geri çekilsin mi? ` +
        'Emir bu sırada dolarsa iptal edilemez.',
      [
        { text: 'Vazgeç', style: 'cancel' },
        {
          text: 'İptal et',
          style: 'destructive',
          onPress: () =>
            cancel.mutate(o.order_id, {
              onError: (e: unknown) =>
                Alert.alert(
                  'İptal edilemedi',
                  e instanceof Error ? e.message : 'Broker isteği reddetti. Emir hâlâ açık olabilir.',
                ),
            }),
        },
      ],
    );
  }, [cancel, o.order_id, o.quantity, o.side, o.ticker]);

  return (
    <View style={styles.card}>
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

      {reasons.length > 0 && (
        <View style={styles.reasons}>
          {reasons.map((r, i) => (
            <Text key={`${o.order_id}-r${i}`} style={styles.reason}>
              • {rejectionReasonTr(r)}
            </Text>
          ))}
        </View>
      )}

      {cancellable && (
        <Pressable
          style={styles.cancelBtn}
          onPress={onCancel}
          disabled={cancel.isPending}
          accessibilityRole="button"
          accessibilityLabel={orderActionLabel(o, 'cancel')}
          accessibilityHint="Onay sorulur"
          accessibilityState={{ disabled: cancel.isPending }}
        >
          <Text style={styles.cancelLabel}>
            {cancel.isPending ? 'İptal ediliyor…' : 'Emri iptal et'}
          </Text>
        </Pressable>
      )}
    </View>
  );
}

function SegmentButton({ label, active, onPress }: { label: string; active: boolean; onPress: () => void }) {
  return (
    <Pressable
      style={[styles.segBtn, active && styles.segBtnActive]}
      onPress={onPress}
      accessibilityRole="tab"
      accessibilityLabel={`${label} emirler`}
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
  segBtn: {
    flex: 1,
    paddingVertical: 10,
    borderRadius: 10,
    backgroundColor: colors.surface,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: MIN_TOUCH_TARGET,
  },
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
  reasons: { marginTop: 8, gap: 2 },
  reason: { color: colors.textSecondary, fontSize: 12 },
  cancelBtn: {
    marginTop: 12,
    minHeight: MIN_TOUCH_TARGET,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: colors.down,
    alignItems: 'center',
    justifyContent: 'center',
  },
  cancelLabel: { color: colors.down, fontSize: 14, fontWeight: '700' },
});
