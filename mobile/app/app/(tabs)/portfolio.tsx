/**
 * Portföy — the screen that explains what Bugün summarises.
 *
 * The prototype's order, top to bottom, and the reason for it: the value
 * header states what the book is worth; the curve says how it got there; the
 * eval scorecard says whether that record is good enough to matter; the three
 * cards under it say whether the strategy is still *choosing* (order flow),
 * what it has actually banked (realized) and how concentrated the risk is
 * (allocation); and the positions table says what it holds right now.
 *
 * Every figure on this screen is either straight off an endpoint or formatted
 * by `utils/format`. Every label and every tone comes from a helper —
 * `actionability`, `realized`, `concentration`, `equity`, `positions`. This
 * file draws them; it does not decide anything. Where the Aurora prototype and
 * a helper disagree (the sector bars, the single-name cap percentage, the gate
 * names), the helper wins and the prototype's version is not drawn.
 */

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
  useReadiness,
} from '@/api/hooks';
import type { EvalResult, Position } from '@/api/types';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { hitSlopFor } from '@/utils/a11y';
import { ErrorState } from '@/components/ErrorState';
import { EquityChart } from '@/components/EquityChart';
import { Card } from '@/components/Card';
import { SectionHeader } from '@/components/SectionHeader';
import { StatCell } from '@/components/StatCell';
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

// Tone -> colour is palette-dependent: in Modernist a gain is the ink colour,
// so these cannot be module constants.
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

/**
 * The floating tab bar's height plus air. Restated rather than imported from
 * `_layout.tsx`, because a route module's exports are the router's namespace
 * and not a place to hang shared constants.
 */
const TAB_BAR_CLEARANCE = 72;

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

/** A fixed-decimal figure that renders an em dash rather than "NaN". */
function fixed(n: number | null | undefined, digits: number): string {
  return n == null || Number.isNaN(n) ? '—' : n.toFixed(digits);
}

/** A signed percentage where the API already speaks percent, not fraction. */
function signedPctPoints(n: number | null | undefined, digits = 1): string {
  if (n == null || Number.isNaN(n)) return '—';
  return `${n >= 0 ? '+' : '−'}${Math.abs(n).toFixed(digits)}%`;
}

