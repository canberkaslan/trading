import { View, Text, StyleSheet, Pressable, Alert, ScrollView } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useState } from 'react';

import { api } from '@/api/endpoints';
import { useKillSwitch, useSetKillSwitch, useHealth, useDecisions, useEval } from '@/api/hooks';
import { setupNotifications } from '@/notifications';
import { colors } from '@/theme/colors';
import type { KillSwitchState } from '@/api/types';

const KILL_STATES: { state: KillSwitchState; label: string; desc: string; color: string }[] = [
  { state: 'RUN', label: 'RUN', desc: 'Normal işlem', color: colors.up },
  { state: 'PAUSE_NEW', label: 'PAUSE', desc: 'Yeni giriş yok, mevcut yönetilir', color: colors.warning },
  { state: 'FLATTEN_ALL', label: 'FLATTEN', desc: 'Tüm pozisyonları kapat', color: colors.down },
];

function EvalStat({ label, value, gate }: { label: string; value: string; gate: string }) {
  return (
    <View style={styles.evalStat}>
      <Text style={styles.evalStatLabel}>{label}</Text>
      <Text style={styles.evalStatValue}>{value}</Text>
      <Text style={styles.evalStatGate}>{gate}</Text>
    </View>
  );
}

function GateRow({ name, passed, detail }: { name: string; passed: boolean | null; detail: string }) {
  const icon = passed === null ? '○' : passed ? '✓' : '✗';
  const color = passed === null ? colors.textMuted : passed ? colors.up : colors.down;
  return (
    <View style={styles.gateRow}>
      <Text style={[styles.gateIcon, { color }]}>{icon}</Text>
      <Text style={styles.gateName}>{name}</Text>
      <Text style={styles.gateDetail}>{detail}</Text>
    </View>
  );
}

