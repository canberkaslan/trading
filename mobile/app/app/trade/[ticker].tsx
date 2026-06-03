import { View, Text, StyleSheet, ScrollView, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useLocalSearchParams, useRouter } from 'expo-router';

import { useDecisions } from '@/api/hooks';
import { colors } from '@/theme/colors';

export default function TradeApproveScreen() {
  const { ticker } = useLocalSearchParams<{ ticker: string }>();
  const router = useRouter();
  const { data, isLoading } = useDecisions({ ticker, limit: 1 });

  const decision = data?.[0];

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView contentContainerStyle={{ padding: 24 }}>
        <Pressable onPress={() => router.back()} style={styles.back}>
          <Text style={styles.backText}>← Back</Text>
        </Pressable>

        <Text style={styles.title}>{ticker}</Text>

        {isLoading || !decision ? (
          <Text style={styles.muted}>Loading…</Text>
        ) : (
          <>
            <View style={styles.headlineCard}>
              <Text style={styles.headlineLabel}>Latest decision</Text>
              <Text style={styles.headlineValue}>{decision.rating}</Text>
              <View style={styles.row}>
                <Stat label="Entry"  value={`$${decision.entry_price ?? '—'}`} />
                <Stat label="Stop"   value={`$${decision.stop_loss ?? '—'}`} />
                <Stat label="Target" value={`$${decision.price_target ?? '—'}`} />
              </View>
              <Text style={styles.muted}>Horizon: {decision.time_horizon ?? '—'}</Text>
              <Text style={styles.muted}>Size: {(decision.suggested_size_pct * 100).toFixed(2)}% of portfolio</Text>
            </View>

            <View style={styles.actions}>
              <Pressable
                style={[styles.btn, styles.btnSecondary]}
                onPress={() => router.back()}
              >
                <Text style={styles.btnSecondaryText}>Reject</Text>
              </Pressable>
              <Pressable
                style={[styles.btn, styles.btnPrimary]}
                onPress={() => {
                  // TODO(5d): biometric -> POST /v1/orders/{order_id}/approve
                  router.back();
                }}
              >
                <Text style={styles.btnPrimaryText}>Approve (TBD)</Text>
              </Pressable>
            </View>

            <Text style={styles.section}>Portfolio Manager output</Text>
            <Text style={styles.body}>
              {decision.final_decision_text ?? '(no PM text)'}
            </Text>
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
  value: { color: colors.textPrimary, fontSize: 16, fontWeight: '600', marginTop: 2 },
});

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  back: { marginBottom: 12 },
  backText: { color: colors.accent, fontSize: 14 },
  title: { color: colors.textPrimary, fontSize: 32, fontWeight: '700', marginBottom: 16 },
  headlineCard: { backgroundColor: colors.surface, borderRadius: 12, padding: 16, gap: 12 },
  headlineLabel: { color: colors.textMuted, fontSize: 12 },
  headlineValue: { color: colors.textPrimary, fontSize: 24, fontWeight: '700' },
  row: { flexDirection: 'row', gap: 16, marginTop: 8 },
  muted: { color: colors.textMuted, fontSize: 12 },
  actions: { flexDirection: 'row', gap: 12, marginTop: 16 },
  btn: { flex: 1, padding: 14, borderRadius: 8, alignItems: 'center' },
  btnPrimary: { backgroundColor: colors.up },
  btnPrimaryText: { color: '#000', fontWeight: '700' },
  btnSecondary: { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.textMuted },
  btnSecondaryText: { color: colors.textPrimary, fontWeight: '600' },
  section: { color: colors.textPrimary, fontSize: 16, fontWeight: '600', marginTop: 24, marginBottom: 8 },
  body: { color: colors.textSecondary, fontSize: 13, lineHeight: 20 },
});
