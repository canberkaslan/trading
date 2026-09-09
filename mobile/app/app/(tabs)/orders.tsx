import { View, Text, StyleSheet, ScrollView, RefreshControl, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useState, useCallback, useEffect, useMemo } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'expo-router';

import { usePendingOrders, useOrders, useCancelOrder, useDecisions } from '@/api/hooks';
import type { AgentDecision, OrderListItem } from '@/api/types';
import { getPermissionStatus, requestAndRegisterPush } from '@/notifications';
import { useTheme } from '@/theme/useTheme';
import { ErrorState } from '@/components/ErrorState';
import { EmptyState } from '@/components/EmptyState';
import { Tag, type TagVariant } from '@/components/Tag';
import { Seg } from '@/components/Seg';
import { Sheet } from '@/components/Sheet';
import { toast } from '@/stores/toast';
import { ratingChip } from '@/theme/rating';
import { formatUsd, relativeAgeTr, parseUtc } from '@/utils/format';
import {
  orderStatusMeta,
  fillSummary,
  formatOrderDate,
  isCancellable,
  rejectionReasonTr,
  type OrderTone,
} from '@/utils/orders';
import { MIN_TOUCH_TARGET, orderActionLabel } from '@/utils/a11y';
import { font, TABULAR, TYPE } from '@/theme/type';

type Tab = 'pending' | 'history';

/**
 * How a status tone becomes a chip.
 *
 * `orderStatusMeta` owns the label and the tone; this is only the tone → shape
 * mapping the handoff draws, and it lines up with `Tag`'s own semantics: a
 * broker-side wait is outlined (awaiting an outcome), a rejection is the accent
 * (a thing that went wrong), everything settled is neutral (a thing that is
 * just so).
 */
const TONE_VARIANT: Record<OrderTone, TagVariant> = {
  up: 'neutral',
  warning: 'outline',
  muted: 'neutral',
  down: 'accent',
};

/**
 * A risk-layer rejection never reached the broker, so it has no broker status
 * to describe it — `orderStatusMeta(null)` would say "Broker durumu yok", which
 * hides the actual reason the order died. The handoff gives it its own accent
 * tag. Same predicate the prototype uses: not risk-approved, or reasons
 * recorded with nothing ever sent.
 */
function riskRejected(o: OrderListItem): boolean {
  return !o.risk_approved || (o.rejection_reasons.length > 0 && !o.broker_order_id);
}

/** Ask at most once per app session, and only with a real order on screen. */
let pushPromptShown = false;

