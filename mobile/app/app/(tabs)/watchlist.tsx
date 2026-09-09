import { useEffect, useMemo, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  Pressable,
  ActivityIndicator,
  Keyboard,
  ScrollView,
  RefreshControl,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useQueryClient } from '@tanstack/react-query';
import Svg, { Path } from 'react-native-svg';

import { useDecisions, usePendingOrders, usePortfolio, usePrices } from '@/api/hooks';
import type { AgentDecision, Bar, OrderListItem, Position } from '@/api/types';
import { Tag } from '@/components/Tag';
import { useTheme } from '@/theme/useTheme';
import { ratingVariant } from '@/theme/rating';
import { font, TABULAR, TYPE } from '@/theme/type';
import { formatOrderDate } from '@/utils/orders';
import { formatPct, formatUsd, parseUtc } from '@/utils/format';
import { pnlTone, type Tone } from '@/utils/realized';
import { hitSlopFor, MIN_TOUCH_TARGET } from '@/utils/a11y';
import { toast } from '@/stores/toast';
import { useWatchStore, WATCH_CAP } from '@/stores/watchlist';

/**
 * Screen 6 — İzleme listesi.
 *
 * A name on this list is not a position and not an order: it is a name the
 * operator wants the daily run to look at. So the row's job is to answer, at a
 * glance, "is anything happening with it?" — the last price and move, what the
 * agents last said about it, and whether it has already turned into something
 * (an open position, or an order waiting for approval).
 *
 * Every one of those four answers comes from a query that another screen has
 * usually already warmed: prices, decisions, the portfolio snapshot and the
 * pending queue. Nothing here is a new endpoint, and the list itself is local —
 * the backend has no watchlist resource, so it lives in SecureStore
 * (`@/stores/watchlist`) and ships over-the-air.
 *
 * The three row actions keep the prototype's 36px ghost-button height and reach
 * the 44pt mobile minimum through `hitSlopFor` rather than by growing: six rows
 * of 44px controls would push the list off the screen, and the tap target is
 * what actually has to be 44 — a mis-tap here removes the wrong name.
 */

/**
 * The shortest window the price route serves — it clamps `days` to
 * `max(5, ...)`, so asking for two would spend a request on five bars anyway
 * and cache them under a key the server never used.
 */
const PRICE_DAYS = 5;

/** One request covers every row; 50 decisions is several runs' worth. */
const DECISION_LIMIT = 50;

/**
 * Today's move, as a fraction, from the last two closes.
 *
 * Deliberately not the series' own `change_pct`: that is the move across the
 * whole window, and the window is at least five bars — a name that fell 3%
 * today after a 4% week would read as "+1%" in a column labelled "Değişim".
 * Fewer than two bars is null, not zero: nothing to compare is not "unchanged".
 */
function dailyChange(bars: Bar[] | undefined): number | null {
  if (!bars || bars.length < 2) return null;
  const prev = bars[bars.length - 2];
  const last = bars[bars.length - 1];
  if (!prev || !last || !(prev.c > 0)) return null;
  return last.c / prev.c - 1;
}

function XIcon({ color }: { color: string }) {
  return (
    <Svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2} strokeLinecap="round">
      <Path d="M18 6 6 18" />
      <Path d="m6 6 12 12" />
    </Svg>
  );
}

