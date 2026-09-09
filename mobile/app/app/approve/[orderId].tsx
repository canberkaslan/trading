import { View, Text, StyleSheet, ScrollView, Pressable, ActivityIndicator } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useState, useMemo, useRef } from 'react';
import Svg, { Path } from 'react-native-svg';
import { HTTPError } from 'ky';

import { useApproveOrder, usePendingOrders, useRejectOrder, usePortfolio } from '@/api/hooks';
import { api } from '@/api/endpoints';
import { useTheme } from '@/theme/useTheme';
import { authenticate } from '@/auth/biometric';
import { Sheet } from '@/components/Sheet';
import { Tag } from '@/components/Tag';
import { toast } from '@/stores/toast';
import { ratingChip, modelBadge } from '@/theme/rating';
import { formatUsd, formatPct, relativeAgeTr, parseUtc } from '@/utils/format';
import { rejectionReasonTr } from '@/utils/orders';
import { MIN_TOUCH_TARGET, hitSlopFor, orderActionLabel } from '@/utils/a11y';
import type { AgentDecision, OrderListItem } from '@/api/types';
import { font, TABULAR, TYPE } from '@/theme/type';

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
function ScanFace({ color, size = 16 }: { color: string; size?: number }) {
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
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);
  const { t } = useTranslation();
  const { orderId } = useLocalSearchParams<{ orderId: string }>();
  const router = useRouter();
  const { data: orders, isLoading: ordersLoading } = usePendingOrders();
  const { data: portfolio } = usePortfolio();
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
      <SafeAreaView style={styles.container} edges={['top']}>
        <View style={styles.center}>
          <ActivityIndicator color={theme.textPrimary} />
          <Text style={styles.muted}>Emir yükleniyor…</Text>
        </View>
      </SafeAreaView>
    );
  }

  if (!order) {
    return (
      <SafeAreaView style={styles.container} edges={['top']}>
        <View style={styles.center}>
          <Text style={styles.muted}>Bu emir artık bekleyen listesinde değil.</Text>
          <Pressable
            onPress={() => router.back()}
            style={styles.btnCompact}
            accessibilityRole="button"
            accessibilityLabel="Geri dön"
          >
            <Text style={styles.btnSecondaryText}>Geri</Text>
          </Pressable>
        </View>
      </SafeAreaView>
    );
  }

  const target = order;
  const label = `${target.side} ${target.quantity} ${target.ticker} onayla`;
  const sideColor = target.side === 'BUY' ? theme.up : theme.downText ?? theme.down;
  const chip = decision ? ratingChip(theme, decision.rating) : null;

  const notional = decision?.entry_price != null ? decision.entry_price * target.quantity : null;
  const equity = portfolio?.total_equity_usd ?? null;
  const weight = notional != null && equity != null && equity > 0 ? notional / equity : null;
  const submitted = parseUtc(target.submitted_at_utc);
  const age = submitted ? relativeAgeTr(Date.now() - submitted.getTime()) : '—';

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
  if (target.side === 'BUY') {
    if (target.stop_loss > 0) legs.push('stop');
    if (decision?.price_target != null) legs.push('kâr al');
  }
  const legsLabel = legs.length === 2 ? 'bracket' : legs.length === 1 ? `broker'da ${legs[0]}` : null;

  /** Step 1: the sheet that explains what the device lock is about to be for. */
  const askForAuth = () => setFlow({ step: 'auth' });

  /**
   * Steps 2–4. The OS prompt covers the screen while it is up; the sheet is
   * already in its busy state behind it, so the moment the prompt clears the
   * user sees "Doğrulanıyor…" rather than the buttons they just dismissed.
   */
  const runApproval = async () => {
    setFlow({ step: 'verifying' });
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

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView contentContainerStyle={styles.scroll}>
        <Pressable
          onPress={() => router.back()}
          style={styles.back}
          hitSlop={hitSlopFor(18)}
          accessibilityRole="button"
          accessibilityLabel="Emirler listesine dön"
        >
          <Text style={styles.backText}>← Emirler</Text>
        </Pressable>

        <View style={styles.titleRow}>
          {/* 40px per the handoff: the ticker is the first thing to resolve on
              a screen that commits real money. */}
          <Text style={styles.title}>{target.ticker}</Text>
          {decision && chip ? (
            <View style={[styles.ratingChip, { backgroundColor: chip.background, borderColor: chip.borderColor ?? 'transparent' }]}>
              <Text style={[styles.ratingLabel, { color: chip.color }]}>{decision.rating}</Text>
            </View>
          ) : null}
        </View>
        <Text style={styles.subtitle}>
          <Text style={[styles.subtitleStrong, { color: sideColor }]}>
            {target.side} {target.quantity} lot
          </Text>
          {` — ${target.order_type}${legsLabel ? ` · ${legsLabel}` : ''}`}
        </Text>

        {/* The 2px-ruled stat grid: everything the order commits to, in one
            block. Rules rather than a card — the system has no cards. */}
        <View style={styles.grid}>
          {/* No null guards: `formatUsd`/`formatPct` own what a missing figure
              looks like, and every cell in the grid must miss the same way. */}
          <Stat label="Tutar" value={formatUsd(notional)} />
          <Stat label="Portföy %" value={formatPct(weight)} />
          <Stat label="Stop" value={formatUsd(target.stop_loss)} />
          {/* `take_profit` is dead on the wire: it exists in the schema and the
              DB row, and nothing ever writes it — the pipeline never sets it, so
              this cell rendered an em dash on every order while the take-profit
              leg the broker actually receives is built from `price_target`
              (executor.py:301, `config.take_profit_price or decision.price_target`).
              An approval screen must show the number that will be placed, so
              this cell reads that field and says whose it is. */}
          <Stat label="Kâr al (broker)" value={formatUsd(decision?.price_target)} />
          <Stat label="Giriş" value={formatUsd(decision?.entry_price)} />
          <Stat label="Hedef" value={formatUsd(decision?.price_target)} />
          <Stat label="Vade" value={decision?.time_horizon ?? '—'} />
          <Stat label="Süre" value={age} />
        </View>

        <View style={styles.actions}>
          <Pressable
            disabled={busy}
            style={[styles.btn, styles.btnSecondary, busy && styles.btnDisabled]}
            onPress={() => setFlow({ step: 'rejectConfirm' })}
            accessibilityRole="button"
            accessibilityLabel={orderActionLabel(target, 'reject')}
            accessibilityState={{ disabled: busy }}
          >
            <Text style={styles.btnSecondaryText}>Reddet</Text>
          </Pressable>
          <Pressable
            disabled={busy}
            style={[styles.btn, styles.btnPrimary, busy && styles.btnDisabled]}
            onPress={askForAuth}
            accessibilityRole="button"
            accessibilityLabel={orderActionLabel(target, 'approve')}
            accessibilityHint="Cihaz kilidi ile doğrulama ister"
            accessibilityState={{ disabled: busy, busy }}
          >
            <Text style={styles.btnPrimaryText}>Doğrula ve onayla</Text>
            <ScanFace color={theme.background} />
          </Pressable>
        </View>
        <Text style={styles.helper}>
          {age} onaya düştü · cihaz kilidi ile doğrulama ister
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
              <Tag key={`${r.agent}-${i}`} label={`${r.agent} · ${modelBadge(theme, r.model).label}`} variant="neutral" />
            ))}
          </View>
        ) : null}

        {decision ? (
          <Pressable
            onPress={() => router.push(`/trade/${decision.ticker}` as never)}
            hitSlop={hitSlopFor(20)}
            style={styles.detailLink}
            accessibilityRole="button"
            accessibilityLabel={`${decision.ticker} için tüm ajan analizleri`}
          >
            <Text style={styles.detailLinkText}>Tüm ajan analizleri →</Text>
          </Pressable>
        ) : null}

        <Text style={styles.disclaimer}>{t('disclaimer.short')}</Text>
        <Text style={styles.helper}>Reddedilen emir bugün yeniden önerilmez.</Text>
      </ScrollView>

      {/* Step 1 + 2: the ask, then the same sheet in its busy state. */}
      <Sheet
        visible={flow.step === 'auth' || flow.step === 'verifying'}
        title="Cihaz kilidi ile doğrula"
        message={`${label} — Face ID / parmak izi / şifre olmadan emir onaylanamaz.`}
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