export default function OrdersScreen() {
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);
  const router = useRouter();
  const [tab, setTab] = useState<Tab>('pending');
  const pending = usePendingOrders();
  const history = useOrders();
  // The pending row needs the rating and the notional, and neither is on the
  // order row — both live on the decision the order came from.
  const decisions = useDecisions({ limit: 60 });
  const active = tab === 'pending' ? pending : history;
  const qc = useQueryClient();
  const cancel = useCancelOrder();
  const [refreshing, setRefreshing] = useState(false);
  const [askPush, setAskPush] = useState(false);
  // The cancel sheet is owned by the screen, not by the row that opens it: a
  // Modal per history row is twenty-odd modals mounted to show at most one.
  const [cancelTarget, setCancelTarget] = useState<OrderListItem | null>(null);
  const [cancelError, setCancelError] = useState<string | null>(null);

  const decisionById = useMemo(() => {
    const map = new Map<string, AgentDecision>();
    for (const d of decisions.data ?? []) map.set(d.decision_id, d);
    return map;
  }, [decisions.data]);

  const pendingCount = pending.data?.length ?? 0;
  /**
   * Contextual push opt-in: the ask lands when there is actually an order
   * waiting, so it is self-explanatory. A cold-start prompt gets denied, and a
   * denial is permanent — which would mean never being told about an approval
   * again.
   */
  useEffect(() => {
    if (pendingCount === 0 || pushPromptShown) return;
    pushPromptShown = true;
    let cancelled = false;
    void getPermissionStatus().then((s) => {
      if (!cancelled && s === 'undetermined') setAskPush(true);
    });
    return () => {
      cancelled = true;
    };
  }, [pendingCount]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await qc.invalidateQueries({ queryKey: ['orders'] });
    setRefreshing(false);
  }, [qc]);

  const doCancel = useCallback(() => {
    if (!cancelTarget) return;
    cancel.mutate(cancelTarget.order_id, {
      onSuccess: () => {
        setCancelTarget(null);
        toast("İptal isteği broker'a gönderildi");
      },
      onError: (e: unknown) => {
        setCancelTarget(null);
        setCancelError(
          e instanceof Error ? e.message : 'Broker isteği reddetti. Emir hâlâ açık olabilir.',
        );
      },
    });
  }, [cancel, cancelTarget]);

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={theme.textPrimary} />}
      >
        <Text style={styles.heading}>Emirler</Text>

        {/* Counts in the label: the operator's first question on this screen is
            how many are waiting, and answering it in the control removes a
            reason to switch tabs to find out. */}
        <Seg
          block
          style={styles.segment}
          value={tab}
          onChange={setTab}
          options={[
            {
              value: 'pending',
              label: `Onay bekleyen · ${pending.data?.length ?? 0}`,
              accessibilityLabel: `Onay bekleyen emirler, ${pending.data?.length ?? 0}`,
            },
            {
              value: 'history',
              label: `Geçmiş · ${history.data?.length ?? 0}`,
              accessibilityLabel: `Emir geçmişi, ${history.data?.length ?? 0}`,
            },
          ]}
        />

        <Text style={styles.subheading}>
          {active.isFetching
            ? 'Yenileniyor…'
            : tab === 'pending'
              ? '--hold ile tutulan, kararını bekleyen emirler'
              : "Broker'a gönderilen son emirler ve durumları"}
        </Text>

        {active.isLoading ? (
          <Text style={styles.muted}>Yükleniyor…</Text>
        ) : active.isError ? (
          <ErrorState onRetry={active.refetch} />
        ) : !active.data || active.data.length === 0 ? (
          tab === 'pending' ? (
            <EmptyState title="Onay bekleyen emir yok" hint="Günlük koşu bir emri onaya düşürdüğünde burada görünür." />
          ) : (
            <EmptyState title="Henüz emir geçmişi yok" hint="Broker'a bir emir gönderildiğinde burada listelenir." />
          )
        ) : tab === 'pending' ? (
          active.data.map((o) => (
            <PendingRow
              key={o.order_id}
              order={o}
              decision={decisionById.get(o.decision_id) ?? null}
              onPress={() => router.push(`/approve/${o.order_id}` as never)}
            />
          ))
        ) : (
          active.data.map((o) => (
            <HistoryRow
              key={o.order_id}
              order={o}
              cancelling={cancel.isPending && cancelTarget?.order_id === o.order_id}
              onAskCancel={() => setCancelTarget(o)}
            />
          ))
        )}
      </ScrollView>

      <Sheet
        visible={cancelTarget != null}
        title="Emri iptal et"
        message={
          cancelTarget
            ? `${cancelTarget.ticker} ${cancelTarget.side} ${cancelTarget.quantity} lot emri broker'dan geri çekilsin mi? Emir bu sırada dolarsa iptal edilemez.`
            : ''
        }
        busy={cancel.isPending}
        busyLabel="İptal isteği gönderiliyor…"
        onDismiss={() => setCancelTarget(null)}
        actions={[
          { label: 'Vazgeç', onPress: () => setCancelTarget(null) },
          { label: 'İptal et', destructive: true, onPress: doCancel },
        ]}
      />

      <Sheet
        visible={cancelError != null}
        title="İptal edilemedi"
        message={cancelError ?? ''}
        onDismiss={() => setCancelError(null)}
        actions={[{ label: 'Tamam', primary: true, onPress: () => setCancelError(null) }]}
      />

      <Sheet
        visible={askPush}
        title="Bu emirden haberdar ol"
        message="Bir emir onayını beklerken telefonuna bildirim gönderelim mi? Sadece onay bekleyen ve gerçekleşen emirler için."
        onDismiss={() => setAskPush(false)}
        actions={[
          { label: 'Şimdi değil', onPress: () => setAskPush(false) },
          {
            label: 'Bildirim aç',
            primary: true,
            onPress: () => {
              setAskPush(false);
              void requestAndRegisterPush();
            },
          },
        ]}
      />
    </SafeAreaView>
  );
}

/**
 * One order waiting on a decision.
 *
 * The row answers the four questions that decide whether it is worth opening:
 * what the council concluded (the rating chip), which way and how big (side +
 * quantity), what it costs and where the stop is (the meta line), and how long
 * it has been sitting — a pending order that is hours old is a different thing
 * from one that landed a minute ago.
 */
function PendingRow({
  order: o,
  decision,
  onPress,
}: {
  order: OrderListItem;
  decision: AgentDecision | null;
  onPress: () => void;
}) {
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);
  const sideColor = o.side === 'BUY' ? theme.up : theme.downText ?? theme.down;
  const chip = decision ? ratingChip(theme, decision.rating) : null;

  const notional =
    decision?.entry_price != null ? decision.entry_price * o.quantity : null;
  const submitted = parseUtc(o.submitted_at_utc);
  const age = submitted ? relativeAgeTr(Date.now() - submitted.getTime()) : null;

  // "MARKET · stop $312.40 · $6,158.00 · 17 sa önce" — parts that have no data
  // drop out rather than rendering an em dash inside a run-on line.
  const meta = [
    o.order_type,
    `stop ${formatUsd(o.stop_loss)}`,
    notional != null ? formatUsd(notional) : null,
    age,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <Pressable
      style={styles.row}
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={orderActionLabel(o, 'review')}
      accessibilityHint={meta}
    >
      <View style={styles.rowTop}>
        <Text style={styles.ticker}>{o.ticker}</Text>
        {decision && chip ? (
          <View style={[styles.ratingChip, { backgroundColor: chip.background, borderColor: chip.borderColor ?? 'transparent' }]}>
            <Text style={[styles.ratingLabel, { color: chip.color }]}>{decision.rating}</Text>
          </View>
        ) : null}
        <Text style={[styles.side, { color: sideColor }]}>
          {o.side} {o.quantity}
        </Text>
      </View>
      <View style={styles.rowBottom}>
        <Text style={styles.meta} numberOfLines={1}>
          {meta}
        </Text>
        <Text style={styles.review}>İncele →</Text>
      </View>
    </Pressable>
  );
}

