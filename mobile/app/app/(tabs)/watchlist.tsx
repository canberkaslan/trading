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
import { ErrorState } from '@/components/ErrorState';
import type { AgentDecision, Bar, OrderListItem, Position } from '@/api/types';
import { Card } from '@/components/Card';
import { Tag } from '@/components/Tag';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { ratingVariant } from '@/theme/rating';
import { font, TABULAR, TYPE } from '@/theme/type';
import { formatOrderDate } from '@/utils/orders';
import { formatPct, formatUsd, parseUtc } from '@/utils/format';
import { pnlTone, type Tone } from '@/utils/realized';
import { hitSlopFor, MIN_TOUCH_TARGET } from '@/utils/a11y';
import { toast } from '@/stores/toast';
import { useWatchStore, WATCH_CAP } from '@/stores/watchlist';

/**
 * Screen 6 — İzleme listesi, in Aurora.
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
 * What the Aurora port changed is only the drawing. The Modernist version was a
 * ruled table: a 2px section rule, then hairline-separated rows on the page
 * ground. The prototype's watchlist is a STACK OF CARDS — each name is its own
 * `--surface` rectangle with its own actions, because each name is an
 * independent decision and the three buttons under it act on that name alone.
 * Rows sharing one ruled block read as a table you scan; cards read as a set of
 * things you act on, which is what this screen is. Behaviour, hooks, helpers,
 * labels and tone rules are untouched.
 *
 * The three row actions keep the prototype's 36px ghost-button height and reach
 * the 44pt mobile minimum through `hitSlopFor` rather than by growing: three
 * rows of 44px controls per card would push the list off the screen, and the
 * tap target is what actually has to be 44 — a mis-tap here removes the wrong
 * name.
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
 * The floating tab bar's height plus air. Stated here rather than imported from
 * `_layout.tsx` because a route module's exports are the router's namespace,
 * not a place to hang shared constants.
 */
const TAB_BAR_CLEARANCE = 72;

/** The prototype's ghost-button height; `hitSlopFor` takes it to 44. */
const GHOST_H = 36;

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
    <Svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2} strokeLinecap="round">
      <Path d="M18 6 6 18" />
      <Path d="m6 6 12 12" />
    </Svg>
  );
}

