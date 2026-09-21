/**
 * Bugün — the first screen in the prototype, and the statement of the visual
 * language every other screen copies.
 *
 * Three blocks, in the prototype's order:
 *   1. the date kicker and the greeting;
 *   2. the portfolio hero — one dark slab with a brand glow, the equity figure
 *      at 40px, two direction pills and a 22-day spark;
 *   3. the pending-approval list, which is the only thing on this screen that
 *      asks the operator to do something.
 *
 * What it deliberately does NOT do is decide anything. Every figure here is
 * either straight off `/v1/portfolio` or formatted by `utils/format`, and the
 * pending rows are the same `OrderListItem` the Emirler tab lists — joined to
 * their decision only to borrow the rating chip. A screen whose whole job is to
 * be the front page must not become a second place where "is this order stale"
 * is answered.
 *
 * The hero is a button, as in the prototype: the front page summarises, the
 * Portföy tab explains.
 */

import { View, Text, StyleSheet, ScrollView, RefreshControl, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useCallback, useMemo, useState } from 'react';
import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';
import Svg, { Path, Defs, RadialGradient, Stop, Circle } from 'react-native-svg';

import {
  usePortfolio,
  usePortfolioHistory,
  usePendingOrders,
  useDecisions,
  useReadiness,
} from '@/api/hooks';
import type { AgentDecision, EquityPoint, OrderListItem } from '@/api/types';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { TYPE, TABULAR, font } from '@/theme/type';
import { Card } from '@/components/Card';
import { DataRow } from '@/components/DataRow';
import { SectionHeader } from '@/components/SectionHeader';
import { Tag } from '@/components/Tag';
import { ErrorState } from '@/components/ErrorState';
import { ratingVariant } from '@/theme/rating';
import { formatUsd, formatPct, relativeAgeTr, parseUtc } from '@/utils/format';
import { orderActionLabel } from '@/utils/a11y';

/**
 * The floating tab bar's height plus air. Stated here rather than imported from
 * `_layout.tsx` because a route module's exports are the router's namespace,
 * not a place to hang shared constants.
 */
const TAB_BAR_CLEARANCE = 72;

/**
 * The inception window.
 *
 * `/v1/portfolio/history` trims every curve at the day the book opened, so the
 * longest window the API offers is inception for any book younger than it —
 * the same reasoning (and the same query key, so TanStack dedupes) as the
 * Portföy tab. The spark shows the tail of it, not the whole thing.
 */
const INCEPTION_PERIOD = '6M';
/** The prototype's spark is 22 sessions — roughly a trading month. */
const SPARK_POINTS = 22;

/**
 * TR dates written out rather than handed to `Intl`. Hermes ships a trimmed ICU
 * on some builds, where `toLocaleDateString('tr-TR')` silently falls back to
 * English and the front page greets the operator with "Monday · 21 September".
 * Twelve strings are cheaper than that failure mode.
 */
const TR_DAYS = ['Pazar', 'Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi'] as const;
const TR_MONTHS = [
  'Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
  'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık',
] as const;

export function todayLabel(now: Date): string {
  const day = TR_DAYS[now.getDay()] ?? '';
  const month = TR_MONTHS[now.getMonth()] ?? '';
  return `${day} · ${now.getDate()} ${month} ${now.getFullYear()}`;
}

/** The prototype hard-codes "İyi akşamlar."; the clock is free and honest. */
export function greetingTr(now: Date): string {
  const h = now.getHours();
  if (h < 6) return 'İyi geceler.';
  if (h < 12) return 'Günaydın.';
  if (h < 18) return 'İyi günler.';
  return 'İyi akşamlar.';
}

/**
 * The spark path, normalised into the 360×56 box the prototype draws it in.
 * Returns null rather than a degenerate path when there is nothing to draw, so
 * a book with one data point shows no chart instead of a flat line implying a
 * flat month.
 */