/**
 * One broker-submitted order. Read-only except for the cancel action, which is
 * only rendered while the broker can still act on it (see `isCancellable`) and
 * is sheet-gated — a mis-tap here pulls a live order off the book.
 */
function HistoryRow({
  order: o,
  cancelling,
  onAskCancel,
}: {
  order: OrderListItem;
  cancelling: boolean;
  onAskCancel: () => void;
}) {
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);

  const sideColor = o.side === 'BUY' ? theme.up : theme.downText ?? theme.down;
  const meta = orderStatusMeta(o.broker_status);
  const risk = riskRejected(o);
  const statusLabel = risk ? 'Risk reddi' : meta.label;
  const statusVariant: TagVariant = risk ? 'accent' : TONE_VARIANT[meta.tone];
  const cancellable = isCancellable(o.broker_status, o.broker_order_id);
  const reasons = o.rejection_reasons ?? [];

  return (
    <View style={styles.row}>
      <View style={styles.rowTop}>
        <Text style={styles.tickerSm}>{o.ticker}</Text>
        <Tag label={statusLabel} variant={statusVariant} />
        <Text style={[styles.side, { color: sideColor }]}>
          {o.side} {o.quantity}
        </Text>
      </View>
      <View style={styles.rowBottom}>
        {/* `formatUsd` is the one place that decides what a missing figure
            looks like — an unfilled order reads "—" because it says so. */}
        <Text style={styles.meta} numberOfLines={1}>
          {fillSummary(o.filled_qty, o.quantity)} · ort. {formatUsd(o.avg_fill_price)}
        </Text>
        <Text style={styles.meta}>{formatOrderDate(o.submitted_at_utc)}</Text>
      </View>

      {reasons.length > 0 && (
        <View style={styles.reasons}>
          {reasons.map((r, i) => (
            <Text key={`${o.order_id}-r${i}`} style={styles.reason}>
              • {rejectionReasonTr(r)}
            </Text>
          ))}
        </View>
      )}

      {o.broker_order_id ? <Text style={styles.reason}>{o.broker_order_id}</Text> : null}

      {cancellable && (
        <Pressable
          style={styles.cancelBtn}
          onPress={onAskCancel}
          disabled={cancelling}
          accessibilityRole="button"
          accessibilityLabel={orderActionLabel(o, 'cancel')}
          accessibilityHint="Onay sorulur"
          accessibilityState={{ disabled: cancelling }}
        >
          <Text style={styles.cancelLabel}>
            {cancelling ? 'İptal ediliyor…' : 'Emri iptal et'}
          </Text>
        </Pressable>
      )}
    </View>
  );
}

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.background },
    scroll: { paddingHorizontal: 16, paddingTop: 20, paddingBottom: 24 },
    heading: { color: t.textPrimary, ...TYPE.h2 },
    segment: { marginTop: 12, marginBottom: 8 },
    subheading: { color: t.textSecondary, ...TYPE.body, marginBottom: 8 },
    // Ruled rows, not stacked cards.
    row: { paddingVertical: 14, gap: 6, borderBottomWidth: 1, borderBottomColor: t.divider },
    rowTop: { flexDirection: 'row', alignItems: 'center', gap: 10 },
    rowBottom: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 10 },
    ticker: { color: t.textPrimary, fontSize: 18, ...font(800) },
    tickerSm: { color: t.textPrimary, fontSize: 16, ...font(800) },
    // Pushed to the trailing edge without a spacer view.
    side: { marginLeft: 'auto', fontSize: 15, ...font(800), ...TABULAR },
    ratingChip: { paddingHorizontal: 8, paddingVertical: 3, borderWidth: 1 },
    ratingLabel: { fontSize: 11, letterSpacing: 0.22, ...font(600) },
    meta: { color: t.textSecondary, flexShrink: 1, ...TYPE.helper, ...TABULAR },
    review: { color: t.accent700 ?? t.accent, ...TYPE.helper, ...font(600) },
    muted: { color: t.textSecondary, ...TYPE.body, marginTop: 6 },
    reasons: { gap: 2 },
    reason: { color: t.textSecondary, ...TYPE.helper },
    cancelBtn: {
      marginTop: 6,
      alignSelf: 'flex-start',
      paddingHorizontal: 16,
      minHeight: MIN_TOUCH_TARGET,
      borderWidth: 1,
      borderColor: t.accent,
      alignItems: 'center',
      justifyContent: 'center',
    },
    cancelLabel: { color: t.accent700 ?? t.accent, fontSize: 14, ...font(800) },
  });
