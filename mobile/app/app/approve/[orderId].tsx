import { View, Text, StyleSheet, ScrollView, Pressable, Alert, ActivityIndicator } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useState } from 'react';

import { useApproveOrder, usePendingOrders, useRejectOrder } from '@/api/hooks';
import { api } from '@/api/endpoints';
import { colors } from '@/theme/colors';
import { authenticate } from '@/auth/biometric';
import { formatUsd } from '@/utils/format';
import { MIN_TOUCH_TARGET, hitSlopFor, orderActionLabel } from '@/utils/a11y';
import type { AgentDecision, OrderListItem } from '@/api/types';

export default function ApproveOrderScreen() {
  const { orderId } = useLocalSearchParams<{ orderId: string }>();
  const router = useRouter();
  const { data: orders, isLoading: ordersLoading } = usePendingOrders();
  const approve = useApproveOrder();
  const reject = useRejectOrder();
  const [decision, setDecision] = useState<AgentDecision | null>(null);
  const [decisionError, setDecisionError] = useState<string | null>(null);

  const order: OrderListItem | undefined = orders?.find((o) => o.order_id === orderId);

  // Fetch the underlying decision for full PM reasoning
  useEffect(() => {
    if (!order) return;
    let cancelled = false;
    api
      .getDecision(order.decision_id)
      .then((d) => { if (!cancelled) setDecision(d); })
      .catch((e) => { if (!cancelled) setDecisionError(String(e)); });
    return () => { cancelled = true; };
  }, [order]);

  if (!orderId) return null;

  // Push deep-links open this screen before the pending list has loaded; showing
  // "not in pending list" during that race falsely tells the user the order is gone.
  if (!order && ordersLoading) {
    return (
      <SafeAreaView style={styles.container} edges={['top']}>
        <View style={styles.center}>
          <ActivityIndicator color={colors.accent} />
          <Text style={styles.muted}>Emir yükleniyor…</Text>
        </View>
      </SafeAreaView>
    );
  }

  if (!order) {
    return (
      <SafeAreaView style={styles.container} edges={['top']}>
        <View style={styles.center}>
          <Text style={styles.muted}>Bu emir artık bekleyen listesinde değil.</Text>
          <Pressable
            onPress={() => router.back()}
            style={[styles.btnCompact, styles.btnSecondary]}
            accessibilityRole="button"
            accessibilityLabel="Geri dön"
          >
            <Text style={styles.btnSecondaryText}>Geri</Text>
          </Pressable>
        </View>
      </SafeAreaView>
    );
  }

  const handleApprove = async () => {
    const { success, mode } = await authenticate(
      `${order.side} ${order.quantity} ${order.ticker} onayla`,
    );
    if (!success) {
      const msg =
        mode === 'none'
          ? 'Cihazınızda ekran kilidi (Face/Touch ID veya şifre) tanımlı değil. Emir onayı için lütfen bir cihaz kilidi kurun.'
          : 'Kimlik doğrulama olmadan emir onaylanamaz.';
      Alert.alert('Doğrulama gerekli', msg);
      return;
    }
    try {
      const result = await approve.mutateAsync(order.order_id);
      Alert.alert('Emir gönderildi', `Broker emri: ${result.broker_order_id}\nDurum: ${result.status}`);
      router.back();
    } catch (e) {
      // 422 from backend means guards refused (stale, no_tp_headroom, too_close_to_stop)
      const msg = (e as Error & { response?: Response }).response
        ? `Risk guard reddetti. Sunucu yanıtına bakın.\n${String(e)}`
        : `Gönderilemedi: ${String(e)}`;
      Alert.alert('Reddedildi', msg);
    }
  };

  const doReject = async () => {
    try {
      await reject.mutateAsync(order.order_id);
      router.back();
    } catch (e) {
      Alert.alert('Reddedilemedi', String(e));
    }
  };

  // Rejecting drops the order for good — the daily run won't re-propose it today.
  const handleReject = () => {
    Alert.alert(
      'Emri reddet?',
      `${order.side} ${order.quantity} ${order.ticker} emri gönderilmeyecek.`,
      [
        { text: 'Vazgeç', style: 'cancel' },
        { text: 'Reddet', style: 'destructive', onPress: () => void doReject() },
      ],
    );
  };

  const submitting = approve.isPending;

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

        <Text style={styles.title}>{order.ticker}</Text>
        <Text style={styles.subtitle}>{order.side} {order.quantity} lot — {order.order_type}</Text>

        <View style={styles.headlineCard}>
          <View style={styles.row}>
            <Stat label="Adet" value={String(order.quantity)} />
            <Stat label="Stop" value={formatUsd(order.stop_loss)} />
            <Stat label="Tür" value={order.order_type} />
          </View>
          {decision && (
            <View style={[styles.row, { marginTop: 12 }]}>
              <Stat label="Puan" value={decision.rating} />
              <Stat label="Giriş" value={formatUsd(decision.entry_price)} />
              <Stat label="Hedef" value={formatUsd(decision.price_target)} />
            </View>
          )}
          {decision?.time_horizon && (
            <Text style={styles.muted}>Vade: {decision.time_horizon}</Text>
          )}
        </View>

        <View style={styles.actions}>
          <Pressable
            disabled={submitting}
            style={[styles.btn, styles.btnSecondary, submitting && { opacity: 0.5 }]}
            onPress={handleReject}
            accessibilityRole="button"
            accessibilityLabel={orderActionLabel(order, 'reject')}
            accessibilityState={{ disabled: submitting }}
          >
            <Text style={styles.btnSecondaryText}>Reddet</Text>
          </Pressable>
          <Pressable
            disabled={submitting}
            style={[styles.btn, styles.btnPrimary, submitting && { opacity: 0.5 }]}
            onPress={handleApprove}
            accessibilityRole="button"
            accessibilityLabel={orderActionLabel(order, 'approve')}
            accessibilityHint="Cihaz kilidi ile doğrulama ister"
            accessibilityState={{ disabled: submitting, busy: submitting }}
          >
            {submitting ? <ActivityIndicator color="#000" /> : (
              <Text style={styles.btnPrimaryText}>Doğrula ve Onayla</Text>
            )}
          </Pressable>
        </View>

        <Text style={styles.section}>Portföy Yöneticisi gerekçesi</Text>
        {decisionError ? (
          <Text style={styles.err}>{decisionError}</Text>
        ) : decision ? (
          <Text style={styles.body}>{decision.final_decision_text ?? 'Gerekçe metni yok.'}</Text>
        ) : (
          <Text style={styles.muted}>Gerekçe yükleniyor…</Text>
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
  value: { color: colors.textPrimary, fontSize: 16, fontWeight: '600', marginTop: 2 },
});

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24, gap: 16 },
  back: { marginBottom: 12 },
  backText: { color: colors.accent, fontSize: 14 },
  title: { color: colors.textPrimary, fontSize: 32, fontWeight: '700' },
  subtitle: { color: colors.textSecondary, fontSize: 14, marginTop: 4, marginBottom: 16 },
  headlineCard: { backgroundColor: colors.surface, borderRadius: 12, padding: 16, gap: 12 },
  row: { flexDirection: 'row', gap: 16 },
  muted: { color: colors.textMuted, fontSize: 12, marginTop: 4 },
  err: { color: colors.danger, fontSize: 13 },
  actions: { flexDirection: 'row', gap: 12, marginTop: 16 },
  btn: {
    flex: 1,
    padding: 14,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: MIN_TOUCH_TARGET,
  },
  btnCompact: {
    paddingHorizontal: 24,
    justifyContent: 'center',
    alignItems: 'center',
    borderRadius: 8,
    minHeight: MIN_TOUCH_TARGET,
  },
  btnPrimary: { backgroundColor: colors.up },
  btnPrimaryText: { color: '#000', fontWeight: '700' },
  btnSecondary: { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.textMuted },
  btnSecondaryText: { color: colors.textPrimary, fontWeight: '600' },
  section: { color: colors.textPrimary, fontSize: 16, fontWeight: '600', marginTop: 24, marginBottom: 8 },
  body: { color: colors.textSecondary, fontSize: 13, lineHeight: 20 },
});
