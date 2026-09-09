/**
 * Screen 9 — Ayarlar.
 *
 * Section order is the handoff's: Hesap & mod · Eval scorecard · Risk & uyarılar
 * · Emir gönderimi · Strateji · Bildirimler · Görünüm · Sistem, each opened by a
 * 2px rule.
 *
 * Two things this screen deliberately refuses to do.
 *
 * First, the kill switch is gone. It used to live here, three taps deep behind
 * a scroll, next to the font picker — the one control that stops the book, in
 * the same list as "Koyu tema". It now has its own screen with the circuit
 * breakers and the portfolio limits it actually belongs to, and this screen
 * keeps only a link to it.
 *
 * Second, every control here that cannot really write is drawn as a read-only
 * state, not a switch. `refuse_outside_hours` and `use_bracket` are fields of
 * the backend's `ExecutionConfig`; sizing method and the single-name cap are
 * `SizingMethod` / `PortfolioLimits`. `src/api/endpoints.ts` has no route that
 * writes any of them and no route that reads them either, so a live-looking
 * toggle would be a lie twice over: it would neither send the change nor be
 * showing the server's current answer. They render in the handoff's toggle
 * shape, marked with their source, and the section says so in words.
 *
 * The toggle's 150ms knob is the only animation the system has, and the one
 * switch that earns it — push permission — is the one that genuinely writes
 * (to the OS, and to `POST /v1/notifications/register`).
 *
 * Third: every fixed figure printed here is traceable to something that runs.
 * The prototype's Strateji grid carried a "2 × ATR(14)" stop and a 20-name
 * universe, and its Bildirimler row a 09:00 weekly push; the deployed system
 * takes its stop from the agent's own decision, trades the eleven tickers in
 * `daily_run.sh`, and pushes the scorecard on `eval-report.timer` at 14:00 UTC.
 * A settings screen is where an operator goes to check what the machine is
 * configured to do, so a plausible-looking wrong number is worse here than a
 * blank.
 */

