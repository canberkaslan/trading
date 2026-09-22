/**
 * İşlem günlüğü — the realized ledger, screen by screen the counterpart to the
 * Portföy hero.
 *
 * Everything above the fold on this app is mark-to-market: the equity figure,
 * the daily P&L, the spark. This screen is the other half — of the round trips
 * that actually closed, what was banked, by which exit path, and on which day.
 *
 * Three things it shows, in the prototype's order:
 *   1. the header pair — the net figure and the trade count;
 *   2. the filter, one pill per exit path the book has actually taken;
 *   3. the rows, each expandable into the prototype's trade-detail cells.
 *
 * What it does NOT do is derive any of those numbers. `src/utils/realized.ts`
 * owns every reading here — the exit split (`exitBreakdown`), its TR copy
 * (`exitClassLabelTr`), whether the split even adds up (`attributionNote`),
 * which slice of history is being reported (`evalWindowNote`) and how stale the
 * ledger is (`reconcileFreshness`). Those rules are conservative on purpose and
 * this screen only draws them.
 *
 * Two constraints worth naming rather than quietly working around:
 *
 *  - The prototype's journal row opens a per-trade detail SCREEN keyed by
 *    `trade_id`. No such route exists in this app and inventing one here would
 *    put a second owner on the trade record, so the detail is an inline
 *    expansion of the row instead. The content is the prototype's verbatim:
 *    entry, exit, size, holding period, both timestamps and the exit-path
 *    sentence. The only navigation offered is the ticker's chart, a route that
 *    already exists and already takes `?ticker=`.
 *
 *  - `ClosedTrade` in `src/api/types.ts` has no `exit_class`, but
 *    `GET /v1/trades` returns one per row (`agent/api/routes/trades.py`, and
 *    `test_each_trade_carries_its_own_exit_class` pins it, nullable for rows
 *    written before attribution existed). Without it there is no per-row filter
 *    at all, only aggregate buckets. Rather than edit a shared type another
 *    agent may be holding, the field is read through a local widening below;
 *    the type owner should add it.
 */

import { View, Text, StyleSheet, ScrollView, Pressable, RefreshControl } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useCallback, useMemo, useState } from 'react';

import { useTrades } from '@/api/hooks';
import type { ClosedTrade } from '@/api/types';
import { Card } from '@/components/Card';
import { SectionHeader } from '@/components/SectionHeader';
import { DataRow } from '@/components/DataRow';
import { StatCell } from '@/components/StatCell';
import { Tag, type TagVariant } from '@/components/Tag';
import { ErrorState } from '@/components/ErrorState';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { TYPE, TABULAR, font } from '@/theme/type';
import { hitSlopFor, MIN_TOUCH_TARGET } from '@/utils/a11y';
import { formatUsd, formatPct } from '@/utils/format';
import { formatOrderDate } from '@/utils/orders';
import {
  attributionNote,
  evalWindowNote,
  exitBreakdown,
  exitClassLabelTr,
  pnlTone,
  reconcileFreshness,
  type Tone,
} from '@/utils/realized';

type Palette = ReturnType<typeof useTheme>;

/**
 * The per-row exit class the API sends and the shared type has not declared
 * yet. Optional and nullable exactly as the backend models it, so a deployment
 * that predates attribution simply yields `null` and the row renders without a
 * chip instead of claiming a path it does not know.
 */
type JournalTrade = ClosedTrade & { exit_class?: string | null };

/** The "everything" pill. Not an exit class — the absence of a filter. */
const ALL = 'all';

/** The prototype's filter pills are 34pt tall; hitSlop carries them to 44. */
const PILL_HEIGHT = 34;

/**
 * Chip tone per exit path, following the prototype: the paths that ended a
 * position at a loss by design (a stop, an operator flatten) are rose, the
 * strategy's own profitable leg is green, an agent decision is the brand mark,
 * and a path the app cannot name stays neutral — API drift must not read as a
 * warning.
 */
const EXIT_VARIANT: Record<string, TagVariant> = {
  take_profit: 'up',
  stop: 'down',
  flatten: 'down',
  decision_sell: 'brand',
  unknown: 'neutral',
};

