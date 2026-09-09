/**
 * Screen 11 — Giriş.
 *
 * The one screen that carries a full accent fill: the banner panel above the
 * form. Everything below it is the ordinary flat system — square corners, ink
 * on ground, no shadow.
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
import Svg, { Path } from 'react-native-svg';

import { useReadiness } from '@/api/hooks';
import { authenticate } from '@/auth/biometric';
import { Tag, type TagVariant } from '@/components/Tag';
import { useTheme } from '@/theme/useTheme';
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
 */
const BANNER_LINES = ['Gerçek para,', "dört gate'in", 'ardında.'] as const;
const GATE_LINE = "≥ 10 işlem günü · Sharpe > 1.0 · MaxDD < 15% · SPY'yi geç";
const DEVICE_UNLOCK = 'Cihaz kilidi ile aç';

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

export default function LoginScreen() {
  const router = useRouter();
  const { t, i18n } = useTranslation();
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const styles = useMemo(() => makeStyles(theme), [theme]);

  const ackText =
    t('disclaimer.short') + (i18n.language?.startsWith('tr') ? ACK_SUFFIX.tr : ACK_SUFFIX.en);

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [ack, setAck] = useState(false);
  const [focused, setFocused] = useState<'email' | 'password' | null>(null);
  const [deviceBusy, setDeviceBusy] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
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
  const modeVariant: TagVariant = mode === 'live' ? 'accent' : mode === 'paper' ? 'outline' : 'neutral';
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
  const canSignIn = emailOk && password.length >= MIN_PASSWORD && ack;

  const enter = () => router.replace('/(tabs)/portfolio');

  const signIn = () => {
    if (!canSignIn) return;
    enter();
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

  const signInLabel = t('auth.signIn');

  return (
    <SafeAreaView style={styles.container} edges={['bottom']}>
      {/* The banner runs under the status bar, so the clock and the carrier
          sit on accent-700 rather than on the page ground. */}
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
           * The one full accent fill in the system. Two deliberate notes.
           *
           * Fill is accent-700, not the base accent: the base puts the page
           * ground at 3.76:1 on top, which is the exact failure `liveStrip` was
           * added to colors.ts to fix and which contrast.test.ts pins. The
           * headline would survive it (34px/800 is large text) — the gate line
           * under it would not, and that line is the actual promise the screen
           * is making. accent-700 clears 6.41:1 for both.
           *
           * Type here is bespoke, not the mobile scale: 34px display and a 12px
           * support line, straight from the prototype. It is the only block in
           * the app allowed to set its own sizes, and it earns that by being
           * the only block that is a poster rather than an interface.
           */}
          <View style={[styles.banner, { paddingTop: insets.top + BANNER_TOP }]}>
            {/* Read as one sentence, not three fragments — the line breaks
                are typography, not structure. */}
            <View accessible accessibilityLabel={BANNER_LINES.join(' ')}>
              {BANNER_LINES.map((line) => (
                <Text key={line} style={styles.bannerLine}>
                  {line}
                </Text>
              ))}
            </View>
            <Text style={styles.gateLine}>{GATE_LINE}</Text>
          </View>

          <View style={styles.form}>
            <View style={styles.brand}>
              <View style={styles.brandSquare} />
              <Text style={styles.brandName}>TRADER</Text>
              <Tag label={modeLabel} variant={modeVariant} />
            </View>

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
                placeholderTextColor={theme.textSecondary}
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
                placeholderTextColor={theme.textSecondary}
                returnKeyType="go"
                onSubmitEditing={signIn}
                style={[styles.input, focused === 'password' && styles.inputFocused]}
                accessibilityLabel={t('auth.password')}
              />
            </View>

            {/* 18px square, 2px ink border, ink fill when ticked. The whole row
                is the target so the box itself never has to be hit. */}
            <Pressable
              onPress={() => setAck((v) => !v)}
              style={styles.ackRow}
              accessibilityRole="checkbox"
              accessibilityState={{ checked: ack }}
              accessibilityLabel={ackText}
            >
              <View style={[styles.ackBox, ack && styles.ackBoxChecked]} />
              <Text style={styles.ackText}>{ackText}</Text>
            </Pressable>

            <Pressable
              onPress={signIn}
              disabled={!canSignIn}
              style={[styles.primaryBtn, !canSignIn && styles.btnDisabled]}
              accessibilityRole="button"
              accessibilityLabel={signInLabel}
              accessibilityState={{ disabled: !canSignIn }}
              accessibilityHint={
                canSignIn
                  ? undefined
                  : !emailOk
                    ? 'Geçerli bir e-posta girin'
                    : password.length < MIN_PASSWORD
                      ? 'Şifrenizi girin'
                      : 'Yasal metni onaylayın'
              }
            >
              <Text style={styles.primaryBtnText}>{signInLabel}</Text>
            </Pressable>

            <Pressable
              onPress={() => void deviceSignIn()}
              disabled={deviceBusy}
              style={[styles.secondaryBtn, deviceBusy && styles.btnDisabled]}
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

            <Text style={styles.account}>{accountLine}</Text>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette) =>
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
      backgroundColor: t.accent700 ?? t.accent,
      paddingHorizontal: 16,
      paddingBottom: 24,
    },
    bannerLine: {
      color: t.background,
      fontSize: 34,
      lineHeight: 35,
      letterSpacing: -0.68,
      ...font(800),
    },
    gateLine: { color: t.background, fontSize: 12, lineHeight: 18, marginTop: 14, ...font(400) },

    // 20 top / 16 horizontal, the mobile screen padding.
    form: { paddingHorizontal: 16, paddingTop: 20, paddingBottom: 24, gap: 14 },

    brand: { flexDirection: 'row', alignItems: 'center', gap: 10 },
    // On the page ground the mark is the accent square the system specifies;
    // inside the banner it would have had to invert, and the mode chip has no
    // legible variant on an accent fill — so the brand row sits here, as it
    // does on the web console, rather than inside the panel.
    brandSquare: { width: 12, height: 12, backgroundColor: t.accent },
    brandName: { color: t.textPrimary, fontSize: 14, letterSpacing: 0.84, ...font(800) },

    h2: { color: t.textPrimary, ...TYPE.h2 },

    field: { gap: 5 },
    label: { color: t.textSecondary, ...TYPE.helper, ...font(600) },
    input: {
      backgroundColor: t.surfaceElevated,
      color: t.textPrimary,
      borderWidth: 1,
      borderColor: t.textPrimary,
      paddingHorizontal: 12,
      minHeight: 48,
      fontSize: 16,
      ...font(400),
    },
    // `.input:focus-visible` — the accent is the caret and focus colour, and it
    // is the only state change on the form.
    inputFocused: { borderColor: t.accent },

    ackRow: {
      flexDirection: 'row',
      alignItems: 'flex-start',
      gap: 12,
      paddingVertical: 4,
      minHeight: MIN_TOUCH_TARGET,
    },
    ackBox: { width: 18, height: 18, borderWidth: 2, borderColor: t.textPrimary, marginTop: 2 },
    ackBoxChecked: { backgroundColor: t.textPrimary },
    ackText: { flex: 1, color: t.textPrimary, ...TYPE.body, lineHeight: 19 },

    // Ink fill, not the accent: the accent is the banner and, everywhere else
    // in this palette, a loss or a warning. A red sign-in button would read as
    // a danger action.
    primaryBtn: {
      backgroundColor: t.textPrimary,
      minHeight: 48,
      alignItems: 'center',
      justifyContent: 'center',
    },
    primaryBtnText: { color: t.background, fontSize: 15, letterSpacing: 0.5, ...font(800) },
    secondaryBtn: {
      flexDirection: 'row',
      gap: 8,
      borderWidth: 1,
      borderColor: t.textPrimary,
      minHeight: 48,
      alignItems: 'center',
      justifyContent: 'center',
    },
    secondaryBtnText: { color: t.textPrimary, fontSize: 14, ...font(600) },
    btnDisabled: { opacity: 0.45 },

    errorLine: { color: t.accent700 ?? t.danger, ...TYPE.body, lineHeight: 19 },
    account: { color: t.textSecondary, ...TYPE.helper, lineHeight: 16 },
  });
