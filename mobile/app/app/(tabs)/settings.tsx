/**
 * Screen 9 — Ayarlar, in Aurora.
 *
 * The prototype's SETTINGS screen is a stack of titled card groups: an 11px
 * uppercase kicker, then one rounded `--surface` card whose rows are separated
 * by `--line` hairlines. That is the only structural change here — the 2px
 * section rules became cards, and every figure, every label and every rule
 * below is the one that was already shipping.
 *
 * Three things this screen deliberately refuses to do, unchanged from before.
 *
 * First, the kill switch is gone. It used to live here, three taps deep behind
 * a scroll, next to the font picker — the one control that stops the book, in
 * the same list as "Koyu tema". It now has its own screen with the circuit
 * breakers and the portfolio limits it actually belongs to, and this screen
 * keeps only a link to it. The prototype draws that link exactly this way.
 *
 * Second, every control here that cannot really write is drawn as a read-only
 * state, not a switch. `refuse_outside_hours` and `use_bracket` are fields of
 * the backend's `ExecutionConfig`; sizing method and the single-name cap are
 * `SizingMethod` / `PortfolioLimits`. `src/api/endpoints.ts` has no route that
 * writes any of them and no route that reads them either, so a live-looking
 * toggle would be a lie twice over. They render in the prototype's toggle
 * shape at 55% opacity — which is how the prototype itself marks `ro` — and
 * the section says so in words.
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
 *
 * What is new: the theme picker offers all three palettes. `useSetTheme` has
 * always accepted `aurora`, and nothing exposed it — so the app shipped in a
 * palette the reader could not get back to once they picked one of the other
 * two.
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
  TextInput,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import Constants from 'expo-constants';

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
import { useQueryClient } from '@tanstack/react-query';

import { useAuthStore } from '@/stores/auth';
import { signOut as firebaseSignOut, deleteAccount as firebaseDeleteAccount } from '@/auth/firebase';
import { useApiTokenStore } from '@/stores/apiToken';
import { toast } from '@/stores/toast';
import { useTheme, useThemeName, useSetTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { setLanguage, type Language } from '@/i18n';
import type { ThemeName } from '@/theme/colors';
import type { TradingMode } from '@/api/types';
import { unreadCount } from '@/utils/inbox';
import { MIN_TOUCH_TARGET, hitSlopFor } from '@/utils/a11y';
import { relativeAgeTr, parseUtc } from '@/utils/format';
import { Card } from '@/components/Card';
import { DataRow } from '@/components/DataRow';
import { StatCell } from '@/components/StatCell';
import { Tag } from '@/components/Tag';
import { Seg } from '@/components/Seg';
import { Sheet } from '@/components/Sheet';
import { font, TABULAR, TYPE } from '@/theme/type';

type Palette = ReturnType<typeof useTheme>;

/**
 * The floating tab bar's height plus air. Stated here rather than imported
 * from `_layout.tsx` because a route module's exports are the router's
 * namespace, not a place to hang shared constants.
 */
const TAB_BAR_CLEARANCE = 72;

/**
 * The prototype's switch: a 51×31 pill track with a 27px knob inset by 2 —
 * the iOS geometry it uses on iOS. The Modernist shape makes the track square
 * (`radiusPill` is 0 there), which is the same switch under a different
 * system rather than a different control.
 */
const TOGGLE_W = 51;
const TOGGLE_H = 31;
const KNOB = 27;
const KNOB_PAD = 2;
const KNOB_OFF_X = KNOB_PAD;
const KNOB_ON_X = TOGGLE_W - KNOB - KNOB_PAD;
/** The only transition in the system. */
const TOGGLE_MS = 150;
/** How the prototype marks a switch it will not let you move (`opacity` on `ro`). */
const READ_ONLY_OPACITY = 0.55;

/** The app's own backend base URL — the one endpoint this screen can honestly print. */
const API_URL = (Constants.expoConfig?.extra?.apiUrl ?? 'http://localhost:8000') as string;
const APP_VERSION = Constants.expoConfig?.version ?? '—';

