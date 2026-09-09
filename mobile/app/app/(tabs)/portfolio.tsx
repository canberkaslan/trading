import { View, Text, StyleSheet, ScrollView, RefreshControl, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import { useState, useCallback, useMemo } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'expo-router';

import {
  usePortfolio,
  useEval,
  usePortfolioHistory,
  usePrices,
  useConcentration,
  useTrades,
  useActionability,
} from '@/api/hooks';
import type { Position } from '@/api/types';
import { useTheme } from '@/theme/useTheme';
import { hitSlopFor } from '@/utils/a11y';
import { ErrorState } from '@/components/ErrorState';
import { EmptyState } from '@/components/EmptyState';
import { EquityChart } from '@/components/EquityChart';
import { Seg, type SegOption } from '@/components/Seg';
import { Tag } from '@/components/Tag';
import { positionStop } from '@/utils/positions';
import { formatUsd, formatPct } from '@/utils/format';
import { verdictTheme, PERIODS, PERIOD_DAYS, type Period } from '@/utils/equity';
import { font, TABULAR, TYPE } from '@/theme/type';
import {
  sectorAllocation,
  sectorLabelTr,
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
  evalWindowNote,
  exitBreakdown,
  exitClassLabelTr,
  strategyReading,
  attributionNote,
  type Tone as PnlToneName,
} from '@/utils/realized';
import {
  actionabilityVerdictMeta,
  inertiaNote,
  lastSubmitLabel,
  submitRate,
  submitRatioLabel,
  topReasons,
  verdictQualifier,
  type ActionabilityTone,
} from '@/utils/actionability';

// Tone -> colour is now palette-dependent: in Modernist a gain is the ink
// colour, so these cannot be module constants any more.
type Palette = ReturnType<typeof useTheme>;
const toneColors = (t: Palette): Record<Tone, string> => ({
  up: t.up,
  warning: t.warning,
  down: t.downText ?? t.down,
});
const pnlToneColors = (t: Palette): Record<PnlToneName, string> => ({
  up: t.up,
  down: t.downText ?? t.down,
  neutral: t.textPrimary,
});
const flowToneColors = (t: Palette): Record<ActionabilityTone, string> => ({
  up: t.up,
  warning: t.warning,
  muted: t.textSecondary,
});

/** The prototype's segment labels: 1A/3A/6A over the API's 1M/3M/6M windows. */
const PERIOD_LABELS: Record<Period, string> = { '1M': '1A', '3M': '3A', '6M': '6A' };
const PERIOD_OPTIONS: readonly SegOption<Period>[] = PERIODS.map((p) => ({
  value: p,
  label: PERIOD_LABELS[p],
  accessibilityLabel: `${PERIOD_LABELS[p]} dönem`,
}));

/**
 * "başlangıçtan" needs the book's first day, not the selected window's.
 *
 * /v1/portfolio/history trims every curve at EVAL_START_DATE — the day the
 * paper book opened — so the longest window the API offers *is* inception for
 * any book younger than that window. Asking for it costs nothing extra when
 * the user is already on 6A: TanStack dedupes on the same query key.
 */
const INCEPTION_PERIOD: Period = PERIODS[PERIODS.length - 1] ?? '1M';

/**
 * Whether that actually held this time — the "younger than that window" half of
 * the paragraph above, checked instead of assumed.
 *
 * The trim is what makes the window's first point the book's first day. Once
 * the book outlives the longest window — or on a deployment that configures no
 * cutoff at all — the curve simply starts at the window's edge, and labelling
 * that "başlangıçtan" would put a date on screen that no endpoint ever gave
 * us. /v1/trades reports the same cutoff (`eval_start_utc`, one env var and one
 * meaning across both routes), so the two can be compared: if the curve opens
 * on the cutoff, it opens on day one. The first trading day can lag the cutoff
 * across a long weekend or a holiday, hence the slack.
 */
const INCEPTION_SLACK_MS = 7 * 24 * 60 * 60 * 1000;

function startsAtInception(
  curve: { points: { date: string }[] } | undefined,
  evalStartUtc: string | null | undefined,
): boolean {
  const first = curve?.points?.[0]?.date;
  if (!first || !evalStartUtc) return false;
  const utcDay = (iso: string) => Date.parse(`${iso.slice(0, 10)}T00:00:00Z`);
  const gap = utcDay(first) - utcDay(evalStartUtc);
  // Negative means the curve predates the cutoff, i.e. it was never trimmed.
  return Number.isFinite(gap) && gap >= 0 && gap <= INCEPTION_SLACK_MS;
}

export default function PortfolioScreen() {
  const { t } = useTranslation();
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);
  const TONE_COLORS = useMemo(() => toneColors(theme), [theme]);
  const PNL_TONE_COLORS = useMemo(() => pnlToneColors(theme), [theme]);
  const FLOW_TONE_COLORS = useMemo(() => flowToneColors(theme), [theme]);
  const router = useRouter();
  const { data, isLoading, isError, error, isFetching, refetch } = usePortfolio();
  const [period, setPeriod] = useState<Period>('1M');
  const { data: evalData } = useEval(period);
  const { data: history } = usePortfolioHistory(period);
  const { data: inception } = usePortfolioHistory(INCEPTION_PERIOD);
  const { data: spy } = usePrices('SPY', PERIOD_DAYS[period]);
  const { data: concentration } = useConcentration();
  const { data: realized } = useTrades();
  const { data: flow } = useActionability();
  const queryClient = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await queryClient.invalidateQueries({ queryKey: ['portfolio'] });
    await queryClient.invalidateQueries({ queryKey: ['eval'] });
    await queryClient.invalidateQueries({ queryKey: ['portfolio', 'history'] });
    await queryClient.invalidateQueries({ queryKey: ['prices', 'SPY'] });
    await queryClient.invalidateQueries({ queryKey: ['trades'] });
    await queryClient.invalidateQueries({ queryKey: ['diagnostics', 'actionability'] });
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

  // Accounting palette, via the util that owns the sign→tone rule: a gain is
  // ink, a loss is the accent, and exactly flat is neither. Restating
  // `>= 0 ? up : down` here would quietly disagree with `pnlTone` on zero.
  const dailyColor = PNL_TONE_COLORS[pnlTone(data.daily_pnl_usd)];
  const badge = evalData ? verdictTheme(evalData.verdict, theme) : null;

  // Since the book opened — when the curve can be shown to reach that far. The
  // dollar leg is measured against live equity (the hero number right above it)
  // so the two can never disagree on screen; the label says which start it is
  // measured from, because a window return worn as an inception return is the
  // kind of number people quote.
  const sinceLabel = startsAtInception(inception, realized?.eval_start_utc)
    ? 'başlangıçtan'
    : `son ${PERIOD_LABELS[INCEPTION_PERIOD]}`;
  const sinceStart =
    inception && inception.start_equity > 0
      ? {
          usd: data.total_equity_usd - inception.start_equity,
          pct: (data.total_equity_usd - inception.start_equity) / inception.start_equity,
        }
      : null;
  // A GO badge over a book that has submitted nothing for days is the single
  // most misleading thing on this screen — qualify it where it is read.
  const badgeQualifier = verdictQualifier(flow);

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={theme.textPrimary} />}
      >
        <View style={styles.hero}>
          <View style={styles.heroTop}>
            <Text style={styles.heroLabel}>Portföy değeri</Text>
            {badge && evalData ? (
              <View style={styles.badgeWrap}>
                <View style={[styles.badge, { borderColor: badge.color }]}>
                  <Text
                    style={[styles.badgeText, { color: badge.color }]}
                    accessibilityLabel={
                      badgeQualifier
                        ? `Eval kararı ${evalData.verdict}, ancak kitap ${flow?.inert_run_days} çalışma günüdür donmuş`
                        : `Eval kararı ${evalData.verdict}`
                    }
                  >
                    {badge.emoji} {evalData.verdict}
                  </Text>
                </View>
                <Text style={styles.evalDays}>
                  {evalData.days} / {evalData.days_required} işlem günü
                </Text>
              </View>
            ) : null}
          </View>

          <Text style={styles.heroValue}>{formatUsd(data.total_equity_usd)}</Text>

          {/* One row of three, in the handoff's order: since inception ·
              today · cash. Each figure signed where it is a P&L delta, and
              tabular so the three stay on one baseline as they tick. */}
          <Text style={styles.heroSubRow}>
            <Text style={styles.subLabel}>{sinceLabel} </Text>
            <Text
              style={[
                styles.subValue,
                sinceStart ? { color: PNL_TONE_COLORS[pnlTone(sinceStart.usd)] } : null,
              ]}
            >
              {sinceStart
                ? `${formatUsd(sinceStart.usd, { signed: true })} (${formatPct(sinceStart.pct, { signed: true })})`
                : '—'}
            </Text>
            <Text style={styles.subSep}> · </Text>
            <Text style={styles.subLabel}>Bugün </Text>
            <Text style={[styles.subValue, { color: dailyColor }]}>
              {`${formatUsd(data.daily_pnl_usd, { signed: true })} (${formatPct(data.daily_pnl_pct, { signed: true })})`}
            </Text>
            <Text style={styles.subSep}> · </Text>
            <Text style={styles.subLabel}>Nakit </Text>
            <Text style={styles.subValue}>{formatUsd(data.cash_usd)}</Text>
          </Text>

          {/* The one mark that outranks the verdict badge: a GO over a book
              that has submitted nothing for days. Outline = awaiting a
              decision, per the tag vocabulary. */}
          {badgeQualifier ? (
            <Tag label={`⚠ ${badgeQualifier}`} variant="outline" style={styles.inertTag} />
          ) : null}

          <Text style={styles.timestamp}>
            Son güncelleme:{' '}
            {new Date(data.timestamp_utc).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            {isFetching ? ' · güncelleniyor…' : ''}
          </Text>
        </View>

        <View style={styles.periodRow}>
          <Seg options={PERIOD_OPTIONS} value={period} onChange={setPeriod} />
        </View>

        {history && history.points.length > 1 ? <EquityChart history={history} spy={spy} /> : null}

        {flow ? (() => {
          // Every number above this card is computed from equity, and a frozen
          // basket still has an equity curve. This card is the only thing on
          // the screen that can tell "working" apart from "stuck".
          const meta = actionabilityVerdictMeta(flow.verdict);
          const rate = submitRate(flow);
          const reasons = topReasons(flow.by_reason);
          const note = inertiaNote(flow);
          return (
            <View style={styles.riskCard}>
              <View style={styles.row}>
                <Text style={styles.cardTitle}>Emir akışı</Text>
                <Text style={styles.freshness}>son {flow.window_days} gün</Text>
              </View>

              <View style={styles.statRow}>
                <View style={styles.stat}>
                  <Text style={styles.statLabel}>Durum</Text>
                  <Text
                    style={[styles.statValue, { color: FLOW_TONE_COLORS[meta.tone] }]}
                    accessibilityLabel={`Emir akışı durumu: ${meta.label}`}
                  >
                    {meta.label}
                  </Text>
                  <Text style={styles.statSub}>{flow.run_days} çalışma günü</Text>
                </View>
                <View style={styles.stat}>
                  <Text style={styles.statLabel}>Broker'a giden</Text>
                  <Text style={styles.statValue}>{submitRatioLabel(flow)}</Text>
                  <Text style={styles.statSub}>{rate == null ? 'emir yok' : formatPct(rate)}</Text>
                </View>
                <View style={styles.stat}>
                  <Text style={styles.statLabel}>Son gönderim</Text>
                  <Text
                    style={[
                      styles.statValue,
                      // "Donmuş is a warning" is `actionabilityVerdictMeta`'s rule.
                      // Testing `flow.verdict === 'inert'` here would be a second
                      // copy of it — one that keeps this figure black on the day
                      // the backend renames the state or grows a fourth one.
                      meta.tone === 'warning' ? { color: FLOW_TONE_COLORS[meta.tone] } : null,
                    ]}
                  >
                    {lastSubmitLabel(flow.last_submitted_at_utc, new Date())}
                  </Text>
                  <Text style={styles.statSub}>ack alınan</Text>
                </View>
              </View>

              {reasons.map((r) => (
                <View key={r.reason} style={styles.sectorRow}>
                  <View style={styles.sectorHeader}>
                    <Text style={styles.sectorLabel} numberOfLines={2}>{r.label}</Text>
                    <Text style={styles.sectorWeight}>{r.count}</Text>
                  </View>
                  <View style={styles.barTrack}>
                    <View style={[styles.barBlocked, { width: `${r.share * 100}%` }]} />
                  </View>
                </View>
              ))}

              {note ? <Text style={styles.flagText}>⚠︎ {note}</Text> : null}
            </View>
          );
        })() : null}

        {realized ? (() => {
          // Realized (banked) vs unrealized (mark-to-market). The hero number
          // above is entirely unrealized, so the split bar is the point of this
          // card — the stats are the supporting detail.
          const s = realized.stats;
          const open = unrealizedTotal(data.positions);
          const split = pnlSplit(s.net_pnl, open);
          const fresh = reconcileFreshness(realized.reconciled_at_utc, new Date());
          const caveat = realizedCaveat(s, open);
          const windowNote = evalWindowNote(realized);
          const realizedPct = Math.round(split.realizedShare * 100);
          // `s` blends every exit path — on this book mostly the 2026-06-24
          // operator flatten. The strategy block below is the agent's own exit
          // record, kept visually separate so the blended number can never be
          // read as "how well does the agent exit?".
          const strat = strategyReading(realized);
          const exits = exitBreakdown(realized);
          const attrNote = attributionNote(realized);
          return (
            <View style={styles.riskCard}>
              <View style={styles.row}>
                <Text style={styles.cardTitle}>Gerçekleşen</Text>
                <Text style={[styles.freshness, fresh.stale && { color: theme.warning }]}>
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
              {windowNote ? <Text style={styles.statSub}>{windowNote}</Text> : null}

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
                  {/* Named as a blend the moment the split exists, so this
                      number stops standing in for the agent's exit record. */}
                  <Text style={styles.statSub}>
                    {strat.status === 'unavailable' ? 'işlem başı' : 'işlem başı · karışık'}
                  </Text>
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

              {/* The agent's own exits, separated from operator flattens. */}
              <View style={styles.exitBlock}>
                <View style={styles.row}>
                  <Text style={styles.exitTitle}>Stratejinin çıkışları</Text>
                  {strat.bucket ? (
                    <Text
                      style={styles.sampleBadge}
                      accessibilityLabel={`${strat.bucket.trades} işlemlik örneklem`}
                    >
                      n={strat.bucket.trades}
                    </Text>
                  ) : null}
                </View>

                {strat.bucket ? (
                  <View style={[styles.statRow, { marginTop: 8, marginBottom: 4 }]}>
                    <View style={styles.stat}>
                      <Text style={styles.statLabel}>Net</Text>
                      <Text
                        style={[
                          styles.statValue,
                          { color: PNL_TONE_COLORS[sampleTone(strat.bucket.net_pnl, strat.bucket.trades)] },
                        ]}
                      >
                        {formatUsd(strat.bucket.net_pnl, { signed: true })}
                      </Text>
                    </View>
                    <View style={styles.stat}>
                      <Text style={styles.statLabel}>Kazanma</Text>
                      <Text
                        style={[
                          styles.statValue,
                          {
                            color:
                              PNL_TONE_COLORS[
                                sampleTone(strat.bucket.win_rate - 0.5, strat.bucket.trades)
                              ],
                          },
                        ]}
                      >
                        {formatWinRate({ win_rate: strat.bucket.win_rate, trades: strat.bucket.trades })}
                      </Text>
                    </View>
                    <View style={styles.stat}>
                      <Text style={styles.statLabel}>Beklenti</Text>
                      <Text
                        style={[
                          styles.statValue,
                          { color: PNL_TONE_COLORS[sampleTone(strat.bucket.avg_pnl, strat.bucket.trades)] },
                        ]}
                      >
                        {formatUsd(strat.bucket.avg_pnl, { signed: true })}
                      </Text>
                    </View>
                  </View>
                ) : null}

                <Text style={styles.statSub}>{strat.note}</Text>

                {exits.length > 0 ? (
                  <View style={{ marginTop: 10 }}>
                    {exits.map((b) => (
                      <View key={b.exit_class} style={styles.exitRow}>
                        <Text style={styles.exitLabel} numberOfLines={1}>
                          {exitClassLabelTr(b.exit_class)}
                        </Text>
                        <Text style={styles.exitCount}>{b.trades}</Text>
                        <Text
                          style={[styles.exitPnl, { color: PNL_TONE_COLORS[pnlTone(b.net_pnl)] }]}
                          accessibilityLabel={`${exitClassLabelTr(b.exit_class)}: ${b.trades} işlem, net ${formatUsd(b.net_pnl, { signed: true })}`}
                        >
                          {formatUsd(b.net_pnl, { signed: true })}
                        </Text>
                      </View>
                    ))}
                  </View>
                ) : null}

                {attrNote ? <Text style={styles.flagText}>{attrNote}</Text> : null}
              </View>

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
                // The cap itself is `topWeightTone`'s to know — naming a
                // percentage here would be a second copy of it, free to drift.
                <Text style={styles.flagText}>
                  ⚠ {concentration.flags.length} isim tek-isim tavanının üstünde
                </Text>
              ) : null}
            </View>
          );
        })()}

        {(() => {
          // Weight goes through the concentration util rather than being
          // multiplied out here: a one-position allocation is that position's
          // share of total equity, the same denominator the sector bars above
          // use — so a name's weight means one thing on this screen. Whether
          // that weight breaches the single-name cap is `topWeightTone`'s
          // call; the threshold lives there, not in this file.
          const rows = data.positions.map((p) => {
            const weightPct = sectorAllocation([p], data.total_equity_usd)[0]?.weightPct ?? 0;
            return { p, weightPct, overCap: topWeightTone(weightPct) !== 'up' };
          });
          const overCapCount = rows.filter((r) => r.overCap).length;
          return (
            <View style={styles.riskCard}>
              <View style={styles.row}>
                <Text style={styles.cardTitle}>{t('portfolio.positions')}</Text>
                <Text style={styles.freshness}>
                  {rows.length} açık · {overCapCount} cap üstü
                </Text>
              </View>
              {rows.length === 0 ? (
                <EmptyState title="Açık pozisyon yok" hint="Günlük çalışma yeni pozisyon açtığında burada görünür." />
              ) : (
                rows.map((r) => (
                  <PositionRow
                    key={r.p.ticker}
                    position={r.p}
                    weightPct={r.weightPct}
                    overCap={r.overCap}
                    onAnalyze={() => router.push(`/(tabs)/ask?ticker=${r.p.ticker}` as never)}
                    onChart={() => router.push(`/(tabs)/charts?ticker=${r.p.ticker}` as never)}
                  />
                ))
              )}
            </View>
          );
        })()}

        <Text style={styles.disclaimer}>{t('disclaimer.short')}</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