const exitVariant = (exitClass: string | null | undefined): TagVariant =>
  EXIT_VARIANT[exitClass ?? ''] ?? 'neutral';

/**
 * What each exit path actually means, verbatim from the prototype's trade
 * detail. These are copy, not rules: the classification itself is the
 * backend's, and `exitClassLabelTr` owns the short label.
 */
const EXIT_NOTE: Record<string, string> = {
  take_profit: 'Kâr al bacağı broker tarafında tetiklendi — stratejinin planladığı çıkış.',
  stop: 'Koruyucu stop tetiklendi. Planlı bir çıkış; zarar planlanan risk bütçesi içinde.',
  decision_sell: 'Ajanlar aktif satış kararı verdi; emir onaydan geçti.',
  flatten:
    'Operatör FLATTEN_ALL ile kapattı — stratejinin çıkışı değil, karneye “karışık” olarak girer.',
  unknown: 'Kapatan emir broker tarafında silinmiş; çıkış yolu belirlenemiyor.',
};

const exitNote = (exitClass: string | null | undefined): string =>
  EXIT_NOTE[exitClass ?? ''] ??
  'Bu işleme çıkış yolu atanmamış — kırılıma dahil değil.';

/** Tone → ink. Neutral is deliberately the primary ink, never a grey. */
function toneColor(t: Palette, tone: Tone): string {
  if (tone === 'up') return t.up;
  if (tone === 'down') return t.downText ?? t.down;
  return t.textPrimary;
}

/**
 * The row's date column: "18 Eyl", the day without the clock.
 *
 * Derived from `formatOrderDate` rather than a second month table — the one in
 * `utils/orders` is already the app's TR month copy, and a screen that grows
 * its own can drift from it by a letter.
 */
export function journalDayLabel(iso: string | null | undefined): string {
  return formatOrderDate(iso).replace(/\s\d{2}:\d{2}$/, '');
}

