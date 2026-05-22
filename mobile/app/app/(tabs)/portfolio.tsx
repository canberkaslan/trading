import { View, Text, StyleSheet, ScrollView } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';

export default function PortfolioScreen() {
  const { t } = useTranslation();

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView>
        <View style={styles.hero}>
          <Text style={styles.heroLabel}>{t('portfolio.totalEquity')}</Text>
          <Text style={styles.heroValue}>—</Text>
          <Text style={styles.heroChange}>—</Text>
        </View>
        <Text style={styles.section}>{t('portfolio.positions')}</Text>
        <View style={styles.placeholder}>
          <Text style={styles.placeholderText}>
            Phase 5 — Wire to /v1/portfolio/snapshot
          </Text>
        </View>
        <Text style={styles.disclaimer}>{t('disclaimer.short')}</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0a0a0a' },
  hero: { padding: 24 },
  heroLabel: { color: '#888', fontSize: 14 },
  heroValue: { color: '#fff', fontSize: 36, fontWeight: '700', marginTop: 4 },
  heroChange: { color: '#22c55e', fontSize: 16, marginTop: 8 },
  section: {
    color: '#fff',
    fontSize: 18,
    fontWeight: '600',
    paddingHorizontal: 24,
    marginTop: 16,
  },
  placeholder: {
    margin: 24,
    padding: 24,
    backgroundColor: '#171717',
    borderRadius: 12,
  },
  placeholderText: { color: '#666', textAlign: 'center' },
  disclaimer: {
    color: '#555',
    fontSize: 11,
    padding: 24,
    fontStyle: 'italic',
    textAlign: 'center',
  },
});
