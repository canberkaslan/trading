import { View, Text, StyleSheet, Switch, Pressable, Alert, ScrollView } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useState } from 'react';

import { api } from '@/api/endpoints';
import { useKillSwitch, useSetKillSwitch, useHealth, useDecisions } from '@/api/hooks';
import { setupNotifications } from '@/notifications';
import { colors } from '@/theme/colors';
import type { KillSwitchState } from '@/api/types';

const KILL_STATES: { state: KillSwitchState; label: string; desc: string; color: string }[] = [
  { state: 'RUN', label: 'RUN', desc: 'Normal işlem', color: colors.up },
  { state: 'PAUSE_NEW', label: 'PAUSE', desc: 'Yeni giriş yok, mevcut yönetilir', color: colors.warning },
  { state: 'FLATTEN_ALL', label: 'FLATTEN', desc: 'Tüm pozisyonları kapat', color: colors.down },
];

export default function SettingsScreen() {
  const [autoExecute, setAutoExecute] = useState(false);
  const [pushBusy, setPushBusy] = useState(false);
  const { data: ks } = useKillSwitch();
  const setKs = useSetKillSwitch();
  const { data: health, isError: healthError } = useHealth();
  const { data: decisions } = useDecisions({ limit: 1 });

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

        <View style={styles.section}>
          <Text style={styles.label}>Auto-execute trades</Text>
          <Switch
            value={autoExecute}
            onValueChange={(v) => {
              // TODO(5h+): biometric prompt + 24h cooldown before first auto-trade
              setAutoExecute(v);
            }}
          />
        </View>
        <Text style={styles.warning}>
          Auto-execute requires biometric confirmation and a 24-hour cooldown before the
          first auto-trade. See ADR-005.
        </Text>

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
  section: {
    padding: 16,
    backgroundColor: colors.surface,
    borderRadius: 12,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  label: { color: colors.textPrimary, fontSize: 16 },
  muted: { color: colors.textMuted, fontSize: 13 },
  killRow: { flexDirection: 'row', gap: 8 },
  killChip: { flex: 1, paddingVertical: 14, borderRadius: 10, backgroundColor: colors.surface, alignItems: 'center' },
  killLabel: { color: colors.textPrimary, fontSize: 14, fontWeight: '700' },
  killDesc: { color: colors.textMuted, fontSize: 12, marginTop: 8, paddingHorizontal: 4 },
  healthCard: { backgroundColor: colors.surface, borderRadius: 12, padding: 16, gap: 12 },
  healthRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  warning: {
    color: colors.warning,
    fontSize: 12,
    paddingHorizontal: 4,
    paddingTop: 8,
    fontStyle: 'italic',
  },
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
