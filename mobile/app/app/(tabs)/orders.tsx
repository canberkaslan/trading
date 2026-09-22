/**
 * Emirler — the approval queue and the broker log, in one segmented screen.
 *
 * Ported to Aurora. What changed is the drawing: ruled rows became cards, the
 * hand-rolled rating chip became `<Tag variant={ratingVariant(...)}>`, and the
 * status chip now uses Aurora's soft tone grounds (`--upSoft` / `--warnSoft` /
 * `--downSoft`) which is exactly the mapping the prototype's `statusOf` writes
 * out by hand.
 *
 * What did NOT change is every rule the screen renders. `orderStatusMeta` still
 * owns the status label and its tone, `isCancellable` still decides whether the
 * cancel button exists at all, `rejectionReasonTr` still owns the wording of a
 * refusal, and `riskRejected` still separates "the risk layer said no" from
 * "the broker said no". The prototype re-implements all four inline and
 * disagrees with them in two places (noted in the port report); the helpers
 * win, because they are the product and they are the tested ones.
 */

import { View, Text, StyleSheet, ScrollView, RefreshControl, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useState, useCallback, useEffect, useMemo } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';

import { usePendingOrders, useOrders, useCancelOrder, useDecisions } from '@/api/hooks';
import type { AgentDecision, OrderListItem } from '@/api/types';
import { getPermissionStatus, requestAndRegisterPush } from '@/notifications';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { useIsAdmin } from '@/api/useMe';
import { ErrorState } from '@/components/ErrorState';
import { Card } from '@/components/Card';
import { DataRow } from '@/components/DataRow';
import { Tag, type TagVariant } from '@/components/Tag';
import { Seg } from '@/components/Seg';
import { Sheet } from '@/components/Sheet';
import { toast } from '@/stores/toast';
import { ratingVariant } from '@/theme/rating';
import { formatUsd, relativeAgeTr, parseUtc } from '@/utils/format';
import {
  orderStatusMeta,
  fillSummary,
  formatOrderDate,
  isCancellable,
  rejectionReasonTr,
  riskRejected,
  type OrderTone,
} from '@/utils/orders';
import { MIN_TOUCH_TARGET, orderActionLabel } from '@/utils/a11y';
import { font, TABULAR, TYPE } from '@/theme/type';

type Tab = 'pending' | 'history';

/**
 * The floating tab bar's height plus air. Stated here rather than imported
 * from `_layout.tsx` because a route module's exports are the router's
 * namespace, not a place to hang shared constants.
 */
const TAB_BAR_CLEARANCE = 72;

/**
 * How a status tone becomes a chip.
 *
 * `orderStatusMeta` owns the label and the tone; this is only the tone → shape
 * mapping, and under Aurora it is the prototype's own table: filled wears the
 * green soft ground, anything the broker is still chewing on wears amber,
 * anything settled is neutral, a refusal is rose. On a palette without those
 * soft grounds `Tag` falls back to `surface` and the meaning survives in the
 * text colour alone — which is what keeps this screen readable under
 * Modernist.
 */
const TONE_VARIANT: Record<OrderTone, TagVariant> = {
  up: 'up',
  warning: 'warn',
  muted: 'neutral',
  down: 'down',
};

/** Ask at most once per app session, and only with a real order on screen. */
let pushPromptShown = false;