export default function PortfolioScreen() {
  const { t: tr } = useTranslation();
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const TONE_COLORS = useMemo(() => toneColors(t), [t]);
  const PNL_TONE_COLORS = useMemo(() => pnlToneColors(t), [t]);
  const FLOW_TONE_COLORS = useMemo(() => flowToneColors(t), [t]);
  const router = useRouter();
  const { data, isLoading, isError, error, isFetching, refetch } = usePortfolio();
  const [period, setPeriod] = useState<Period>('1M');
  const { data: evalData } = useEval(period);
  const { data: history, isLoading: historyLoading } = usePortfolioHistory(period);
  const { data: inception } = usePortfolioHistory(INCEPTION_PERIOD);
  const { data: spy } = usePrices('SPY', PERIOD_DAYS[period]);
  const { data: concentration } = useConcentration();
  const { data: realized } = useTrades();
  const { data: flow } = useActionability();
  const { data: readiness } = useReadiness();
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
      <SafeAreaView style={styles.screen} edges={['top']}>
        <View style={styles.center}><Text style={styles.muted}>Yükleniyor…</Text></View>
      </SafeAreaView>
    );
  }

  // A failed read and a not-yet-arrived one are different states, and folding
  // them together made the error screen lie twice over. `isLoading` is
  // first-load-only in react-query, so a query that is retrying after a failure
  // leaves it false while `error` is still null — and this branch then rendered
  // the generic "Sunucuya ulaşılamıyor" with no detail, for a request that had
  // not actually failed yet. Worse, once it HAD failed, ErrorState could not
  // classify the 401 it was never handed.
  if (isError) {
    return (
      <SafeAreaView style={styles.screen} edges={['top']}>
        <ErrorState detail={error} onRetry={refetch} />
      </SafeAreaView>
    );
  }

  if (!data) {
    return (
      <SafeAreaView style={styles.screen} edges={['top']}>
        <View style={styles.center}><Text style={styles.muted}>Yükleniyor…</Text></View>
      </SafeAreaView>
    );
  }

  // Accounting palette, via the util that owns the sign→tone rule: a gain is
  // ink, a loss is the accent, and exactly flat is neither. Restating
  // `>= 0 ? up : down` here would quietly disagree with `pnlTone` on zero.
  const dailyColor = PNL_TONE_COLORS[pnlTone(data.daily_pnl_usd)];
  const badge = evalData ? verdictTheme(evalData.verdict, t) : null;

  // Since the book opened — when the curve can be shown to reach that far. The
  // dollar leg is measured against live equity (the hero number right above it)
  // so the two can never disagree on screen; the label says which start it is
  // measured from, because a window return worn as an inception return is the
  // kind of number people quote.
  const sinceLabel = startsAtInception(inception, realized?.eval_start_utc)
    ? 'Başlangıçtan'
    : `Son ${PERIOD_LABELS[INCEPTION_PERIOD]}`;
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
  const mode = readiness?.trading_mode;
  const isLive = mode === 'live';
  const accountLabel =
    mode == null ? 'Alpaca hesabı' : isLive ? 'Alpaca LIVE hesabı' : 'Alpaca paper hesabı';

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={t.textSecondary} />
        }
      >
        {/* ── Value header ─────────────────────────────────────── */}
        <View style={styles.headRow}>
          <Text style={styles.kicker} numberOfLines={1}>
            Portföy değeri · {accountLabel}
          </Text>
          {/* Tri-state, matching StatusBanner: PAPER is a claim about where
              real money goes, and `mode` is null whenever readiness AND health
              both failed — including a plain 401. Collapsing null to PAPER
              would let an unauthenticated app assert the account is on paper. */}
          <Tag
            label={mode == null ? 'MOD ?' : isLive ? 'LIVE' : 'PAPER'}
            variant={isLive ? 'down' : 'neutral'}
            size="sm"
            caps
          />
        </View>

        <Text style={styles.equity} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.65}>
          {formatUsd(data.total_equity_usd)}
        </Text>

        {/* One row of three, in the prototype's order: since inception · today
            · cash. Each figure signed where it is a P&L delta, and tabular so
            the three stay on one baseline as they tick. One Text, not three
            Views, so a narrow phone reflows it as prose instead of orphaning a
            separator or clipping a figure. */}
        <Text style={styles.subRow}>
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

        <Text style={styles.timestamp}>
          Son güncelleme:{' '}
          {new Date(data.timestamp_utc).toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
          })}
          {isFetching ? ' · güncelleniyor…' : ''}
        </Text>

        {/* ── Curve ────────────────────────────────────────────── */}
        <Card style={styles.card}>
          <View style={styles.chartHead}>
            <View style={styles.legend}>
              <View style={[styles.legendDash, { backgroundColor: t.brand ?? t.textPrimary }]} />
              <Text style={styles.legendText}>Portföy</Text>
              <View
                style={[styles.legendDash, { backgroundColor: t.ink3 ?? t.textMuted, marginLeft: 12 }]}
              />
              <Text style={styles.legendText}>SPY</Text>
            </View>
            <Seg options={PERIOD_OPTIONS} value={period} onChange={setPeriod} />
          </View>

          {history && history.points.length > 1 ? (
            <EquityChart history={history} spy={spy} />
          ) : (
            <Text style={styles.slotText}>
              {historyLoading ? 'Eğri yükleniyor…' : 'Bu pencerede eğri çizecek kadar gün yok.'}
            </Text>
          )}
        </Card>

        {/* ── Eval scorecard ───────────────────────────────────── */}
        {evalData && badge ? (
          <EvalCard
            data={evalData}
            verdictColor={badge.color}
            verdictEmoji={badge.emoji}
            qualifier={badgeQualifier}
            inertRunDays={flow?.inert_run_days}
          />
        ) : null}

        {/* ── Order flow ───────────────────────────────────────── */}
        {flow ? (() => {
          // Every number above this card is computed from equity, and a frozen
          // basket still has an equity curve. This card is the only thing on
          // the screen that can tell "working" apart from "stuck".
          const meta = actionabilityVerdictMeta(flow.verdict);
          const rate = submitRate(flow);
          const reasons = topReasons(flow.by_reason);
          const note = inertiaNote(flow);
          return (
            <Card style={styles.card}>
              <View style={styles.cardHead}>
                <Text style={styles.cardTitle}>Emir akışı</Text>
                <Text style={styles.freshness}>son {flow.window_days} gün</Text>
              </View>

              <View style={styles.grid}>
                <StatCell
                  style={styles.gridCell}
                  size="sm"
                  label="Durum"
                  value={meta.label}
                  hint={`${flow.run_days} çalışma günü`}
                  valueColor={FLOW_TONE_COLORS[meta.tone]}
                  accessibilityLabel={`Emir akışı durumu: ${meta.label}`}
                />
                <StatCell
                  style={styles.gridCell}
                  size="sm"
                  label="Broker'a giden"
                  value={submitRatioLabel(flow)}
                  hint={rate == null ? 'emir yok' : formatPct(rate)}
                />
                <StatCell
                  style={styles.gridCell}
                  size="sm"
                  label="Son gönderim"
                  value={lastSubmitLabel(flow.last_submitted_at_utc, new Date())}
                  hint="broker onayı"
                  // "Donmuş is a warning" is `actionabilityVerdictMeta`'s rule.
                  // Testing `flow.verdict === 'inert'` here would be a second
                  // copy of it — one that keeps this figure neutral on the day
                  // the backend renames the state or grows a fourth one.
                  valueColor={meta.tone === 'warning' ? FLOW_TONE_COLORS[meta.tone] : undefined}
                />
              </View>

              {reasons.map((r) => (
                <View key={r.reason} style={styles.barRow}>
                  <View style={styles.barHead}>
                    <Text style={styles.barLabel} numberOfLines={2}>{r.label}</Text>
                    <Text style={styles.barValue}>{r.count}</Text>
                  </View>
                  <View style={styles.barTrack}>
                    {/* Blocked order flow is not an allocation — it gets the
                        warning colour, so the two bar lists on this screen do
                        not read alike. */}
                    <View
                      style={[styles.barFill, { width: `${r.share * 100}%`, backgroundColor: t.warning }]}
                    />
                  </View>
                </View>
              ))}

              {note ? <Text style={styles.flagText}>⚠︎ {note}</Text> : null}
            </Card>
          );
        })() : null}

        {/* ── Realized ─────────────────────────────────────────── */}
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
            <Card style={styles.card}>
              <View style={styles.cardHead}>
                <Text style={styles.cardTitle}>Gerçekleşen</Text>
                <Text style={[styles.freshness, fresh.stale ? { color: t.warning } : null]}>
                  {fresh.stale ? '⚠︎ ' : ''}{fresh.label}
                </Text>
              </View>

              <Text
                style={[styles.bigFigure, { color: PNL_TONE_COLORS[pnlTone(s.net_pnl)] }]}
                accessibilityLabel={`Gerçekleşen net kâr zarar ${formatUsd(s.net_pnl, { signed: true })}, ${s.trades} kapanan işlem`}
              >
                {formatUsd(s.net_pnl, { signed: true })}
              </Text>
              <Text style={styles.meta}>
                {s.trades} kapanan işlem · {s.wins}K / {s.losses}Z
                {s.scratches > 0 ? ` / ${s.scratches}B` : ''}
              </Text>
              {windowNote ? <Text style={styles.meta}>{windowNote}</Text> : null}

              <View style={styles.grid}>
                <StatCell
                  style={styles.gridCell}
                  size="sm"
                  label="Kazanma"
                  value={formatWinRate(s)}
                  hint="oranı"
                  valueColor={PNL_TONE_COLORS[sampleTone(s.win_rate - 0.5, s.trades)]}
                />
                <StatCell
                  style={styles.gridCell}
                  size="sm"
                  label="Beklenti"
                  value={formatUsd(s.expectancy, { signed: true })}
                  // Named as a blend the moment the split exists, so this
                  // number stops standing in for the agent's exit record.
                  hint={strat.status === 'unavailable' ? 'işlem başı' : 'işlem başı · karışık'}
                  valueColor={PNL_TONE_COLORS[sampleTone(s.expectancy, s.trades)]}
                />
                <StatCell
                  style={styles.gridCell}
                  size="sm"
                  label="Kâr faktörü"
                  value={formatProfitFactor(s.profit_factor)}
                  hint="brüt K/Z"
                  valueColor={
                    PNL_TONE_COLORS[
                      sampleTone(s.profit_factor == null ? null : s.profit_factor - 1, s.trades)
                    ]
                  }
                />
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
                  <View style={styles.cardHead}>
                    <Text style={styles.meta}>Gerçekleşen {formatUsd(s.net_pnl, { signed: true })}</Text>
                    <Text style={styles.meta}>Açık {formatUsd(open, { signed: true })}</Text>
                  </View>
                </>
              ) : null}

              {/* The agent's own exits, separated from operator flattens: the
                  two answer different questions and must not read as one
                  continuous list. */}
              <View style={styles.exitBlock}>
                <View style={styles.cardHead}>
                  <Text style={styles.subTitle}>Stratejinin çıkışları</Text>
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
                  <View style={styles.grid}>
                    <StatCell
                      style={styles.gridCell}
                      size="sm"
                      label="Net"
                      value={formatUsd(strat.bucket.net_pnl, { signed: true })}
                      valueColor={
                        PNL_TONE_COLORS[sampleTone(strat.bucket.net_pnl, strat.bucket.trades)]
                      }
                    />
                    <StatCell
                      style={styles.gridCell}
                      size="sm"
                      label="Kazanma"
                      value={formatWinRate({
                        win_rate: strat.bucket.win_rate,
                        trades: strat.bucket.trades,
                      })}
                      valueColor={
                        PNL_TONE_COLORS[sampleTone(strat.bucket.win_rate - 0.5, strat.bucket.trades)]
                      }
                    />
                    <StatCell
                      style={styles.gridCell}
                      size="sm"
                      label="Beklenti"
                      value={formatUsd(strat.bucket.avg_pnl, { signed: true })}
                      valueColor={
                        PNL_TONE_COLORS[sampleTone(strat.bucket.avg_pnl, strat.bucket.trades)]
                      }
                    />
                  </View>
                ) : null}

                <Text style={styles.meta}>{strat.note}</Text>

                {exits.length > 0 ? (
                  <View style={styles.exitList}>
                    <Text style={styles.exitHead}>Çıkış yolları</Text>
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
            </Card>
          );
        })() : null}

        {/* ── Risk & dağılım ───────────────────────────────────── */}
        {(() => {
          const sectors = sectorAllocation(data.positions, data.total_equity_usd);
          if (!concentration && sectors.length === 0) return null;
          const maxWeight = sectors[0]?.weightPct ?? 0;
          const div = concentration ? diversificationLabel(concentration.effective_n) : null;
          const topTone = concentration ? topWeightTone(concentration.top_weight_pct) : 'up';
          return (
            <Card style={styles.card}>
              <View style={styles.cardHead}>
                <Text style={styles.cardTitle}>Risk & dağılım</Text>
              </View>
              {concentration ? (
                <View style={styles.grid}>
                  <StatCell
                    style={styles.gridCell}
                    size="sm"
                    label="Çeşitlilik"
                    value={div ? div.label : '—'}
                    hint={`${concentration.effective_n.toFixed(1)} etkin isim`}
                    valueColor={div ? TONE_COLORS[div.tone] : undefined}
                  />
                  <StatCell
                    style={styles.gridCell}
                    size="sm"
                    label="En yüksek"
                    value={formatPct(concentration.top_weight_pct / 100)}
                    // The cap's percentage is `topWeightTone`'s to know; naming
                    // it here would be a second copy of it, free to drift.
                    hint="tek isim"
                    valueColor={TONE_COLORS[topTone]}
                  />
                  <StatCell
                    style={styles.gridCell}
                    size="sm"
                    label="İlk 3"
                    value={formatPct(concentration.top3_weight_pct / 100)}
                    hint="toplam ağırlık"
                  />
                </View>
              ) : null}

              {sectors.map((s) => (
                <View key={s.label} style={styles.barRow}>
                  <View style={styles.barHead}>
                    <Text style={styles.barLabel}>{s.label}</Text>
                    <Text style={styles.barValue}>{formatPct(s.weightPct / 100)}</Text>
                  </View>
                  <View style={styles.barTrack}>
                    {/* One colour for every sector. The prototype reddens a
                        sector above 30%, but no helper owns a sector cap, and
                        inventing the threshold here is exactly the drift the
                        util layer exists to prevent. */}
                    <View
                      style={[
                        styles.barFill,
                        {
                          width: `${maxWeight > 0 ? (s.weightPct / maxWeight) * 100 : 0}%`,
                          backgroundColor: t.brand ?? t.textPrimary,
                        },
                      ]}
                    />
                  </View>
                </View>
              ))}

              {concentration && concentration.flags.length > 0 ? (
                <Text style={styles.flagText}>
                  ⚠ {concentration.flags.length} isim tek-isim tavanının üstünde
                </Text>
              ) : null}
            </Card>
          );
        })()}

        {/* ── Positions ────────────────────────────────────────── */}
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
            <>
              <SectionHeader title={tr('portfolio.positions')} count={rows.length} />
              {overCapCount > 0 ? (
                <Text style={styles.overCap}>{overCapCount} isim tavan üstü</Text>
              ) : null}
              {rows.length === 0 ? (
                <Card tone="dashed" style={styles.slot}>
                  <Text style={styles.slotText}>
                    Açık pozisyon yok. Günlük koşu yeni pozisyon açtığında burada görünür.
                  </Text>
                </Card>
              ) : (
                <Card padded={false} clip>
                  {rows.map((r, i) => (
                    <PositionRow
                      key={r.p.ticker}
                      position={r.p}
                      weightPct={r.weightPct}
                      overCap={r.overCap}
                      divider={i < rows.length - 1}
                      onAnalyze={() => router.push(`/(tabs)/ask?ticker=${r.p.ticker}` as never)}
                      onChart={() => router.push(`/(tabs)/charts?ticker=${r.p.ticker}` as never)}
                    />
                  ))}
                </Card>
              )}
            </>
          );
        })()}

        <Text style={styles.disclaimer}>{tr('disclaimer.short')}</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