import {
  View,
  Text,
  StyleSheet,
  Pressable,
  ScrollView,
  Animated,
  AppState,
  Linking,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import Constants from 'expo-constants';
import Svg, { Path } from 'react-native-svg';

import { api } from '@/api/endpoints';
import {
  useHealth,
  useReadiness,
  useDecisions,
  useEval,
  useActionability,
  useConcentration,
} from '@/api/hooks';
import { inertiaNote } from '@/utils/actionability';
import { topWeightTone } from '@/utils/concentration';
import { getPermissionStatus, requestAndRegisterPush, type PushPermission } from '@/notifications';
import { useInboxStore } from '@/stores/notifications';
import { useAuthStore } from '@/stores/auth';
import { toast } from '@/stores/toast';
import { useTheme, useThemeName, useSetTheme } from '@/theme/useTheme';
import { setLanguage, type Language } from '@/i18n';
import type { ThemeName } from '@/theme/colors';
import type { TradingMode } from '@/api/types';
import { unreadCount } from '@/utils/inbox';
import { MIN_TOUCH_TARGET, hitSlopFor } from '@/utils/a11y';
import { relativeAgeTr, parseUtc } from '@/utils/format';
import { Tag } from '@/components/Tag';
import { Seg } from '@/components/Seg';
import { Sheet } from '@/components/Sheet';
import { font, TABULAR, TYPE } from '@/theme/type';

type Palette = ReturnType<typeof useTheme>;

/** The handoff's switch: 40×22 track, 16px square knob, 3px inset. */
const TOGGLE_W = 40;
const TOGGLE_H = 22;
const KNOB = 16;
const KNOB_PAD = 3;
const KNOB_OFF_X = KNOB_PAD;
const KNOB_ON_X = TOGGLE_W - KNOB - KNOB_PAD;
/** The only transition in the system. */
const TOGGLE_MS = 150;

/** The app's own backend base URL — the one endpoint this screen can honestly print. */
const API_URL = (Constants.expoConfig?.extra?.apiUrl ?? 'http://localhost:8000') as string;
const APP_VERSION = Constants.expoConfig?.version ?? '—';

/**
 * `PortfolioLimits.max_position_pct` (0.10), as a percent. Named once because
 * it is both the figure in the cap box and the threshold `topWeightTone` tones
 * the live top weight against — two copies of one server-side limit is exactly
 * how the box and its own warning drift apart.
 */
const SINGLE_NAME_CAP_PCT = 10;

/**
 * `toFixed` emits an ASCII hyphen and there is no way to ask it for U+2212.
 * `utils/format` prepends the true minus for money and percents; a bare ratio
 * (Sharpe, Sortino, Calmar) has no formatter there, and every one of them goes
 * negative on a losing book — which is precisely when the scorecard is read.
 */
const MINUS = '−';
const fixed = (n: number, digits: number): string =>
  `${n < 0 ? MINUS : ''}${Math.abs(n).toFixed(digits)}`;

const permissionCopy = (t: Palette): Record<PushPermission, { text: string; color: string }> => ({
  granted: { text: '● açık', color: t.textPrimary },
  denied: { text: '● kapalı (cihaz ayarları)', color: t.accent700 ?? t.down },
  undetermined: { text: '● izin verilmedi', color: t.warning },
  unsupported: { text: '● bu cihazda çalışmaz', color: t.textSecondary },
});

/** What the push row says under its label, per OS permission state. */
const PUSH_DESC: Record<PushPermission, string> = {
  granted: 'Onay bekleyen ve gerçekleşen emirler için. Kapatmak cihaz ayarlarından.',
  denied: 'Cihaz ayarlarından kapatılmış — açmak için ayarlara git.',
  undetermined: 'Onay bekleyen ve gerçekleşen emirler için.',
  unsupported: 'Bu cihaz push desteklemiyor (simülatörde çalışmaz).',
};

function Chevron({ color }: { color: string }) {
  return (
    <Svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <Path d="m9 18 6-6-6-6" />
    </Svg>
  );
}

/** A section, opened by the 2px rule. `first` sits directly under the title. */
function Section({
  title,
  right,
  first,
  children,
}: {
  title: string;
  right?: React.ReactNode;
  first?: boolean;
  children: React.ReactNode;
}) {
  const styles = makeStyles(useTheme());
  return (
    <View style={[styles.section, first && styles.sectionFirst]}>
      <View style={styles.sectionHead}>
        <Text style={styles.sectionTitle}>{title}</Text>
        {right}
      </View>
      {children}
    </View>
  );
}

/**
 * The switch. `readOnly` renders the same shape without a press target — the
 * state it shows is the server's or the OS's, and nothing here can change it.
 */
function Toggle({
  on,
  readOnly,
  onPress,
  accessibilityLabel,
  accessibilityHint,
}: {
  on: boolean;
  readOnly?: boolean;
  onPress?: () => void;
  accessibilityLabel: string;
  accessibilityHint?: string;
}) {
  const t = useTheme();
  const styles = useMemo(() => makeStyles(t), [t]);
  const x = useRef(new Animated.Value(on ? KNOB_ON_X : KNOB_OFF_X)).current;

  useEffect(() => {
    Animated.timing(x, {
      toValue: on ? KNOB_ON_X : KNOB_OFF_X,
      duration: TOGGLE_MS,
      useNativeDriver: true,
    }).start();
  }, [on, x]);

  const track = (
    <View style={[styles.track, { backgroundColor: on ? t.textPrimary : t.neutral400 ?? t.textMuted }]}>
      <Animated.View
        style={[styles.knob, { backgroundColor: t.background, transform: [{ translateX: x }] }]}
      />
    </View>
  );

  if (readOnly) {
    return (
      <View
        accessible
        accessibilityRole="switch"
        accessibilityLabel={accessibilityLabel}
        accessibilityHint={accessibilityHint}
        accessibilityState={{ checked: on, disabled: true }}
      >
        {track}
      </View>
    );
  }

  return (
    <Pressable
      onPress={onPress}
      hitSlop={hitSlopFor(TOGGLE_H)}
      accessibilityRole="switch"
      accessibilityLabel={accessibilityLabel}
      accessibilityHint={accessibilityHint}
      accessibilityState={{ checked: on }}
    >
      {track}
    </Pressable>
  );
}

function ToggleRow({
  label,
  desc,
  source,
  on,
  readOnly,
  onPress,
  hint,
}: {
  label: string;
  desc: string;
  /** Where the value comes from, when the app cannot write it. */
  source?: string;
  on: boolean;
  readOnly?: boolean;
  onPress?: () => void;
  hint?: string;
}) {
  const styles = makeStyles(useTheme());
  return (
    <View style={styles.toggleRow}>
      <View style={styles.toggleText}>
        <Text style={styles.toggleLabel}>{label}</Text>
        <Text style={styles.toggleDesc}>{desc}</Text>
        {source ? <Tag label={source} variant="neutral" style={styles.sourceTag} /> : null}
      </View>
      <Toggle
        on={on}
        readOnly={readOnly}
        onPress={onPress}
        accessibilityLabel={label}
        accessibilityHint={hint ?? (readOnly ? 'Salt okunur — uygulamadan değiştirilemez' : undefined)}
      />
    </View>
  );
}

/** One cell of the small label/value grid used by Strateji and Sistem. */
function Field({ label, value, color }: { label: string; value: string; color?: string }) {
  const t = useTheme();
  const styles = makeStyles(t);
  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <Text style={[styles.fieldValue, color ? { color } : null]}>{value}</Text>
    </View>
  );
}

