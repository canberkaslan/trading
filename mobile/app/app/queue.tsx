/**
 * Onay kuyruğu — the prototype's `/* QUEUE *\/` screen.
 *
 * The Emirler tab lists pending orders; this screen is for working through them
 * one after another. Same orders, same two calls the approve screen makes, and
 * deliberately no new judgement: the decision joined to each row supplies the
 * rating chip and the entry price, and nothing here re-answers "is this order
 * still good" — `/approve/[orderId]` and the backend guards own that.
 *
 * Two things the port had to decide.
 *
 * 1. The prototype's J/K/A/R/↵/Esc shortcuts are a web affordance. They are
 *    implemented as a `window` key listener that only mounts on web, and the
 *    touch path is primary everywhere: every shortcut has a button beside it,
 *    and the key caps only render where the keys exist.
 * 2. The prototype approves straight from the row. The app does not: approving
 *    an order goes through the device lock (see `app/approve/[orderId].tsx`),
 *    and a faster screen is not a reason to drop the one gate that stands in
 *    front of real money. So "Onayla" here opens the same auth → verify →
 *    broker-answer sequence, just without leaving the list.
 */

import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  Pressable,
  RefreshControl,
  Platform,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { HTTPError } from 'ky';

import { usePendingOrders, useDecisions, useApproveOrder, useRejectOrder } from '@/api/hooks';
import { useIsAdmin } from '@/api/useMe';
import type { AgentDecision, OrderListItem } from '@/api/types';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { TYPE, TABULAR, font } from '@/theme/type';
import { Card } from '@/components/Card';
import { Tag } from '@/components/Tag';
import { Sheet } from '@/components/Sheet';
import { ErrorState } from '@/components/ErrorState';
import { ratingVariant } from '@/theme/rating';
import { authenticate } from '@/auth/biometric';
import { toast } from '@/stores/toast';
import { formatUsd, relativeAgeTr, parseUtc } from '@/utils/format';
import { rejectionReasonTr } from '@/utils/orders';
import { orderActionLabel, hitSlopFor } from '@/utils/a11y';

/** The floating tab bar's height plus air — the shell's number, restated. */
const TAB_BAR_CLEARANCE = 72;

const IS_WEB = Platform.OS === 'web';

/**
 * One value, so exactly one sheet is on screen — the same state machine the
 * approve screen runs, carrying the order it is about because a successful
 * approve removes that order from the list before the confirmation is read.
 */
type Flow =
  | { step: 'idle' }
  | { step: 'auth'; target: OrderListItem }
  | { step: 'verifying'; target: OrderListItem }
  | { step: 'authFailed'; message: string }
  | { step: 'sent'; ticker: string; brokerOrderId: string; status: string }
  | { step: 'failed'; title: string; message: string }
  | { step: 'rejectConfirm'; target: OrderListItem }
  | { step: 'rejecting'; target: OrderListItem };

/** FastAPI wraps every `HTTPException` payload in `detail`. */
async function errorDetail(res: Response): Promise<unknown> {
  try {
    const body = (await res.clone().json()) as { detail?: unknown };
    return body?.detail ?? null;
  } catch {
    return null;
  }
}

/**
 * What actually stopped the order, in the words the backend used — 422 is the
 * executor's own refusal and carries `refusal_reasons`, 409 is the pre-flight
 * refusal (armed kill switch, or already at the broker) and carries a sentence,
 * everything else is transport and must not be dressed up as a risk decision.
 *
 * This mirrors `approveFailure` in `app/approve/[orderId].tsx`. That copy lives
 * inside a route module and is not exported, and route files are not mine to
 * edit; both go through `rejectionReasonTr`, which is where the wording that
 * matters actually lives. Worth lifting into `utils/orders` once one owner has
 * both files.
 */