export default function JournalScreen() {
  const t = useTheme();
  const sh = useShape();
  const router = useRouter();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);

  // No params: the same query key the Portföy tab uses, so the two screens
  // share one cached ledger instead of each fetching their own.
  const trades = useTrades();
  const [filter, setFilter] = useState<string>(ALL);
  const [openId, setOpenId] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await trades.refetch();
    } finally {
      setRefreshing(false);
    }
  }, [trades]);

  const data = trades.data;
  const stats = data?.stats;

  /** The exit split, ordered and stripped of empty paths by the helper. */
  const buckets = useMemo(() => exitBreakdown(data), [data]);

  /**
   * The filter pills: "Hepsi" plus one per path the book has actually taken.
   * Paths with no trades are not offered — a filter that can only ever return
   * nothing is a dead control, and the helper has already dropped them.
   */
  const filters = useMemo(
    () => [
      { key: ALL, label: 'Hepsi', count: stats?.trades ?? 0 },
      ...buckets.map((b) => ({
        key: b.exit_class,
        label: exitClassLabelTr(b.exit_class),
        count: b.trades,
      })),
    ],
    [buckets, stats],
  );

  /** Newest close first, which is the order the prototype lists them in. */
  const rows = useMemo(() => {
    const all = (data?.trades ?? []) as JournalTrade[];
    const picked = filter === ALL ? all : all.filter((x) => x.exit_class === filter);
    return picked.slice().sort((a, b) => b.closed_at_utc.localeCompare(a.closed_at_utc));
  }, [data, filter]);

  const netTone = pnlTone(stats?.net_pnl);
  const fresh = reconcileFreshness(data?.reconciled_at_utc, new Date());
  const windowNote = evalWindowNote(data);
  const attrNote = attributionNote(data);

  const header = (
    <>
      <Pressable
        style={styles.backBtn}
        onPress={() => router.back()}
        accessibilityRole="button"
        accessibilityLabel="Geri"
      >
        <Text style={styles.backText}>← Geri</Text>
      </Pressable>

      <View style={styles.headRow}>
        <View style={styles.headText}>
          <Text style={styles.title} accessibilityRole="header">
            İşlem günlüğü
          </Text>
          <Text style={styles.subtitle}>Kapanan işlemler</Text>
        </View>

        {stats ? (
          <StatCell
            align="right"
            size="md"
            label={`Net · ${stats.trades} işlem`}
            value={formatUsd(stats.net_pnl, { signed: true })}
            valueColor={toneColor(t, netTone)}
            accessibilityLabel={`Gerçekleşen net kâr zarar ${formatUsd(stats.net_pnl, {
              signed: true,
            })}, ${stats.trades} kapanan işlem`}
          />
        ) : null}
      </View>
    </>
  );

  if (trades.isLoading && !data) {
    return (
      <SafeAreaView style={styles.screen} edges={['top']}>
        <ScrollView contentContainerStyle={styles.content}>
          {header}
          <Card tone="dashed" style={styles.slot}>
            <Text style={styles.slotText}>Yükleniyor…</Text>
          </Card>
        </ScrollView>
      </SafeAreaView>
    );
  }

  if (trades.isError && !data) {
    return (
      <SafeAreaView style={styles.screen} edges={['top']}>
        <ScrollView contentContainerStyle={styles.content}>
          {header}
          <ErrorState
            title="İşlem günlüğü okunamadı"
            detail={trades.error}
            onRetry={() => void trades.refetch()}
          />
        </ScrollView>
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
        {header}

        {/* The filter. One pill per path the ledger actually contains, so a
            deployment with no attribution shows only "Hepsi" rather than five
            controls that all return the same list. */}
        {filters.length > 1 ? (
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.chipRow}
            style={styles.chipScroll}
          >
            {filters.map((f) => {
              const active = f.key === filter;
              return (
                <Pressable
                  key={f.key}
                  onPress={() => {
                    setFilter(f.key);
                    // A row expanded under the old filter may not be in the new
                    // list; leaving it open would silently reopen a different
                    // row when the filter comes back.
                    setOpenId(null);
                  }}
                  hitSlop={hitSlopFor(PILL_HEIGHT)}
                  style={({ pressed }) => [
                    styles.chip,
                    active && styles.chipActive,
                    pressed && !active && styles.chipPressed,
                  ]}
                  accessibilityRole="tab"
                  accessibilityState={{ selected: active }}
                  accessibilityLabel={`${f.label}, ${f.count} işlem`}
                >
                  <Text style={[styles.chipText, active && styles.chipTextActive]} numberOfLines={1}>
                    {`${f.label} · ${f.count}`}
                  </Text>
                </Pressable>
              );
            })}
          </ScrollView>
        ) : null}

        {/* The exit split. Aggregate, and separate from the rows on purpose:
            `stats` blends every path into one expectancy, and this card is the
            only place that says which path produced which part of it. */}
        {buckets.length > 0 ? (
          <>
            <SectionHeader title="Çıkış yolu" count={buckets.length} />
            <Card padded={false} clip>
              {buckets.map((b, i) => (
                <DataRow
                  key={b.exit_class}
                  chevron={false}
                  divider={i < buckets.length - 1}
                  title={exitClassLabelTr(b.exit_class)}
                  subtitle={`${b.trades} işlem · ${b.wins}K / ${b.losses}Z · ort. ${formatUsd(
                    b.avg_pnl,
                    { signed: true },
                  )}`}
                  value={formatUsd(b.net_pnl, { signed: true })}
                  valueColor={toneColor(t, pnlTone(b.net_pnl))}
                  valueHint={`%${Math.round(b.win_rate * 100)} kazanma`}
                />
              ))}
            </Card>
            {attrNote ? <Text style={styles.note}>⚠︎ {attrNote}</Text> : null}
          </>
        ) : attrNote ? (
          <Text style={styles.note}>⚠︎ {attrNote}</Text>
        ) : null}

        <SectionHeader title="Kapanan işlemler" count={rows.length} />

        {rows.length === 0 ? (
          <Card tone="dashed" style={styles.slot}>
            <Text style={styles.slotText}>
              {filter === ALL
                ? 'Henüz kapanan işlem yok — tüm P&L açık pozisyonlarda.'
                : 'Bu filtrede kapanan işlem yok.'}
            </Text>
          </Card>
        ) : (
          <Card padded={false} clip>
            {rows.map((row, i) => {
              const open = openId === row.trade_id;
              const tone = pnlTone(row.realized_pnl);
              const color = toneColor(t, tone);
              const label = row.exit_class ? exitClassLabelTr(row.exit_class) : null;
              const line = `${row.direction} ${row.quantity} · ${formatUsd(
                row.entry_price,
              )} → ${formatUsd(row.exit_price)} · ${row.holding_days} gün`;
              return (
                <View key={row.trade_id}>
                  <DataRow
                    divider={open ? false : i < rows.length - 1}
                    chevron={false}
                    onPress={() => setOpenId(open ? null : row.trade_id)}
                    title={row.ticker}
                    titleAfter={label ? <Tag label={label} variant={exitVariant(row.exit_class)} size="sm" /> : null}
                    subtitle={line}
                    // `trailing` rather than `value`/`valueHint`: the prototype's
                    // row carries a third line the two-line value column has no
                    // slot for — the close date, in muted ink beside the figure.
                    trailing={
                      <View style={styles.rowRight}>
                        <Text style={[styles.rowPnl, { color }]} numberOfLines={1}>
                          {formatUsd(row.realized_pnl, { signed: true })}
                        </Text>
                        <Text style={[styles.rowPct, { color }]} numberOfLines={1}>
                          {formatPct(row.realized_pnl_pct, { signed: true })}
                        </Text>
                        <Text style={styles.rowDate} numberOfLines={1}>
                          {journalDayLabel(row.closed_at_utc)}
                        </Text>
                      </View>
                    }
                    accessibilityLabel={`${row.ticker}, ${line}, gerçekleşen ${formatUsd(
                      row.realized_pnl,
                      { signed: true },
                    )}, ${formatPct(row.realized_pnl_pct, { signed: true })}${
                      label ? `, ${label}` : ''
                    }`}
                    accessibilityHint={open ? 'Detayı kapat' : 'İşlem detayını aç'}
                  />

                  {open ? (
                    <View style={[styles.detail, i < rows.length - 1 && styles.detailDivider]}>
                      <View style={styles.grid}>
                        <StatCell style={styles.gridCell} size="sm" label="Giriş" value={formatUsd(row.entry_price)} />
                        <StatCell style={styles.gridCell} size="sm" label="Çıkış" value={formatUsd(row.exit_price)} />
                        <StatCell style={styles.gridCell} size="sm" label="Adet" value={String(row.quantity)} />
                        <StatCell
                          style={styles.gridCell}
                          size="sm"
                          label="Süre"
                          value={`${row.holding_days} gün`}
                        />
                        <StatCell
                          style={styles.gridCell}
                          size="sm"
                          label="Açılış"
                          value={formatOrderDate(row.opened_at_utc)}
                        />
                        <StatCell
                          style={styles.gridCell}
                          size="sm"
                          label="Kapanış"
                          value={formatOrderDate(row.closed_at_utc)}
                        />
                      </View>

                      <Text style={styles.detailKicker}>Çıkış yolu</Text>
                      <Text style={styles.detailNote}>{exitNote(row.exit_class)}</Text>

                      <Pressable
                        onPress={() => router.push(`/(tabs)/charts?ticker=${row.ticker}` as never)}
                        style={({ pressed }) => [styles.chartBtn, pressed && styles.chartBtnPressed]}
                        accessibilityRole="button"
                        accessibilityLabel={`${row.ticker} grafiğini aç`}
                      >
                        <Text style={styles.chartBtnText}>Grafik →</Text>
                      </Pressable>
                    </View>
                  ) : null}
                </View>
              );
            })}
          </Card>
        )}

        {/* The footer is the prototype's `journalNote`, except every clause is
            a helper's sentence rather than a hard-coded date: which window is
            being reported, and how far behind the hourly reconcile is. */}
        {windowNote ? <Text style={styles.note}>{windowNote}</Text> : null}
        <Text style={[styles.note, fresh.stale ? styles.noteStale : null]}>
          {`${fresh.stale ? '⚠︎ ' : ''}Broker dolumlarından; son eşitleme ${fresh.label}.`}
        </Text>
      </ScrollView>
    </SafeAreaView>
  );
}

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    screen: { flex: 1, backgroundColor: t.background },
    content: { paddingHorizontal: sh.space[3], paddingBottom: sh.space[5] },

    backBtn: { minHeight: MIN_TOUCH_TARGET, justifyContent: 'center', alignSelf: 'flex-start' },
    backText: { ...TYPE.bodyStrong, fontSize: 15, ...font(800), color: t.textPrimary },

    headRow: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'flex-end',
      gap: sh.space[2],
      paddingTop: sh.space[0],
      paddingBottom: sh.space[2],
    },
    headText: { flexShrink: 1 },
    title: { ...TYPE.h2, fontSize: 26, letterSpacing: -0.5, color: t.textPrimary },
    subtitle: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, marginTop: sh.space[0] },

    // The pill strip bleeds to both edges, as in the prototype, so the last
    // chip is visibly cut rather than ending flush and reading as the end.
    chipScroll: { marginHorizontal: -sh.space[3] },
    chipRow: { paddingHorizontal: sh.space[3], gap: sh.space[0] + 2, paddingBottom: sh.space[1] },
    chip: {
      height: PILL_HEIGHT,
      paddingHorizontal: 14,
      borderRadius: sh.radiusPill,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
      alignItems: 'center',
      justifyContent: 'center',
    },
    chipActive: { backgroundColor: t.textPrimary, borderColor: t.textPrimary },
    chipPressed: { backgroundColor: t.surface2 ?? t.surfaceElevated },
    chipText: { fontSize: 12, ...font(600), ...TABULAR, color: t.textPrimary },
    chipTextActive: { color: t.background },

    rowRight: { alignItems: 'flex-end', minWidth: 96 },
    rowPnl: { ...TYPE.bodyStrong, fontSize: 14, ...TABULAR },
    rowPct: { ...TYPE.helper, ...TABULAR, marginTop: 1 },
    rowDate: { ...TYPE.helper, ...TABULAR, color: t.ink3 ?? t.textMuted, marginTop: 2 },

    detail: {
      paddingHorizontal: sh.space[3],
      paddingBottom: sh.space[3],
      backgroundColor: t.surface2 ?? t.surfaceElevated,
    },
    detailDivider: { borderBottomWidth: sh.hairline, borderBottomColor: t.line ?? t.divider },
    grid: { flexDirection: 'row', flexWrap: 'wrap', rowGap: sh.space[2], columnGap: sh.space[2] },
    // Two columns, which is the prototype's grid and the widest a six-cell
    // block can be without the timestamps wrapping on a small phone.
    gridCell: { flexBasis: '46%', flexGrow: 1 },

    detailKicker: { ...TYPE.kicker, color: t.ink3 ?? t.textMuted, marginTop: sh.space[2] },
    detailNote: { ...TYPE.body, color: t.textPrimary, lineHeight: 19, marginTop: sh.space[0] },

    chartBtn: {
      marginTop: sh.space[2],
      alignSelf: 'flex-start',
      minHeight: MIN_TOUCH_TARGET,
      paddingHorizontal: sh.space[3],
      justifyContent: 'center',
      borderRadius: sh.radius,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
      backgroundColor: t.surface,
    },
    chartBtnPressed: { borderColor: t.brand ?? t.accent },
    chartBtnText: { ...TYPE.bodyStrong, fontSize: 14, color: t.textPrimary },

    slot: { paddingVertical: sh.space[4], alignItems: 'center' },
    slotText: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, textAlign: 'center' },

    note: {
      ...TYPE.helper,
      color: t.ink3 ?? t.textMuted,
      lineHeight: 16,
      marginTop: sh.space[2],
    },
    noteStale: { color: t.warning },
  });
