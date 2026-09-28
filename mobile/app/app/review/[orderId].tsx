import { View, Text, StyleSheet, ScrollView, Pressable, ActivityIndicator } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useIsAdmin } from '@/api/useMe';
import { ErrorState } from '@/components/ErrorState';
import { useTranslation } from 'react-i18next';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useState, useMemo, useRef } from 'react';
import Svg, { Path } from 'react-native-svg';
import { HTTPError } from 'ky';

import { useApproveOrder, usePendingOrders, useRejectOrder, usePortfolio, useReadiness } from '@/api/hooks';
import { api } from '@/api/endpoints';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { authenticate } from '@/auth/biometric';
import { Card } from '@/components/Card';
import { Sheet } from '@/components/Sheet';
import { StatCell } from '@/components/StatCell';
import { Tag } from '@/components/Tag';
import { toast } from '@/stores/toast';
import { ratingVariant, modelBadge } from '@/theme/rating';
import { formatUsd, formatPct, relativeAgeTr, parseUtc } from '@/utils/format';
import { rejectionReasonTr } from '@/utils/orders';
import { topWeightTone } from '@/utils/concentration';
import { MIN_TOUCH_TARGET, hitSlopFor, orderActionLabel } from '@/utils/a11y';
import type { AgentDecision, OrderListItem } from '@/api/types';
import { font, TYPE } from '@/theme/type';

/**
 * The approval flow, as one state machine.
 *
 * The handoff specifies a sequence, not a set of independent alerts: device
 * lock → "Doğrulanıyor…" → the broker's answer → the row leaving the list. A
 * boolean per dialog let two of them be true at once and had no state at all
 * for the middle step, which is why the spinner the handoff asks for did not
 * exist. One value means exactly one sheet can be on screen.
 */
type Flow =
  | { step: 'idle' }
  | { step: 'auth' }
  | { step: 'verifying' }
  | { step: 'authFailed'; message: string }
  | { step: 'sent'; brokerOrderId: string; status: string }
  | { step: 'failed'; title: string; message: string }
  | { step: 'rejectConfirm' }
  | { step: 'rejecting' };

/** Lucide `scan-face` — the handoff's mark for "this asks for the device lock". */
function ScanFace({ color, size = 18 }: { color: string; size?: number }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <Path d="M3 7V5a2 2 0 0 1 2-2h2" />
      <Path d="M17 3h2a2 2 0 0 1 2 2v2" />
      <Path d="M21 17v2a2 2 0 0 1-2 2h-2" />
      <Path d="M7 21H5a2 2 0 0 1-2-2v-2" />
      <Path d="M8 14s1.5 2 4 2 4-2 4-2" />
      <Path d="M9 9h.01" />
      <Path d="M15 9h.01" />
    </Svg>
  );
}

/** Lucide `line-chart` — the prototype's square button beside the ticker. */
function LineChart({ color, size = 18 }: { color: string; size?: number }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <Path d="M3 3v18h18" />
      <Path d="M7 14l4-5 4 3 5-7" />
    </Svg>
  );
}

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
 * What actually stopped the order, in the words the backend used.
 *
 * 422 is the executor's own refusal, and its body carries `refusal_reasons` —
 * the same machine-keyed strings the history tab renders (`stale_decision:
 * age=31.2h max=24.0h`, `no_tp_headroom: current=$…`). Naming three guards
 * inline as a guess restated what `rejectionReasonTr` already encodes, and was
 * wrong whenever a fourth fired: it told the operator the decision had gone
 * stale when the real answer was the PDT flag or a closed market. 409 is the
 * pre-flight refusal — an armed kill switch, or an order already at the broker
 * — and its detail is a plain sentence worth showing. Everything else is
 * transport, and must not be dressed up as a risk decision.
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