async function approveFailure(e: unknown): Promise<Flow> {
  if (!(e instanceof HTTPError)) {
    return { step: 'failed', title: 'Gönderilemedi', message: String(e) };
  }
  const detail = await errorDetail(e.response);

  if (e.response.status === 422) {
    const refusals =
      detail && typeof detail === 'object'
        ? (detail as { refusal_reasons?: unknown }).refusal_reasons
        : null;
    const reasons = (Array.isArray(refusals) ? refusals : [])
      .filter((r): r is string => typeof r === 'string')
      .map(rejectionReasonTr)
      .filter(Boolean);
    return {
      step: 'failed',
      title: 'Risk guard reddetti',
      message: reasons.length
        ? `Emir gönderilmedi — ${reasons.join(' · ')}. Emir bekleyen listesinde kalır.`
        : 'Emir gönderilmedi — guard onay anında reddetti. Emir bekleyen listesinde kalır.',
    };
  }

  if (e.response.status === 409) {
    return {
      step: 'failed',
      title: 'Gönderilemedi',
      message:
        typeof detail === 'string' && detail
          ? detail
          : "Emir artık onaylanabilir durumda değil — kill switch açık olabilir ya da emir zaten broker'a gitmiş olabilir.",
    };
  }

  return { step: 'failed', title: 'Gönderilemedi', message: String(e) };
}

/** The prototype's key caps. Web only — a phone has no J key to press. */
function KeyCap({ label }: { label: string }) {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  if (!IS_WEB) return null;
  return (
    <View style={styles.keyCap}>
      <Text style={styles.keyCapText}>{label}</Text>
    </View>
  );
}