export default function WatchlistScreen() {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
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

  /**
   * The ticker list is local, so this screen renders complete and healthy even
   * when every server read has failed — and each server-derived field then
   * degrades into a POSITIVE claim: "karar yok" for a decision we were not
   * allowed to fetch, no position tag for a name the operator is actually
   * holding. A watchlist that quietly drops the position on a held name is the
   * worst version of this, so the failure is stated once at the top and the
   * rows stop asserting absence.
   */
  const serverDown = portfolio.isError || decisions.isError || pending.isError;
  const serverError = portfolio.error ?? decisions.error ?? pending.error;

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
    <SafeAreaView style={styles.screen} edges={['top']}>
      <ScrollView
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={t.textSecondary} />
        }
      >
        {/* The prototype's h1 with the count as a lighter span beside it: the
            count is data and it changes, so baking it into the heading string
            would make "İzleme listesi 0" read as a different screen from
            "İzleme listesi 7". */}
        <Text style={styles.title} accessibilityRole="header">
          İzleme listesi
          {hydrated ? <Text style={styles.titleCount}>{`  ${tickers.length}`}</Text> : null}
        </Text>
        {/* NOT "Günlük koşu evrenine eklenir", which is what the prototype says.
            The daily run's universe is US_UNIVERSE, fixed server-side in
            bulk_loader.py, and no endpoint accepts a watchlist — the list
            never leaves the device. The handoff's fidelity mandate covers
            colour, type, spacing and interaction; it does not license a
            money screen to promise a behaviour the backend does not have. */}
        <Text style={styles.subtitle}>Bu cihazda tutulur · günlük koşu evrenini değiştirmez</Text>

        <View style={styles.addRow}>
          <TextInput
            style={styles.input}
            value={input}
            onChangeText={setInput}
            placeholder="Sembol ekle · NVDA"
            placeholderTextColor={t.ink3 ?? t.textMuted}
            autoCapitalize="characters"
            autoCorrect={false}
            maxLength={6}
            returnKeyType="done"
            onSubmitEditing={onAdd}
            accessibilityLabel="İzleme listesine eklenecek sembol"
          />
          <Pressable
            style={({ pressed }) => [styles.addBtn, pressed && styles.addBtnPressed]}
            onPress={onAdd}
            accessibilityRole="button"
            accessibilityLabel="Sembolü izleme listesine ekle"
          >
            <Text style={styles.addBtnText}>+ Ekle</Text>
          </Pressable>
        </View>

        {serverDown ? (
          <ErrorState
            title="Sunucu verileri okunamadı"
            detail={serverError}
            onRetry={() => {
              void portfolio.refetch();
              void decisions.refetch();
              void pending.refetch();
            }}
          />
        ) : null}

        {!hydrated ? (
          <ActivityIndicator color={t.textPrimary} style={styles.spinner} />
        ) : tickers.length === 0 ? (
          <Card tone="dashed" style={styles.slot}>
            <Text style={styles.slotText}>
              Liste boş. Bir sembol ekle ya da grafikten “İzle”ye dokun.
            </Text>
          </Card>
        ) : (
          <View style={styles.list}>
            {tickers.map((ticker) => (
              <WatchRow
                key={ticker}
                ticker={ticker}
                decision={latestDecision.get(ticker) ?? null}
                serverDown={serverDown}
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
 * One name, one card. Its own component because the price query is per-ticker:
 * a hook cannot be called in a loop, and one component per row is also what
 * keeps a slow quote from holding up the rest of the list.
 */
function WatchRow({
  ticker,
  decision,
  serverDown,
  position,
  pending,
  equity,
  onChart,
  onAnalyze,
  onRemove,
}: {
  ticker: string;
  decision: AgentDecision | null;
  serverDown: boolean;
  position: Position | null;
  pending: boolean;
  equity: number;
  onChart: () => void;
  onAnalyze: () => void;
  onRemove: () => void;
}) {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const { data } = usePrices(ticker, PRICE_DAYS);

  const change = dailyChange(data?.bars);
  const toneColors = useMemo(() => pnlToneColors(t), [t]);
  const changeColor = toneColors[pnlTone(change)];

  // Weight against total equity, the same denominator the concentration card
  // and the position table use, so "Pozisyon · 8.40%" means one thing app-wide.
  const weight = position && equity > 0 ? (position.quantity * position.current_price) / equity : null;

  /*
   * The prototype's precedence is on the POSITION, not on the weight: a held
   * name says "Pozisyon" even in the moment before the snapshot lands, rather
   * than dropping to the pending order's label and reading as merely proposed.
   * The weight joins the label only once equity is known — an unknown share is
   * left unsaid, not shown as 0.00%.
   */
  const status = position
    ? weight != null
      ? `Pozisyon · ${formatPct(weight)}`
      : 'Pozisyon'
    : pending
      ? 'Onay bekliyor'
      : '—';

  return (
    <Card style={styles.card}>
      <View style={styles.cardTop}>
        <Text style={styles.ticker} numberOfLines={1}>
          {ticker}
        </Text>
        <View style={styles.quote}>
          <Text style={styles.last} numberOfLines={1}>
            {formatUsd(data?.last)}
          </Text>
          <Text style={[styles.change, { color: changeColor }]} numberOfLines={1}>
            {formatPct(change, { signed: true })}
          </Text>
        </View>
      </View>

      <View style={styles.meta}>
        {decision ? (
          <Tag label={decision.rating} variant={ratingVariant(decision.rating)} size="sm" caps />
        ) : (
          <Tag label="—" variant="neutral" size="sm" />
        )}
        <Text style={styles.date} numberOfLines={1}>
          {decision ? formatOrderDate(decision.timestamp_utc) : serverDown ? '—' : 'karar yok'}
        </Text>
        <Text style={styles.status} numberOfLines={1}>
          {status}
        </Text>
      </View>

      <View style={styles.actions}>
        <Pressable
          style={({ pressed }) => [styles.ghost, styles.ghostWide, pressed && styles.ghostPressed]}
          hitSlop={hitSlopFor(GHOST_H)}
          onPress={onChart}
          accessibilityRole="button"
          accessibilityLabel={`${ticker} grafiğini aç`}
        >
          <Text style={styles.ghostText}>Grafik</Text>
        </Pressable>
        <Pressable
          style={({ pressed }) => [styles.ghost, styles.ghostWide, pressed && styles.ghostPressed]}
          hitSlop={hitSlopFor(GHOST_H)}
          onPress={onAnalyze}
          accessibilityRole="button"
          accessibilityLabel={`${ticker} analiz et`}
        >
          <Text style={styles.ghostText}>Analiz et</Text>
        </Pressable>
        {/* The prototype's remove button only turns rose on hover. A phone has
            no hover, so the same intent lands on press — the destructive colour
            appears at the moment of the touch, not before it. */}
        <Pressable
          style={({ pressed }) => [styles.ghost, styles.ghostIcon, pressed && styles.removePressed]}
          hitSlop={hitSlopFor(GHOST_H)}
          onPress={onRemove}
          accessibilityRole="button"
          accessibilityLabel={`${ticker} sembolünü izleme listesinden çıkar`}
        >
          <XIcon color={t.ink3 ?? t.textMuted} />
        </Pressable>
      </View>
    </Card>
  );
}

type Palette = ReturnType<typeof useTheme>;

/**
 * Tone → colour, palette-dependent because Modernist's P&L is the accounting
 * one: a gain is ink, a loss is accent-700 (the base accent is a fill colour,
 * too weak at 13px), flat is secondary. Under Aurora `downText` is deliberately
 * the same value as `down`. `pnlTone` decides WHICH tone; this only paints it —
 * the same split portfolio.tsx and charts.tsx use.
 */
const pnlToneColors = (t: Palette): Record<Tone, string> => ({
  up: t.up,
  down: t.downText ?? t.down,
  neutral: t.textSecondary,
});

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    screen: { flex: 1, backgroundColor: t.background },
    content: {
      paddingHorizontal: sh.space[3],
      paddingTop: sh.space[1],
      paddingBottom: TAB_BAR_CLEARANCE,
    },

    title: { ...TYPE.h2, fontSize: 26, letterSpacing: -0.52, color: t.textPrimary },
    titleCount: { ...font(600), ...TABULAR, color: t.ink3 ?? t.textMuted },
    subtitle: { ...TYPE.helper, color: t.ink2 ?? t.textSecondary, marginTop: sh.space[0] },

    addRow: { flexDirection: 'row', gap: sh.space[1], marginTop: sh.space[2] },
    input: {
      flex: 1,
      minHeight: MIN_TOUCH_TARGET + 4,
      backgroundColor: t.surface,
      color: t.textPrimary,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
      borderRadius: sh.radius,
      paddingHorizontal: sh.space[2],
      fontSize: 14,
      letterSpacing: 0.56,
      ...font(600),
    },
    // The prototype's primary button: the ink fill, ground-coloured label.
    addBtn: {
      minHeight: MIN_TOUCH_TARGET + 4,
      paddingHorizontal: sh.space[3],
      backgroundColor: t.textPrimary,
      borderRadius: sh.radius,
      alignItems: 'center',
      justifyContent: 'center',
    },
    addBtnPressed: { opacity: 0.82 },
    addBtnText: { color: t.inkInv ?? t.background, fontSize: 14, ...font(600) },

    spinner: { marginTop: sh.space[4] },

    slot: { marginTop: sh.space[2], paddingVertical: sh.space[4], alignItems: 'center' },
    slotText: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, textAlign: 'center' },

    // A stack of cards, not a ruled table: each name is an independent thing
    // with its own actions.
    list: { gap: sh.space[1], marginTop: sh.space[2] },
    card: { gap: sh.space[1] },

    cardTop: { flexDirection: 'row', alignItems: 'center', gap: sh.space[2] },
    ticker: { flex: 1, minWidth: 0, color: t.textPrimary, fontSize: 16, ...font(800) },
    quote: { alignItems: 'flex-end' },
    last: { color: t.textPrimary, fontSize: 14, ...font(600), ...TABULAR },
    change: { fontSize: 12, ...font(600), ...TABULAR },

    meta: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1] },
    date: { color: t.ink3 ?? t.textMuted, flexShrink: 1, ...TYPE.helper, fontSize: 12, ...TABULAR },
    status: { marginLeft: 'auto', color: t.ink2 ?? t.textSecondary, fontSize: 12, ...font(400) },

    actions: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1] },
    // The prototype's ghost button: one rule, no fill, ink label.
    ghost: {
      height: GHOST_H,
      borderRadius: sh.radiusSmall,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
      alignItems: 'center',
      justifyContent: 'center',
    },
    ghostWide: { flex: 1 },
    ghostIcon: { width: GHOST_H, borderColor: t.line ?? t.divider },
    ghostPressed: { backgroundColor: t.surface2 ?? t.surfaceElevated },
    removePressed: { borderColor: t.down },
    ghostText: { color: t.textPrimary, fontSize: 12, ...font(600) },
  });
