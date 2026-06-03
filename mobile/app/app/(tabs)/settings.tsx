import { View, Text, StyleSheet, Switch, Pressable, Alert, ScrollView } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useState } from 'react';

import { api } from '@/api/endpoints';
import { setupNotifications } from '@/notifications';
import { colors } from '@/theme/colors';

export default function SettingsScreen() {
  const [autoExecute, setAutoExecute] = useState(false);
  const [pushBusy, setPushBusy] = useState(false);

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