export default function ApproveOrderScreen() {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const { t: tr } = useTranslation();
  const { orderId } = useLocalSearchParams<{ orderId: string }>();
  const router = useRouter();
  const {
    data: orders,
    isLoading: ordersLoading,
    isError: ordersFailed,
    error: ordersError,
    refetch: refetchOrders,
  } = usePendingOrders();
  const { data: portfolio } = usePortfolio();
  const { data: readiness } = useReadiness();
  const isAdmin = useIsAdmin();
  const approve = useApproveOrder();
  const reject = useRejectOrder();
  const [decision, setDecision] = useState<AgentDecision | null>(null);
  const [decisionError, setDecisionError] = useState<string | null>(null);
  const [flow, setFlow] = useState<Flow>({ step: 'idle' });

  const listed: OrderListItem | undefined = orders?.find((o) => o.order_id === orderId);

  /**
   * A successful approve invalidates the pending list, so the order this screen
   * is about disappears from it milliseconds before the "Emir gönderildi" sheet
   * would be read. Rendering the "no longer pending" state at that moment
   * replaces the confirmation with what looks like an error, and the operator
   * never learns the broker id. So while a flow is in flight the screen keeps
   * drawing the last version it saw; once the flow is idle the live list is
   * authoritative again, and an order approved from another device correctly
   * reads as gone.
   */
  const lastSeen = useRef<OrderListItem | null>(null);
  if (listed) lastSeen.current = listed;
  const order: OrderListItem | undefined =
    listed ?? (flow.step === 'idle' ? undefined : lastSeen.current ?? undefined);

  // Fetch the underlying decision for full PM reasoning. Keyed on the decision
  // id rather than the order object: the pending list is re-fetched every ten
  // seconds and hands back a fresh object each time, which re-ran this — and
  // therefore re-fetched an immutable decision — on that same cadence.
  const decisionId = order?.decision_id;
  useEffect(() => {
    if (!decisionId) return;
    let cancelled = false;
    api
      .getDecision(decisionId)
      .then((d) => { if (!cancelled) setDecision(d); })
      .catch((e) => { if (!cancelled) setDecisionError(String(e)); });
    return () => { cancelled = true; };
  }, [decisionId]);

  if (!orderId) return null;

  // Push deep-links open this screen before the pending list has loaded; showing
  // "not in pending list" during that race falsely tells the user the order is gone.
  if (!order && ordersLoading) {
    return (
      <SafeAreaView style={styles.screen} edges={['top']}>
        <View style={styles.center}>
          <ActivityIndicator color={t.textPrimary} />
          <Text style={styles.muted}>Emir yükleniyor…</Text>
        </View>
      </SafeAreaView>
    );
  }

  /**
   * A failed read is not an absent order.
   *
   * The loading race above is guarded for exactly this reason, but the error
   * case fell straight through to it: a 401 leaves `orders` undefined with
   * `ordersLoading` false, so the screen told the operator "Bu emir artık
   * bekleyen listesinde değil" — that someone had already approved, rejected
   * or filled it. This screen is opened by a push deep-link and it approves
   * real orders, so a false claim that an order is gone is the worst sentence
   * the app can produce: the operator stops looking for a trade that is still
   * sitting there waiting for them.
   *
   * ErrorState knows the difference between a refused credential and a dead
   * socket, so it says which one happened and offers the matching action.
   */
  if (!order && ordersFailed) {
    return (
      <SafeAreaView style={styles.screen} edges={['top']}>
        <ErrorState
          title="Bekleyen emirler okunamadı"
          detail={ordersError}
          onRetry={() => void refetchOrders()}
        />
      </SafeAreaView>
    );
  }

  /* The prototype's `approveGone` slot: a dashed card, never a filled one — an
     order that is no longer there should read as an absence. */
  if (!order) {
    return (
      <SafeAreaView style={styles.screen} edges={['top']}>
        <View style={styles.center}>
          <Card tone="dashed" style={styles.gone}>
            <Text style={styles.goneText}>
              Bu emir artık bekleyen listesinde değil — onaylanmış, reddedilmiş ya da dolmuş olabilir.
            </Text>
            <Pressable
              onPress={() => router.back()}
              style={({ pressed }) => [styles.btnCompact, pressed && styles.pressed]}
              accessibilityRole="button"
              accessibilityLabel="Emirler listesine dön"
            >
              <Text style={styles.btnSecondaryText}>Emirlere dön</Text>
            </Pressable>
          </Card>
        </View>
      </SafeAreaView>
    );
  }

  const target = order;
  const label = `${target.side} ${target.quantity} ${target.ticker} onayla`;
  const buy = target.side === 'BUY';
  const sideColor = buy ? t.up : t.downText ?? t.down;

  const notional = decision?.entry_price != null ? decision.entry_price * target.quantity : null;
  const equity = portfolio?.total_equity_usd ?? null;
  const weight = notional != null && equity != null && equity > 0 ? notional / equity : null;
  const submitted = parseUtc(target.submitted_at_utc);
  const age = submitted ? relativeAgeTr(Date.now() - submitted.getTime()) : '—';

  /*
   * The single-name cap is not this screen's rule to invent: `topWeightTone`
   * already owns "is this weight over the 10% cap", and the Risk screen reads
   * the same helper. All the design does here is colour the cell it applies to,
   * so the figure and the judgement cannot drift apart.
   */
  const weightTone = weight != null ? topWeightTone(weight * 100) : null;
  const weightColor =
    weightTone === 'down' ? t.downText ?? t.down : weightTone === 'warning' ? t.warning : undefined;

  /**
   * What the broker will actually be left holding behind this order.
   *
   * The prototype prints "· bracket" on every order, but the executor attaches
   * protective legs for BUY only, and the two legs attach independently
   * (`use_bracket and order.side == "BUY"`, then `tp`/`sl`, in
   * `execution/executor.py`). So the flat label is wrong twice: a SELL gets no
   * legs at all, and a BUY with no target goes out with the stop alone.
   *
   * Note which field the take-profit leg comes from: `price_target` — the grid's
   * "Hedef" — not `take_profit`. The condition below reads the same field the
   * executor does, so the label cannot promise a leg the executor will skip.
   *
   * Nothing in the approve response reports the legs back, so this states what
   * will be sent and says nothing when nothing will be attached.
   */
  const legs: string[] = [];
  if (buy) {
    if (target.stop_loss > 0) legs.push('stop');
    if (decision?.price_target != null) legs.push('kâr al');
  }
  const legsLabel = legs.length === 2 ? 'bracket' : legs.length === 1 ? `broker'da ${legs[0]}` : null;
  const legsNote =
    legs.length === 2
      ? 'Stop ve kâr al bacakları broker tarafında kalır.'
      : legs.length === 1
        ? `${legs[0] === 'stop' ? 'Stop' : 'Kâr al'} bacağı broker tarafında kalır.`
        : 'Bracket bacağı eklenmez.';

  /** Step 1: the sheet that explains what the device lock is about to be for. */
  const askForAuth = () => setFlow({ step: 'auth' });

  /**
   * Steps 2–4. The OS prompt covers the screen while it is up; the sheet is
   * already in its busy state behind it, so the moment the prompt clears the
   * user sees "Doğrulanıyor…" rather than the buttons they just dismissed.
   */
  const runApproval = async () => {
    setFlow({ step: 'verifying' });
    const { success, mode: authMode } = await authenticate(label);
    if (!success) {
      setFlow({
        step: 'authFailed',
        message:
          authMode === 'none'
            ? 'Cihazınızda ekran kilidi (Face/Touch ID veya şifre) tanımlı değil. Emir onayı için lütfen bir cihaz kilidi kurun.'
            : 'Kimlik doğrulama olmadan emir onaylanamaz.',
      });
      return;
    }
    try {
      const result = await approve.mutateAsync(target.order_id);
      setFlow({ step: 'sent', brokerOrderId: result.broker_order_id, status: result.status });
    } catch (e) {
      setFlow(await approveFailure(e));
    }
  };

  const runReject = async () => {
    setFlow({ step: 'rejecting' });
    try {
      await reject.mutateAsync(target.order_id);
      router.back();
      toast(`${target.ticker} ${target.side} ${target.quantity} reddedildi — bugün yeniden önerilmez`);
    } catch (e) {
      setFlow({ step: 'failed', title: 'Reddedilemedi', message: String(e) });
    }
  };

  const busy = flow.step === 'verifying' || flow.step === 'rejecting';
  /*
   * Both buttons were already refused for a non-admin, and both still drew
   * themselves at full strength — an ink-filled "Doğrula ve onayla" that does
   * nothing when tapped. A control that looks live and is not teaches the
   * operator to distrust the screen, which on an approval screen is the whole
   * product. The gate is unchanged; only its appearance now tells the truth,
   * and a line under the pair says which of the two reasons applies.
   */
  const blocked = !isAdmin;
  const disabled = busy || blocked;
  const mode = readiness?.trading_mode;
  const isLive = mode === 'live';

  /* The three figures the decision turns on, repeated inside the sheet so the
     operator is not asked to remember what they tapped. */
  const sheetSummary = [
    { label: 'Tutar', value: formatUsd(notional) },
    { label: 'Stop', value: formatUsd(target.stop_loss) },
    { label: 'Portföy %', value: formatPct(weight), color: weightColor },
  ];

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <ScrollView contentContainerStyle={styles.content}>
        <Pressable
          onPress={() => router.back()}
          style={styles.back}
          hitSlop={hitSlopFor(24)}
          accessibilityRole="button"
          accessibilityLabel="Emirler listesine dön"
        >
          <Text style={styles.backText}>← Emirler</Text>
        </Pressable>

        <View style={styles.modeRow}>
          <Tag
            label={mode == null ? 'MOD ?' : isLive ? 'LIVE — GERÇEK PARA' : 'PAPER'}
            variant={mode == null ? 'outlineMuted' : isLive ? 'down' : 'neutral'}
            caps
          />
        </View>

        {/* The prototype's header: ticker + rating on the left, the chart
            button pushed to the right edge by `margin-left:auto`. */}
        <View style={styles.titleRow}>
          <View style={styles.titleBlock}>
            <View style={styles.tickerLine}>
              <Text style={styles.ticker} accessibilityRole="header">
                {target.ticker}
              </Text>
              {decision ? (
                <Tag label={decision.rating} variant={ratingVariant(decision.rating)} size="sm" caps />
              ) : null}
            </View>
            <Text style={styles.subtitle}>
              <Text style={[styles.subtitleStrong, { color: sideColor }]}>
                {target.side} {target.quantity} lot
              </Text>
              {` — ${target.order_type}${legsLabel ? ` · ${legsLabel}` : ''}`}
            </Text>
          </View>
          <Pressable
            onPress={() => router.push(`/trade/${target.ticker}` as never)}
            style={({ pressed }) => [styles.iconBtn, pressed && styles.pressed]}
            accessibilityRole="button"
            accessibilityLabel={`${target.ticker} grafiğini ve analizlerini aç`}
          >
            <LineChart color={t.textPrimary} />
          </Pressable>
        </View>

        <Text style={styles.disclaimer}>{tr('disclaimer.short')}</Text>

        {/* Everything the order commits to, in one card. Three-up, as the
            prototype grids it. */}
        <Card style={styles.grid}>
          {/* No null guards: `formatUsd`/`formatPct` own what a missing figure
              looks like, and every cell in the grid must miss the same way. */}
          <StatCell size="sm" style={styles.cell} label="Tutar" value={formatUsd(notional)} />
          <StatCell
            size="sm"
            style={styles.cell}
            label="Portföy %"
            value={formatPct(weight)}
            valueColor={weightColor}
          />
          <StatCell size="sm" style={styles.cell} label="Stop" value={formatUsd(target.stop_loss)} />
          {/* `take_profit` is dead on the wire: it exists in the schema and the
              DB row, and nothing ever writes it — the pipeline never sets it, so
              this cell rendered an em dash on every order while the take-profit
              leg the broker actually receives is built from `price_target`
              (executor.py:301, `config.take_profit_price or decision.price_target`).
              An approval screen must show the number that will be placed, so
              this cell reads that field and says whose it is. */}
          <StatCell size="sm" style={styles.cell} label="Kâr al (broker)" value={formatUsd(decision?.price_target)} />
          <StatCell size="sm" style={styles.cell} label="Giriş" value={formatUsd(decision?.entry_price)} />
          <StatCell size="sm" style={styles.cell} label="Hedef" value={formatUsd(decision?.price_target)} />
          <StatCell size="sm" style={styles.cell} label="Vade" value={decision?.time_horizon ?? '—'} />
          <StatCell size="sm" style={styles.cell} label="Süre" value={age} />
        </Card>

        <View style={styles.actions}>
          <Pressable
            disabled={disabled}
            style={({ pressed }) => [
              styles.btn,
              styles.btnSecondary,
              pressed && !disabled && styles.pressed,
              disabled && styles.btnSecondaryDisabled,
            ]}
            onPress={() => setFlow({ step: 'rejectConfirm' })}
            accessibilityRole="button"
            accessibilityLabel={orderActionLabel(target, 'reject')}
            accessibilityState={{ disabled }}
          >
            <Text style={[styles.btnSecondaryText, disabled && styles.btnTextDisabled]}>Reddet</Text>
          </Pressable>
          <Pressable
            disabled={disabled}
            style={({ pressed }) => [
              styles.btn,
              styles.btnPrimary,
              styles.btnPrimaryWide,
              pressed && !disabled && styles.btnPrimaryPressed,
              disabled && styles.btnPrimaryDisabled,
            ]}
            onPress={askForAuth}
            accessibilityRole="button"
            accessibilityLabel={orderActionLabel(target, 'approve')}
            accessibilityHint="Cihaz kilidi ile doğrulama ister"
            accessibilityState={{ disabled, busy }}
          >
            <Text style={[styles.btnPrimaryText, disabled && styles.btnPrimaryTextDisabled]}>
              Doğrula ve onayla
            </Text>
            <ScanFace color={disabled ? t.ink3 ?? t.textMuted : t.inkInv ?? t.background} />
          </Pressable>
        </View>
        <Text style={styles.helper}>
          {blocked
            ? 'Bu hesapta emir onaylama yetkisi yok — onay ve red yöneticiye açıktır.'
            : `${age} onaya düştü · cihaz kilidi ile doğrulama ister · ${legsNote}`}
        </Text>

        <Text style={styles.kicker}>Portföy yöneticisi gerekçesi</Text>
        {decisionError ? (
          <Text style={styles.err}>{decisionError}</Text>
        ) : decision ? (
          <Text style={styles.body}>{decision.final_decision_text ?? 'Gerekçe metni yok.'}</Text>
        ) : (
          <Text style={styles.muted}>Gerekçe yükleniyor…</Text>
        )}

        {decision?.reasoning?.length ? (
          <View style={styles.council}>
            <Text style={styles.councilLabel}>Konsey</Text>
            {decision.reasoning.map((r, i) => (
              <Tag
                key={`${r.agent}-${i}`}
                label={`${r.agent} · ${modelBadge(t, r.model).label}`}
                variant="neutral"
                size="sm"
              />
            ))}
          </View>
        ) : null}

        {decision ? (
          <Pressable
            onPress={() => router.push(`/trade/${decision.ticker}` as never)}
            hitSlop={hitSlopFor(24)}
            style={styles.detailLink}
            accessibilityRole="button"
            accessibilityLabel={`${decision.ticker} için tüm ajan analizleri`}
          >
            <Text style={styles.detailLinkText}>Tüm ajan analizleri →</Text>
          </Pressable>
        ) : null}

        <Text style={styles.disclaimer}>{tr('disclaimer.short')}</Text>
        <Text style={styles.footNote}>Reddedilen emir bugün yeniden önerilmez.</Text>
      </ScrollView>

      {/* Step 1 + 2: the ask, then the same sheet in its busy state. */}
      <Sheet
        visible={flow.step === 'auth' || flow.step === 'verifying'}
        title="Cihaz kilidi ile doğrula"
        message={`${label} — Face ID / parmak izi / şifre olmadan emir onaylanamaz.`}
        summary={sheetSummary}
        busy={flow.step === 'verifying'}
        busyLabel="Doğrulanıyor…"
        onDismiss={flow.step === 'auth' ? () => setFlow({ step: 'idle' }) : undefined}
        actions={[
          { label: 'Vazgeç', onPress: () => setFlow({ step: 'idle' }) },
          { label: 'Doğrula', primary: true, onPress: () => void runApproval() },
        ]}
      />

      <Sheet
        visible={flow.step === 'authFailed'}
        title="Doğrulama gerekli"
        message={flow.step === 'authFailed' ? flow.message : ''}
        onDismiss={() => setFlow({ step: 'idle' })}
        actions={[{ label: 'Tamam', primary: true, onPress: () => setFlow({ step: 'idle' }) }]}
      />

      {/* Step 3: the broker's answer. Dismissing it pops the screen, and the
          approve mutation has already invalidated the pending list — so the row
          is gone by the time the list is back on screen. */}
      <Sheet
        visible={flow.step === 'sent'}
        title="Emir gönderildi"
        message={
          flow.step === 'sent'
            ? `Broker emri ${flow.brokerOrderId} · durum ${flow.status}${
                legs.length ? ` · ${legs.join(' + ')} broker tarafında bırakıldı.` : '.'
              }`
            : ''
        }
        onDismiss={() => router.back()}
        actions={[{ label: 'Tamam', primary: true, onPress: () => router.back() }]}
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
        message={`${label} — emir gönderilmeyecek; günlük koşu bugün yeniden önermez.`}
        summary={sheetSummary}
        busy={flow.step === 'rejecting'}
        busyLabel="Reddediliyor…"
        onDismiss={flow.step === 'rejectConfirm' ? () => setFlow({ step: 'idle' }) : undefined}
        actions={[
          { label: 'Vazgeç', onPress: () => setFlow({ step: 'idle' }) },
          { label: 'Reddet', destructive: true, onPress: () => void runReject() },
        ]}
      />
    </SafeAreaView>
  );
}

