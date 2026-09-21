/**
 * Piyasa tahtası — the prototype's MARKET BOARD as its own screen.
 *
 * The board already existed, folded inside Sor as a disclosure the operator had
 * to open before it would even fetch. That made "what moved today" a sub-answer
 * to "which symbol do I want to ask about", which is backwards: the board is
 * how you decide there is a question at all. As its own route it fetches on
 * mount, keeps its own sort and universe, and hands the symbol back to Sor.
 *
 * The prototype, top to bottom (lines 379–402 of Trader.dc.html):
 *   1. title + sub;
 *   2. a three-way pill strip — Yükselenler · Düşenler · En çok işlem;
 *   3. the note about the filter, and the S&P 500 switch beside it;
 *   4. one clipped card of 60pt rows: symbol over company name on the left,
 *      price over a signed, arrowed, toned change on the right, and a chart
 *      button that is a SIBLING of the row's own press target, not nested in it;
 *   5. the footer that says what a tap does.
 *
 * Nothing here decides anything. `useMarketMovers` owns the request and its
 * caching, `formatUsd`/`formatPct` own the figures, and `pnlTone` owns the sign
 * → tone rule, exactly as the Sor board used them. The screen draws.
 *
 * One deliberate departure from the prototype: its filter note is a constant
 * that describes the filter as on, because its `marketUniverse` never changes
 * in the mock. Here the switch actually moves, so the note follows it — a line
 * reading "filtre açık" under a switch that is off would be the screen lying
 * about its own state.
 */

