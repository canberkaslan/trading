import { View, Text, StyleSheet, Switch } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useState } from 'react';

export default function SettingsScreen() {
  const [autoExecute, setAutoExecute] = useState(false);

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.section}>
        <Text style={styles.label}>Auto-execute trades</Text>
        <Switch
          value={autoExecute}
          onValueChange={(v) => {
            // TODO(phase-5): biometric prompt + 24h cooldown before first auto-trade
            setAutoExecute(v);
          }}
        />
      </View>
      <Text style={styles.warning}>
        Auto-execute requires biometric confirmation and a 24-hour cooldown before the
        first auto-trade. See ADR-005.
      </Text>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0a0a0a' },
  section: {
    padding: 16,
    margin: 16,
    backgroundColor: '#171717',
    borderRadius: 12,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  label: { color: '#fff', fontSize: 16 },
  warning: {
    color: '#f59e0b',
    fontSize: 12,
    paddingHorizontal: 24,
    fontStyle: 'italic',
  },
});