/** Legal and support URLs for App Store compliance. */
const PRIVACY_URL = 'https://canberkaslan.co/trading/privacy';
const TERMS_URL = 'https://canberkaslan.co/trading/terms';
const SUPPORT_URL = 'https://canberkaslan.co/trading/support';

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
  granted: { text: '● açık', color: t.up },
  denied: { text: '● kapalı (cihaz ayarları)', color: t.downText },
  undetermined: { text: '● izin verilmedi', color: t.warning },
  unsupported: { text: '● bu cihazda çalışmaz', color: t.ink2 ?? t.textSecondary },
});

/** What the push row says under its label, per OS permission state. */
const PUSH_DESC: Record<PushPermission, string> = {
  granted: 'Onay bekleyen ve gerçekleşen emirler için. Kapatmak cihaz ayarlarından.',
  denied: 'Cihaz ayarlarından kapatılmış — açmak için ayarlara git.',
  undetermined: 'Onay bekleyen ve gerçekleşen emirler için.',
  unsupported: 'Bu cihaz push desteklemiyor (simülatörde çalışmaz).',
};

/**
 * The group title: 11px uppercase, tertiary ink, indented four points so it
 * hangs off the card's left edge rather than lining up with it. `right` is the
 * verdict / permission state the prototype parks on the same line.
 */