export default function SettingsScreen() {
  const [pushBusy, setPushBusy] = useState(false);
  const { data: ks } = useKillSwitch();
  const setKs = useSetKillSwitch();
  const { data: health, isError: healthError } = useHealth();
  const { data: decisions } = useDecisions({ limit: 1 });
  const { data: evalData, isLoading: evalLoading } = useEval('1M');

  const VERDICT_COLOR: Record<string, string> = {
    GO: colors.up,
    'NO-GO': colors.down,
    'TOO EARLY': colors.warning,
  };

  const applyKill = (state: KillSwitchState) => {
    if (state === ks?.state) return;
    const go = () => setKs.mutate(state);
    if (state === 'FLATTEN_ALL') {
      Alert.alert(
        'Tüm pozisyonları kapat?',
        'FLATTEN_ALL tüm açık pozisyonları piyasa fiyatından kapatır. Emin misin?',
        [
          { text: 'Vazgeç', style: 'cancel' },
          { text: 'FLATTEN', style: 'destructive', onPress: go },
        ],
      );
    } else {
      go();
    }
  };

  const lastRun = decisions?.[0]?.timestamp_utc
    ? new Date(decisions[0].timestamp_utc).toLocaleString()
    : '—';

  const handleRegisterPush = async () => {
    setPushBusy(true);
    try {
      const token = await setupNotifications();
      if (token) {
        Alert.alert('Push registered', `Token: ${token.slice(0, 28)}…`);
      } else {
        Alert.alert(
          'No token',
          'Permission denied, or notifications only work on physical devices.',
        );
      }
    } catch (e) {
      Alert.alert('Failed', String(e));
    } finally {
      setPushBusy(false);
    }
  };

  const handleTestPush = async () => {
    try {
      const result = await api.testNotification();
      Alert.alert('Sent', `${result.sent} device(s) — check your notifications.`);
    } catch (e) {
      Alert.alert('Failed', String(e));
    }
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView contentContainerStyle={{ padding: 16 }}>
        <Text style={styles.heading}>Settings</Text>

        <Text style={styles.subheading}>Eval scorecard</Text>
        <View style={styles.evalCard}>
          {evalLoading ? (
            <Text style={styles.muted}>hesaplanıyor…</Text>
          ) : !evalData ? (
            <Text style={styles.muted}>Yeterli geçmiş yok — eval yeni başladı.</Text>
          ) : (
            <>
              <View style={styles.healthRow}>
                <Text style={styles.label}>Karar</Text>
                <View style={styles.verdictWrap}>
                  <Text style={[styles.verdict, { color: VERDICT_COLOR[evalData.verdict] ?? colors.textMuted }]}>
                    {evalData.verdict}
                  </Text>
                  {evalData.verdict === 'TOO EARLY' && evalData.provisional_verdict ? (
                    <Text style={[styles.trend, { color: VERDICT_COLOR[evalData.provisional_verdict] ?? colors.textMuted }]}>
                      eğilim: {evalData.provisional_verdict}
                    </Text>
                  ) : null}
                </View>
              </View>
              <View style={styles.evalGrid}>
                <EvalStat label="Sharpe" value={evalData.sharpe.toFixed(2)} gate={`>${evalData.gate_sharpe}`} />
                <EvalStat label="Sortino" value={evalData.sortino.toFixed(2)} gate="downside" />
                <EvalStat label="Max DD" value={`${evalData.max_dd_pct.toFixed(1)}%`} gate={`<${evalData.gate_max_dd_pct}%`} />
                <EvalStat label="Calmar" value={evalData.calmar.toFixed(2)} gate="getiri/DD" />
                <EvalStat label="Getiri" value={`${evalData.total_return_pct >= 0 ? '+' : ''}${evalData.total_return_pct.toFixed(1)}%`} gate={`${evalData.days}g`} />
                {evalData.spy_return_pct != null ? (
                  <EvalStat
                    label="α vs SPY"
                    value={`${evalData.total_return_pct - evalData.spy_return_pct >= 0 ? '+' : ''}${(evalData.total_return_pct - evalData.spy_return_pct).toFixed(1)}%`}
                    gate={`SPY ${evalData.spy_return_pct >= 0 ? '+' : ''}${evalData.spy_return_pct.toFixed(1)}%`}
                  />
                ) : null}
              </View>
              {evalData.days_remaining > 0 ? (
                <Text style={styles.countdown}>
                  Karara {evalData.days_remaining} gün · {evalData.days}/{evalData.days_required} işlem günü
                </Text>
              ) : evalData.eval_complete ? (
                <Text style={styles.countdown}>
                  Eval tamamlandı · {evalData.days}/{evalData.days_required} işlem günü · karar kesin
                </Text>
              ) : (
                <Text style={styles.countdown}>
                  {evalData.days}/{evalData.days_required} işlem günü · karar penceresi açık
                </Text>
              )}
              {evalData.gates?.length ? (
                <View style={styles.gateList}>
                  {evalData.gates.map((g) => (
                    <GateRow key={g.name} name={g.name} passed={g.passed} detail={g.detail} />
                  ))}
                </View>
              ) : null}
              {evalData.reasons.length ? (
                <Text style={styles.evalReason}>{evalData.reasons.join(' · ')}</Text>
              ) : null}
            </>
          )}
        </View>

        <Text style={styles.subheading}>Kill switch</Text>
        <View style={styles.killRow}>
          {KILL_STATES.map((k) => {
            const active = ks?.state === k.state;
            return (
              <Pressable
                key={k.state}
                style={[styles.killChip, active && { backgroundColor: k.color }]}
                onPress={() => applyKill(k.state)}
                disabled={setKs.isPending}
              >
                <Text style={[styles.killLabel, active && { color: '#000' }]}>{k.label}</Text>
              </Pressable>
            );
          })}
        </View>
        <Text style={styles.killDesc}>
          {KILL_STATES.find((k) => k.state === ks?.state)?.desc ?? 'durum yükleniyor…'}
        </Text>

        <Text style={styles.subheading}>Sistem</Text>
        <View style={styles.healthCard}>
          <View style={styles.healthRow}>
            <Text style={styles.label}>Backend</Text>
            <Text style={{ color: healthError ? colors.down : colors.up, fontWeight: '600' }}>
              {healthError ? '● offline' : health?.status === 'ok' ? '● online' : '…'}
            </Text>
          </View>
          <View style={styles.healthRow}>
            <Text style={styles.label}>Son ajan kararı</Text>
            <Text style={styles.muted}>{lastRun}</Text>
          </View>
        </View>

        <Text style={styles.subheading}>Emir gönderimi</Text>
        <View style={styles.healthCard}>
          <Text style={styles.label}>Günlük koşu otomatik gönderir (paper)</Text>
          <Text style={styles.muted}>
            Paper hesapta daily run, risk guard'lardan geçen emirleri bracket
            (stop + take-profit) ile otomatik gönderir. Live'a geçişte emirler
            mobil onaya düşer (hold → biometric approve, ADR-005). Bu davranış
            uygulamadan değiştirilemez — tek kontrol yukarıdaki kill switch.
          </Text>
        </View>

        <Text style={styles.subheading}>Notifications</Text>
        <Pressable style={styles.button} disabled={pushBusy} onPress={handleRegisterPush}>
          <Text style={styles.buttonText}>
            {pushBusy ? 'Registering…' : 'Register / refresh push token'}
          </Text>
        </Pressable>
        <Pressable style={[styles.button, styles.buttonSecondary]} onPress={handleTestPush}>
          <Text style={styles.buttonSecondaryText}>Send test notification</Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  heading: { color: colors.textPrimary, fontSize: 24, fontWeight: '700', marginBottom: 16 },
  subheading: { color: colors.textPrimary, fontSize: 16, fontWeight: '600', marginTop: 24, marginBottom: 8 },
  label: { color: colors.textPrimary, fontSize: 16 },
  muted: { color: colors.textMuted, fontSize: 13 },
  killRow: { flexDirection: 'row', gap: 8 },
  killChip: { flex: 1, paddingVertical: 14, borderRadius: 10, backgroundColor: colors.surface, alignItems: 'center' },
  killLabel: { color: colors.textPrimary, fontSize: 14, fontWeight: '700' },
  killDesc: { color: colors.textMuted, fontSize: 12, marginTop: 8, paddingHorizontal: 4 },
  healthCard: { backgroundColor: colors.surface, borderRadius: 12, padding: 16, gap: 12 },
  healthRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  evalCard: { backgroundColor: colors.surface, borderRadius: 12, padding: 16, gap: 12 },
  verdict: { fontSize: 16, fontWeight: '800' },
  verdictWrap: { alignItems: 'flex-end' },
  trend: { fontSize: 11, fontWeight: '700', marginTop: 1 },
  evalGrid: { flexDirection: 'row', flexWrap: 'wrap', rowGap: 12, justifyContent: 'space-between' },
  evalStat: { alignItems: 'flex-start' },
  evalStatLabel: { color: colors.textMuted, fontSize: 11 },
  evalStatValue: { color: colors.textPrimary, fontSize: 17, fontWeight: '700', marginTop: 2 },
  evalStatGate: { color: colors.textMuted, fontSize: 10, marginTop: 1 },
  evalReason: { color: colors.warning, fontSize: 11, fontStyle: 'italic' },
  countdown: { color: colors.textMuted, fontSize: 12, fontWeight: '600' },
  gateList: { gap: 6, borderTopWidth: 1, borderTopColor: colors.surfaceElevated, paddingTop: 10 },
  gateRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  gateIcon: { fontSize: 13, fontWeight: '800', width: 16, textAlign: 'center' },
  gateName: { color: colors.textPrimary, fontSize: 13, flex: 1 },
  gateDetail: { color: colors.textMuted, fontSize: 12 },
  button: {
    backgroundColor: colors.surface,
    padding: 14,
    borderRadius: 8,
    alignItems: 'center',
    marginTop: 8,
  },
  buttonText: { color: colors.textPrimary, fontSize: 14, fontWeight: '600' },
  buttonSecondary: { backgroundColor: 'transparent', borderWidth: 1, borderColor: colors.textMuted },
  buttonSecondaryText: { color: colors.textSecondary, fontSize: 13 },
});