function EvalStat({ label, value, gate, color }: { label: string; value: string; gate: string; color?: string }) {
  const styles = makeStyles(useTheme());
  return (
    <View style={styles.evalStat}>
      <Text style={styles.evalStatLabel}>{label}</Text>
      <Text style={[styles.evalStatValue, color ? { color } : null]}>{value}</Text>
      <Text style={styles.evalStatGate}>{gate}</Text>
    </View>
  );
}

function GateRow({ name, passed, detail }: { name: string; passed: boolean | null; detail: string }) {
  const t = useTheme();
  const styles = makeStyles(t);
  const icon = passed === null ? '○' : passed ? '✓' : '✗';
  // Small red type is accent-700, never the base accent.
  const color = passed === null ? t.textSecondary : passed ? t.textPrimary : t.accent700 ?? t.accent;
  return (
    <View style={styles.gateRow}>
      <Text style={[styles.gateIcon, { color }]}>{icon}</Text>
      <Text style={styles.gateName}>{name}</Text>
      <Text style={styles.gateDetail}>{detail}</Text>
    </View>
  );
}

function SecondaryButton({ label, onPress, hint }: { label: string; onPress: () => void; hint?: string }) {
  const styles = makeStyles(useTheme());
  return (
    <Pressable
      style={styles.buttonSecondary}
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityHint={hint}
    >
      <Text style={styles.buttonSecondaryText}>{label}</Text>
    </Pressable>
  );
}