export function sparkPath(points: readonly EquityPoint[], w = 360, h = 56, pad = 3):
  | { line: string; area: string }
  | null {
  if (points.length < 2) return null;
  const values = points.map((p) => p.equity);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const xy = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w;
    const y = pad + (1 - (v - min) / span) * (h - 2 * pad);
    return [x, y] as const;
  });
  const line = xy.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join('');
  const area = `M0 ${h}${xy.map(([x, y]) => `L${x.toFixed(1)} ${y.toFixed(1)}`).join('')}L${w} ${h}Z`;
  return { line, area };
}

export default function TodayScreen() {
  const t = useTheme();
  const sh = useShape();
  const router = useRouter();
  const { t: tr } = useTranslation();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);

  const portfolio = usePortfolio();
  const history = usePortfolioHistory(INCEPTION_PERIOD);
  const pending = usePendingOrders();
  // Only to borrow the rating chip and the entry price; the orders themselves
  // are authoritative for everything else.
  const decisions = useDecisions({ limit: 60 });
  const readiness = useReadiness();

  const [refreshing, setRefreshing] = useState(false);
  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await Promise.all([portfolio.refetch(), pending.refetch(), history.refetch(), decisions.refetch()]);
    } finally {
      setRefreshing(false);
    }
  }, [portfolio, pending, history, decisions]);

  const now = new Date();
  const snapshot = portfolio.data;

  const decisionById = useMemo(() => {
    const map = new Map<string, AgentDecision>();
    for (const d of decisions.data ?? []) map.set(d.decision_id, d);
    return map;
  }, [decisions.data]);

  const curvePoints = history.data?.points;
  const spark = useMemo(
    () => sparkPath((curvePoints ?? []).slice(-SPARK_POINTS)),
    [curvePoints],
  );

  /*
   * "Başlangıçtan" is the history endpoint's own start/end pair, not a figure
   * this screen derives: the same two numbers the Portföy tab quotes.
   *
   * The two percentages on this card are NOT in the same unit, which is a trap
   * worth naming rather than tidying away. `/v1/portfolio` returns
   * `daily_pnl_pct` as a fraction (the Portföy tab passes it to `formatPct`
   * raw) and `/v1/portfolio/history` returns `total_return_pct` as a percent
   * (EquityChart divides it by 100). Both call sites predate this screen and
   * agree with their own endpoint, so this one matches them rather than
   * normalising one of them behind everyone's back.
   */
  const sinceUsd =
    history.data ? history.data.end_equity - history.data.start_equity : null;
  const sincePct = history.data?.total_return_pct ?? null;

  const live = readiness.data?.trading_mode === 'live';
  const accountLabel =
    readiness.data?.trading_mode == null
      ? 'Alpaca hesabı'
      : live
        ? 'Alpaca LIVE hesabı'
        : 'Alpaca paper hesabı';

  const pendingRows: OrderListItem[] = pending.data ?? [];

  // The portfolio query is the only one this screen cannot render without.
  // A failed spark or a missing rating degrades; a missing equity figure is
  // the screen.
  if (portfolio.isLoading && !snapshot) {
    return (
      <SafeAreaView style={styles.screen} edges={['top']}>
        <View style={styles.center}>
          <Text style={styles.muted}>Yükleniyor…</Text>
        </View>
      </SafeAreaView>
    );
  }

  if (portfolio.isError && !snapshot) {
    return (
      <SafeAreaView style={styles.screen} edges={['top']}>
        <ErrorState detail={portfolio.error} onRetry={() => void portfolio.refetch()} />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={t.textSecondary} />
        }
      >
        <Text style={styles.dateKicker}>{todayLabel(now)}</Text>
        <Text style={styles.greeting} accessibilityRole="header">
          {greetingTr(now)}
        </Text>

        {/* The hero. A button in the prototype too: the front page summarises,
            the Portföy tab explains. */}
        <Pressable
          onPress={() => router.push('/(tabs)/portfolio' as never)}
          style={({ pressed }) => [styles.hero, pressed && styles.heroPressed]}
          accessibilityRole="button"
          accessibilityLabel={`Portföy değeri ${formatUsd(snapshot?.total_equity_usd)}, ${accountLabel}. Portföy ekranını aç.`}
        >
          {/* The prototype's radial glow. RN has no gradient primitive, so it is
              drawn with the SVG the spark already needs rather than by adding a
              gradient dependency to the native build. */}
          <Svg style={styles.glow} width="100%" height="100%" pointerEvents="none">
            <Defs>
              <RadialGradient id="heroGlow" cx="50%" cy="50%" r="50%">
                <Stop offset="0%" stopColor={t.brand ?? t.accent} stopOpacity={0.55} />
                <Stop offset="70%" stopColor={t.brand ?? t.accent} stopOpacity={0} />
              </RadialGradient>
            </Defs>
            <Circle cx="88%" cy="-6%" r="110" fill="url(#heroGlow)" />
          </Svg>

          <View style={styles.heroTopRow}>
            <Text style={styles.heroKicker}>Portföy değeri</Text>
            <Text style={styles.heroAccount}>{accountLabel}</Text>
          </View>

          <Text style={styles.equity} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.7}>
            {formatUsd(snapshot?.total_equity_usd)}
          </Text>

          <View style={styles.pills}>
            {sinceUsd != null ? (
              <Tag
                variant={sinceUsd >= 0 ? 'up' : 'down'}
                numeric
                label={`${sinceUsd >= 0 ? '▲' : '▼'} ${formatUsd(sinceUsd, { signed: true })}${
                  sincePct != null ? ` (${formatPct(sincePct / 100, { signed: true })})` : ''
                } · Başlangıçtan`}
              />
            ) : null}
            {snapshot ? (
              <Tag
                variant={snapshot.daily_pnl_usd >= 0 ? 'up' : 'down'}
                numeric
                label={`${snapshot.daily_pnl_usd >= 0 ? '▲' : '▼'} ${formatUsd(snapshot.daily_pnl_usd, {
                  signed: true,
                })} (${formatPct(snapshot.daily_pnl_pct, { signed: true })}) · Bugün`}
              />
            ) : null}
          </View>

          {spark ? (
            <Svg
              viewBox="0 0 360 56"
              preserveAspectRatio="none"
              width="100%"
              height={52}
              style={styles.spark}
              pointerEvents="none"
            >
              <Path d={spark.area} fill={t.brandSoft ?? t.surfaceElevated} opacity={0.65} />
              <Path d={spark.line} fill="none" stroke={t.brand ?? t.accent} strokeWidth={2} />
            </Svg>
          ) : null}
        </Pressable>

        <SectionHeader
          title="Onay bekleyen"
          count={pending.data ? pendingRows.length : undefined}
          actionLabel={pendingRows.length > 0 ? 'Kuyruğu aç' : undefined}
          onAction={pendingRows.length > 0 ? () => router.push('/(tabs)/orders' as never) : undefined}
          actionAccessibilityLabel="Onay bekleyen emirlerin tamamını aç"
        />

        {pending.isError ? (
          <ErrorState
            title="Onay kuyruğu okunamadı"
            detail={pending.error}
            onRetry={() => void pending.refetch()}
          />
        ) : pending.isLoading ? (
          <Card tone="dashed" style={styles.slot}>
            <Text style={styles.slotText}>Yükleniyor…</Text>
          </Card>
        ) : pendingRows.length === 0 ? (
          <Card tone="dashed" style={styles.slot}>
            <Text style={styles.slotText}>
              Onay bekleyen emir yok. Günlük koşu bir emri onaya düşürdüğünde burada görünür.
            </Text>
          </Card>
        ) : (
          <View style={styles.list}>
            {pendingRows.map((o) => {
              const d = decisionById.get(o.decision_id) ?? null;
              const notional = d?.entry_price != null ? d.entry_price * o.quantity : null;
              const submitted = parseUtc(o.submitted_at_utc);
              const buy = o.side === 'BUY';
              return (
                <Card key={o.order_id} padded={false} onPress={() => router.push(`/approve/${o.order_id}` as never)}
                  accessibilityLabel={orderActionLabel(o, 'review')}>
                  <DataRow
                    chevron={false}
                    title={o.ticker}
                    titleAfter={
                      d ? <Tag label={d.rating} variant={ratingVariant(d.rating)} size="sm" caps /> : null
                    }
                    subtitle={`${o.side} ${o.quantity} · ${o.order_type} · stop ${formatUsd(o.stop_loss)}`}
                    value={formatUsd(notional)}
                    valueHint={submitted ? relativeAgeTr(now.getTime() - submitted.getTime()) : undefined}
                    leading={
                      <View
                        style={[
                          styles.side,
                          { backgroundColor: (buy ? t.upSoft : t.downSoft) ?? t.surfaceElevated },
                        ]}
                      >
                        <Text style={[styles.sideText, { color: buy ? t.up : (t.downText ?? t.down) }]}>
                          {buy ? 'AL' : 'SAT'}
                        </Text>
                      </View>
                    }
                  />
                </Card>
              );
            })}
          </View>
        )}

        <Text style={styles.disclaimer}>{tr('disclaimer.short')}</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    screen: { flex: 1, backgroundColor: t.background },
    content: { paddingHorizontal: sh.space[3], paddingBottom: TAB_BAR_CLEARANCE },
    center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
    muted: { ...TYPE.body, color: t.textSecondary },

    dateKicker: { ...TYPE.kicker, color: t.ink3 ?? t.textMuted, paddingTop: sh.space[1] },
    greeting: { ...TYPE.h2, fontSize: 28, letterSpacing: -0.55, color: t.textPrimary, marginBottom: sh.space[2] },

    /*
     * The prototype's hero is `#18181b` on a light page — a slab darker than
     * everything around it. Aurora's page ground is already the darkest thing
     * in the system, so the slab inverts direction and becomes the ELEVATED
     * surface: still the one rectangle that is not the page, which is the
     * property that made it the hero.
     */
    hero: {
      backgroundColor: t.surfaceElevated,
      borderRadius: sh.radius,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
      paddingHorizontal: sh.space[3],
      paddingTop: sh.space[3],
      paddingBottom: sh.space[2],
      overflow: 'hidden',
      shadowColor: t.shadowColor,
      shadowOpacity: 0.28,
      shadowRadius: 32,
      shadowOffset: { width: 0, height: 12 },
      elevation: 8,
    },
    heroPressed: { borderColor: t.brand ?? t.accent },
    glow: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 },
    heroTopRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', gap: sh.space[1] },
    heroKicker: { ...TYPE.helper, fontSize: 12, ...font(600), color: t.ink2 ?? t.textSecondary },
    heroAccount: { ...TYPE.helper, color: t.ink3 ?? t.textMuted },
    equity: {
      ...font(800),
      ...TABULAR,
      fontSize: 40,
      letterSpacing: -1.2,
      color: t.textPrimary,
      marginTop: sh.space[1],
      marginBottom: sh.space[2],
    },
    pills: { flexDirection: 'row', flexWrap: 'wrap', gap: sh.space[1] },
    spark: { marginTop: sh.space[2] },

    list: { gap: sh.space[1] },
    side: { width: 40, height: 40, borderRadius: sh.radiusSmall, alignItems: 'center', justifyContent: 'center' },
    sideText: { fontSize: 12, ...font(600), ...TABULAR },

    slot: { paddingVertical: sh.space[4], alignItems: 'center' },
    slotText: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, textAlign: 'center' },

    disclaimer: {
      ...TYPE.helper,
      color: t.ink3 ?? t.textMuted,
      textAlign: 'center',
      lineHeight: 16,
      marginTop: sh.space[4],
    },
  });
