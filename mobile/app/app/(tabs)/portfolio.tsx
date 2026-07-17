import { View, Text, StyleSheet, ScrollView, RefreshControl, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import { useState, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'expo-router';

import { usePortfolio } from '@/api/hooks';
import { colors } from '@/theme/colors';
import { ErrorState } from '@/components/ErrorState';
import { EmptyState } from '@/components/EmptyState';
import { formatUsd, formatPct } from '@/utils/format';

export default function PortfolioScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const { data, isLoading, isError, error, isFetching, refetch } = usePortfolio();
  const queryClient = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await queryClient.invalidateQueries({ queryKey: ['portfolio'] });
    setRefreshing(false);
  }, [queryClient]);

  if (isLoading) {
    return (
      <SafeAreaView style={styles.container} edges={['top']}>
        <View style={styles.center}><Text style={styles.muted}>Loading…</Text></View>
      </SafeAreaView>
    );
  }

  if (isError || !data) {
    return (
      <SafeAreaView style={styles.container} edges={['top']}>
        <ErrorState detail={error} onRetry={refetch} />
      </SafeAreaView>
    );
  }

  const pnlColor = data.daily_pnl_usd >= 0 ? colors.up : colors.down;

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#fff" />}
      >
        <View style={styles.hero}>
          <Text style={styles.heroLabel}>{t('portfolio.totalEquity')}</Text>
          <Text style={styles.heroValue}>{formatUsd(data.total_equity_usd)}</Text>
          <Text style={[styles.heroChange, { color: pnlColor }]}>
            {formatUsd(data.daily_pnl_usd, { signed: true })} ({formatPct(data.daily_pnl_pct, { signed: true })})
          </Text>
          <Text style={styles.muted}>
            Cash: {formatUsd(data.cash_usd)}  •  {isFetching ? 'updating…' : 'live'}
          </Text>
          <Text style={styles.timestamp}>
            Son güncelleme:{' '}
            {new Date(data.timestamp_utc).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
          </Text>
        </View>

        <Text style={styles.section}>{t('portfolio.positions')}</Text>
        {data.positions.length === 0 ? (
          <EmptyState title="Açık pozisyon yok" hint="Günlük çalışma yeni pozisyon açtığında burada görünür." />
        ) : (
          data.positions.map((p) => {
            const c = p.unrealized_pnl >= 0 ? colors.up : colors.down;
            return (
              <Pressable
                key={p.ticker}
                style={styles.positionCard}
                onPress={() => router.push(`/(tabs)/ask?ticker=${p.ticker}` as never)}
              >
                <View style={styles.row}>
                  <Text style={styles.posTicker}>{p.ticker}</Text>
                  <Text style={[styles.posPnl, { color: c }]}>
                    {formatUsd(p.unrealized_pnl, { signed: true })}
                    {'  '}({formatPct(p.unrealized_pnl_pct, { signed: true })})
                  </Text>
                </View>
                <View style={styles.row}>
                  <Text style={styles.muted}>
                    {p.quantity} @ {formatUsd(p.avg_entry_price)}  •  now {formatUsd(p.current_price)}
                  </Text>
                  <Text style={styles.analyzeHint}>Analiz →</Text>
                </View>
              </Pressable>
            );
          })
        )}

        <Text style={styles.disclaimer}>{t('disclaimer.short')}</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  hero: { padding: 24 },
  heroLabel: { color: colors.textSecondary, fontSize: 14 },
  heroValue: { color: colors.textPrimary, fontSize: 36, fontWeight: '700', marginTop: 4 },
  heroChange: { fontSize: 16, marginTop: 8, fontWeight: '600' },
  muted: { color: colors.textMuted, marginTop: 4 },
  timestamp: { color: colors.textMuted, fontSize: 11, marginTop: 2 },
  section: { color: colors.textPrimary, fontSize: 18, fontWeight: '600', paddingHorizontal: 24, marginTop: 16 },
  positionCard: { marginHorizontal: 24, marginTop: 12, padding: 16, backgroundColor: colors.surface, borderRadius: 12 },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  posTicker: { color: colors.textPrimary, fontSize: 18, fontWeight: '600' },
  posPnl: { fontSize: 14, fontWeight: '600' },
  analyzeHint: { color: colors.accent, fontSize: 12, fontWeight: '600', marginTop: 4 },
  disclaimer: { color: '#555', fontSize: 11, padding: 24, fontStyle: 'italic', textAlign: 'center' },
});
