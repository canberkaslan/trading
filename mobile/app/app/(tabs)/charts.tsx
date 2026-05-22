import { View, Text, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

export default function ChartsScreen() {
  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.placeholder}>
        <Text style={styles.text}>
          Phase 5+ — TradingView Lightweight Charts in WebView
        </Text>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0a0a0a' },
  placeholder: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  text: { color: '#666', textAlign: 'center' },
});