export default function SettingsScreen() {
  const router = useRouter();
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);
  const themeName = useThemeName();
  const setTheme = useSetTheme();
  const { t: tr, i18n } = useTranslation();
  const lang: Language = i18n.language?.startsWith('tr') ? 'tr' : 'en';
  const PERMISSION_COPY = useMemo(() => permissionCopy(theme), [theme]);

  const [pushBusy, setPushBusy] = useState(false);
  const [permission, setPermission] = useState<PushPermission | null>(null);
  const [signOutOpen, setSignOutOpen] = useState(false);

  const inbox = useInboxStore((s) => s.items);
  const signOut = useAuthStore((s) => s.signOut);

  const { data: health, isError: healthError } = useHealth();
  const { data: readiness } = useReadiness();
  const { data: decisions } = useDecisions({ limit: 1 });
  const { data: evalData, isLoading: evalLoading } = useEval('1M');
  const { data: flow } = useActionability();
  const { data: concentration } = useConcentration();

  // /readyz is the badge's source of truth (it reads the same routing default
  // the broker client uses); /healthz is the fallback when readiness is down.
  const mode: TradingMode | null = readiness?.trading_mode ?? health?.trading_mode ?? null;
  const isLive = mode === 'live';

  const VERDICT_COLOR: Record<string, string> = {
    GO: theme.textPrimary,
    'NO-GO': theme.accent,
    'TOO EARLY': theme.warning,
  };

  const lastDecisionAt = parseUtc(decisions?.[0]?.timestamp_utc);
  const lastRun = lastDecisionAt ? relativeAgeTr(Date.now() - lastDecisionAt.getTime()) : '—';

  const refreshPermission = useCallback(async () => {
    setPermission(await getPermissionStatus());
  }, []);

  useEffect(() => {
    void refreshPermission();
    // The grant is changed in the OS Settings app, so the only moment we can
    // learn about it is when this app comes back to the foreground. Without
    // this the row keeps claiming "kapalı" after the user just enabled it.
    const sub = AppState.addEventListener('change', (state) => {
      if (state === 'active') void refreshPermission();
    });
    return () => sub.remove();
  }, [refreshPermission]);

  const pushOn = permission === 'granted';

  const handlePushToggle = async () => {
    if (pushBusy) return;
    if (permission === 'unsupported') {
      toast('Bu cihaz push desteklemiyor — gerçek cihazda dene');
      return;
    }
    // The OS owns the grant: once given or refused, only Settings can flip it.
    if (permission === 'granted' || permission === 'denied') {
      await Linking.openSettings();
      return;
    }
    setPushBusy(true);
    try {
      const token = await requestAndRegisterPush();
      await refreshPermission();
      toast(token ? 'Bildirimler açık — bu cihaz kayıtlı' : 'İzin verilmedi — cihaz ayarlarından açabilirsin');
    } catch (e) {
      toast(`Bildirim açılamadı: ${String(e)}`);
    } finally {
      setPushBusy(false);
    }
  };

  const handleTestPush = async () => {
    try {
      const result = await api.testNotification();
      toast(`Test bildirimi ${result.sent} cihaza gönderildi`);
    } catch (e) {
      toast(`Test gönderilemedi: ${String(e)}`);
    }
  };

  const handleSignOut = () => {
    setSignOutOpen(false);
    signOut();
    router.replace('/(auth)/login' as never);
  };

  // ExecutionConfig, as deployed. The app can neither write these nor read
  // them back — the section note says exactly that, and `needs` carries the
  // missing GET.
  const execRows = [
    {
      key: 'autoExecute',
      label: 'Risk kontrolünden geçen emirleri otomatik gönder',
      desc:
        'Paper hesapta bracket (stop + TP) ile gönderilir. LIVE modda emirler onaya düşer.' +
        (mode ? '' : ' Mod okunamadı.'),
      source: 'salt okunur · moddan türetilir',
      on: mode === 'paper',
    },
    {
      key: 'refuseOutside',
      label: 'Piyasa kapalıyken gönderme',
      desc: "refuse_outside_hours — LIVE'da açık olmalı.",
      source: 'salt okunur · ExecutionConfig',
      on: false,
    },
    {
      key: 'bracket',
      label: 'Bracket emirler',
      desc: 'Her giriş broker tarafında stop + kâr al ile gider.',
      source: 'salt okunur · ExecutionConfig',
      on: true,
    },
  ];

  const topWeight = concentration?.top_weight_pct;
  const capTone = topWeight != null ? topWeightTone(topWeight, SINGLE_NAME_CAP_PCT) : null;
  const capToneColor =
    capTone === 'down' ? theme.downText : capTone === 'warning' ? theme.warning : theme.textPrimary;

  /**
   * The verdict's gates are decided server-side and arrive in `gates`, each
   * with a `passed` that is deliberately null while the book is below the
   * minimum day count — nothing is evaluable yet. Re-deriving the comparison
   * here is what inverted the drawdown tone: `max_dd_pct` comes back negative
   * (`sc.max_dd * 100`) while `gate_max_dd_pct` is the positive 15, so
   * `max_dd_pct < gate_max_dd_pct` was true for every drawdown ever recorded
   * and Max DD could never colour as a miss. The backend compares magnitudes
   * (`abs(sc.max_dd) < GATE_MAX_DD`); reading its answer is the only way this
   * screen and the gate list below it can't drift apart.
   */
  const gatePassed = (name: string): boolean | null =>
    evalData?.gates?.find((g) => g.name === name)?.passed ?? null;
  const sharpeFail = gatePassed('Sharpe') === false;
  const ddFail = gatePassed('Max drawdown') === false;
  const failColor = theme.accent700 ?? theme.accent;

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView contentContainerStyle={styles.scroll}>
        <Text style={styles.heading}>{tr('tabs.settings')}</Text>

        {/* ── Hesap & mod ───────────────────────────────────────── */}
        <Section title="Hesap & mod" first>
          <View style={styles.modeRow}>
            <Tag label={isLive ? 'LIVE — GERÇEK PARA' : 'PAPER'} variant="outline" />
            <Text style={styles.modeAccount}>{isLive ? 'Alpaca LIVE hesabı' : 'Alpaca paper hesabı'}</Text>
          </View>
          <View style={styles.kvList}>
            <View style={styles.kvRow}>
              <Text style={styles.kvKey}>Uygulama API'si</Text>
              <Text style={styles.kvValue} numberOfLines={1}>{API_URL}</Text>
            </View>
            <View style={styles.kvRow}>
              <Text style={styles.kvKey}>Broker ucu</Text>
              <Text style={styles.kvValue} numberOfLines={1}>
                {mode ? `ALPACA_BASE_URL · ${mode}` : 'okunamadı'}
              </Text>
            </View>
          </View>
          <Text style={styles.note}>
            Broker ucu sunucudaki ALPACA_BASE_URL ile belirlenir; yukarıdaki mod oradan okunur.
            Live'a geçiş: canlı hesap + KYC + live key + secrets.env. Uygulamadan yapılamaz.
          </Text>
          <SecondaryButton
            label="Çıkış yap"
            onPress={() => setSignOutOpen(true)}
            hint="Oturumu kapatır ve giriş ekranına döner"
          />
        </Section>

        {/* ── Eval scorecard ────────────────────────────────────── */}
        <Section
          title="Eval scorecard"
          right={
            evalData ? (
              <Text style={[styles.verdict, { color: VERDICT_COLOR[evalData.verdict] ?? theme.textSecondary }]}>
                {evalData.verdict}
              </Text>
            ) : null
          }
        >
          {evalLoading ? (
            <Text style={styles.muted}>hesaplanıyor…</Text>
          ) : !evalData ? (
            <Text style={styles.muted}>Yeterli geçmiş yok — eval yeni başladı.</Text>
          ) : (
            <>
              {evalData.verdict === 'TOO EARLY' && evalData.provisional_verdict ? (
                <Text
                  style={[
                    styles.trend,
                    { color: VERDICT_COLOR[evalData.provisional_verdict] ?? theme.textSecondary },
                  ]}
                >
                  eğilim: {evalData.provisional_verdict}
                </Text>
              ) : null}

              <View style={styles.evalGrid}>
                <EvalStat
                  label="Sharpe"
                  value={fixed(evalData.sharpe, 2)}
                  gate={`> ${evalData.gate_sharpe}`}
                  color={sharpeFail ? failColor : undefined}
                />
                <EvalStat label="Sortino" value={fixed(evalData.sortino, 2)} gate="downside" />
                <EvalStat
                  label="Max DD"
                  value={`${fixed(evalData.max_dd_pct, 1)}%`}
                  gate={`< ${evalData.gate_max_dd_pct}%`}
                  color={ddFail ? failColor : undefined}
                />
                <EvalStat label="Calmar" value={fixed(evalData.calmar, 2)} gate="getiri/DD" />
                <EvalStat
                  label="Getiri"
                  value={`${evalData.total_return_pct >= 0 ? '+' : '−'}${Math.abs(evalData.total_return_pct).toFixed(1)}%`}
                  gate={`${evalData.days}g`}
                />
                {evalData.spy_return_pct != null ? (
                  <EvalStat
                    label="α vs SPY"
                    value={`${evalData.total_return_pct - evalData.spy_return_pct >= 0 ? '+' : '−'}${Math.abs(evalData.total_return_pct - evalData.spy_return_pct).toFixed(1)}%`}
                    gate={`SPY ${evalData.spy_return_pct >= 0 ? '+' : '−'}${Math.abs(evalData.spy_return_pct).toFixed(1)}%`}
                  />
                ) : null}
              </View>

              {evalData.days_remaining > 0 ? (
                <Text style={styles.countdown}>
                  Karara {evalData.days_remaining} gün · {evalData.days}/{evalData.days_required} işlem günü
                </Text>
              ) : evalData.eval_complete ? (
                <Text style={styles.countdown}>
                  Eval tamamlandı · {evalData.days}/{evalData.days_required} işlem günü · karar kesin
                </Text>
              ) : (
                <Text style={styles.countdown}>
                  {evalData.days}/{evalData.days_required} işlem günü · karar penceresi açık
                </Text>
              )}

              {evalData.gates?.length ? (
                <View style={styles.gateList}>
                  {evalData.gates.map((g) => (
                    <GateRow key={g.name} name={g.name} passed={g.passed} detail={g.detail} />
                  ))}
                </View>
              ) : null}

              {evalData.reasons.length ? (
                <Text style={styles.evalReason}>{evalData.reasons.join(' · ')}</Text>
              ) : null}

              {/*
                Every gate above is computed from the equity curve, which a book
                that stopped trading still has. When order flow says the book is
                frozen, that caveat belongs next to the verdict.
              */}
              {inertiaNote(flow) ? <Text style={styles.flowCaveat}>⚠︎ {inertiaNote(flow)}</Text> : null}
            </>
          )}
        </Section>

        {/* ── Risk: the kill switch's new home ──────────────────── */}
        <Section title="Risk & uyarılar">
          <Pressable
            style={styles.linkRow}
            onPress={() => router.push('/(tabs)/risk' as never)}
            accessibilityRole="link"
            accessibilityLabel="Risk ve uyarılar ekranını aç"
            accessibilityHint="Kill switch, devre kesiciler ve portföy limitleri"
          >
            <View style={styles.linkText}>
              <Text style={styles.linkLabel}>Kill switch ve limitler</Text>
              <Text style={styles.linkHint}>
                RUN / PAUSE / FLATTEN, devre kesiciler ve portföy limitleri Risk ekranında.
              </Text>
            </View>
            <Chevron color={theme.textSecondary} />
          </Pressable>
        </Section>

        {/* ── Emir gönderimi ────────────────────────────────────── */}
        <Section title="Emir gönderimi">
          <Text style={styles.note}>
            Bu üç ayar sunucudaki ExecutionConfig'ten gelir. Uygulama bunları yazamaz; API bir okuma
            ucu da sunmadığı için gösterilenler dağıtımdaki varsayılanlar ve moddan türetilen
            davranıştır.
          </Text>
          {execRows.map((r) => (
            <ToggleRow
              key={r.key}
              label={r.label}
              desc={r.desc}
              source={r.source}
              on={r.on}
              readOnly
            />
          ))}
        </Section>

        {/* ── Strateji ──────────────────────────────────────────── */}
        <Section title="Strateji">
          <Text style={styles.note}>
            Boyutlama yöntemi ve tek isim tavanı sunucudaki risk yapılandırmasıdır
            (SizingMethod / PortfolioLimits) — uygulamadan değiştirilemez.
          </Text>

          <Text style={styles.fieldLabel}>Boyutlama yöntemi</Text>
          {/* SizingMethod has four members but the sizer wires two: 'atr' sizes
              off the real entry→stop distance, 'llm_pct' off the decision's
              suggested size. 'kelly' and 'vol_tgt' return 0 shares — naming one
              of them on the chip would claim a method that cannot place a trade. */}
          <Seg
            options={[
              { value: 'atr', label: 'ATR' },
              { value: 'llm_pct', label: 'LLM önerisi' },
            ]}
            value="atr"
            onChange={() => {}}
            disabled
            block
            style={styles.sizingSeg}
          />

          <View style={styles.capRow}>
            <Text style={styles.capLabel}>Tek isim tavanı</Text>
            <View style={styles.capBox}>
              <Text style={styles.capValue}>{SINGLE_NAME_CAP_PCT}</Text>
            </View>
            <Text style={styles.capSuffix}>%</Text>
          </View>
          {topWeight != null ? (
            <Text style={[styles.capNow, { color: capToneColor }]}>
              şu an en yüksek ağırlık %{topWeight.toFixed(1)}
            </Text>
          ) : null}

          {/*
            Four constants of the deployed run, each traceable to something that
            actually executes: risk_per_trade=0.005 in the sizer, the eleven
            tickers daily_run.sh trades, and the Mon–Fri 22:30 UTC timer.

            The prototype's fourth cell read "Stop 2 × ATR(14)". Nothing
            computes a stop that way: the stop rides on the agent's decision
            (`AgentDecision.stop_loss`) and the sizer measures the risk budget
            against that distance rather than an ATR standing in for it. A fixed
            multiple printed here would be the one figure on this screen that no
            code anywhere produces.
          */}
          <View style={styles.grid}>
            <Field label="Risk / işlem" value="%0.5 equity" />
            <Field label="Stop" value="Ajan kararında gelir" />
            <Field label="Evren" value="SPY + 10 isim" />
            <Field label="Koşu" value="Hafta içi 22:30 UTC" />
          </View>
        </Section>

        {/* ── Bildirimler ───────────────────────────────────────── */}
        <Section
          title="Bildirimler"
          right={
            <Text style={[styles.permission, { color: PERMISSION_COPY[permission ?? 'undetermined'].color }]}>
              {permission ? PERMISSION_COPY[permission].text : '…'}
            </Text>
          }
        >
          <ToggleRow
            label="Push bildirimleri"
            desc={pushBusy ? 'Kaydediliyor…' : PUSH_DESC[permission ?? 'undetermined']}
            on={pushOn}
            onPress={() => void handlePushToggle()}
            hint={
              pushOn || permission === 'denied'
                ? 'Cihaz ayarlarını açar — izin yalnız oradan değiştirilir'
                : 'Bu cihazı bildirimlere kaydeder'
            }
          />
          {/* eval-report.timer: OnCalendar=Mon *-*-* 14:00:00 UTC. The prototype
              said 09:00, which is neither the timer nor a zone the app converts
              to — a push that lands five hours after the label reads as a broken
              job rather than a wrong caption. */}
          <ToggleRow
            label="Haftalık eval raporu"
            desc="Pazartesi 14:00 UTC — Sharpe, MaxDD, GO/NO-GO. Sunucu tarafında planlı."
            source="salt okunur · sunucu görevi"
            on
            readOnly
          />
          <ToggleRow
            label="Onayda cihaz kilidi zorunlu"
            desc="Face ID · parmak izi · şifre olmadan emir onaylanamaz. Kapatılamaz (ADR-005)."
            source="salt okunur · zorunlu"
            on
            readOnly
          />
          <View>
            <SecondaryButton label="Test bildirimi gönder" onPress={() => void handleTestPush()} />
            <SecondaryButton
              label={`Bildirim geçmişi (${inbox.length}${unreadCount(inbox) > 0 ? ` · ${unreadCount(inbox)} okunmamış` : ''})`}
              onPress={() => router.push('/notifications' as never)}
            />
          </View>
        </Section>

        {/* ── Görünüm ───────────────────────────────────────────── */}
        {/* Language and palette were both settable only by the device: the app
            followed the phone's locale and opened in whichever palette the
            build defaulted to. Both are reader choices, and both persist. */}
        <Section title="Görünüm">
          <Text style={styles.fieldLabel}>Dil / Language</Text>
          <Seg
            options={[
              { value: 'tr', label: 'Türkçe' },
              { value: 'en', label: 'English' },
            ]}
            value={lang}
            onChange={(next) => void setLanguage(next)}
            block
            style={styles.appearanceSeg}
          />

          <Text style={styles.fieldLabel}>Tema</Text>
          <Seg
            options={[
              { value: 'modernist', label: 'Açık', accessibilityLabel: 'Açık tema' },
              { value: 'dark', label: 'Koyu', accessibilityLabel: 'Koyu tema' },
            ]}
            value={themeName}
            onChange={(next: ThemeName) => setTheme(next)}
            block
            style={styles.appearanceSeg}
          />
        </Section>

        {/* ── Sistem ────────────────────────────────────────────── */}
        <Section title="Sistem">
          <View style={styles.grid}>
            <Field
              label="Backend"
              value={healthError ? '● offline' : health?.status === 'ok' ? '● online' : '…'}
              color={healthError ? theme.accent700 ?? theme.accent : theme.textPrimary}
            />
            <Field
              label="Broker · DB"
              value={
                readiness
                  ? `Alpaca ${readiness.alpaca ? '✓' : '✗'} · DB ${readiness.db ? '✓' : '✗'}`
                  : '…'
              }
              color={readiness && (!readiness.alpaca || !readiness.db) ? theme.accent700 ?? theme.accent : undefined}
            />
            <Field label="Son ajan kararı" value={lastRun} />
            <Field label="Uygulama sürümü" value={APP_VERSION} />
          </View>
        </Section>

        <Text style={styles.disclaimer}>{tr('disclaimer.short')}</Text>
      </ScrollView>

      <Sheet
        visible={signOutOpen}
        title="Çıkış yap?"
        message="Oturum kapanır ve giriş ekranına dönersin. Ajan koşusu ve açık pozisyonlar etkilenmez."
        actions={[
          { label: 'Vazgeç', onPress: () => setSignOutOpen(false) },
          { label: 'Çıkış yap', onPress: handleSignOut, primary: true },
        ]}
        onDismiss={() => setSignOutOpen(false)}
      />
    </SafeAreaView>
  );
}

