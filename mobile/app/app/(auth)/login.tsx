/**
 * Screen 11 — Giriş. Aurora.
 *
 * The prototype makes this screen a poster on top of a form: a full-bleed dark
 * slab with a brand glow bleeding off the top-right corner, and below it the
 * ordinary rounded system — surface inputs on the page ground, an ink button,
 * an outlined one under it.
 *
 * Two things this screen is deliberately NOT.
 *
 * It is not a session. There is no Cognito yet — the client rides the dev
 * bearer set up in `api/client.ts` — so "Giriş yap" gates the disclaimer
 * acknowledgement and the shape of the e-mail, and then hands over to the tabs.
 * `stores/auth.ts` is left alone on purpose: writing a fabricated user id into
 * a store nothing reads would look like a session without being one.
 *
 * It is not its own auth flow. "Cihaz kilidi ile aç" calls the same
 * `authenticate()` from `src/auth/` that the order-approval flow calls, so the
 * biometric/passcode fallback decision lives in exactly one place
 * (`authPolicy.resolveAuthMode`) and is unit-tested there.
 */

import {
  currentUser,
  isConfigured as isFirebaseConfigured,
  signIn as firebaseSignIn,
  signUp as firebaseSignUp,
} from '@/auth/firebase';
import { isInviteCodeValid, signUpEnabled } from '@/auth/inviteCode';
import { useAuthStore } from '@/stores/auth';
import { signInErrorTr } from '@/auth/signInError';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  Pressable,
  ScrollView,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import { useMemo, useRef, useState } from 'react';
import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';
import Svg, { Path, Defs, RadialGradient, Stop, Circle } from 'react-native-svg';

import { useReadiness } from '@/api/hooks';
import { authenticate } from '@/auth/biometric';
import { Tag, type TagVariant } from '@/components/Tag';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { font, TYPE } from '@/theme/type';
import { MIN_TOUCH_TARGET } from '@/utils/a11y';

/*
 * Copy. Turkish, verbatim from the prototype, kept at the top so it reads as
 * content rather than as three string literals buried in the markup.
 *
 * `auth.*` and `disclaimer.*` already exist in the i18n bundle, so those come
 * from `t()`. The rest has no key and the bundles are not mine to edit — see
 * the handoff notes; adding `auth.ack`, `auth.deviceUnlock` and the banner
 * copy to tr/en is the follow-up.
 *
 * The gate line is not decoration and not a guess: 10 trading days, Sharpe
 * 1.0 and MaxDD 15% are `MIN_TRADING_DAYS` / `GATE_SHARPE` / `GATE_MAX_DD` in
 * `agent/scripts/eval_report.py`, and "beat SPY" is the fourth scorecard row —
 * a flag rather than a hard gate, exactly as the `go_live_gates` lesson this
 * app ships already explains.
 *
 * Two lines, not three: the prototype breaks it once (`loginBanner1` /
 * `loginBanner2`), and where the break falls is typography.
 */
const BANNER_LINES = ['Gerçek para,', "dört gate'in ardında."] as const;
const GATE_LINE = "≥ 10 işlem günü · Sharpe > 1.0 · MaxDD < 15% · SPY'yi geç";
const DEVICE_UNLOCK = 'Cihaz kilidi ile aç';

/** The wordmark beside the brand mark, as the prototype sets it. */
const WORDMARK = 'Trader';

/**
 * The affirmation appended to the disclaimer. The sentence itself is NOT
 * repeated here: it is `disclaimer.short`, which five other screens already
 * render from the bundle. This is the one screen where the reader *consents*
 * to it, so a hand-typed second copy is the one that would silently disagree
 * with the rest of the app the day the wording changes.
 */
const ACK_SUFFIX = { tr: ' — okudum.', en: ' — I have read this.' } as const;

/** The prototype's own minimum (`trader-core.js`: `s.password.length >= 4`). */
const MIN_PASSWORD = 4;

/** The banner's own top padding, on top of whatever the notch costs. */
const BANNER_TOP = 28;

/** The prototype's control height: 50 for inputs, 52 for the two buttons. */
const INPUT_HEIGHT = 50;
const BUTTON_HEIGHT = 52;