type Palette = ReturnType<typeof useTheme>;

/** The prototype's 52px commit buttons — above the 44pt floor either way. */
const ACTION_HEIGHT = 52;

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    screen: { flex: 1, backgroundColor: t.background },
    content: { paddingHorizontal: sh.space[3], paddingTop: sh.space[2], paddingBottom: sh.space[5] },
    center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: sh.space[4] },

    gone: { alignItems: 'center', paddingVertical: sh.space[4], gap: sh.space[3] },
    goneText: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, textAlign: 'center', lineHeight: 19 },

    back: { marginBottom: sh.space[1], minHeight: 24, justifyContent: 'center' },
    backText: { ...TYPE.body, ...font(600), color: t.brand ?? t.accent },
    modeRow: { flexDirection: 'row', marginBottom: sh.space[2] },

    titleRow: { flexDirection: 'row', alignItems: 'center', gap: sh.space[2] },
    titleBlock: { flex: 1, minWidth: 0 },
    tickerLine: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1], flexWrap: 'wrap' },
    /* 34px, as the prototype sizes it: the ticker is the first thing to resolve
       on a screen that commits real money. */
    ticker: { ...font(800), fontSize: 34, letterSpacing: -1, color: t.textPrimary },
    subtitle: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, marginTop: 2 },
    subtitleStrong: { ...TYPE.body, ...font(800) },
    iconBtn: {
      width: MIN_TOUCH_TARGET,
      height: MIN_TOUCH_TARGET,
      borderRadius: sh.radiusSmall,
      borderWidth: sh.hairline,
      borderColor: t.line ?? t.divider,
      backgroundColor: t.surface,
      alignItems: 'center',
      justifyContent: 'center',
    },

    /* Three-up. A percentage width plus padding rather than a `gap`: gaps and
       percentages together overflow the row by the gap on Android. */
    grid: { flexDirection: 'row', flexWrap: 'wrap', marginTop: sh.space[2] },
    cell: { width: '33.33%', paddingRight: sh.space[1], marginBottom: sh.space[2] },

    actions: { flexDirection: 'row', gap: sh.space[2], marginTop: sh.space[2] },
    btn: {
      flex: 1,
      flexDirection: 'row',
      gap: sh.space[1],
      paddingHorizontal: sh.space[2],
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: ACTION_HEIGHT,
      borderRadius: sh.radius,
    },
    /* The prototype weights the pair 1 : 2 — the committing action is the wide
       one, and Reddet is deliberately not its equal. */
    btnPrimaryWide: { flex: 2 },
    // Ink fill for the committing action. Not the accent: under every palette
    // in this app the accent means loss or warning, so an approve button in it
    // would read as danger.
    btnPrimary: { backgroundColor: t.textPrimary },
    btnPrimaryPressed: { opacity: 0.86 },
    btnPrimaryText: { ...TYPE.bodyStrong, fontSize: 15, ...font(800), color: t.inkInv ?? t.background },
    /*
     * A disabled control must not merely be dimmer — on a dark ground a 45%
     * ink slab still reads as a filled button. So the fill drops out entirely
     * and the button falls back to a rule, which is the shape the system
     * already uses for "not the action".
     */
    btnPrimaryDisabled: { backgroundColor: t.surface, borderWidth: sh.hairline, borderColor: t.line ?? t.divider },
    btnPrimaryTextDisabled: { color: t.ink3 ?? t.textMuted },
    btnSecondary: { backgroundColor: t.surface, borderWidth: sh.hairline, borderColor: t.line2 ?? t.divider },
    btnSecondaryDisabled: { borderColor: t.line ?? t.divider },
    btnSecondaryText: { ...TYPE.bodyStrong, fontSize: 15, ...font(800), color: t.textPrimary },
    btnTextDisabled: { color: t.ink3 ?? t.textMuted },
    btnCompact: {
      paddingHorizontal: sh.space[3],
      borderRadius: sh.radius,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
      justifyContent: 'center',
      alignItems: 'center',
      minHeight: MIN_TOUCH_TARGET,
    },
    pressed: { borderColor: t.brand ?? t.accent },

    kicker: { ...TYPE.kicker, color: t.brand ?? t.accent, marginTop: sh.space[4], marginBottom: sh.space[1] },
    body: { ...TYPE.body, color: t.textPrimary, lineHeight: 21 },
    muted: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, marginTop: sh.space[0] },
    helper: { ...TYPE.helper, color: t.ink3 ?? t.textMuted, marginTop: sh.space[1], textAlign: 'center', lineHeight: 16 },
    err: { ...TYPE.body, color: t.downText ?? t.danger },

    council: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: sh.space[0] + 2, marginTop: sh.space[2] },
    councilLabel: { ...TYPE.helper, color: t.ink3 ?? t.textMuted },
    detailLink: { marginTop: sh.space[2], minHeight: 24, justifyContent: 'center', alignSelf: 'flex-start' },
    detailLinkText: { ...TYPE.body, ...font(600), color: t.brand ?? t.accent },

    disclaimer: { ...TYPE.helper, color: t.ink3 ?? t.textMuted, marginTop: sh.space[3], lineHeight: 16 },
    footNote: { ...TYPE.helper, color: t.ink3 ?? t.textMuted, marginTop: sh.space[0], lineHeight: 16 },
  });