function GroupTitle({ title, right }: { title: string; right?: React.ReactNode }) {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  return (
    <View style={styles.groupTitleRow}>
      <Text style={styles.groupTitle} accessibilityRole="header">
        {title}
      </Text>
      {right}
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
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const x = useRef(new Animated.Value(on ? KNOB_ON_X : KNOB_OFF_X)).current;

  useEffect(() => {
    Animated.timing(x, {
      toValue: on ? KNOB_ON_X : KNOB_OFF_X,
      duration: TOGGLE_MS,
      useNativeDriver: true,
    }).start();
  }, [on, x]);

  const track = (
    <View
      style={[
        styles.track,
        // On: the brand fill the prototype uses. Off: the heavier hairline,
        // which is the only thing in either palette that reads as an empty
        // groove on both a light and a dark ground.
        { backgroundColor: on ? t.brand ?? t.textPrimary : t.line2 ?? t.divider },
        readOnly && styles.trackReadOnly,
      ]}
    >
      <Animated.View
        style={[
          styles.knob,
          // The prototype's knob is `--inkInv`: the ground, not the ink. Under
          // Aurora that is dark-on-indigo, which is what the design's own
          // tokens produce on a dark system.
          { backgroundColor: on ? t.background : t.ink2 ?? t.textSecondary, transform: [{ translateX: x }] },
        ]}
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
  first,
}: {
  label: string;
  desc: string;
  /** Where the value comes from, when the app cannot write it. */
  source?: string;
  on: boolean;
  readOnly?: boolean;
  onPress?: () => void;
  hint?: string;
  /** The first row in a card draws no hairline above it. */
  first?: boolean;
}) {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  return (
    <View style={[styles.toggleRow, !first && styles.toggleRowRuled]}>
      <View style={styles.toggleText}>
        <Text style={styles.toggleLabel}>{label}</Text>
        <Text style={styles.toggleDesc}>{desc}</Text>
        {source ? <Tag label={source} variant="neutral" size="sm" style={styles.sourceTag} /> : null}
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

function GateRow({ name, passed, detail }: { name: string; passed: boolean | null; detail: string }) {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const icon = passed === null ? '○' : passed ? '✓' : '✗';
  const color = passed === null ? t.ink3 ?? t.textMuted : passed ? t.up : t.downText;
  return (
    <View style={styles.gateRow}>
      <Text style={[styles.gateIcon, { color }]}>{icon}</Text>
      <Text style={styles.gateName}>{name}</Text>
      <Text style={styles.gateDetail}>{detail}</Text>
    </View>
  );
}

/** The prototype's outlined 44pt button. `danger` drops the outline and reds the label. */
function SecondaryButton({
  label,
  onPress,
  hint,
  danger,
  style,
}: {
  label: string;
  onPress: () => void;
  hint?: string;
  danger?: boolean;
  style?: StyleProp<ViewStyle>;
}) {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  return (
    <Pressable
      style={({ pressed }) => [
        styles.buttonSecondary,
        danger && styles.buttonDanger,
        pressed && styles.buttonPressed,
        style,
      ]}
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityHint={hint}
    >
      <Text style={[styles.buttonSecondaryText, danger && styles.buttonDangerText]}>{label}</Text>
    </Pressable>
  );
}

export default function SettingsScreen() {
  const router = useRouter();
  const theme = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(theme, sh), [theme, sh]);
  const themeName = useThemeName();
  const setTheme = useSetTheme();
  const { t: tr, i18n } = useTranslation();
  const lang: Language = i18n.language?.startsWith('tr') ? 'tr' : 'en';

  const qc = useQueryClient();
  const apiToken = useApiTokenStore((s) => s.token);
  const setApiToken = useApiTokenStore((s) => s.set);
  const hydrateToken = useApiTokenStore((s) => s.hydrate);
  const clearToken = useApiTokenStore((s) => s.clear);
  const [tokenDraft, setTokenDraft] = useState('');
  const hasToken = !!apiToken;

  useEffect(() => {
    void hydrateToken();
  }, [hydrateToken]);

  const saveToken = useCallback(async () => {
    await setApiToken(tokenDraft);
    setTokenDraft('');
    // Every screen fetched without a bearer, or with a stale one. Drop the
    // cache so they refetch rather than sitting on their error states until
    // something else happens to invalidate them.
    await qc.invalidateQueries();
    toast('Token kaydedildi');
  }, [qc, setApiToken, tokenDraft]);
  const PERMISSION_COPY = useMemo(() => permissionCopy(theme), [theme]);

  const [pushBusy, setPushBusy] = useState(false);
  const [permission, setPermission] = useState<PushPermission | null>(null);
  const [signOutOpen, setSignOutOpen] = useState(false);
  const [deleteAccountOpen, setDeleteAccountOpen] = useState(false);

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
    GO: theme.up,
    'NO-GO': theme.downText,
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
    // Sign-out cleared only the identity fields and left the bearer in the
    // keystore — the one credential that approves orders, rejects them and
    // throws the kill switch. Handing the device to someone else after
    // "Çıkış yap" therefore handed over the ability to flatten the book. The
    // token is the session here, so signing out has to take it with it.
    void clearToken();
    // Firebase holds its own persisted session; clearing only the local store
    // would leave the app signed out while the credential that proves who you
    // are is still on the device.
    void firebaseSignOut();
    signOut();
    // Queries cached under the old token are not this user's to keep.
    qc.clear();
    router.replace('/(auth)/login' as never);
  };

  const handleDeleteAccount = async () => {
    setDeleteAccountOpen(false);
    try {
      // Delete from backend first — if this fails, the Firebase account remains
      await api.deleteAccount();
      // Delete from Firebase Authentication
      await firebaseDeleteAccount();
      // Clear local state
      void clearToken();
      signOut();
      qc.clear();
      toast('Hesap silindi');
      router.replace('/(auth)/login' as never);
    } catch (e) {
      toast(`Hesap silinemedi: ${String(e)}`);
    }
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
  const failColor = theme.downText;

  const systemFields: { label: string; value: string; color?: string }[] = [
    {
      label: 'Backend',
      value: healthError ? '● offline' : health?.status === 'ok' ? '● online' : '…',
      color: healthError ? theme.downText : theme.up,
    },
    {
      label: 'Broker · DB',
      value: readiness
        ? `Alpaca ${readiness.alpaca ? '✓' : '✗'} · DB ${readiness.db ? '✓' : '✗'}`
        : '…',
      color: readiness && (!readiness.alpaca || !readiness.db) ? theme.downText : undefined,
    },
    { label: 'Son ajan kararı', value: lastRun },
    { label: 'Uygulama sürümü', value: APP_VERSION },
  ];

  const strategyFields = [
    { label: 'Risk / işlem', value: '%0.5 equity' },
    { label: 'Stop', value: 'Ajan kararında gelir' },
    { label: 'Evren', value: 'SPY + 10 isim' },
    { label: 'Koşu', value: 'Hafta içi 22:30 UTC' },
  ];

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.heading}>{tr('tabs.settings')}</Text>

        {/* ── Hesap & mod ───────────────────────────────────────── */}
        <GroupTitle title="Hesap & mod" />
        <Card>
          <View style={styles.modeRow}>
            {/* Tri-state, matching StatusBanner: PAPER is a claim about where
                real money goes, and `mode` is null whenever readiness AND health
                both failed — including a plain 401. Collapsing null to PAPER
                meant an unauthenticated app asserted the account was on paper.
                After go-live that is the one lie this screen must never tell. */}
            <Tag
              label={mode == null ? 'MOD ?' : isLive ? 'LIVE — GERÇEK PARA' : 'PAPER'}
              variant="outline"
              caps
            />
            <Text style={styles.modeAccount}>{isLive ? 'Alpaca LIVE hesabı' : 'Alpaca paper hesabı'}</Text>
          </View>

          <View style={styles.kvList}>
            <View style={styles.kvRow}>
              <Text style={styles.kvKey}>Uygulama API&apos;si</Text>
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
            Live&apos;a geçiş: canlı hesap + KYC + live key + secrets.env. Uygulamadan yapılamaz.
          </Text>

          {/* The bearer lives in the device keystore, not in the bundle: Expo
              republishes app.config's `extra` verbatim in the OTA manifest,
              which anyone can fetch unauthenticated. A token that gates order
              approval and the kill switch does not belong there. Cost is one
              setup step per phone. */}
          <Text style={styles.fieldLabel}>Sunucu token&apos;ı</Text>
          <View style={styles.tokenRow}>
            <TextInput
              style={styles.tokenInput}
              value={tokenDraft}
              onChangeText={setTokenDraft}
              placeholder={hasToken ? '•••••••• kayıtlı' : 'secrets.env içindeki DEV_API_TOKEN'}
              placeholderTextColor={theme.ink3 ?? theme.textMuted}
              autoCapitalize="none"
              autoCorrect={false}
              secureTextEntry
              accessibilityLabel="Sunucu token'ı"
            />
            <Pressable
              style={({ pressed }) => [
                styles.tokenSave,
                !tokenDraft.trim() && styles.tokenSaveOff,
                pressed && styles.buttonPressed,
              ]}
              onPress={saveToken}
              disabled={!tokenDraft.trim()}
              accessibilityRole="button"
              accessibilityLabel="Token'ı kaydet"
            >
              <Text style={styles.tokenSaveLabel}>Kaydet</Text>
            </Pressable>
          </View>
          <Text style={styles.note}>
            {hasToken
              ? 'Bu cihazda kayıtlı. Sunucuda token değişirse yenisini buraya gir.'
              : 'Token olmadan portföy, emirler ve ajan ekranları 401 döner. Sunucudaki secrets.env içinde DEV_API_TOKEN olarak duruyor.'}
          </Text>
        </Card>

        {/* ── Eval scorecard ────────────────────────────────────── */}
        <GroupTitle
          title="Eval scorecard"
          right={
            evalData ? (
              <Text style={[styles.verdict, { color: VERDICT_COLOR[evalData.verdict] ?? theme.ink2 ?? theme.textSecondary }]}>
                {evalData.verdict}
              </Text>
            ) : null
          }
        />
        <Card>
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
                    { color: VERDICT_COLOR[evalData.provisional_verdict] ?? theme.ink2 ?? theme.textSecondary },
                  ]}
                >
                  eğilim: {evalData.provisional_verdict}
                </Text>
              ) : null}

              <View style={styles.evalGrid}>
                <StatCell
                  style={styles.evalCell}
                  size="sm"
                  label="Sharpe"
                  value={fixed(evalData.sharpe, 2)}
                  hint={`> ${evalData.gate_sharpe}`}
                  valueColor={sharpeFail ? failColor : undefined}
                />
                <StatCell
                  style={styles.evalCell}
                  size="sm"
                  label="Sortino"
                  value={fixed(evalData.sortino, 2)}
                  hint="downside"
                />
                <StatCell
                  style={styles.evalCell}
                  size="sm"
                  label="Max DD"
                  value={`${fixed(evalData.max_dd_pct, 1)}%`}
                  hint={`< ${evalData.gate_max_dd_pct}%`}
                  valueColor={ddFail ? failColor : undefined}
                />
                <StatCell
                  style={styles.evalCell}
                  size="sm"
                  label="Calmar"
                  value={fixed(evalData.calmar, 2)}
                  hint="getiri/DD"
                />
                <StatCell
                  style={styles.evalCell}
                  size="sm"
                  label="Getiri"
                  value={`${evalData.total_return_pct >= 0 ? '+' : MINUS}${Math.abs(evalData.total_return_pct).toFixed(1)}%`}
                  hint={`${evalData.days}g`}
                />
                {evalData.spy_return_pct != null ? (
                  <StatCell
                    style={styles.evalCell}
                    size="sm"
                    label="α vs SPY"
                    value={`${evalData.total_return_pct - evalData.spy_return_pct >= 0 ? '+' : MINUS}${Math.abs(evalData.total_return_pct - evalData.spy_return_pct).toFixed(1)}%`}
                    hint={`SPY ${evalData.spy_return_pct >= 0 ? '+' : MINUS}${Math.abs(evalData.spy_return_pct).toFixed(1)}%`}
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
        </Card>

        {/* ── Risk: the kill switch's new home ──────────────────── */}
        <GroupTitle title="Risk & uyarılar" />
        <Card padded={false} clip>
          <DataRow
            title="Kill switch ve limitler"
            subtitle="RUN / PAUSE / FLATTEN, devre kesiciler ve portföy limitleri Risk ekranında."
            onPress={() => router.push('/(tabs)/risk' as never)}
            accessibilityLabel="Risk ve uyarılar ekranını aç"
            accessibilityHint="Kill switch, devre kesiciler ve portföy limitleri"
          />
        </Card>

        {/* ── Emir gönderimi ────────────────────────────────────── */}
        <GroupTitle title="Emir gönderimi" />
        <Card>
          <Text style={styles.noteFirst}>
            Bu üç ayar sunucudaki ExecutionConfig&apos;ten gelir. Uygulama bunları yazamaz; API bir okuma
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
        </Card>

        {/* ── Strateji ──────────────────────────────────────────── */}
        <GroupTitle title="Strateji" />
        <Card>
          <Text style={styles.noteFirst}>
            Boyutlama yöntemi ve tek isim tavanı sunucudaki risk yapılandırmasıdır
            (SizingMethod / PortfolioLimits) — uygulamadan değiştirilemez.
          </Text>

          <View style={styles.labelWithTag}>
            <Text style={styles.fieldLabel}>Boyutlama yöntemi</Text>
            <Tag label="salt okunur" variant="neutral" size="sm" />
          </View>
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
            style={styles.seg}
          />

          <View style={styles.capRow}>
            <Text style={styles.capLabel}>Tek isim tavanı</Text>
            <View style={styles.capBox}>
              <Text style={styles.capValue}>{SINGLE_NAME_CAP_PCT}%</Text>
            </View>
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
            {strategyFields.map((f) => (
              <StatCell key={f.label} style={styles.gridCell} size="sm" label={f.label} value={f.value} />
            ))}
          </View>
        </Card>

        {/* ── Bildirimler ───────────────────────────────────────── */}
        <GroupTitle
          title="Bildirimler"
          right={
            <Text style={[styles.permission, { color: PERMISSION_COPY[permission ?? 'undetermined'].color }]}>
              {permission ? PERMISSION_COPY[permission].text : '…'}
            </Text>
          }
        />
        <Card>
          <ToggleRow
            first
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
          <View style={styles.buttonPair}>
            <SecondaryButton
              label="Test bildirimi"
              onPress={() => void handleTestPush()}
              hint="Bu cihaza bir test bildirimi gönderir"
              style={styles.buttonHalf}
            />
            <SecondaryButton
              label={`Geçmiş (${inbox.length}${unreadCount(inbox) > 0 ? ` · ${unreadCount(inbox)}` : ''})`}
              onPress={() => router.push('/notifications' as never)}
              hint="Bildirim geçmişini açar"
              style={styles.buttonHalf}
            />
          </View>
        </Card>

        {/* ── Görünüm ───────────────────────────────────────────── */}
        {/* Language and palette were both settable only by the device: the app
            followed the phone's locale and opened in whichever palette the
            build defaulted to. Both are reader choices, and both persist. */}
        <GroupTitle title="Görünüm" />
        <Card>
          <Text style={styles.fieldLabelFirst}>Dil / Language</Text>
          <Seg
            options={[
              { value: 'tr', label: 'Türkçe' },
              { value: 'en', label: 'English' },
            ]}
            value={lang}
            onChange={(next) => void setLanguage(next)}
            block
            style={styles.seg}
          />

          {/* Three palettes ship; until now only two were reachable, so a
              reader who left Aurora could not come back to it. */}
          <Text style={styles.fieldLabel}>Tema</Text>
          <Seg
            options={[
              { value: 'aurora', label: 'Aurora', accessibilityLabel: 'Aurora teması' },
              { value: 'modernist', label: 'Açık', accessibilityLabel: 'Açık tema' },
              { value: 'dark', label: 'Koyu', accessibilityLabel: 'Koyu tema' },
            ]}
            value={themeName}
            onChange={(next: ThemeName) => setTheme(next)}
            block
            style={styles.seg}
          />
          <Text style={styles.note}>
            Seçim bu cihazda saklanır. Henüz taşınmamış ekranlar koyu paletle çizilir.
          </Text>
        </Card>

        {/* ── Sistem ────────────────────────────────────────────── */}
        <GroupTitle title="Sistem" />
        <Card>
          <View style={styles.gridFirst}>
            {systemFields.map((f) => (
              <StatCell
                key={f.label}
                style={styles.gridCell}
                size="sm"
                label={f.label}
                value={f.value}
                valueColor={f.color}
              />
            ))}
          </View>
        </Card>

        <View style={styles.footerButtons}>
          <SecondaryButton
            label="Çıkış yap"
            onPress={() => setSignOutOpen(true)}
            hint="Oturumu kapatır ve giriş ekranına döner"
          />
          <SecondaryButton
            label="Hesabı sil"
            onPress={() => setDeleteAccountOpen(true)}
            hint="Hesabınızı ve tüm verilerinizi kalıcı olarak siler"
            danger
          />
        </View>

        <View style={styles.legalLinks}>
          <Pressable
            onPress={() => void Linking.openURL(PRIVACY_URL)}
            accessibilityRole="link"
            accessibilityLabel="Gizlilik Politikası"
            style={styles.legalLink}
          >
            <Text style={styles.legalLinkText}>Gizlilik Politikası</Text>
          </Pressable>
          <Text style={styles.legalSep}>·</Text>
          <Pressable
            onPress={() => void Linking.openURL(TERMS_URL)}
            accessibilityRole="link"
            accessibilityLabel="Kullanım Koşulları"
            style={styles.legalLink}
          >
            <Text style={styles.legalLinkText}>Kullanım Koşulları</Text>
          </Pressable>
          <Text style={styles.legalSep}>·</Text>
          <Pressable
            onPress={() => void Linking.openURL(SUPPORT_URL)}
            accessibilityRole="link"
            accessibilityLabel="Destek"
            style={styles.legalLink}
          >
            <Text style={styles.legalLinkText}>Destek</Text>
          </Pressable>
        </View>

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

      <Sheet
        visible={deleteAccountOpen}
        title="Hesabı kalıcı olarak sil?"
        message="Bu işlem geri alınamaz. Firebase kimliğiniz ve sunucudaki tüm verileriniz silinecek."
        actions={[
          { label: 'Vazgeç', onPress: () => setDeleteAccountOpen(false) },
          { label: 'Hesabı sil', onPress: () => void handleDeleteAccount(), primary: true },
        ]}
        onDismiss={() => setDeleteAccountOpen(false)}
      />
    </SafeAreaView>
  );
}

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.background },
    content: {
      paddingHorizontal: sh.space[3],
      paddingTop: sh.space[1],
      paddingBottom: TAB_BAR_CLEARANCE,
    },
    heading: { color: t.textPrimary, letterSpacing: -0.5, paddingBottom: sh.space[1], ...TYPE.h2 },

    // The group title: kicker ink, hung off the card's left edge.
    groupTitleRow: {
      flexDirection: 'row',
      alignItems: 'baseline',
      justifyContent: 'space-between',
      gap: sh.space[1],
      marginTop: sh.space[3] + 2,
      marginBottom: sh.space[1],
      paddingHorizontal: sh.space[0],
    },
    groupTitle: { color: t.ink3 ?? t.textMuted, flexShrink: 1, ...TYPE.kicker },

    note: { color: t.ink2 ?? t.textSecondary, marginTop: sh.space[1], lineHeight: 16, ...TYPE.helper },
    noteFirst: { color: t.ink3 ?? t.textMuted, lineHeight: 16, ...TYPE.helper },
    muted: { color: t.ink2 ?? t.textSecondary, lineHeight: 18, ...TYPE.body },

    // Hesap & mod
    modeRow: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1] + 2, flexWrap: 'wrap' },
    modeAccount: { color: t.textPrimary, flexShrink: 1, ...TYPE.bodyStrong },
    kvList: { marginTop: sh.space[2], gap: sh.space[1] },
    kvRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: sh.space[2] },
    kvKey: { color: t.ink2 ?? t.textSecondary, ...TYPE.helper, fontSize: 12 },
    kvValue: { color: t.textPrimary, flexShrink: 1, fontSize: 12, ...font(600), ...TABULAR },

    fieldLabel: { color: t.ink2 ?? t.textSecondary, marginTop: sh.space[2] + 2, ...TYPE.helper, fontSize: 12 },
    fieldLabelFirst: { color: t.ink2 ?? t.textSecondary, ...TYPE.helper, fontSize: 12 },
    labelWithTag: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1] },

    tokenRow: { flexDirection: 'row', gap: sh.space[1], marginTop: sh.space[1] - 2 },
    tokenInput: {
      flex: 1,
      minHeight: MIN_TOUCH_TARGET,
      backgroundColor: t.paper ?? t.background,
      color: t.textPrimary,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
      borderRadius: sh.radiusSmall,
      paddingHorizontal: sh.space[2],
      paddingVertical: sh.space[1] + 2,
      ...TYPE.body,
    },
    tokenSave: {
      backgroundColor: t.textPrimary,
      borderRadius: sh.radiusSmall,
      paddingHorizontal: sh.space[3],
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: MIN_TOUCH_TARGET,
    },
    tokenSaveOff: { opacity: 0.45 },
    tokenSaveLabel: { color: t.background, ...TYPE.body, ...font(800) },

    // Eval
    verdict: { fontSize: 15, letterSpacing: 0.5, ...font(800) },
    trend: { marginBottom: sh.space[1], ...TYPE.helper, ...font(800) },
    evalGrid: { flexDirection: 'row', flexWrap: 'wrap', rowGap: sh.space[2], marginBottom: sh.space[2] },
    evalCell: { width: '33.33%', paddingRight: sh.space[1] },
    countdown: { color: t.ink2 ?? t.textSecondary, ...TYPE.helper, ...font(600) },
    gateList: {
      marginTop: sh.space[2],
      paddingTop: sh.space[2],
      gap: sh.space[0],
      borderTopWidth: sh.hairline,
      borderTopColor: t.line ?? t.divider,
    },
    gateRow: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1] + 2 },
    gateIcon: { width: 14, textAlign: 'center', ...TYPE.body, ...font(800) },
    gateName: { color: t.textPrimary, flex: 1, ...TYPE.body },
    gateDetail: { color: t.ink2 ?? t.textSecondary, ...TYPE.helper, ...TABULAR },
    evalReason: { color: t.warning, marginTop: sh.space[2], lineHeight: 16, ...TYPE.helper },
    flowCaveat: { color: t.downText, marginTop: sh.space[2], lineHeight: 16, ...TYPE.helper },

    // Toggles
    toggleRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: sh.space[2] + 2,
      paddingVertical: sh.space[2],
      minHeight: 56,
    },
    toggleRowRuled: { borderTopWidth: sh.hairline, borderTopColor: t.line ?? t.divider },
    toggleText: { flex: 1 },
    toggleLabel: { color: t.textPrimary, ...TYPE.bodyStrong },
    toggleDesc: { color: t.ink2 ?? t.textSecondary, marginTop: 2, lineHeight: 16, ...TYPE.helper },
    sourceTag: { marginTop: sh.space[1] - 2 },
    track: {
      width: TOGGLE_W,
      height: TOGGLE_H,
      borderRadius: sh.radiusPill,
      justifyContent: 'center',
    },
    trackReadOnly: { opacity: READ_ONLY_OPACITY },
    knob: {
      width: KNOB,
      height: KNOB,
      borderRadius: sh.radiusPill,
      position: 'absolute',
      left: 0,
    },

    // Strateji
    seg: { marginTop: sh.space[1] },
    capRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: sh.space[1],
      marginTop: sh.space[2] + 2,
    },
    capLabel: { color: t.textPrimary, flexShrink: 1, ...TYPE.body },
    capBox: {
      paddingHorizontal: sh.space[2],
      paddingVertical: sh.space[0] + 2,
      borderWidth: sh.hairline,
      borderColor: t.line ?? t.divider,
      borderRadius: sh.radiusSmall,
      backgroundColor: t.paper ?? t.background,
    },
    capValue: { color: t.textPrimary, ...TYPE.body, ...font(800), ...TABULAR },
    capNow: { marginTop: sh.space[1] - 2, ...TYPE.helper, ...font(600), ...TABULAR },

    // Label/value grids (Strateji constants, Sistem)
    grid: { flexDirection: 'row', flexWrap: 'wrap', rowGap: sh.space[2], marginTop: sh.space[2] + 2 },
    gridFirst: { flexDirection: 'row', flexWrap: 'wrap', rowGap: sh.space[2] },
    gridCell: { width: '50%', paddingRight: sh.space[1] },

    // Bildirimler
    permission: { fontSize: 12, ...font(600) },
    buttonPair: {
      flexDirection: 'row',
      gap: sh.space[1],
      paddingTop: sh.space[2],
      borderTopWidth: sh.hairline,
      borderTopColor: t.line ?? t.divider,
    },
    buttonHalf: { flex: 1, marginTop: 0 },

    buttonSecondary: {
      marginTop: sh.space[1],
      paddingHorizontal: sh.space[2],
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: MIN_TOUCH_TARGET,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
      borderRadius: sh.radius,
      backgroundColor: t.surface,
      alignSelf: 'stretch',
    },
    buttonPressed: { opacity: 0.7 },
    buttonDanger: { backgroundColor: 'transparent', borderColor: 'transparent' },
    buttonSecondaryText: { color: t.textPrimary, fontSize: 13, ...font(800) },
    buttonDangerText: { color: t.downText },

    footerButtons: { marginTop: sh.space[3], gap: sh.space[1] },

    legalLinks: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      flexWrap: 'wrap',
      gap: sh.space[1],
      marginTop: sh.space[2],
    },
    legalLink: { minHeight: MIN_TOUCH_TARGET, justifyContent: 'center' },
    legalLinkText: { color: t.ink2 ?? t.textSecondary, ...TYPE.helper, textDecorationLine: 'underline' },
    legalSep: { color: t.ink3 ?? t.textMuted, ...TYPE.helper },

    disclaimer: {
      color: t.ink3 ?? t.textMuted,
      paddingTop: sh.space[2],
      textAlign: 'center',
      lineHeight: 16,
      ...TYPE.helper,
    },
  });