/**
 * The eval scorecard.
 *
 * The verdict and the six figures come off `/v1/eval` unchanged; the PASS/FAIL
 * tone on Sharpe and Max DD is read from `gates[].passed` rather than
 * re-derived. That is not fussiness: `max_dd_pct` arrives negative while
 * `gate_max_dd_pct` is the positive 15, so a local `value < gate` comparison
 * is true for every drawdown ever recorded and Max DD could never colour as a
 * miss. The backend compares magnitudes; reading its answer is the only way
 * this card and the gate list under it cannot drift apart.
 *
 * Gate NAMES are the backend's own strings, not translated here. The prototype
 * writes them in Turkish with the thresholds baked in ("≥ 10 işlem günü"), and
 * a threshold restated in a screen is a threshold that goes stale the day the
 * server's changes.
 */
function EvalCard({
  data,
  verdictColor,
  verdictEmoji,
  qualifier,
  inertRunDays,
}: {
  data: EvalResult;
  verdictColor: string;
  verdictEmoji: string;
  qualifier: string | null;
  inertRunDays?: number | null;
}) {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const PNL = useMemo(() => pnlToneColors(t), [t]);

  const gatePassed = (name: string): boolean | null =>
    data.gates?.find((g) => g.name === name)?.passed ?? null;
  const fail = t.downText ?? t.down;
  const sharpeColor = gatePassed('Sharpe') === false ? fail : undefined;
  const ddColor = gatePassed('Max drawdown') === false ? fail : undefined;
  const alpha = data.spy_return_pct == null ? null : data.total_return_pct - data.spy_return_pct;

  return (
    <Card style={styles.card}>
      <View style={styles.evalHead}>
        <View style={styles.evalHeadText}>
          <Text style={styles.kickerPlain}>Eval kararı</Text>
          <Text style={styles.meta}>
            {data.days} / {data.days_required} işlem günü
          </Text>
        </View>
        <Text
          style={[styles.verdict, { color: verdictColor }]}
          accessibilityLabel={
            qualifier
              ? `Eval kararı ${data.verdict}, ancak kitap ${inertRunDays} çalışma günüdür donmuş`
              : `Eval kararı ${data.verdict}`
          }
        >
          {verdictEmoji} {data.verdict}
        </Text>
      </View>

      {data.verdict === 'TOO EARLY' && data.provisional_verdict ? (
        <Text style={styles.meta}>
          Eğilim:{' '}
          <Text style={styles.metaStrong}>{data.provisional_verdict}</Text>
          {data.days_remaining > 0 ? ` · karara ${data.days_remaining} işlem günü` : ''}
        </Text>
      ) : null}

      <View style={styles.grid}>
        <StatCell
          style={styles.gridCell}
          size="sm"
          label="Sharpe"
          value={fixed(data.sharpe, 2)}
          hint={`> ${data.gate_sharpe}`}
          valueColor={sharpeColor}
        />
        <StatCell
          style={styles.gridCell}
          size="sm"
          label="Sortino"
          value={fixed(data.sortino, 2)}
          hint="aşağı yön"
        />
        <StatCell
          style={styles.gridCell}
          size="sm"
          label="Maks. düşüş"
          value={`${fixed(data.max_dd_pct, 1)}%`}
          hint={`< ${data.gate_max_dd_pct}%`}
          valueColor={ddColor}
        />
      </View>
      <View style={styles.grid}>
        <StatCell
          style={styles.gridCell}
          size="sm"
          label="Calmar"
          value={fixed(data.calmar, 2)}
          hint="getiri / düşüş"
        />
        <StatCell
          style={styles.gridCell}
          size="sm"
          label="Getiri"
          value={signedPctPoints(data.total_return_pct)}
          hint={`${data.days} g`}
          valueColor={PNL[pnlTone(data.total_return_pct)]}
        />
        {alpha != null ? (
          <StatCell
            style={styles.gridCell}
            size="sm"
            label="α vs SPY"
            value={signedPctPoints(alpha)}
            hint={`SPY ${signedPctPoints(data.spy_return_pct)}`}
            valueColor={PNL[pnlTone(alpha)]}
          />
        ) : (
          <View style={styles.gridCell} />
        )}
      </View>

      {data.gates?.length ? (
        <View style={styles.gateList}>
          {data.gates.map((g) => {
            const icon = g.passed == null ? '·' : g.passed ? '✓' : '✗';
            const fg = g.passed == null ? t.ink3 ?? t.textMuted : g.passed ? t.up : fail;
            const bg =
              g.passed == null
                ? t.surface2 ?? t.surfaceElevated
                : g.passed
                  ? t.upSoft ?? t.surfaceElevated
                  : t.downSoft ?? t.surfaceElevated;
            return (
              <View key={g.key ?? g.name} style={styles.gateRow}>
                <View style={[styles.gateMark, { backgroundColor: bg }]}>
                  <Text style={[styles.gateIcon, { color: fg }]}>{icon}</Text>
                </View>
                <Text style={styles.gateName} numberOfLines={2}>{g.name}</Text>
                <Text style={styles.gateDetail}>{g.detail}</Text>
              </View>
            );
          })}
        </View>
      ) : null}

      {data.reasons.length ? <Text style={styles.meta}>{data.reasons.join(' · ')}</Text> : null}

      {/* The one mark that outranks the verdict itself: a GO over a book that
          has submitted nothing for days. Every gate above is computed from the
          equity curve, which a frozen book still has. */}
      {qualifier ? <Text style={styles.flagText}>⚠ {qualifier}</Text> : null}
    </Card>
  );
}