const makeStyles = (t: Palette) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.background },
    scroll: { paddingHorizontal: 16, paddingTop: 20, paddingBottom: 24 },
    heading: { color: t.textPrimary, ...TYPE.h2 },

    // Sections are opened by the 2px rule; the first one sits under the title.
    section: { marginTop: 16, paddingTop: 12, borderTopWidth: 2, borderTopColor: t.divider },
    sectionFirst: { borderTopWidth: 0, paddingTop: 0 },
    sectionHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
    sectionTitle: { color: t.textPrimary, ...TYPE.section },

    note: { color: t.textSecondary, marginTop: 8, lineHeight: 16, ...TYPE.helper },
    muted: { color: t.textSecondary, marginTop: 8, lineHeight: 18, ...TYPE.body },

    // Hesap & mod
    modeRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 10 },
    modeAccount: { color: t.textPrimary, flexShrink: 1, ...TYPE.bodyStrong },
    kvList: { marginTop: 10, borderTopWidth: 1, borderTopColor: t.divider },
    kvRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 12,
      paddingVertical: 8,
      borderBottomWidth: 1,
      borderBottomColor: t.divider,
    },
    kvKey: { color: t.textSecondary, ...TYPE.helper },
    kvValue: { color: t.textPrimary, flexShrink: 1, ...TYPE.helper, ...font(600), ...TABULAR },

    // Eval
    verdict: { fontSize: 18, letterSpacing: 0.5, ...font(800) },
    trend: { marginTop: 6, ...TYPE.helper, ...font(800) },
    evalGrid: { flexDirection: 'row', flexWrap: 'wrap', marginTop: 10 },
    evalStat: { width: '33.33%', paddingRight: 8, marginBottom: 12 },
    evalStatLabel: { color: t.textSecondary, fontSize: 11, ...font(400) },
    evalStatValue: { color: t.textPrimary, fontSize: 16, marginTop: 1, ...font(800), ...TABULAR },
    // The one style on this screen that named no family: without font() the
    // caption drops off Archivo onto the Android system face, beside a value
    // that stays on it.
    evalStatGate: { color: t.textSecondary, fontSize: 10, marginTop: 1, ...font(400) },
    countdown: { color: t.textSecondary, ...TYPE.helper, ...font(600) },
    gateList: { marginTop: 10, paddingTop: 10, gap: 4, borderTopWidth: 1, borderTopColor: t.divider },
    gateRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
    gateIcon: { width: 14, textAlign: 'center', ...TYPE.body, ...font(800) },
    gateName: { color: t.textPrimary, flex: 1, ...TYPE.body },
    gateDetail: { color: t.textSecondary, ...TYPE.helper, ...TABULAR },
    evalReason: { color: t.warning, marginTop: 10, lineHeight: 16, ...TYPE.helper },
    flowCaveat: { color: t.accent700 ?? t.accent, marginTop: 10, lineHeight: 16, ...TYPE.helper },

    // Risk link
    linkRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 12,
      paddingVertical: 12,
      minHeight: MIN_TOUCH_TARGET,
    },
    linkText: { flex: 1, gap: 2 },
    linkLabel: { color: t.textPrimary, ...TYPE.bodyStrong },
    linkHint: { color: t.textSecondary, lineHeight: 16, ...TYPE.helper },

    // Toggles
    toggleRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 14,
      paddingVertical: 12,
      minHeight: MIN_TOUCH_TARGET,
      borderBottomWidth: 1,
      borderBottomColor: t.divider,
    },
    toggleText: { flex: 1 },
    toggleLabel: { color: t.textPrimary, ...TYPE.bodyStrong },
    toggleDesc: { color: t.textSecondary, marginTop: 2, lineHeight: 16, ...TYPE.helper },
    sourceTag: { marginTop: 6 },
    track: { width: TOGGLE_W, height: TOGGLE_H, justifyContent: 'center' },
    knob: { width: KNOB, height: KNOB, position: 'absolute', left: 0 },

    // Strateji
    sizingSeg: { marginTop: 6 },
    capRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 14 },
    capLabel: { color: t.textPrimary, flex: 1, ...TYPE.body },
    capBox: {
      minWidth: 64,
      minHeight: 40,
      paddingHorizontal: 12,
      alignItems: 'flex-start',
      justifyContent: 'center',
      borderWidth: 1,
      borderColor: t.divider,
      backgroundColor: t.surface,
    },
    capValue: { color: t.textPrimary, ...TYPE.body, ...font(800), ...TABULAR },
    capSuffix: { color: t.textPrimary, ...TYPE.body },
    capNow: { marginTop: 6, ...TYPE.helper, ...font(600), ...TABULAR },

    // Label/value grid (Strateji fixed values, Sistem)
    grid: { flexDirection: 'row', flexWrap: 'wrap', marginTop: 14 },
    field: { width: '50%', paddingRight: 8, marginBottom: 10 },
    fieldLabel: { color: t.textSecondary, marginTop: 10, ...TYPE.helper },
    fieldValue: { color: t.textPrimary, marginTop: 2, fontSize: 12, ...font(800) },

    // Bildirimler
    permission: { fontSize: 12, ...font(600) },

    appearanceSeg: { marginTop: 6 },

    buttonSecondary: {
      marginTop: 12,
      paddingHorizontal: 14,
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: MIN_TOUCH_TARGET,
      borderWidth: 1,
      borderColor: t.textPrimary,
      backgroundColor: 'transparent',
      alignSelf: 'stretch',
    },
    buttonSecondaryText: { color: t.textPrimary, fontSize: 14, ...font(800) },

    disclaimer: {
      color: t.textSecondary,
      paddingVertical: 24,
      textAlign: 'center',
      fontStyle: 'italic',
      ...TYPE.helper,
    },
  });