function Stat({ label, value }: { label: string; value: string }) {
  const theme = useTheme();
  const statStyles = useMemo(() => makeStatStyles(theme), [theme]);
  return (
    <View style={statStyles.stat}>
      <Text style={statStyles.label}>{label}</Text>
      <Text style={statStyles.value}>{value}</Text>
    </View>
  );
}

const makeStatStyles = (t: Palette) =>
  StyleSheet.create({
    // Thirds, so the eight cells fall into 3 / 3 / 2. A percentage width plus
    // padding rather than a gap: gaps and percentages together overflow the row
    // by the gap on Android.
    stat: { width: '33.33%', paddingRight: 12, marginBottom: 12 },
    label: { color: t.textSecondary, ...TYPE.helper },
    value: { color: t.textPrimary, fontSize: 16, ...font(800), marginTop: 2, ...TABULAR },
  });

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.background },
    scroll: { paddingHorizontal: 16, paddingTop: 20, paddingBottom: 32 },
    center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24, gap: 16 },
    back: { marginBottom: 12, minHeight: 24, justifyContent: 'center' },
    backText: { color: t.accent700 ?? t.accent, fontSize: 14, ...font(600) },
    titleRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
    title: { color: t.textPrimary, fontSize: 40, ...font(800), letterSpacing: -0.5 },
    ratingChip: { paddingHorizontal: 8, paddingVertical: 3, borderWidth: 1 },
    ratingLabel: { fontSize: 11, letterSpacing: 0.22, ...font(600) },
    subtitle: { color: t.textSecondary, ...TYPE.body, marginTop: 2 },
    subtitleStrong: { ...TYPE.body, ...font(800) },
    // A ruled block, not a filled card — the 2px rules do the separating.
    grid: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      marginTop: 16,
      paddingTop: 14,
      // The cells carry their own 12px bottom margin, so the block's own bottom
      // padding is short by that much.
      paddingBottom: 2,
      borderTopWidth: 2,
      borderBottomWidth: 2,
      borderColor: t.divider,
    },
    muted: { color: t.textSecondary, ...TYPE.body, marginTop: 4 },
    helper: { color: t.textSecondary, ...TYPE.helper, marginTop: 8 },
    err: { color: t.accent700 ?? t.danger, ...TYPE.body },
    actions: { flexDirection: 'row', gap: 12, marginTop: 16 },
    btn: {
      flex: 1,
      flexDirection: 'row',
      gap: 8,
      paddingHorizontal: 12,
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: 48,
    },
    btnCompact: {
      paddingHorizontal: 24,
      borderWidth: 1,
      borderColor: t.textPrimary,
      justifyContent: 'center',
      alignItems: 'center',
      minHeight: MIN_TOUCH_TARGET,
    },
    btnDisabled: { opacity: 0.45 },
    // Ink fill for the committing action. In Modernist the accent is reserved
    // for loss and for warnings — an approve button in it would read as danger.
    btnPrimary: { backgroundColor: t.textPrimary },
    btnPrimaryText: { color: t.background, fontSize: 14, ...font(800) },
    btnSecondary: { backgroundColor: 'transparent', borderWidth: 1, borderColor: t.textPrimary },
    btnSecondaryText: { color: t.textPrimary, fontSize: 14, ...font(600) },
    kicker: { color: t.accent700 ?? t.accent, ...TYPE.kicker, marginTop: 24, marginBottom: 8 },
    body: { color: t.textPrimary, ...TYPE.body, lineHeight: 21 },
    council: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 6, marginTop: 20 },
    councilLabel: { color: t.textSecondary, ...TYPE.helper },
    detailLink: { marginTop: 12, minHeight: 24, justifyContent: 'center', alignSelf: 'flex-start' },
    detailLinkText: { color: t.accent700 ?? t.accent, fontSize: 14, ...font(600) },
    disclaimer: { color: t.textSecondary, ...TYPE.helper, marginTop: 24, lineHeight: 16 },
  });
