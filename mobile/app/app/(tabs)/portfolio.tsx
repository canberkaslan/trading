import { View, Text, StyleSheet, ScrollView, RefreshControl, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import { useState, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'expo-router';

import {
  usePortfolio,
  useEval,
  usePortfolioHistory,
  usePrices,
  useConcentration,
  useTrades,
} from '@/api/hooks';
import { colors } from '@/theme/colors';
import { MIN_TOUCH_TARGET } from '@/utils/a11y';
import { ErrorState } from '@/components/ErrorState';
import { EmptyState } from '@/components/EmptyState';
import { EquityChart } from '@/components/EquityChart';
import { formatUsd, formatPct } from '@/utils/format';
import { verdictTheme, PERIODS, PERIOD_DAYS, type Period } from '@/utils/equity';
import {
  sectorAllocation,
  topWeightTone,
  diversificationLabel,
  type Tone,
} from '@/utils/concentration';
import {
  unrealizedTotal,
  pnlTone,
  sampleTone,
  pnlSplit,
  formatWinRate,
  formatProfitFactor,
  reconcileFreshness,
  realizedCaveat,
  type Tone as PnlToneName,
} from '@/utils/realized';

const TONE_COLORS: Record<Tone, string> = {
  up: colors.up,
  warning: colors.warning,
  down: colors.down,
};

const PNL_TONE_COLORS: Record<PnlToneName, string> = {
  up: colors.up,
  down: colors.down,
  neutral: colors.textPrimary,
};

export default function PortfolioScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const { data, isLoading, isError, error, isFetching, refetch } = usePortfolio();
  const [period, setPeriod] = useState<Period>('1M');
  const { data: evalData } = useEval(period);
  const { data: history } = usePortfolioHistory(period);
  const { data: spy } = usePrices('SPY', PERIOD_DAYS[period]);
  const { data: concentration } = useConcentration();
  const { data: realized } = useTrades();
  const queryClient = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await queryClient.invalidateQueries({ queryKey: ['portfolio'] });
    await queryClient.invalidateQueries({ queryKey: ['eval'] });
    await queryClient.invalidateQueries({ queryKey: ['portfolio', 'history'] });
    await queryClient.invalidateQueries({ queryKey: ['prices', 'SPY'] });
    await queryClient.invalidateQueries({ queryKey: ['trades'] });
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
  const badge = evalData ? verdictTheme(evalData.verdict, colors) : null;

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#fff" />}
      >
        <View style={styles.hero}>
          <View style={styles.heroTop}>
            <Text style={styles.heroLabel}>{t('portfolio.totalEquity')}</Text>
            {badge && evalData ? (
              <View style={[styles.badge, { borderColor: badge.color }]}>
                <Text style={[styles.badgeText, { color: badge.color }]}>
                  {badge.emoji} {evalData.verdict}
                </Text>
              </View>
            ) : null}
          </View>
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

        <View style={styles.periodRow}>
          {PERIODS.map((p) => {
            const active = p === period;
            return (
              <Pressable
                key={p}
                onPress={() => setPeriod(p)}
                accessibilityRole="button"
                accessibilityLabel={`${p} dönem`}
                accessibilityState={{ selected: active }}
                style={[styles.periodPill, active && styles.periodPillActive]}
              >
                <Text style={[styles.periodText, active && styles.periodTextActive]}>{p}</Text>
              </Pressable>
            );
          })}
        </View>

        {history && history.points.length > 1 ? <EquityChart history={history} spy={spy} /> : null}

        {realized ? (() => {
          // Realized (banked) vs unrealized (mark-to-market). The hero number
          // above is entirely unrealized, so the split bar is the point of this
          // card — the stats are the supporting detail.
          const s = realized.stats;
          const open = unrealizedTotal(data.positions);
          const split = pnlSplit(s.net_pnl, open);
          const fresh = reconcileFreshness(realized.reconciled_at_utc, new Date());
          const caveat = realizedCaveat(s, open);
          const realizedPct = Math.round(split.realizedShare * 100);
          return (
            <View style={styles.riskCard}>
              <View style={styles.row}>
                <Text style={styles.cardTitle}>Gerçekleşen</Text>
                <Text style={[styles.freshness, fresh.stale && { color: colors.warning }]}>
                  {fresh.stale ? '⚠︎ ' : ''}{fresh.label}
                </Text>
              </View>

              <Text
                style={[styles.realizedValue, { color: PNL_TONE_COLORS[pnlTone(s.net_pnl)] }]}
                accessibilityLabel={`Gerçekleşen net kâr zarar ${formatUsd(s.net_pnl, { signed: true })}, ${s.trades} kapanan işlem`}
              >
                {formatUsd(s.net_pnl, { signed: true })}
              </Text>
              <Text style={styles.statSub}>
                {s.trades} kapanan işlem · {s.wins}K / {s.losses}Z
                {s.scratches > 0 ? ` / ${s.scratches}B` : ''}
              </Text>

              <View style={[styles.statRow, { marginTop: 12 }]}>
                <View style={styles.stat}>
                  <Text style={styles.statLabel}>Kazanma</Text>
                  <Text
                    style={[
                      styles.statValue,
                      { color: PNL_TONE_COLORS[sampleTone(s.win_rate - 0.5, s.trades)] },
                    ]}
                  >
                    {formatWinRate(s)}
                  </Text>
                  <Text style={styles.statSub}>oranı</Text>
                </View>
                <View style={styles.stat}>
                  <Text style={styles.statLabel}>Beklenti</Text>
                  <Text
                    style={[
                      styles.statValue,
                      { color: PNL_TONE_COLORS[sampleTone(s.expectancy, s.trades)] },
                    ]}
                  >
                    {formatUsd(s.expectancy, { signed: true })}
                  </Text>
                  <Text style={styles.statSub}>işlem başı</Text>
                </View>
                <View style={styles.stat}>
                  <Text style={styles.statLabel}>Kâr faktörü</Text>
                  <Text
                    style={[
                      styles.statValue,
                      { color: PNL_TONE_COLORS[sampleTone(s.profit_factor == null ? null : s.profit_factor - 1, s.trades)] },
                    ]}
                  >
                    {formatProfitFactor(s.profit_factor)}
                  </Text>
                  <Text style={styles.statSub}>brüt K/Z</Text>
                </View>
              </View>

              {s.trades > 0 ? (
                <>
                  <View
                    style={styles.splitTrack}
                    accessibilityLabel={`Toplam kâr zararın yüzde ${realizedPct} kadarı gerçekleşti, kalanı açık pozisyonlarda`}
                  >
                    <View style={[styles.splitRealized, { flex: Math.max(split.realizedShare, 0.01) }]} />
                    <View style={[styles.splitOpen, { flex: Math.max(1 - split.realizedShare, 0.01) }]} />
                  </View>
                  <View style={styles.row}>
                    <Text style={styles.statSub}>
                      Gerçekleşen {formatUsd(s.net_pnl, { signed: true })}
                    </Text>
                    <Text style={styles.statSub}>
                      Açık {formatUsd(open, { signed: true })}
                    </Text>
                  </View>
                </>
              ) : null}

              {caveat ? <Text style={styles.flagText}>{caveat}</Text> : null}
            </View>
          );
        })() : null}

        {(() => {
          const sectors = sectorAllocation(data.positions, data.total_equity_usd);
          if (!concentration && sectors.length === 0) return null;
          const maxWeight = sectors[0]?.weightPct ?? 0;
          const div = concentration ? diversificationLabel(concentration.effective_n) : null;
          const topTone = concentration ? topWeightTone(concentration.top_weight_pct) : 'up';
          return (
            <View style={styles.riskCard}>
              <Text style={styles.cardTitle}>Risk & Dağılım</Text>
              {concentration ? (
                <View style={styles.statRow}>
                  <View style={styles.stat}>
                    <Text style={styles.statLabel}>Çeşitlilik</Text>
                    <Text style={[styles.statValue, div ? { color: TONE_COLORS[div.tone] } : null]}>
                      {div ? div.label : '—'}
                    </Text>
                    <Text style={styles.statSub}>{concentration.effective_n.toFixed(1)} etkin isim</Text>
                  </View>
                  <View style={styles.stat}>
                    <Text style={styles.statLabel}>En yüksek</Text>
                    <Text style={[styles.statValue, { color: TONE_COLORS[topTone] }]}>
                      {formatPct(concentration.top_weight_pct / 100)}
                    </Text>
                    <Text style={styles.statSub}>tek isim</Text>
                  </View>
                  <View style={styles.stat}>
                    <Text style={styles.statLabel}>İlk 3</Text>
                    <Text style={styles.statValue}>{formatPct(concentration.top3_weight_pct / 100)}</Text>
                    <Text style={styles.statSub}>toplam ağırlık</Text>
                  </View>
                </View>
              ) : null}

              {sectors.map((s) => (
                <View key={s.label} style={styles.sectorRow}>
                  <View style={styles.sectorHeader}>
                    <Text style={styles.sectorLabel}>{s.label}</Text>
                    <Text style={styles.sectorWeight}>{formatPct(s.weightPct / 100)}</Text>
                  </View>
                  <View style={styles.barTrack}>
                    <View
                      style={[
                        styles.barFill,
                        { width: `${maxWeight > 0 ? (s.weightPct / maxWeight) * 100 : 0}%` },
                      ]}
                    />
                  </View>
                </View>
              ))}

              {concentration && concentration.flags.length > 0 ? (
                <Text style={styles.flagText}>
                  ⚠︎ {concentration.flags.length} isim %10 tek-isim sınırının üzerinde
                </Text>
              ) : null}
            </View>
          );
        })()}

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
                accessibilityRole="button"
                accessibilityLabel={`${p.ticker} pozisyonunu analiz et`}
                accessibilityHint={`${p.quantity} lot, kâr/zarar ${formatUsd(p.unrealized_pnl, { signed: true })}`}
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
  heroTop: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  badge: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 4 },
  badgeText: { fontSize: 13, fontWeight: '700', letterSpacing: 0.5 },
  heroLabel: { color: colors.textSecondary, fontSize: 14 },
  heroValue: { color: colors.textPrimary, fontSize: 36, fontWeight: '700', marginTop: 4 },
  heroChange: { fontSize: 16, marginTop: 8, fontWeight: '600' },
  muted: { color: colors.textMuted, marginTop: 4 },
  timestamp: { color: colors.textMuted, fontSize: 11, marginTop: 2 },
  periodRow: { flexDirection: 'row', gap: 8, paddingHorizontal: 24, marginTop: 8 },
  periodPill: {
    paddingHorizontal: 14,
    paddingVertical: 6,
    borderRadius: 999,
    backgroundColor: colors.surface,
    minHeight: MIN_TOUCH_TARGET,
    minWidth: MIN_TOUCH_TARGET,
    alignItems: 'center',
    justifyContent: 'center',
  },
  periodPillActive: { backgroundColor: colors.surfaceElevated },
  periodText: { color: colors.textMuted, fontSize: 13, fontWeight: '600' },
  periodTextActive: { color: colors.textPrimary },
  section: { color: colors.textPrimary, fontSize: 18, fontWeight: '600', paddingHorizontal: 24, marginTop: 16 },
  riskCard: { marginHorizontal: 24, marginTop: 16, padding: 16, backgroundColor: colors.surface, borderRadius: 12 },
  cardTitle: { color: colors.textPrimary, fontSize: 16, fontWeight: '600', marginBottom: 8 },
  statRow: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 4, marginBottom: 12 },
  stat: { flex: 1 },
  statLabel: { color: colors.textMuted, fontSize: 12 },
  statValue: { color: colors.textPrimary, fontSize: 17, fontWeight: '700', marginTop: 2 },
  statSub: { color: colors.textMuted, fontSize: 11, marginTop: 1 },
  sectorRow: { marginTop: 10 },
  sectorHeader: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 },
  sectorLabel: { color: colors.textSecondary, fontSize: 13 },
  sectorWeight: { color: colors.textPrimary, fontSize: 13, fontWeight: '600' },
  barTrack: { height: 6, backgroundColor: colors.surfaceElevated, borderRadius: 999, overflow: 'hidden' },
  barFill: { height: 6, backgroundColor: colors.accent, borderRadius: 999 },
  flagText: { color: colors.warning, fontSize: 12, marginTop: 12 },
  freshness: { color: colors.textMuted, fontSize: 11 },
  realizedValue: { fontSize: 26, fontWeight: '700', marginTop: 2 },
  splitTrack: { flexDirection: 'row', height: 6, borderRadius: 999, overflow: 'hidden', marginTop: 14, marginBottom: 6 },
  splitRealized: { backgroundColor: colors.accent },
  splitOpen: { backgroundColor: colors.surfaceElevated },
  positionCard: { marginHorizontal: 24, marginTop: 12, padding: 16, backgroundColor: colors.surface, borderRadius: 12 },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  posTicker: { color: colors.textPrimary, fontSize: 18, fontWeight: '600' },
  posPnl: { fontSize: 14, fontWeight: '600' },
  analyzeHint: { color: colors.accent, fontSize: 12, fontWeight: '600', marginTop: 4 },
  disclaimer: { color: '#555', fontSize: 11, padding: 24, fontStyle: 'italic', textAlign: 'center' },
});