/** The prototype's ghost-button height; `hitSlopFor` takes it to 44. */
const GHOST_H = 36;

/**
 * One position, as the handoff's table read top to bottom.
 *
 * The web table's columns — Sembol+sektör, Adet, Ort., Son, Değer, Ağırlık,
 * K/Z, K/Z %, Stop, then Grafik/Analiz — become label→value rows in that exact
 * order, which is what "mobilde aynı sıra dikey" asks for: the same reading
 * order on both surfaces, so a name means the same thing wherever it is read.
 */
function PositionRow({
  position: p,
  weightPct,
  overCap,
  onAnalyze,
  onChart,
}: {
  position: Position;
  weightPct: number;
  overCap: boolean;
  onAnalyze: () => void;
  onChart: () => void;
}) {
  const t = useTheme();
  const styles = useMemo(() => makeStyles(t), [t]);
  // Accounting palette through the util that owns the rule — same call the
  // hero and the Gerçekleşen card make, so one flat position cannot render
  // green here and neutral there.
  const pnlColor = pnlToneColors(t)[pnlTone(p.unrealized_pnl)];

  const fields: { label: string; value: string; color?: string; cap?: boolean }[] = [
    { label: 'Adet', value: String(p.quantity) },
    { label: 'Ort.', value: formatUsd(p.avg_entry_price) },
    { label: 'Son', value: formatUsd(p.current_price) },
    { label: 'Değer', value: formatUsd(p.quantity * p.current_price) },
    { label: 'Ağırlık', value: formatPct(weightPct / 100), cap: overCap },
    { label: 'K/Z', value: formatUsd(p.unrealized_pnl, { signed: true }), color: pnlColor },
    { label: 'K/Z %', value: formatPct(p.unrealized_pnl_pct, { signed: true }), color: pnlColor },
    // The stop leg lives on the broker order, not on the position: a book with
    // no bracket answers 0, which must read as "no stop", never as a $0.00 one.
    { label: 'Stop', value: formatUsd(positionStop(p)) },
  ];

  return (
    // `accessible={false}`: the block stays one tap target for Sor, but the two
    // actions inside remain their own elements for a screen reader.
    <Pressable style={styles.posBlock} onPress={onAnalyze} accessible={false}>
      <View style={styles.posHead}>
        <Text style={styles.posTicker}>{p.ticker}</Text>
        <Text style={styles.posSector}>{sectorLabelTr(p.sector)}</Text>
      </View>

      {fields.map((f) => (
        <View key={f.label} style={styles.posField}>
          <Text style={styles.posFieldLabel}>{f.label}</Text>
          <View style={styles.posFieldValue}>
            <Text style={[styles.posFieldText, f.color ? { color: f.color } : null]}>{f.value}</Text>
            {f.cap ? <Tag label="cap" variant="accent" /> : null}
          </View>
        </View>
      ))}

      <View style={styles.posActions}>
        <Pressable
          style={styles.ghost}
          hitSlop={hitSlopFor(GHOST_H)}
          onPress={onChart}
          accessibilityRole="button"
          accessibilityLabel={`${p.ticker} grafiğini aç`}
        >
          <Text style={styles.ghostText}>Grafik</Text>
        </Pressable>
        <Pressable
          style={styles.ghost}
          hitSlop={hitSlopFor(GHOST_H)}
          onPress={onAnalyze}
          accessibilityRole="button"
          accessibilityLabel={`${p.ticker} pozisyonunu analiz et`}
          accessibilityHint={`${p.quantity} lot, ağırlık ${formatPct(weightPct / 100)}, kâr/zarar ${formatUsd(p.unrealized_pnl, { signed: true })}`}
        >
          <Text style={styles.ghostText}>Analiz →</Text>
        </Pressable>
      </View>
    </Pressable>
  );
}