import { useMemo, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  ScrollView,
  ActivityIndicator,
  RefreshControl,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import Svg, { Path } from 'react-native-svg';
import { useRouter } from 'expo-router';

import {
  useMarketMovers,
  type MoverSort,
  type MoverUniverse,
} from '@/api/useTickerSearch';
import type { Mover } from '@/api/types';
import { Card } from '@/components/Card';
import { EmptyState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { TYPE, TABULAR, font } from '@/theme/type';
import { formatPct, formatUsd } from '@/utils/format';
import { pnlTone } from '@/utils/realized';
import { MIN_TOUCH_TARGET, hitSlopFor } from '@/utils/a11y';

type Palette = ReturnType<typeof useTheme>;

/** The prototype's tab strip, in its order. Defaults to `gainers`, as it does. */
const TABS: readonly { value: MoverSort; label: string }[] = [
  { value: 'gainers', label: 'Yükselenler' },
  { value: 'losers', label: 'Düşenler' },
  { value: 'volume', label: 'En çok işlem' },
];

/** The prototype's pill heights — under 44, so both carry hitSlop. */
const TAB_HEIGHT = 34;
const SWITCH_HEIGHT = 20;
const SWITCH_WIDTH = 34;
const KNOB = 16;
const KNOB_INSET = 2;
const CHART_BTN = 36;
/** The prototype's rows are 60px tall; every one of them clears the 44 floor. */
const ROW_HEIGHT = 60;

/**
 * Sign → colour. The same three-way the Sor board drew, routed through
 * `pnlTone` so exactly-flat stays neutral rather than being painted green by a
 * `>= 0` test — a 0.00% day is not an up day.
 */
function toneColor(t: Palette, pct: number): string {
  switch (pnlTone(pct)) {
    case 'up':
      return t.up;
    case 'down':
      return t.downText ?? t.down;
    default:
      return t.ink2 ?? t.textSecondary;
  }
}

/** ▲ / ▼ / — , matching the tone rather than the raw sign. */
function toneArrow(pct: number): string {
  switch (pnlTone(pct)) {
    case 'up':
      return '▲';
    case 'down':
      return '▼';
    default:
      return '•';
  }
}

function ChartGlyph({ color }: { color: string }) {
  return (
    <Svg
      width={16}
      height={16}
      viewBox="0 0 24 24"
      fill="none"
      stroke={color}
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <Path d="M3 3v18h18" />
      <Path d="M7 14l4-5 4 3 5-7" />
    </Svg>
  );
}

/**
 * One board row.
 *
 * Two sibling pressables, as in the prototype, rather than a chart button
 * nested inside the row's own target: nesting makes the outer press the
 * fallback for every miss on the inner one, so a thumb that lands a pixel off
 * the chart icon silently spends a model call on an analysis instead.
 */
function MarketRow({
  mover,
  divider,
  onAsk,
  onChart,
  styles,
  t,
}: {
  mover: Mover;
  divider: boolean;
  onAsk: () => void;
  onChart: () => void;
  styles: ReturnType<typeof makeStyles>;
  t: Palette;
}) {
  const change = mover.change_pct / 100;
  const color = toneColor(t, mover.change_pct);
  const spokenChange = `${formatPct(change, { signed: true })} değişim`;

  return (
    <View style={[styles.row, divider && styles.rowDivider]}>
      <Pressable
        onPress={onAsk}
        style={({ pressed }) => [styles.rowMain, pressed && styles.rowMainPressed]}
        accessibilityRole="button"
        accessibilityLabel={`${mover.ticker}, ${mover.name}, ${formatUsd(mover.price)}, ${spokenChange}`}
        accessibilityHint="Bu sembolü analize gönderir"
      >
        <View style={styles.rowText}>
          <Text style={styles.ticker} numberOfLines={1}>
            {mover.ticker}
          </Text>
          <Text style={styles.name} numberOfLines={1}>
            {mover.name}
          </Text>
        </View>
        <View style={styles.rowFigures}>
          <Text style={styles.price} numberOfLines={1}>
            {formatUsd(mover.price)}
          </Text>
          <Text style={[styles.change, { color }]} numberOfLines={1}>
            {`${toneArrow(mover.change_pct)} ${formatPct(change, { signed: true })}`}
          </Text>
        </View>
      </Pressable>

      <Pressable
        onPress={onChart}
        hitSlop={hitSlopFor(CHART_BTN)}
        style={({ pressed }) => [styles.chartBtn, pressed && styles.chartBtnPressed]}
        accessibilityRole="button"
        accessibilityLabel={`${mover.ticker} fiyat grafiğini aç`}
      >
        <ChartGlyph color={t.ink ?? t.textPrimary} />
      </Pressable>
    </View>
  );
}

export default function MarketScreen() {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const router = useRouter();

  const [sort, setSort] = useState<MoverSort>('gainers');
  const [universe, setUniverse] = useState<MoverUniverse>('sp500');
  const {
    data: movers,
    isLoading,
    isFetching,
    error,
    refetch,
  } = useMarketMovers(sort, universe, true);

  const rows = movers ?? [];
  const sp500 = universe === 'sp500';

  const goBack = () => {
    // Reachable as a deep link, where there is nothing to go back to.
    if (router.canGoBack()) router.back();
    else router.replace('/(tabs)/ask' as never);
  };

  // Same query-string form every other screen uses to hand a symbol over; Sor
  // and Grafik both pick it up as a one-shot deep link.
  const ask = (ticker: string) =>
    router.push(`/(tabs)/ask?ticker=${encodeURIComponent(ticker)}` as never);
  const chart = (ticker: string) =>
    router.push(`/(tabs)/charts?ticker=${encodeURIComponent(ticker)}` as never);

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl
            refreshing={isFetching && !isLoading}
            onRefresh={() => void refetch()}
            tintColor={t.ink2 ?? t.textSecondary}
          />
        }
      >
        <Pressable
          style={styles.backBtn}
          onPress={goBack}
          accessibilityRole="button"
          accessibilityLabel="Geri"
        >
          <Text style={styles.backText}>← Geri</Text>
        </Pressable>

        <View style={styles.head}>
          <Text style={styles.title} accessibilityRole="header">
            Piyasa tahtası
          </Text>
          <Text style={styles.subtitle}>Bugünün tahtası · dolar hacmine ve değişime göre</Text>
        </View>

        {/* The prototype's pill strip: one ink-filled pill on a --surface2
            track. Not the shared `Seg` — that control is bordered and fills
            with the accent, and this strip is the one place the board uses the
            ink fill the shell's tab bar uses. */}
        <View style={styles.tabs} accessibilityRole="tablist">
          {TABS.map((tab) => {
            const on = tab.value === sort;
            return (
              <Pressable
                key={tab.value}
                onPress={() => setSort(tab.value)}
                hitSlop={hitSlopFor(TAB_HEIGHT)}
                style={[styles.tab, on && styles.tabOn]}
                accessibilityRole="tab"
                accessibilityLabel={tab.label}
                accessibilityState={{ selected: on }}
              >
                <Text style={[styles.tabText, on && styles.tabTextOn]} numberOfLines={1}>
                  {tab.label}
                </Text>
              </Pressable>
            );
          })}
        </View>

        <View style={styles.filterRow}>
          {/* Defaults ON, and the note says why: unfiltered, the gainers board
              is penny stocks, and a board whose top row is an $8 name at +179%
              is one the operator learns to scroll past. */}
          <Text style={styles.note}>
            {sp500
              ? 'S&P 500 filtresi açık — filtresiz tahta çoğunlukla kuruş hisselerinden oluşur.'
              : 'S&P 500 filtresi kapalı — tahta çoğunlukla kuruş hisselerinden oluşabilir.'}
          </Text>
          <Pressable
            onPress={() => setUniverse(sp500 ? 'all' : 'sp500')}
            hitSlop={hitSlopFor(SWITCH_HEIGHT)}
            style={styles.switchBtn}
            accessibilityRole="switch"
            accessibilityLabel="Sadece S&P 500"
            accessibilityHint="Tahtayı S&P 500 üyeleriyle sınırlar"
            accessibilityState={{ checked: sp500 }}
          >
            <View style={[styles.track, sp500 && styles.trackOn]}>
              <View style={[styles.knob, sp500 && styles.knobOn]} />
            </View>
            <Text style={styles.switchLabel}>S&P 500</Text>
          </Pressable>
        </View>

        {error ? (
          <ErrorState
            title="Piyasa verisi alınamadı"
            detail={error}
            onRetry={() => void refetch()}
          />
        ) : isLoading ? (
          <View style={styles.loading}>
            <ActivityIndicator color={t.brand ?? t.accent} />
            <Text style={styles.loadingText}>Tahta yükleniyor…</Text>
          </View>
        ) : rows.length === 0 ? (
          <EmptyState
            title="Gösterilecek hareket yok"
            hint={
              sp500
                ? 'Bu seans için veri gelmedi. S&P 500 filtresini kapatmayı deneyebilirsin.'
                : 'Bu seans için veri gelmedi.'
            }
          />
        ) : (
          <Card padded={false} clip>
            {rows.map((m, i) => (
              <MarketRow
                key={m.ticker}
                mover={m}
                divider={i < rows.length - 1}
                onAsk={() => ask(m.ticker)}
                onChart={() => chart(m.ticker)}
                styles={styles}
                t={t}
              />
            ))}
          </Card>
        )}

        <Text style={styles.foot}>
          Bir satıra dokunmak sembolü analize gönderir. Grafik düğmesi fiyat grafiğini açar.
        </Text>
      </ScrollView>
    </SafeAreaView>
  );
}

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    screen: { flex: 1, backgroundColor: t.background },
    content: {
      paddingHorizontal: sh.space[3],
      paddingTop: sh.space[0],
      paddingBottom: sh.space[5],
    },

    backBtn: { minHeight: MIN_TOUCH_TARGET, justifyContent: 'center', alignSelf: 'flex-start' },
    backText: { ...TYPE.bodyStrong, fontSize: 15, ...font(800), color: t.textPrimary },

    head: { paddingTop: sh.space[1], paddingBottom: sh.space[2] },
    title: { ...TYPE.h2, fontSize: 26, letterSpacing: -0.5, color: t.textPrimary },
    subtitle: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, marginTop: sh.space[0] },

    tabs: {
      flexDirection: 'row',
      gap: 6,
      backgroundColor: t.surface2 ?? t.surfaceElevated,
      padding: sh.space[0],
      borderRadius: sh.radiusPill,
    },
    tab: {
      flex: 1,
      height: TAB_HEIGHT,
      borderRadius: sh.radiusPill,
      alignItems: 'center',
      justifyContent: 'center',
      paddingHorizontal: sh.space[1],
    },
    tabOn: { backgroundColor: t.ink ?? t.textPrimary },
    tabText: { ...TYPE.helper, fontSize: 12, ...font(600), color: t.ink2 ?? t.textSecondary },
    tabTextOn: { color: t.inkInv ?? t.background },

    filterRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: sh.space[2],
      marginTop: sh.space[2],
      marginBottom: sh.space[1],
    },
    note: { ...TYPE.helper, fontSize: 12, color: t.ink2 ?? t.textSecondary, flex: 1 },
    switchBtn: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1] },
    track: {
      width: SWITCH_WIDTH,
      height: SWITCH_HEIGHT,
      borderRadius: sh.radiusPill,
      backgroundColor: t.line2 ?? t.divider,
      justifyContent: 'center',
    },
    trackOn: { backgroundColor: t.brand ?? t.accent },
    knob: {
      position: 'absolute',
      left: KNOB_INSET,
      width: KNOB,
      height: KNOB,
      // A circle under Aurora, a square under a system whose radius is 0.
      borderRadius: sh.radiusPill ? KNOB / 2 : 0,
      backgroundColor: t.textPrimary,
    },
    knobOn: { left: SWITCH_WIDTH - KNOB - KNOB_INSET },
    switchLabel: { ...TYPE.helper, fontSize: 12, ...font(600), color: t.ink ?? t.textPrimary },

    loading: { paddingVertical: sh.space[4], alignItems: 'center', gap: sh.space[1] },
    loadingText: { ...TYPE.body, color: t.ink2 ?? t.textSecondary },

    row: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: sh.space[1],
      paddingHorizontal: sh.space[2],
      minHeight: ROW_HEIGHT,
    },
    rowDivider: { borderBottomWidth: sh.hairline, borderBottomColor: t.line ?? t.divider },
    rowMain: {
      flex: 1,
      flexDirection: 'row',
      alignItems: 'center',
      gap: sh.space[1],
      minWidth: 0,
      minHeight: MIN_TOUCH_TARGET,
      paddingVertical: sh.space[1],
      paddingLeft: sh.space[1],
    },
    rowMainPressed: { opacity: 0.6 },
    rowText: { flex: 1, minWidth: 0 },
    ticker: { ...TYPE.bodyStrong, fontSize: 15, ...font(800), color: t.textPrimary },
    name: { ...TYPE.helper, color: t.ink3 ?? t.textMuted, marginTop: 1 },
    rowFigures: { alignItems: 'flex-end' },
    price: { ...TYPE.bodyStrong, fontSize: 14, ...TABULAR, color: t.textPrimary },
    change: { ...TYPE.helper, fontSize: 12, ...font(600), ...TABULAR, marginTop: 1 },

    chartBtn: {
      width: CHART_BTN,
      height: CHART_BTN,
      borderRadius: sh.radiusSmall,
      borderWidth: sh.hairline,
      borderColor: t.line ?? t.divider,
      backgroundColor: t.paper ?? t.background,
      alignItems: 'center',
      justifyContent: 'center',
    },
    // The prototype's hover darkens the edge to --ink; on a phone that lands
    // on press.
    chartBtnPressed: { borderColor: t.ink ?? t.textPrimary },

    foot: {
      ...TYPE.helper,
      color: t.ink3 ?? t.textMuted,
      marginTop: sh.space[2],
      lineHeight: 16,
    },
  });