/**
 * `canSignIn` in `trader-core.js` is `email.includes('@') && password.length >= 4
 * && ack`. Only the e-mail test is tightened here: `includes('@')` accepts "@",
 * and a sign-in button that lights up for a single character reads as broken
 * rather than permissive. Local part, domain, and a dot in the domain — nothing
 * stricter, so a valid address is never refused by the client.
 */
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function isValidEmail(value: string): boolean {
  return EMAIL_RE.test(value.trim());
}

/** Lucide `scan-face` — the system's mark for "this asks for the device lock". */
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

/** Lucide `check`, stroke 3 — the tick inside the acknowledgement box. */
function Check({ color, size = 14 }: { color: string; size?: number }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={3} strokeLinecap="round" strokeLinejoin="round">
      <Path d="M20 6 9 17l-5-5" />
    </Svg>
  );
}

export default function LoginScreen() {
  const router = useRouter();
  const { t, i18n } = useTranslation();
  const theme = useTheme();
  const sh = useShape();
  const insets = useSafeAreaInsets();
  const styles = useMemo(() => makeStyles(theme, sh), [theme, sh]);

  const ackText =
    t('disclaimer.short') + (i18n.language?.startsWith('tr') ? ACK_SUFFIX.tr : ACK_SUFFIX.en);

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [ack, setAck] = useState(false);
  const [focused, setFocused] = useState<'email' | 'password' | 'invite' | null>(null);
  // Kayit akisi yalnizca bir davet kodu YAPILANDIRILMISSA var olur; kodsuz
  // uretilmis bir build'de sekme hic cizilmez ve ekran bugunku gibi davranir.
  const signUpAvailable = signUpEnabled();
  const [authMode, setAuthMode] = useState<'signIn' | 'signUp'>('signIn');
  const [invite, setInvite] = useState('');
  const isSignUp = signUpAvailable && authMode === 'signUp';
  const [deviceBusy, setDeviceBusy] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const authSignIn = useAuthStore((s) => s.signIn);
  const passwordRef = useRef<TextInput>(null);

  /*
   * Mode is tri-state, exactly as the status strip has it: PAPER and LIVE only
   * when the backend said so. Asserting PAPER while offline would be a lie the
   * day this app points at a live account, and the login screen is the one
   * place a reader decides how much care the next tap deserves.
   */
  const { data: readiness, isError: readinessFailed } = useReadiness();
  const mode = readiness?.trading_mode;
  const modeLabel = mode === 'live' ? 'LIVE — GERÇEK PARA' : mode === 'paper' ? 'PAPER' : 'MOD ?';
  /*
   * The prototype tints the chip rather than filling it: rose-300 on LIVE,
   * indigo-200 otherwise. Aurora already owns both as soft-ground variants, so
   * the chip borrows them instead of naming two literals — and on a palette
   * with no soft grounds `Tag` falls back to `surface`, where the meaning
   * survives in the text colour alone.
   */
  const modeVariant: TagVariant = mode === 'live' ? 'down' : mode === 'paper' ? 'brand' : 'outlineMuted';
  // The unknown branch has to say which unknown it is. `useReadiness` does not
  // retry, so a failed poll sits at `undefined` forever — reporting that as
  // "waiting for the backend" would describe a stage the app is not in.
  const accountLine =
    mode === 'live'
      ? 'LIVE hesap — onaylanan her emir gerçek para ile gönderilir.'
      : mode === 'paper'
        ? 'Alpaca paper hesabı — gerçek para değildir.'
        : readinessFailed
          ? 'Hesap modu okunamadı — backend yanıt vermiyor.'
          : 'Hesap modu bilinmiyor — backend bağlantısı bekleniyor.';

  const emailOk = isValidEmail(email);
  const inviteOk = !isSignUp || isInviteCodeValid(invite);
  const canSignIn = emailOk && password.length >= MIN_PASSWORD && ack && inviteOk;

  const enter = () => router.replace('/(tabs)/portfolio');

  /**
   * The credentialed path. Requires Firebase configuration and will not allow
   * entry without valid authentication. The fallback to local-only signin has
   * been removed for App Store compliance.
   */
  const signIn = async () => {
    if (!canSignIn || busy) return;
    setAuthError(null);

    if (!isFirebaseConfigured()) {
      setAuthError('Giriş yapılamıyor. Lütfen daha sonra tekrar deneyin.');
      return;
    }

    // Kapi burada BIR KEZ DAHA yoklaniyor. `canSignIn` dugmeyi devre disi
    // birakiyor ama klavyedeki "git" tusu de bu fonksiyonu cagiriyor; tek
    // savunmanin gorsel bir devre disi birakma olmasi yeterli degil.
    if (isSignUp && !isInviteCodeValid(invite)) {
      setAuthError('Davet kodu geçersiz.');
      return;
    }

    setBusy(true);
    try {
      const user = isSignUp
        ? await firebaseSignUp(email, password)
        : await firebaseSignIn(email, password);
      // The uid is what will separate one family member's actions from
      // another's; the email is only for display.
      authSignIn(user.uid, user.email ?? email.trim());
      enter();
    } catch (e) {
      setAuthError(signInErrorTr(e));
    } finally {
      setBusy(false);
    }
  };

  /*
   * The returning-user path. It is not gated on the acknowledgement: the tick
   * belongs to the credentialed first sign-in, and the device lock proves who
   * is holding the phone, not what they have read. Same call the approve flow
   * makes, so a device with only a PIN gets in here too.
   */
  const deviceSignIn = async () => {
    if (deviceBusy) return;
    setAuthError(null);
    setDeviceBusy(true);
    try {
      const { success, mode: factor } = await authenticate('Trader hesabını aç');
      if (success) {
        // Device unlock is a RE-ENTRY, not an authentication. It proves the
        // person holding the device is its owner; it cannot prove who they are
        // to the server. Before Firebase that distinction did not matter,
        // because the shared bearer was the whole security model — so this
        // path called enter() and the app fell back to that secret.
        //
        // With Firebase configured it does matter: no session means no ID
        // token, the app silently drops to the shared bearer, and the moment
        // that secret is deleted this path 401s on every screen with nothing
        // on screen explaining why. And while it works it is a hole — the
        // whole point of identities is that a device lock is not one.
        //
        // Firebase persists the session, so a returning user still gets the
        // one tap. Someone who has never signed in on this device is asked to,
        // once.
        if (isFirebaseConfigured() && currentUser() === null) {
          setAuthError(
            'Bu cihazda önce e-posta ve şifre ile giriş yapmalısın. Sonraki açılışlarda cihaz kilidi yeterli.',
          );
          return;
        }
        enter();
        return;
      }
      setAuthError(
        factor === 'none'
          ? 'Cihazınızda ekran kilidi (Face/Touch ID veya şifre) tanımlı değil. E-posta ve şifre ile giriş yapın.'
          : 'Cihaz kilidi doğrulanmadı. Tekrar deneyin veya e-posta ile giriş yapın.',
      );
    } catch {
      // A throw here is the OS module failing, not a refused face — say so
      // rather than accusing the reader of failing a check they never saw.
      setAuthError('Cihaz kilidi açılamadı. E-posta ve şifre ile giriş yapın.');
    } finally {
      setDeviceBusy(false);
    }
  };

  const signInLabel = isSignUp ? 'Hesap oluştur' : t('auth.signIn');

  return (
    <SafeAreaView style={styles.container} edges={['bottom']}>
      {/* The banner runs under the status bar, so the clock and the carrier
          sit on the slab rather than on the page ground. Aurora is dark
          throughout, so light content is right either way. */}
      <StatusBar style="light" />
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView
          contentContainerStyle={styles.scroll}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="on-drag"
        >
          {/*
           * The poster. The prototype draws `#18181b` on a LIGHT page — a slab
           * darker than everything around it. Aurora's page ground is already
           * the darkest thing in the system, so the slab inverts direction and
           * becomes the elevated surface: still the one full-bleed rectangle
           * that is not the page, which is the property that made it a poster.
           * Same decision the Bugün hero made, for the same reason.
           *
           * Type here is bespoke, not the mobile scale: 36px display and a 12px
           * support line, straight from the prototype. It is the only block in
           * the app allowed to set its own sizes, and it earns that by being
           * the only block that is a poster rather than an interface.
           */}
          <View style={[styles.banner, { paddingTop: insets.top + BANNER_TOP }]}>
            {/* The prototype's radial glow bleeding off the top-right corner.
                RN has no gradient primitive and `expo-linear-gradient` is not a
                dependency, so it is drawn with react-native-svg — the same way
                the Bugün hero draws its own. */}
            <Svg style={styles.glow} width="100%" height="100%" pointerEvents="none">
              <Defs>
                <RadialGradient id="loginGlow" cx="50%" cy="50%" r="50%">
                  <Stop offset="0%" stopColor={theme.brand ?? theme.accent} stopOpacity={0.55} />
                  <Stop offset="70%" stopColor={theme.brand ?? theme.accent} stopOpacity={0} />
                </RadialGradient>
              </Defs>
              <Circle cx="82%" cy={50} r={130} fill="url(#loginGlow)" />
            </Svg>

            {/* The brand row lives INSIDE the slab here, unlike the Modernist
                port: Aurora's mode chip has soft-ground variants that read on
                an elevated surface, which is the thing that had forced the row
                down onto the page ground before. */}
            <View style={styles.brand}>
              <View style={styles.brandMark}>
                <Text style={styles.brandMarkText}>T</Text>
              </View>
              <Text style={styles.wordmark}>{WORDMARK}</Text>
              <Tag label={modeLabel} variant={modeVariant} caps />
            </View>

            {/* Read as one sentence, not two fragments — the line break is
                typography, not structure. */}
            <View style={styles.headline} accessible accessibilityLabel={BANNER_LINES.join(' ')}>
              {BANNER_LINES.map((line) => (
                <Text key={line} style={styles.bannerLine}>
                  {line}
                </Text>
              ))}
            </View>
            <Text style={styles.gateLine}>{GATE_LINE}</Text>
          </View>

          <View style={styles.form}>
            <Text style={styles.h2} accessibilityRole="header">
              {signInLabel}
            </Text>

            <View style={styles.field}>
              <Text style={styles.label}>{t('auth.email')}</Text>
              <TextInput
                value={email}
                onChangeText={setEmail}
                onFocus={() => setFocused('email')}
                onBlur={() => setFocused(null)}
                autoCapitalize="none"
                autoCorrect={false}
                autoComplete="email"
                keyboardType="email-address"
                textContentType="emailAddress"
                placeholder="ad@ornek.com"
                placeholderTextColor={theme.ink3 ?? theme.textMuted}
                returnKeyType="next"
                onSubmitEditing={() => passwordRef.current?.focus()}
                submitBehavior="submit"
                style={[styles.input, focused === 'email' && styles.inputFocused]}
                accessibilityLabel={t('auth.email')}
              />
            </View>

            <View style={styles.field}>
              <Text style={styles.label}>{t('auth.password')}</Text>
              <TextInput
                ref={passwordRef}
                value={password}
                onChangeText={setPassword}
                onFocus={() => setFocused('password')}
                onBlur={() => setFocused(null)}
                secureTextEntry
                autoCapitalize="none"
                autoComplete="current-password"
                textContentType="password"
                placeholder="••••••••"
                placeholderTextColor={theme.ink3 ?? theme.textMuted}
                returnKeyType="go"
                onSubmitEditing={signIn}
                style={[styles.input, focused === 'password' && styles.inputFocused]}
                accessibilityLabel={t('auth.password')}
              />
            </View>

            {isSignUp ? (
              <View style={styles.field}>
                <Text style={styles.label}>Davet kodu</Text>
                <TextInput
                  value={invite}
                  onChangeText={setInvite}
                  onFocus={() => setFocused('invite')}
                  onBlur={() => setFocused(null)}
                  autoCapitalize="characters"
                  autoCorrect={false}
                  autoComplete="off"
                  placeholder="DAVET-KODU"
                  placeholderTextColor={theme.ink3 ?? theme.textMuted}
                  returnKeyType="go"
                  onSubmitEditing={signIn}
                  style={[styles.input, focused === 'invite' && styles.inputFocused]}
                  accessibilityLabel="Davet kodu"
                  accessibilityHint="Hesap açmak için size verilen kodu girin"
                />
              </View>
            ) : null}

            {/* 22px rounded box, 2px ink border, ink fill and an inverted tick
                when ticked. The whole row is the target so the box itself never
                has to be hit. */}
            <Pressable
              onPress={() => setAck((v) => !v)}
              style={styles.ackRow}
              accessibilityRole="checkbox"
              accessibilityState={{ checked: ack }}
              accessibilityLabel={ackText}
            >
              <View style={[styles.ackBox, ack && styles.ackBoxChecked]}>
                {ack ? <Check color={theme.inkInv ?? theme.background} /> : null}
              </View>
              <Text style={styles.ackText}>{ackText}</Text>
            </Pressable>

            <Pressable
              onPress={signIn}
              // Sign-in is now a network call, so the button has to say it is
              // working. Without this the form looks inert for a second or two
              // and the natural response is to tap again.
              disabled={!canSignIn || busy}
              style={({ pressed }) => [
                styles.primaryBtn,
                pressed && styles.pressed,
                (!canSignIn || busy) && styles.btnDisabled,
              ]}
              accessibilityRole="button"
              accessibilityLabel={signInLabel}
              accessibilityState={{ disabled: !canSignIn || busy, busy }}
              accessibilityHint={
                canSignIn
                  ? undefined
                  : !emailOk
                    ? 'Geçerli bir e-posta girin'
                    : password.length < MIN_PASSWORD
                      ? 'Şifrenizi girin'
                      : !ack
                        ? 'Yasal metni onaylayın'
                        : 'Davet kodunu girin'
              }
            >
              <Text style={styles.primaryBtnText}>
                {busy ? 'Giriş yapılıyor…' : signInLabel}
              </Text>
            </Pressable>

            <Pressable
              onPress={() => void deviceSignIn()}
              disabled={deviceBusy}
              style={({ pressed }) => [
                styles.secondaryBtn,
                pressed && styles.secondaryPressed,
                deviceBusy && styles.btnDisabled,
              ]}
              accessibilityRole="button"
              accessibilityLabel={DEVICE_UNLOCK}
              accessibilityHint="Face ID, parmak izi veya cihaz şifresi ile giriş yapar"
              accessibilityState={{ disabled: deviceBusy, busy: deviceBusy }}
            >
              <ScanFace color={theme.textPrimary} />
              <Text style={styles.secondaryBtnText}>
                {deviceBusy ? 'Doğrulanıyor…' : DEVICE_UNLOCK}
              </Text>
            </Pressable>

            {/* The tab bar's Toast is mounted inside (tabs), so nothing raised
                from here would ever render. The failure states belong on the
                screen anyway — they say what to do next. */}
            {authError ? (
              <Text
                style={styles.errorLine}
                accessibilityRole="alert"
                accessibilityLiveRegion="polite"
              >
                {authError}
              </Text>
            ) : null}

            {signUpAvailable ? (
              <Pressable
                onPress={() => {
                  // Mod degisince hata ve kod temizlenir: onceki moddan kalan
                  // bir hata mesaji yeni modu anlatmiyor.
                  setAuthMode((m) => (m === 'signIn' ? 'signUp' : 'signIn'));
                  setAuthError(null);
                  setInvite('');
                }}
                disabled={busy}
                style={styles.modeSwitch}
                accessibilityRole="button"
                accessibilityLabel={
                  isSignUp ? 'Girişe dön' : 'Davet kodu ile hesap oluştur'
                }
              >
                <Text style={styles.modeSwitchText}>
                  {isSignUp ? 'Hesabım var — giriş yap' : 'Davet kodum var — hesap oluştur'}
                </Text>
              </Pressable>
            ) : null}

            <Text style={styles.account}>{accountLine}</Text>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.background },
    flex: { flex: 1 },
    scroll: { flexGrow: 1 },

    // paddingTop is applied at the call site: the panel is the one full-bleed
    // fill in the system, so it has to reach the top of the screen rather than
    // start below the notch with a strip of page ground above it. Same trick
    // the status strip uses on every tab — safe-area inset as padding, so the
    // fill goes under the bar and the text does not.
    banner: {
      backgroundColor: t.surfaceElevated,
      borderBottomWidth: sh.hairline,
      borderBottomColor: t.line ?? t.divider,
      paddingHorizontal: sh.space[4],
      paddingBottom: sh.space[4] + sh.space[0],
      overflow: 'hidden',
    },
    glow: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 },

    brand: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1] },
    // The prototype's 30px disc: ink fill, ground letter. Under Modernist
    // `radiusPill` is 0 and the mark becomes the square that system specifies,
    // which is the point of reading the radius rather than writing one.
    brandMark: {
      width: 30,
      height: 30,
      borderRadius: sh.radiusPill,
      backgroundColor: t.textPrimary,
      alignItems: 'center',
      justifyContent: 'center',
    },
    brandMarkText: { color: t.inkInv ?? t.background, fontSize: 13, ...font(800) },
    wordmark: { color: t.textPrimary, fontSize: 16, ...font(600) },

    headline: { marginTop: sh.space[4] + sh.space[0] },
    bannerLine: {
      color: t.textPrimary,
      fontSize: 36,
      lineHeight: 38,
      letterSpacing: -1.08,
      ...font(800),
    },
    gateLine: {
      color: t.ink2 ?? t.textSecondary,
      fontSize: 12,
      lineHeight: 18,
      marginTop: sh.space[2] + 2,
      ...font(400),
    },

    // 20 top / 20 horizontal, the prototype's own form padding.
    form: {
      paddingHorizontal: sh.space[3] + sh.space[0],
      paddingTop: sh.space[3] + sh.space[0],
      paddingBottom: sh.space[4],
      gap: sh.space[2],
    },

    h2: { color: t.textPrimary, ...TYPE.h2, letterSpacing: -0.48 },

    field: { gap: sh.space[0] + 2 },
    label: { color: t.ink2 ?? t.textSecondary, fontSize: 12, ...font(400) },
    input: {
      backgroundColor: t.surface,
      color: t.textPrimary,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
      borderRadius: sh.radius,
      paddingHorizontal: sh.space[2] + 2,
      minHeight: INPUT_HEIGHT,
      fontSize: 15,
      ...font(400),
    },
    // `style-focus="border-color:var(--brand)"` — the brand is the caret and
    // focus colour, and it is the only state change on the form.
    inputFocused: { borderColor: t.brand ?? t.accent },

    ackRow: {
      flexDirection: 'row',
      alignItems: 'flex-start',
      gap: sh.space[2],
      paddingVertical: sh.space[0],
      minHeight: MIN_TOUCH_TARGET,
    },
    ackBox: {
      width: 22,
      height: 22,
      borderWidth: 2,
      borderColor: t.textPrimary,
      borderRadius: sh.radiusSmall,
      marginTop: 1,
      alignItems: 'center',
      justifyContent: 'center',
    },
    ackBoxChecked: { backgroundColor: t.textPrimary },
    ackText: { flex: 1, color: t.textPrimary, ...TYPE.body, lineHeight: 20 },

    // Ink fill, not the brand: the brand is the glow and the focus ring, and a
    // solid indigo button here would compete with the poster above it. The
    // prototype makes the same call — `background:var(--ink)`.
    primaryBtn: {
      backgroundColor: t.textPrimary,
      borderRadius: sh.radius,
      minHeight: BUTTON_HEIGHT,
      alignItems: 'center',
      justifyContent: 'center',
    },
    primaryBtnText: { color: t.inkInv ?? t.background, fontSize: 15, ...font(600) },
    secondaryBtn: {
      flexDirection: 'row',
      gap: sh.space[1] + 2,
      backgroundColor: t.surface,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
      borderRadius: sh.radius,
      minHeight: BUTTON_HEIGHT,
      alignItems: 'center',
      justifyContent: 'center',
    },
    secondaryBtnText: { color: t.textPrimary, fontSize: 14, ...font(600) },
    // A phone has no hover, so the prototype's hover lands on press: the ink
    // button dims, the outlined one brightens its edge.
    pressed: { opacity: 0.82 },
    secondaryPressed: { borderColor: t.brand ?? t.accent },
    btnDisabled: { opacity: 0.45 },

    errorLine: { color: t.downText ?? t.danger, ...TYPE.body, lineHeight: 20 },
    modeSwitch: {
      minHeight: MIN_TOUCH_TARGET,
      justifyContent: 'center',
      alignItems: 'center',
    },
    modeSwitchText: {
      color: t.brand ?? t.textPrimary,
      ...TYPE.bodyStrong,
      textDecorationLine: 'underline',
    },
  account: { color: t.ink3 ?? t.textMuted, ...TYPE.helper, lineHeight: 16 },
  });