/**
 * Modernist geometry: no radii anywhere, 2px rules between sections and 1px
 * between rows, and a 44px hero. Cards become ruled blocks rather than filled
 * rounded ones — the divider does the separating, so nothing needs a shadow.
 */
const makeStyles = (t: Palette) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.background },
    center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },

    hero: { paddingHorizontal: 16, paddingTop: 20, paddingBottom: 16, ...TABULAR },
    heroTop: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' },
    badgeWrap: { alignItems: 'flex-end' },
    // Square, outlined — the verdict is a stamp, not a pill.
    badge: { borderWidth: 1, paddingHorizontal: 10, paddingVertical: 4 },
    badgeText: { fontSize: 13, ...font(800), letterSpacing: 0.5 },
    evalDays: { color: t.textSecondary, marginTop: 4, ...TYPE.helper, ...TABULAR },
    // Kicker: the label above the number, in accent-700 so small red type stays
    // legible where the base accent would not be.
    heroLabel: { color: t.accent700 ?? t.accent, ...TYPE.kicker },
    heroValue: { color: t.textPrimary, marginTop: 6, ...TYPE.hero },

    // The handoff's one row of three: başlangıçtan · Bugün · Nakit. One Text,
    // not three Views, so a narrow phone reflows it as prose instead of
    // orphaning a separator or clipping a figure.
    heroSubRow: { color: t.textSecondary, marginTop: 10, lineHeight: 20, ...TYPE.body },
    subLabel: { color: t.textSecondary, ...TYPE.body },
    subValue: { color: t.textPrimary, fontSize: 13, ...font(600), ...TABULAR },
    subSep: { color: t.neutral500 ?? t.textSecondary, ...TYPE.body },
    inertTag: { marginTop: 10 },

    muted: { color: t.textSecondary, marginTop: 4, ...TYPE.body },
    timestamp: { color: t.textSecondary, marginTop: 8, ...TYPE.helper },

    // The 1A/3A/6A control sits with the curve it scopes, right-aligned as in
    // the prototype's chart header.
    periodRow: { paddingHorizontal: 16, marginTop: 16, alignItems: 'flex-end' },
    // The 2px top rule is what separates one block from the next.
    riskCard: { marginTop: 24, paddingHorizontal: 16, paddingTop: 16, borderTopWidth: 2, borderTopColor: t.divider },
    cardTitle: { color: t.textPrimary, fontSize: 15, ...font(800), marginBottom: 12 },
    statRow: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 4, marginBottom: 12 },
    stat: { flex: 1 },
    statLabel: { color: t.textSecondary, ...TYPE.helper },
    statValue: { color: t.textPrimary, fontSize: 17, ...font(800), marginTop: 2, ...TABULAR },
    statSub: { color: t.textSecondary, marginTop: 1, ...TYPE.helper },

    sectorRow: { marginTop: 10 },
    sectorHeader: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 },
    sectorLabel: { color: t.textSecondary, ...TYPE.body },
    sectorWeight: { color: t.textPrimary, ...TYPE.bodyStrong, ...TABULAR },
    barTrack: { height: 6, backgroundColor: t.neutral300 ?? t.surfaceElevated, overflow: 'hidden' },
    barFill: { height: 6, backgroundColor: t.textPrimary },
    // Blocked order flow is not an allocation — it gets the accent, so the two
    // bar lists on this screen do not read alike.
    barBlocked: { height: 6, backgroundColor: t.accent500 ?? t.warning },
    flagText: { color: t.warning, fontSize: 12, lineHeight: 18, marginTop: 12, ...font(400) },
    freshness: { color: t.textSecondary, ...TYPE.helper },

    realizedValue: { fontSize: 26, ...font(800), marginTop: 2 },
    splitTrack: { flexDirection: 'row', height: 6, overflow: 'hidden', marginTop: 14, marginBottom: 6 },
    // The strategy block is fenced off from the blended stats above it: the two
    // answer different questions and must not read as one continuous list.
    exitBlock: { marginTop: 16, paddingTop: 12, borderTopWidth: 1, borderTopColor: t.divider },
    exitTitle: { color: t.textSecondary, ...TYPE.bodyStrong },
    sampleBadge: { color: t.textSecondary, fontSize: 11, ...font(600), ...TABULAR },
    exitRow: { flexDirection: 'row', alignItems: 'center', marginTop: 6 },
    exitLabel: { color: t.textSecondary, fontSize: 12, flex: 1, ...font(400) },
    exitCount: { color: t.textSecondary, fontSize: 12, width: 28, textAlign: 'right', ...font(400), ...TABULAR },
    exitPnl: { fontSize: 12, ...font(600), ...TABULAR, width: 90, textAlign: 'right' },
    splitRealized: { backgroundColor: t.textPrimary },
    splitOpen: { backgroundColor: t.neutral300 ?? t.surfaceElevated },

    // Rows in a ruled table, not stacked cards: 1px between names.
    posBlock: { paddingTop: 14, paddingBottom: 10, borderBottomWidth: 1, borderBottomColor: t.divider },
    row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
    posHead: { marginBottom: 8 },
    posTicker: { color: t.textPrimary, fontSize: 17, ...font(800) },
    posSector: { color: t.textSecondary, marginTop: 1, ...TYPE.helper },
    posField: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      paddingVertical: 3,
    },
    posFieldLabel: { color: t.textSecondary, ...TYPE.helper },
    posFieldValue: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    posFieldText: { color: t.textPrimary, fontSize: 13, ...font(600), ...TABULAR },
    posActions: { flexDirection: 'row', gap: 20, marginTop: 8 },
    ghost: { height: GHOST_H, justifyContent: 'center' },
    ghostText: { color: t.accent700 ?? t.accent, fontSize: 13, ...font(600) },
    // No italic: three Archivo faces are loaded and none of them is one, so
    // `fontStyle` here only buys a synthesized oblique — or Android's system
    // italic, which is the fallback this file is meant to avoid.
    disclaimer: { color: t.textSecondary, paddingHorizontal: 16, paddingVertical: 24, textAlign: 'center', ...TYPE.helper },
  });