export default function QueueScreen() {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const router = useRouter();
  const { t: tr } = useTranslation();

  const pending = usePendingOrders();
  // Only to borrow the rating chip, the entry price and the PM sentence; the
  // orders themselves are authoritative for everything else.
  const decisions = useDecisions({ limit: 60 });
  const isAdmin = useIsAdmin();
  const approve = useApproveOrder();
  const reject = useRejectOrder();

  const [flow, setFlow] = useState<Flow>({ step: 'idle' });
  const [index, setIndex] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const rows: OrderListItem[] = useMemo(() => pending.data ?? [], [pending.data]);

  const decisionById = useMemo(() => {
    const map = new Map<string, AgentDecision>();
    for (const d of decisions.data ?? []) map.set(d.decision_id, d);
    return map;
  }, [decisions.data]);

  // An approved or rejected order leaves the list under the cursor. Clamping
  // keeps the selection on a real row instead of pointing past the end, which
  // is what made the keyboard path go dead after the last order.
  useEffect(() => {
    setIndex((i) => (rows.length === 0 ? 0 : Math.min(i, rows.length - 1)));
  }, [rows.length]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await Promise.all([pending.refetch(), decisions.refetch()]);
    } finally {
      setRefreshing(false);
    }
  }, [pending, decisions]);

  const notionalOf = useCallback(
    (o: OrderListItem) => {
      const entry = decisionById.get(o.decision_id)?.entry_price;
      return entry != null ? entry * o.quantity : null;
    },
    [decisionById],
  );

  /**
   * The total the queue commits if every order in it is approved. Orders whose
   * decision has not loaded contribute nothing rather than zero — the count
   * beside the figure says how many were actually priced, so a partial join
   * reads as partial instead of as a cheaper queue.
   */
  const { total, priced } = useMemo(() => {
    let sum = 0;
    let n = 0;
    for (const o of rows) {
      const v = notionalOf(o);
      if (v != null) {
        sum += v;
        n += 1;
      }
    }
    return { total: n > 0 ? sum : null, priced: n };
  }, [rows, notionalOf]);

  const openOrder = useCallback(
    (o: OrderListItem) => router.push(`/approve/${o.order_id}` as never),
    [router],
  );

  const askApprove = useCallback((o: OrderListItem) => setFlow({ step: 'auth', target: o }), []);
  const askReject = useCallback(
    (o: OrderListItem) => setFlow({ step: 'rejectConfirm', target: o }),
    [],
  );

  const runApproval = useCallback(
    async (target: OrderListItem) => {
      const label = `${target.side} ${target.quantity} ${target.ticker} onayla`;
      setFlow({ step: 'verifying', target });
      const { success, mode } = await authenticate(label);
      if (!success) {
        setFlow({
          step: 'authFailed',
          message:
            mode === 'none'
              ? 'Cihazınızda ekran kilidi (Face/Touch ID veya şifre) tanımlı değil. Emir onayı için lütfen bir cihaz kilidi kurun.'
              : 'Kimlik doğrulama olmadan emir onaylanamaz.',
        });
        return;
      }
      try {
        const result = await approve.mutateAsync(target.order_id);
        setFlow({
          step: 'sent',
          ticker: target.ticker,
          brokerOrderId: result.broker_order_id,
          status: result.status,
        });
      } catch (e) {
        setFlow(await approveFailure(e));
      }
    },
    [approve],
  );

  const runReject = useCallback(
    async (target: OrderListItem) => {
      setFlow({ step: 'rejecting', target });
      try {
        await reject.mutateAsync(target.order_id);
        setFlow({ step: 'idle' });
        toast(`${target.ticker} ${target.side} ${target.quantity} reddedildi — bugün yeniden önerilmez`);
      } catch (e) {
        setFlow({ step: 'failed', title: 'Reddedilemedi', message: String(e) });
      }
    },
    [reject],
  );

  /**
   * The prototype's shortcuts, web only. They are the accelerator, never the
   * only way in: every one of them is a button on the row as well, and they are
   * ignored while a sheet is up so a keystroke cannot answer a dialog the
   * operator has not read.
   */
  useEffect(() => {
    if (!IS_WEB || typeof window === 'undefined') return;
    if (flow.step !== 'idle') return;

    const onKey = (ev: KeyboardEvent) => {
      if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
      const key = ev.key;
      const current = rows[index];

      if (key === 'Escape') {
        ev.preventDefault();
        router.back();
        return;
      }
      if (key === 'j' || key === 'J' || key === 'ArrowDown') {
        ev.preventDefault();
        setIndex((i) => (rows.length === 0 ? 0 : Math.min(i + 1, rows.length - 1)));
        return;
      }
      if (key === 'k' || key === 'K' || key === 'ArrowUp') {
        ev.preventDefault();
        setIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (!current || !isAdmin) return;
      if (key === 'Enter') {
        ev.preventDefault();
        openOrder(current);
      } else if (key === 'a' || key === 'A') {
        ev.preventDefault();
        askApprove(current);
      } else if (key === 'r' || key === 'R') {
        ev.preventDefault();
        askReject(current);
      }
    };

    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [flow.step, rows, index, isAdmin, router, openOrder, askApprove, askReject]);

  const busy = flow.step === 'verifying' || flow.step === 'rejecting';
  const now = Date.now();

  if (pending.isLoading && rows.length === 0) {
    return (
      <SafeAreaView style={styles.screen} edges={['top']}>
        <View style={styles.center}>
          <Text style={styles.muted}>Kuyruk yükleniyor…</Text>
        </View>
      </SafeAreaView>
    );
  }

  if (pending.isError && rows.length === 0) {
    return (
      <SafeAreaView style={styles.screen} edges={['top']}>
        <ErrorState
          title="Onay kuyruğu okunamadı"
          detail={pending.error}
          onRetry={() => void pending.refetch()}
        />
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
        <Pressable
          onPress={() => router.back()}
          style={styles.back}
          hitSlop={hitSlopFor(24)}
          accessibilityRole="button"
          accessibilityLabel="Emirler listesine dön"
        >
          <Text style={styles.backText}>← Emirler</Text>
        </Pressable>

        <View style={styles.head}>
          <Text style={styles.title} accessibilityRole="header">
            Onay kuyruğu <Text style={styles.titleCount}>{rows.length}</Text>
          </Text>
          <Text style={styles.sub}>
            {IS_WEB
              ? 'Klavye: J / K gez · A onayla · R reddet · ↵ aç · Esc çık'
              : 'Her onay cihaz kilidi ister · reddedilen emir bugün yeniden önerilmez'}
          </Text>
        </View>

        {rows.length === 0 ? (
          <Card tone="dashed" style={styles.slot}>
            <Text style={styles.slotText}>Kuyruk boş — tüm emirler karara bağlandı.</Text>
          </Card>
        ) : (
          <View style={styles.list}>
            {rows.map((o, i) => {
              const d = decisionById.get(o.decision_id) ?? null;
              const selected = i === index;
              const buy = o.side === 'BUY';
              const submitted = parseUtc(o.submitted_at_utc);
              const pm = d?.final_decision_text ?? null;
              return (
                <Card
                  key={o.order_id}
                  onPress={() => setIndex(i)}
                  style={[styles.row, selected && styles.rowSelected]}
                  accessibilityLabel={`${o.ticker}, ${o.side === 'BUY' ? 'al' : 'sat'} ${o.quantity} lot${
                    selected ? ', seçili' : ''
                  }`}
                  accessibilityHint="Seç; aşağıdaki düğmeler bu emri onaylar, reddeder ya da açar"
                >
                  <View style={styles.rowHead}>
                    <Text style={styles.ticker}>{o.ticker}</Text>
                    {d ? <Tag label={d.rating} variant={ratingVariant(d.rating)} size="sm" caps /> : null}
                    <Text
                      style={[styles.side, { color: buy ? t.up : (t.downText ?? t.down) }]}
                      numberOfLines={1}
                    >
                      {o.side} {o.quantity}
                    </Text>
                  </View>

                  <View style={styles.metaRow}>
                    <Text style={styles.meta}>
                      Tutar <Text style={styles.metaStrong}>{formatUsd(notionalOf(o))}</Text>
                    </Text>
                    <Text style={styles.meta}>
                      Stop <Text style={styles.metaStrong}>{formatUsd(o.stop_loss)}</Text>
                    </Text>
                    {submitted ? (
                      <Text style={styles.meta}>{relativeAgeTr(now - submitted.getTime())}</Text>
                    ) : null}
                  </View>

                  {pm ? (
                    <Text style={styles.pm} numberOfLines={3}>
                      {pm}
                    </Text>
                  ) : decisions.isLoading ? (
                    <Text style={styles.pmMuted}>Gerekçe yükleniyor…</Text>
                  ) : (
                    <Text style={styles.pmMuted}>Gerekçe metni yok.</Text>
                  )}

                  <View style={styles.actions}>
                    <Pressable
                      disabled={busy || !isAdmin}
                      onPress={() => {
                        setIndex(i);
                        askReject(o);
                      }}
                      style={({ pressed }) => [
                        styles.btn,
                        styles.btnSecondary,
                        pressed && styles.btnPressed,
                        (busy || !isAdmin) && styles.btnDisabled,
                      ]}
                      accessibilityRole="button"
                      accessibilityLabel={orderActionLabel(o, 'reject')}
                      accessibilityState={{ disabled: busy || !isAdmin }}
                    >
                      <Text style={styles.btnSecondaryText}>Reddet</Text>
                      <KeyCap label="R" />
                    </Pressable>

                    <Pressable
                      onPress={() => {
                        setIndex(i);
                        openOrder(o);
                      }}
                      style={({ pressed }) => [
                        styles.btn,
                        styles.btnSecondary,
                        pressed && styles.btnPressed,
                      ]}
                      accessibilityRole="button"
                      accessibilityLabel={orderActionLabel(o, 'review')}
                    >
                      <Text style={styles.btnSecondaryText}>İncele</Text>
                      <KeyCap label="↵" />
                    </Pressable>

                    <Pressable
                      disabled={busy || !isAdmin}
                      onPress={() => {
                        setIndex(i);
                        askApprove(o);
                      }}
                      style={({ pressed }) => [
                        styles.btn,
                        styles.btnPrimary,
                        pressed && styles.btnPrimaryPressed,
                        (busy || !isAdmin) && styles.btnDisabled,
                      ]}
                      accessibilityRole="button"
                      accessibilityLabel={orderActionLabel(o, 'approve')}
                      accessibilityHint="Cihaz kilidi ile doğrulama ister"
                      accessibilityState={{ disabled: busy || !isAdmin, busy }}
                    >
                      <Text style={styles.btnPrimaryText}>Onayla</Text>
                      <KeyCap label="A" />
                    </Pressable>
                  </View>
                </Card>
              );
            })}
          </View>
        )}

        {rows.length > 0 ? (
          <View
            style={styles.totalRow}
            accessible
            accessibilityLabel={`Toplam tutar ${formatUsd(total)}`}
          >
            <Text style={styles.meta}>
              Toplam tutar
              {priced < rows.length ? (
                <Text style={styles.metaThin}> · {priced}/{rows.length} emir fiyatlandı</Text>
              ) : null}
            </Text>
            <Text style={styles.totalValue}>{formatUsd(total)}</Text>
          </View>
        ) : null}

        {!isAdmin && rows.length > 0 ? (
          <Text style={styles.helper}>
            Bu hesap emirleri onaylayamaz — sadece görüntüleme yetkisi var.
          </Text>
        ) : null}

        <Text style={styles.disclaimer}>{tr('disclaimer.short')}</Text>
      </ScrollView>

      {/* The ask, then the same sheet in its busy state while the OS prompt is
          up — so the moment the prompt clears the operator sees "Doğrulanıyor…"
          rather than the buttons they just dismissed. */}
      <Sheet
        visible={flow.step === 'auth' || flow.step === 'verifying'}
        title="Cihaz kilidi ile doğrula"
        message={
          flow.step === 'auth' || flow.step === 'verifying'
            ? `${flow.target.side} ${flow.target.quantity} ${flow.target.ticker} onayla — Face ID / parmak izi / şifre olmadan emir onaylanamaz.`
            : ''
        }
        summary={
          flow.step === 'auth' || flow.step === 'verifying'
            ? [
                { label: 'Tutar', value: formatUsd(notionalOf(flow.target)) },
                { label: 'Stop', value: formatUsd(flow.target.stop_loss) },
                { label: 'Lot', value: String(flow.target.quantity) },
              ]
            : undefined
        }
        busy={flow.step === 'verifying'}
        busyLabel="Doğrulanıyor…"
        onDismiss={flow.step === 'auth' ? () => setFlow({ step: 'idle' }) : undefined}
        actions={[
          { label: 'Vazgeç', onPress: () => setFlow({ step: 'idle' }) },
          {
            label: 'Doğrula',
            primary: true,
            onPress: () => {
              if (flow.step === 'auth') void runApproval(flow.target);
            },
          },
        ]}
      />

      <Sheet
        visible={flow.step === 'authFailed'}
        title="Doğrulama gerekli"
        message={flow.step === 'authFailed' ? flow.message : ''}
        onDismiss={() => setFlow({ step: 'idle' })}
        actions={[{ label: 'Tamam', primary: true, onPress: () => setFlow({ step: 'idle' }) }]}
      />

      {/* The broker's answer. Dismissing it stays on the queue — that is the
          point of the screen — and the approve mutation has already invalidated
          the pending list, so the row is gone behind the sheet. */}
      <Sheet
        visible={flow.step === 'sent'}
        title="Emir gönderildi"
        message={
          flow.step === 'sent'
            ? `${flow.ticker} · broker emri ${flow.brokerOrderId} · durum ${flow.status}`
            : ''
        }
        onDismiss={() => setFlow({ step: 'idle' })}
        actions={[{ label: 'Sıradaki', primary: true, onPress: () => setFlow({ step: 'idle' }) }]}
      />

      <Sheet
        visible={flow.step === 'failed'}
        title={flow.step === 'failed' ? flow.title : ''}
        message={flow.step === 'failed' ? flow.message : ''}
        onDismiss={() => setFlow({ step: 'idle' })}
        actions={[{ label: 'Tamam', primary: true, onPress: () => setFlow({ step: 'idle' }) }]}
      />

      <Sheet
        visible={flow.step === 'rejectConfirm' || flow.step === 'rejecting'}
        title="Emri reddet?"
        message={
          flow.step === 'rejectConfirm' || flow.step === 'rejecting'
            ? `${flow.target.side} ${flow.target.quantity} ${flow.target.ticker} — emir gönderilmeyecek; günlük koşu bugün yeniden önermez.`
            : ''
        }
        busy={flow.step === 'rejecting'}
        busyLabel="Reddediliyor…"
        onDismiss={flow.step === 'rejectConfirm' ? () => setFlow({ step: 'idle' }) : undefined}
        actions={[
          { label: 'Vazgeç', onPress: () => setFlow({ step: 'idle' }) },
          {
            label: 'Reddet',
            destructive: true,
            onPress: () => {
              if (flow.step === 'rejectConfirm') void runReject(flow.target);
            },
          },
        ]}
      />
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

    back: { marginTop: sh.space[1], minHeight: 24, justifyContent: 'center', alignSelf: 'flex-start' },
    backText: { ...TYPE.bodyStrong, color: t.brand ?? t.accent },

    head: { paddingTop: sh.space[1], paddingBottom: sh.space[2] },
    title: { ...TYPE.h2, fontSize: 26, letterSpacing: -0.5, color: t.textPrimary },
    titleCount: { ...font(600), color: t.ink3 ?? t.textMuted, ...TABULAR },
    sub: { ...TYPE.helper, fontSize: 12, color: t.ink2 ?? t.textSecondary, marginTop: sh.space[0], ...TABULAR },

    list: { gap: sh.space[1] },
    // The prototype marks the cursor with a 2px brand border. Under Modernist
    // `rule` is the heavier of the two widths and the radius is 0, so the same
    // declaration lands as a square 2px frame there and a rounded one here.
    row: { borderWidth: sh.rule, borderColor: 'transparent' },
    rowSelected: { borderColor: t.brand ?? t.accent },

    rowHead: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1] },
    ticker: { fontSize: 20, ...font(800), letterSpacing: -0.2, color: t.textPrimary },
    side: { marginLeft: 'auto', fontSize: 14, ...font(600), ...TABULAR },

    metaRow: { flexDirection: 'row', flexWrap: 'wrap', gap: sh.space[2], marginTop: sh.space[1] },
    meta: { ...TYPE.helper, fontSize: 12, color: t.ink2 ?? t.textSecondary, ...TABULAR },
    metaStrong: { ...font(600), color: t.textPrimary },
    metaThin: { ...TYPE.helper, color: t.ink3 ?? t.textMuted },

    pm: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, lineHeight: 19, marginTop: sh.space[1] },
    pmMuted: { ...TYPE.helper, color: t.ink3 ?? t.textMuted, marginTop: sh.space[1] },

    actions: { flexDirection: 'row', gap: sh.space[1], marginTop: sh.space[2] },
    btn: {
      flex: 1,
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: sh.space[0],
      paddingHorizontal: sh.space[1],
      // 44pt, not the prototype's 40: this is the minimum tappable size, and
      // the button that sends a real order is not where to shave 4 points.
      minHeight: 44,
      borderRadius: sh.radiusSmall,
    },
    btnDisabled: { opacity: 0.45 },
    btnPressed: { borderColor: t.brand ?? t.accent },
    btnSecondary: { borderWidth: sh.hairline, borderColor: t.line2 ?? t.divider },
    btnSecondaryText: { fontSize: 13, ...font(600), color: t.textPrimary },
    // Ink fill for the committing action: the accent means loss here, so an
    // approve button in it would read as danger.
    btnPrimary: { flex: 1.4, backgroundColor: t.textPrimary },
    btnPrimaryPressed: { opacity: 0.85 },
    btnPrimaryText: { fontSize: 13, ...font(800), color: t.background },

    keyCap: {
      paddingHorizontal: 5,
      paddingVertical: 1,
      borderRadius: sh.radiusSmall,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
    },
    keyCapText: { fontSize: 10, ...font(600), color: t.ink3 ?? t.textMuted },

    totalRow: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'baseline',
      marginTop: sh.space[2],
      gap: sh.space[1],
    },
    totalValue: { fontSize: 14, ...font(800), color: t.textPrimary, ...TABULAR },

    helper: { ...TYPE.helper, color: t.ink2 ?? t.textSecondary, marginTop: sh.space[2] },
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