export default function OrdersScreen() {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const router = useRouter();
  const { t: tr } = useTranslation();
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
    <SafeAreaView style={styles.screen} edges={['top']}>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={t.textSecondary} />
        }
      >
        <Text style={styles.heading} accessibilityRole="header">
          Emirler
        </Text>
        <Text style={styles.subheading}>
          {active.isFetching
            ? 'Yenileniyor…'
            : tab === 'pending'
              ? '--hold ile tutulan, kararını bekleyen emirler'
              : "Broker'a gönderilen son emirler ve durumları"}
        </Text>

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

        {active.isLoading ? (
          <Card tone="dashed" style={styles.slot}>
            <Text style={styles.slotText}>Yükleniyor…</Text>
          </Card>
        ) : active.isError ? (
          <ErrorState detail={active.error} onRetry={active.refetch} />
        ) : !active.data || active.data.length === 0 ? (
          <Card tone="dashed" style={styles.slot}>
            {tab === 'pending' ? (
              <>
                <Text style={styles.slotTitle}>Onay bekleyen emir yok</Text>
                <Text style={styles.slotText}>
                  Günlük koşu bir emri onaya düşürdüğünde burada görünür.
                </Text>
              </>
            ) : (
              <>
                <Text style={styles.slotTitle}>Henüz emir geçmişi yok</Text>
                <Text style={styles.slotText}>
                  Broker&apos;a bir emir gönderildiğinde burada listelenir.
                </Text>
              </>
            )}
          </Card>
        ) : (
          <View style={styles.list}>
            {tab === 'pending'
              ? active.data.map((o) => (
                  <PendingRow
                    key={o.order_id}
                    order={o}
                    decision={decisionById.get(o.decision_id) ?? null}
                    onPress={() => router.push(`/approve/${o.order_id}` as never)}
                  />
                ))
              : active.data.map((o) => (
                  <HistoryRow
                    key={o.order_id}
                    order={o}
                    cancelling={cancel.isPending && cancelTarget?.order_id === o.order_id}
                    onAskCancel={() => setCancelTarget(o)}
                  />
                ))}
          </View>
        )}

        <Text style={styles.disclaimer}>{tr('disclaimer.short')}</Text>
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
 * what the council concluded (the rating chip), which way and how big (the
 * side square and the sub-line), what it costs and where the stop is, and how
 * long it has been sitting — a pending order that is hours old is a different
 * thing from one that landed a minute ago.
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
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const buy = o.side === 'BUY';

  const notional = decision?.entry_price != null ? decision.entry_price * o.quantity : null;
  const submitted = parseUtc(o.submitted_at_utc);
  const age = submitted ? relativeAgeTr(Date.now() - submitted.getTime()) : null;

  // "BUY 18 · MARKET · stop $312.40" — the side and size lead, because the
  // right column already carries the money. Parts with no data drop out rather
  // than rendering an em dash inside a run-on line.
  const meta = [`${o.side} ${o.quantity}`, o.order_type, `stop ${formatUsd(o.stop_loss)}`]
    .filter(Boolean)
    .join(' · ');

  return (
    <Card
      padded={false}
      onPress={onPress}
      accessibilityLabel={orderActionLabel(o, 'review')}
      accessibilityHint={[meta, age].filter(Boolean).join(' · ')}
    >
      <DataRow
        chevron
        title={o.ticker}
        titleAfter={
          decision ? <Tag label={decision.rating} variant={ratingVariant(decision.rating)} size="sm" caps /> : null
        }
        subtitle={meta}
        value={notional != null ? formatUsd(notional) : undefined}
        valueHint={age ?? undefined}
        leading={
          <View style={[styles.side, { backgroundColor: (buy ? t.upSoft : t.downSoft) ?? t.surfaceElevated }]}>
            <Text style={[styles.sideText, { color: buy ? t.up : (t.downText ?? t.down) }]}>
              {buy ? 'AL' : 'SAT'}
            </Text>
          </View>
        }
      />
    </Card>
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
  // The server refuses a cancel from a non-administrator; drawing the button
  // anyway would deliver that answer as a 403 after the decision was made.
  const isAdmin = useIsAdmin();
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);

  const sideColor = o.side === 'BUY' ? t.up : (t.downText ?? t.down);
  const meta = orderStatusMeta(o.broker_status);
  /**
   * A risk-layer rejection never reached the broker, so it has no broker
   * status to describe it — `orderStatusMeta(null)` would say "Broker durumu
   * yok", which hides the actual reason the order died. `riskRejected` in
   * `utils/orders` is the one predicate for this; the screen used to carry a
   * byte-identical private copy.
   */
  const risk = riskRejected(o);
  const statusLabel = risk ? 'Risk reddi' : meta.label;
  const statusVariant: TagVariant = risk ? 'down' : TONE_VARIANT[meta.tone];
  const cancellable = isCancellable(o.broker_status, o.broker_order_id);
  const reasons = o.rejection_reasons ?? [];

  return (
    <Card>
      <View style={styles.histTop}>
        <Text style={styles.ticker}>{o.ticker}</Text>
        <Tag label={statusLabel} variant={statusVariant} size="sm" />
        <Text style={[styles.side2, { color: sideColor }]}>
          {o.side} {o.quantity}
        </Text>
      </View>

      <View style={styles.histMeta}>
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

      {o.broker_order_id ? <Text style={styles.brokerId}>{o.broker_order_id}</Text> : null}

      {cancellable && (
        <Pressable
          style={({ pressed }) => [styles.cancelBtn, pressed && styles.cancelBtnPressed]}
          onPress={onAskCancel}
          disabled={cancelling || !isAdmin}
          accessibilityRole="button"
          accessibilityLabel={orderActionLabel(o, 'cancel')}
          accessibilityHint="Onay sorulur"
          accessibilityState={{ disabled: cancelling || !isAdmin }}
        >
          <Text style={styles.cancelLabel}>{cancelling ? 'İptal ediliyor…' : 'Emri iptal et'}</Text>
        </Pressable>
      )}
    </Card>
  );
}

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    screen: { flex: 1, backgroundColor: t.background },
    content: { paddingHorizontal: sh.space[3], paddingBottom: TAB_BAR_CLEARANCE },

    heading: { ...TYPE.h2, fontSize: 26, letterSpacing: -0.5, color: t.textPrimary, paddingTop: sh.space[1] },
    subheading: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, marginTop: sh.space[0] },
    segment: { marginTop: sh.space[2], marginBottom: sh.space[2] },

    list: { gap: sh.space[1] },

    // The prototype's 42px side square: a green or rose tint carrying AL/SAT,
    // not the ink chip — the ink chip on this screen means the rating.
    side: { width: 42, height: 42, borderRadius: sh.radiusSmall, alignItems: 'center', justifyContent: 'center' },
    sideText: { fontSize: 12, ...font(800), ...TABULAR },

    histTop: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1] },
    ticker: { ...TYPE.bodyStrong, fontSize: 15, color: t.textPrimary },
    // Pushed to the trailing edge without a spacer view.
    side2: { marginLeft: 'auto', fontSize: 13, ...font(800), ...TABULAR },
    histMeta: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: sh.space[1],
      marginTop: sh.space[1],
    },
    meta: { ...TYPE.helper, fontSize: 12, ...TABULAR, color: t.ink2 ?? t.textSecondary, flexShrink: 1 },

    reasons: { gap: 2, marginTop: sh.space[1] },
    reason: { ...TYPE.helper, fontSize: 12, color: t.downText ?? t.down },
    brokerId: { ...TYPE.helper, ...TABULAR, color: t.ink3 ?? t.textMuted, marginTop: sh.space[1] },

    cancelBtn: {
      marginTop: sh.space[2],
      alignSelf: 'flex-start',
      paddingHorizontal: sh.space[3],
      minHeight: MIN_TOUCH_TARGET,
      borderWidth: sh.hairline,
      borderColor: t.down,
      borderRadius: sh.radius,
      alignItems: 'center',
      justifyContent: 'center',
    },
    cancelBtnPressed: { backgroundColor: t.downSoft ?? t.surfaceElevated },
    cancelLabel: { ...TYPE.bodyStrong, color: t.downText ?? t.down },

    slot: { marginTop: sh.space[1], paddingVertical: sh.space[4], alignItems: 'center', gap: sh.space[0] },
    slotTitle: { ...TYPE.section, color: t.textPrimary, textAlign: 'center' },
    slotText: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, textAlign: 'center' },

    disclaimer: {
      ...TYPE.helper,
      color: t.ink3 ?? t.textMuted,
      textAlign: 'center',
      lineHeight: 16,
      marginTop: sh.space[4],
    },
  });