/** The prototype's ghost-button height; `hitSlopFor` takes it to 44. */
const GHOST_H = 36;

/**
 * One position, as the prototype's 60px row reads — with nothing dropped.
 *
 * The prototype shows ticker + cap chip, a meta line, value/weight and P&L.
 * The screen it replaces also showed Son and Stop and offered Grafik and
 * Analiz, and those are not decoration: a position with no protective leg is
 * the thing an operator most needs to see. So the row keeps the prototype's
 * three-column head and carries the rest on a second meta line and an action
 * strip under it.
 */
function PositionRow({
  position: p,
  weightPct,
  overCap,
  divider,
  onAnalyze,
  onChart,
}: {
  position: Position;
  weightPct: number;
  overCap: boolean;
  divider: boolean;
  onAnalyze: () => void;
  onChart: () => void;
}) {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  // Accounting palette through the util that owns the rule — the same call the
  // header and the Gerçekleşen card make, so one flat position cannot render
  // green here and neutral there.
  const pnlColor = pnlToneColors(t)[pnlTone(p.unrealized_pnl)];

  return (
    // `accessible={false}`: the block stays one tap target for Sor, but the two
    // actions inside remain their own elements for a screen reader.
    <Pressable
      style={({ pressed }) => [
        styles.posBlock,
        divider && styles.posDivider,
        pressed && styles.posPressed,
      ]}
      onPress={onAnalyze}
      accessible={false}
    >
      <View style={styles.posTop}>
        <View style={styles.posMain}>
          <View style={styles.posTitleRow}>
            <Text style={styles.posTicker} numberOfLines={1}>{p.ticker}</Text>
            {overCap ? <Tag label="tavan" variant="down" size="sm" caps /> : null}
          </View>
          <Text style={styles.posMeta} numberOfLines={1}>
            {sectorLabelTr(p.sector)} · {p.quantity} lot · ort. {formatUsd(p.avg_entry_price)}
          </Text>
          {/* The stop leg lives on the broker order, not on the position: a
              book with no bracket answers 0, which must read as "no stop",
              never as a $0.00 one. */}
          <Text style={styles.posMeta} numberOfLines={1}>
            Son {formatUsd(p.current_price)} · stop {formatUsd(positionStop(p))}
          </Text>
        </View>

        <View style={styles.posCol}>
          <Text style={styles.posValue}>{formatUsd(p.quantity * p.current_price)}</Text>
          <Text style={styles.posHint}>{formatPct(weightPct / 100)}</Text>
        </View>

        <View style={styles.posColWide}>
          <Text style={[styles.posValue, { color: pnlColor }]}>
            {formatUsd(p.unrealized_pnl, { signed: true })}
          </Text>
          <Text style={[styles.posHint, { color: pnlColor }]}>
            {formatPct(p.unrealized_pnl_pct, { signed: true })}
          </Text>
        </View>
      </View>

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

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    screen: { flex: 1, backgroundColor: t.background },
    content: { paddingHorizontal: sh.space[3], paddingBottom: TAB_BAR_CLEARANCE },
    center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: sh.space[4] },
    muted: { ...TYPE.body, color: t.textSecondary },

    // ── Value header ──────────────────────────────────────────
    headRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: sh.space[1],
      paddingTop: sh.space[1],
    },
    kicker: { ...TYPE.kicker, color: t.ink3 ?? t.textMuted, flexShrink: 1 },
    kickerPlain: { ...TYPE.kicker, color: t.ink3 ?? t.textMuted },
    equity: {
      ...font(800),
      ...TABULAR,
      fontSize: 44,
      letterSpacing: -1.2,
      color: t.textPrimary,
      marginTop: sh.space[0],
      marginBottom: sh.space[1],
    },
    subRow: { ...TYPE.helper, fontSize: 12, lineHeight: 20, color: t.ink2 ?? t.textSecondary },
    subLabel: { ...TYPE.helper, fontSize: 12, color: t.ink2 ?? t.textSecondary },
    subValue: { fontSize: 12, ...font(600), ...TABULAR, color: t.textPrimary },
    subSep: { fontSize: 12, color: t.ink3 ?? t.textMuted },
    timestamp: { ...TYPE.helper, color: t.ink3 ?? t.textMuted, marginTop: sh.space[1] },

    // ── Cards ─────────────────────────────────────────────────
    card: { marginTop: sh.space[2] },
    cardHead: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'baseline',
      gap: sh.space[1],
    },
    cardTitle: { ...TYPE.section, color: t.textPrimary },
    subTitle: { ...TYPE.bodyStrong, color: t.ink2 ?? t.textSecondary },
    freshness: { ...TYPE.helper, color: t.ink3 ?? t.textMuted },
    meta: { ...TYPE.helper, fontSize: 12, lineHeight: 18, color: t.ink2 ?? t.textSecondary, marginTop: 4 },
    metaStrong: { ...TYPE.helper, fontSize: 12, ...font(600), color: t.textPrimary },
    bigFigure: { ...font(800), ...TABULAR, fontSize: 30, letterSpacing: -0.6, marginTop: sh.space[1] },
    flagText: { ...TYPE.helper, fontSize: 12, lineHeight: 18, color: t.warning, marginTop: sh.space[2] },

    // The prototype's three-up figure grid.
    grid: { flexDirection: 'row', gap: sh.space[1], marginTop: sh.space[2] },
    gridCell: { flex: 1 },

    // ── Chart card ────────────────────────────────────────────
    chartHead: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      gap: sh.space[1],
      flexWrap: 'wrap',
    },
    legend: { flexDirection: 'row', alignItems: 'center' },
    legendDash: { width: 14, height: 2, marginRight: 6, borderRadius: sh.radiusSmall },
    legendText: { ...TYPE.helper, color: t.ink2 ?? t.textSecondary },

    // ── Eval ──────────────────────────────────────────────────
    evalHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: sh.space[2] },
    evalHeadText: { flex: 1, minWidth: 0 },
    verdict: { ...font(800), fontSize: 26, letterSpacing: -0.6 },
    gateList: {
      marginTop: sh.space[2],
      paddingTop: sh.space[2],
      borderTopWidth: sh.hairline,
      borderTopColor: t.line ?? t.divider,
      gap: sh.space[1],
    },
    gateRow: { flexDirection: 'row', alignItems: 'center', gap: sh.space[2] },
    gateMark: {
      width: 18,
      height: 18,
      borderRadius: sh.radiusPill > 0 ? 9 : 0,
      alignItems: 'center',
      justifyContent: 'center',
    },
    gateIcon: { fontSize: 11, ...font(800) },
    gateName: { ...TYPE.body, color: t.textPrimary, flex: 1 },
    gateDetail: { ...TYPE.helper, fontSize: 12, ...TABULAR, color: t.ink2 ?? t.textSecondary },

    // ── Bars (order flow + sectors) ───────────────────────────
    barRow: { marginTop: sh.space[2] },
    barHead: { flexDirection: 'row', justifyContent: 'space-between', gap: sh.space[1], marginBottom: 3 },
    barLabel: { ...TYPE.helper, fontSize: 12, color: t.ink2 ?? t.textSecondary, flexShrink: 1 },
    barValue: { ...TYPE.helper, fontSize: 12, ...TABULAR, color: t.textPrimary },
    barTrack: {
      height: 4,
      borderRadius: sh.radiusPill,
      overflow: 'hidden',
      backgroundColor: t.surface2 ?? t.surfaceElevated,
    },
    barFill: { height: 4, borderRadius: sh.radiusPill },

    // ── Realized ──────────────────────────────────────────────
    splitTrack: {
      flexDirection: 'row',
      height: 6,
      borderRadius: sh.radiusPill,
      overflow: 'hidden',
      marginTop: sh.space[3],
      marginBottom: sh.space[0],
    },
    splitRealized: { backgroundColor: t.brand ?? t.textPrimary },
    splitOpen: { backgroundColor: t.surface2 ?? t.surfaceElevated },
    exitBlock: {
      marginTop: sh.space[3],
      paddingTop: sh.space[2],
      borderTopWidth: sh.hairline,
      borderTopColor: t.line ?? t.divider,
    },
    sampleBadge: { ...TYPE.helper, ...font(600), ...TABULAR, color: t.ink3 ?? t.textMuted },
    exitList: { marginTop: sh.space[2] },
    exitHead: { ...TYPE.helper, fontSize: 12, ...font(600), color: t.ink2 ?? t.textSecondary, marginBottom: 4 },
    exitRow: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1], paddingVertical: 3 },
    exitLabel: { ...TYPE.helper, fontSize: 12, color: t.ink2 ?? t.textSecondary, flex: 1 },
    exitCount: { ...TYPE.helper, fontSize: 12, ...TABULAR, color: t.ink3 ?? t.textMuted, width: 24, textAlign: 'right' },
    exitPnl: { fontSize: 12, ...font(600), ...TABULAR, width: 90, textAlign: 'right' },

    // ── Positions ─────────────────────────────────────────────
    overCap: { ...TYPE.helper, color: t.warning, marginBottom: sh.space[1] },
    posBlock: { paddingHorizontal: sh.space[3], paddingVertical: sh.space[2] },
    posDivider: { borderBottomWidth: sh.hairline, borderBottomColor: t.line ?? t.divider },
    posPressed: { backgroundColor: t.surface2 ?? t.surfaceElevated },
    posTop: { flexDirection: 'row', alignItems: 'center', gap: sh.space[2] },
    posMain: { flex: 1, minWidth: 0 },
    posTitleRow: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1] },
    posTicker: { ...TYPE.bodyStrong, fontSize: 15, color: t.textPrimary, flexShrink: 1 },
    posMeta: { ...TYPE.helper, ...TABULAR, color: t.ink3 ?? t.textMuted, marginTop: 2 },
    posCol: { alignItems: 'flex-end' },
    posColWide: { alignItems: 'flex-end', minWidth: 74 },
    posValue: { ...TYPE.bodyStrong, fontSize: 14, ...TABULAR, color: t.textPrimary },
    posHint: { ...TYPE.helper, ...TABULAR, color: t.ink3 ?? t.textMuted, marginTop: 1 },
    posActions: { flexDirection: 'row', gap: sh.space[4], marginTop: sh.space[1] },
    ghost: { height: GHOST_H, justifyContent: 'center' },
    ghostText: { ...TYPE.body, ...font(600), color: t.brand ?? t.accent },

    // ── Slots ─────────────────────────────────────────────────
    slot: { paddingVertical: sh.space[4], alignItems: 'center' },
    slotText: {
      ...TYPE.body,
      color: t.ink2 ?? t.textSecondary,
      textAlign: 'center',
      paddingVertical: sh.space[3],
    },

    disclaimer: {
      ...TYPE.helper,
      color: t.ink3 ?? t.textMuted,
      textAlign: 'center',
      lineHeight: 16,
      marginTop: sh.space[4],
    },
  });