export default function WatchlistScreen() {
  const t = useTheme();
  const styles = useMemo(() => makeStyles(t), [t]);
  const router = useRouter();

  const tickers = useWatchStore((s) => s.tickers);
  const hydrated = useWatchStore((s) => s.hydrated);
  const hydrate = useWatchStore((s) => s.hydrate);
  const add = useWatchStore((s) => s.add);
  const remove = useWatchStore((s) => s.remove);
  const [input, setInput] = useState('');

  // The store is read from disk here rather than in the shell: the list is only
  // ever needed on this screen, and the call is idempotent, so it stays correct
  // if the shell later hydrates it at startup alongside the inbox.
  useEffect(() => {
    void hydrate();
  }, [hydrate]);

  const portfolio = usePortfolio();
  const pending = usePendingOrders();
  const decisions = useDecisions({ limit: DECISION_LIMIT });
  const queryClient = useQueryClient();

  /**
   * Newest decision per ticker. The endpoint answers newest-first, but the
   * order is not part of its contract and picking the wrong row here would
   * caption a name with a stale rating — so compare timestamps rather than
   * trusting position.
   */
  const latestDecision = useMemo(() => {
    const byTicker = new Map<string, AgentDecision>();
    for (const d of decisions.data ?? []) {
      const prev = byTicker.get(d.ticker);
      if (!prev) {
        byTicker.set(d.ticker, d);
        continue;
      }
      const a = parseUtc(d.timestamp_utc)?.getTime() ?? 0;
      const b = parseUtc(prev.timestamp_utc)?.getTime() ?? 0;
      if (a > b) byTicker.set(d.ticker, d);
    }
    return byTicker;
  }, [decisions.data]);

  const positions = useMemo(() => {
    const byTicker = new Map<string, Position>();
    for (const p of portfolio.data?.positions ?? []) byTicker.set(p.ticker, p);
    return byTicker;
  }, [portfolio.data?.positions]);

  const pendingByTicker = useMemo(() => {
    const byTicker = new Map<string, OrderListItem>();
    for (const o of pending.data ?? []) if (!byTicker.has(o.ticker)) byTicker.set(o.ticker, o);
    return byTicker;
  }, [pending.data]);

  const equity = portfolio.data?.total_equity_usd ?? 0;

  const onAdd = () => {
    const { outcome, ticker } = add(input);
    switch (outcome) {
      case 'added':
        setInput('');
        Keyboard.dismiss();
        break;
      case 'duplicate':
        toast(`${ticker} zaten listede`);
        break;
      case 'invalid':
        toast('Geçerli bir sembol gir — örn. TSLA');
        break;
      case 'full':
        toast(`Liste dolu — en fazla ${WATCH_CAP} sembol`);
        break;
    }
  };

  const onRemove = (ticker: string) => {
    remove(ticker);
    toast(`${ticker} listeden çıkarıldı`);
  };

  const refreshing = portfolio.isRefetching || pending.isRefetching || decisions.isRefetching;
  const onRefresh = () => {
    void portfolio.refetch();
    void pending.refetch();
    void decisions.refetch();
    // The row's price lives in its own per-ticker query with a five-minute
    // staleTime, so none of the three above touches Son or Değişim — the two
    // figures a pull-to-refresh is actually asking for. One prefix key covers
    // every row; the server's own 5-minute cache absorbs the burst.
    void queryClient.invalidateQueries({ queryKey: ['prices'] });
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView
        contentContainerStyle={styles.scroll}
        keyboardShouldPersistTaps="handled"
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
            tintColor={t.textPrimary}
            colors={[t.textPrimary]}
          />
        }
      >
        <View>
          <Text style={styles.h2}>İzleme listesi</Text>
          {/* NOT "Günlük koşu evrenine eklenir", which is what the prototype says.
              The daily run's universe is US_UNIVERSE, fixed server-side in
              bulk_loader.py, and no endpoint accepts a watchlist — the list
              never leaves the device. The handoff's fidelity mandate covers
              colour, type, spacing and interaction; it does not license a
              money screen to promise a behaviour the backend does not have. */}
          <Text style={styles.subtitle}>
            Bu cihazda tutulur · {tickers.length} sembol · günlük koşu evrenini değiştirmez
          </Text>
        </View>

        <View style={styles.addRow}>
          <TextInput
            style={styles.input}
            value={input}
            onChangeText={setInput}
            placeholder="Sembol ekle — TSLA"
            placeholderTextColor={t.textSecondary}
            autoCapitalize="characters"
            autoCorrect={false}
            maxLength={6}
            returnKeyType="done"
            onSubmitEditing={onAdd}
            accessibilityLabel="İzleme listesine eklenecek sembol"
          />
          <Pressable
            style={styles.addBtn}
            onPress={onAdd}
            accessibilityRole="button"
            accessibilityLabel="Sembolü izleme listesine ekle"
          >
            <Text style={styles.addBtnText}>Ekle</Text>
          </Pressable>
        </View>

        {!hydrated ? (
          <ActivityIndicator color={t.textPrimary} style={styles.spinner} />
        ) : tickers.length === 0 ? (
          <Text style={styles.empty}>Liste boş. Bir sembol ekle — günlük koşu onu da analiz eder.</Text>
        ) : (
          <View style={styles.list}>
            {tickers.map((ticker) => (
              <WatchRow
                key={ticker}
                ticker={ticker}
                decision={latestDecision.get(ticker) ?? null}
                position={positions.get(ticker) ?? null}
                pending={pendingByTicker.has(ticker)}
                equity={equity}
                onChart={() => router.push(`/(tabs)/charts?ticker=${ticker}` as never)}
                onAnalyze={() => router.push(`/(tabs)/ask?ticker=${ticker}` as never)}
                onRemove={() => onRemove(ticker)}
              />
            ))}
          </View>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

/**
 * One name. Its own component because the price query is per-ticker: a hook
 * cannot be called in a loop, and one component per row is also what keeps a
 * slow quote from holding up the rest of the list.
 */
function WatchRow({
  ticker,
  decision,
  position,
  pending,
  equity,
  onChart,
  onAnalyze,
  onRemove,
}: {
  ticker: string;
  decision: AgentDecision | null;
  position: Position | null;
  pending: boolean;
  equity: number;
  onChart: () => void;
  onAnalyze: () => void;
  onRemove: () => void;
}) {
  const t = useTheme();
  const styles = useMemo(() => makeStyles(t), [t]);
  const { data } = usePrices(ticker, PRICE_DAYS);

  const change = dailyChange(data?.bars);
  const toneColors = useMemo(() => pnlToneColors(t), [t]);
  const changeColor = toneColors[pnlTone(change)];

  // Weight against total equity, the same denominator the concentration card
  // and the position table use, so "Pozisyon · 8.40%" means one thing app-wide.
  const weight = position && equity > 0 ? (position.quantity * position.current_price) / equity : null;

  return (
    <View style={styles.row}>
      <View style={styles.rowTop}>
        <Text style={styles.ticker}>{ticker}</Text>
        {decision ? (
          <Tag label={decision.rating} variant={ratingVariant(decision.rating)} />
        ) : (
          <Tag label="—" variant="neutral" />
        )}
        <Text style={styles.last}>{formatUsd(data?.last)}</Text>
        <Text style={[styles.change, { color: changeColor }]}>
          {formatPct(change, { signed: true })}
        </Text>
      </View>

      <View style={styles.rowBottom}>
        <View style={styles.meta}>
          <Text style={styles.date} numberOfLines={1}>
            {decision ? formatOrderDate(decision.timestamp_utc) : 'karar yok'}
          </Text>
          {/* The prototype's precedence is on the POSITION, not on the weight:
              a held name says "Pozisyon" even in the moment before the snapshot
              lands, rather than dropping to the pending order's label and
              reading as merely proposed. The weight joins the label only once
              equity is known — an unknown share is left unsaid, not shown as
              0.00%. */}
          {position ? (
            <Tag
              label={weight != null ? `Pozisyon · ${formatPct(weight)}` : 'Pozisyon'}
              variant="neutral"
            />
          ) : pending ? (
            <Tag label="Onay bekliyor" variant="outline" />
          ) : null}
        </View>

        <View style={styles.actions}>
          <Pressable
            style={styles.ghost}
            hitSlop={hitSlopFor(GHOST_H)}
            onPress={onChart}
            accessibilityRole="button"
            accessibilityLabel={`${ticker} grafiğini aç`}
          >
            <Text style={styles.ghostText}>Grafik</Text>
          </Pressable>
          <Pressable
            style={styles.ghost}
            hitSlop={hitSlopFor(GHOST_H)}
            onPress={onAnalyze}
            accessibilityRole="button"
            accessibilityLabel={`${ticker} analiz et`}
          >
            <Text style={styles.ghostText}>Analiz</Text>
          </Pressable>
          <Pressable
            style={styles.ghost}
            hitSlop={hitSlopFor(GHOST_H)}
            onPress={onRemove}
            accessibilityRole="button"
            accessibilityLabel={`${ticker} sembolünü izleme listesinden çıkar`}
          >
            <XIcon color={t.textSecondary} />
          </Pressable>
        </View>
      </View>
    </View>
  );
}

type Palette = ReturnType<typeof useTheme>;

/**
 * Tone → colour, palette-dependent because Modernist's P&L is the accounting
 * one: a gain is ink, a loss is accent-700 (the base accent is a fill colour,
 * too weak at 13px), flat is secondary. `pnlTone` decides WHICH tone; this only
 * paints it — the same split portfolio.tsx and charts.tsx use.
 */
const pnlToneColors = (t: Palette): Record<Tone, string> => ({
  up: t.up,
  down: t.downText ?? t.down,
  neutral: t.textSecondary,
});

/**
 * The rating's chip, in the Tag component's vocabulary.
 *
 * `ratingChip` stays the single source of truth for what a rating looks like —
 * this only asks which of Tag's four variants paints those exact colours, so a
 * retune of the rating buckets moves this with it. A rating `ratingChip` does
 * not recognise returns colours no variant matches, which is precisely when it
 * should outline: visibly not one of the five.
 */

/** The prototype's ghost-button height; `hitSlopFor` takes it to 44. */
const GHOST_H = 36;

const makeStyles = (t: Palette) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.background },
    scroll: { paddingTop: 20, paddingHorizontal: 16, paddingBottom: 24, gap: 14 },

    h2: { color: t.textPrimary, ...TYPE.h2 },
    subtitle: { color: t.textSecondary, marginTop: 4, ...TYPE.helper },

    addRow: { flexDirection: 'row', gap: 8 },
    input: {
      flex: 1,
      minHeight: MIN_TOUCH_TARGET,
      backgroundColor: t.surface,
      color: t.textPrimary,
      borderWidth: 1,
      borderColor: t.divider,
      paddingHorizontal: 12,
      fontSize: 15,
      letterSpacing: 1.5,
      ...font(600),
    },
    // .btn-primary — the accent fill, ground-coloured label.
    addBtn: {
      minHeight: MIN_TOUCH_TARGET,
      paddingHorizontal: 20,
      backgroundColor: t.accent,
      alignItems: 'center',
      justifyContent: 'center',
    },
    addBtnText: { color: t.background, fontSize: 14, ...font(800) },

    spinner: { marginTop: 24 },
    empty: { color: t.textSecondary, ...TYPE.body },

    // A section rule opens the list; each row closes with a 1px one.
    list: { borderTopWidth: 2, borderTopColor: t.divider },
    row: { paddingVertical: 12, gap: 6, borderBottomWidth: 1, borderBottomColor: t.divider },

    rowTop: { flexDirection: 'row', alignItems: 'center', gap: 10 },
    ticker: { color: t.textPrimary, fontSize: 16, ...font(800) },
    // Pushed right by the ticker/tag pair; the change keeps a fixed column so
    // the figures stay aligned down the list.
    last: { color: t.textPrimary, marginLeft: 'auto', fontSize: 13, ...font(600), ...TABULAR },
    change: { width: 62, textAlign: 'right', fontSize: 13, ...font(600), ...TABULAR },

    rowBottom: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    meta: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 6 },
    date: { color: t.textSecondary, flexShrink: 1, ...TYPE.helper, ...TABULAR },

    actions: { flexDirection: 'row', alignItems: 'center', gap: 10 },
    // .btn-ghost — accent text, no fill. accent-700 rather than the base
    // accent: this is 11px type, and the base accent is a 3.6:1 fill colour.
    ghost: { height: GHOST_H, paddingHorizontal: 2, alignItems: 'center', justifyContent: 'center' },
    ghostText: { color: t.accent700 ?? t.accent, fontSize: 11, ...font(600) },
  });
