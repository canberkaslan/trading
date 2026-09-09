import { View, Text, StyleSheet, Pressable, Alert, ScrollView } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '@/api/endpoints';
import {
  useKillSwitch,
  useSetKillSwitch,
  useHealth,
  useDecisions,
  useEval,
  useActionability,
} from '@/api/hooks';
import { inertiaNote } from '@/utils/actionability';
import { getPermissionStatus, requestAndRegisterPush, type PushPermission } from '@/notifications';
import { useInboxStore } from '@/stores/notifications';
import { useTheme, useThemeName, useSetTheme } from '@/theme/useTheme';
import { setLanguage, type Language } from '@/i18n';
import type { ThemeName } from '@/theme/colors';
import { unreadCount } from '@/utils/inbox';
import { MIN_TOUCH_TARGET, killSwitchLabel } from '@/utils/a11y';
import type { KillSwitchState } from '@/api/types';

// These were module constants holding dark-palette colours, which is why they
// had to become functions: under Modernist an "ok" state is ink, not green.
const permissionCopy = (t: Palette): Record<PushPermission, { text: string; color: string }> => ({
  granted: { text: '● açık', color: t.textPrimary },
  denied: { text: '● kapalı (cihaz ayarları)', color: t.accent700 ?? t.down },
  undetermined: { text: '● izin verilmedi', color: t.warning },
  unsupported: { text: '● bu cihazda çalışmaz', color: t.textSecondary },
});

const killStates = (t: Palette): { state: KillSwitchState; label: string; desc: string; color: string }[] => [
  { state: 'RUN', label: 'RUN', desc: 'Normal işlem', color: t.textPrimary },
  { state: 'PAUSE_NEW', label: 'PAUSE', desc: 'Yeni giriş yok, mevcut yönetilir', color: t.warning },
  { state: 'FLATTEN_ALL', label: 'FLATTEN', desc: 'Tüm pozisyonları kapat', color: t.accent },
];

function EvalStat({ label, value, gate }: { label: string; value: string; gate: string }) {
  const styles = makeStyles(useTheme());
  return (
    <View style={styles.evalStat}>
      <Text style={styles.evalStatLabel}>{label}</Text>
      <Text style={styles.evalStatValue}>{value}</Text>
      <Text style={styles.evalStatGate}>{gate}</Text>
    </View>
  );
}

function GateRow({ name, passed, detail }: { name: string; passed: boolean | null; detail: string }) {
  const t = useTheme();
  const styles = makeStyles(t);
  const icon = passed === null ? '○' : passed ? '✓' : '✗';
  const color = passed === null ? t.textSecondary : passed ? t.textPrimary : t.accent;
  return (
    <View style={styles.gateRow}>
      <Text style={[styles.gateIcon, { color }]}>{icon}</Text>
      <Text style={styles.gateName}>{name}</Text>
      <Text style={styles.gateDetail}>{detail}</Text>
    </View>
  );
}

export default function SettingsScreen() {
  const router = useRouter();
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);
  const themeName = useThemeName();
  const setTheme = useSetTheme();
  const { i18n } = useTranslation();
  const lang: Language = i18n.language?.startsWith('tr') ? 'tr' : 'en';
  const KILL_STATES = useMemo(() => killStates(theme), [theme]);
  const PERMISSION_COPY = useMemo(() => permissionCopy(theme), [theme]);
  const [pushBusy, setPushBusy] = useState(false);
  const [permission, setPermission] = useState<PushPermission | null>(null);
  const inbox = useInboxStore((s) => s.items);
  const { data: ks } = useKillSwitch();
  const setKs = useSetKillSwitch();
  const { data: health, isError: healthError } = useHealth();
  const { data: decisions } = useDecisions({ limit: 1 });
  const { data: evalData, isLoading: evalLoading } = useEval('1M');
  const { data: flow } = useActionability();

  const VERDICT_COLOR: Record<string, string> = {
    GO: theme.textPrimary,
    'NO-GO': theme.accent,
    'TOO EARLY': theme.warning,
  };

  const applyKill = (state: KillSwitchState) => {
    if (state === ks?.state) return;
    const go = () => setKs.mutate(state);
    if (state === 'FLATTEN_ALL') {
      Alert.alert(
        'Tüm pozisyonları kapat?',
        'FLATTEN_ALL tüm açık pozisyonları piyasa fiyatından kapatır. Emin misin?',
        [
          { text: 'Vazgeç', style: 'cancel' },
          { text: 'FLATTEN', style: 'destructive', onPress: go },
        ],
      );
    } else {
      go();
    }
  };

  const lastRun = decisions?.[0]?.timestamp_utc
    ? new Date(decisions[0].timestamp_utc).toLocaleString()
    : '—';

  const refreshPermission = useCallback(async () => {
    setPermission(await getPermissionStatus());
  }, []);

  useEffect(() => {
    void refreshPermission();
  }, [refreshPermission]);

  const handleRegisterPush = async () => {
    setPushBusy(true);
    try {
      const token = await requestAndRegisterPush();
      await refreshPermission();
      if (token) {
        Alert.alert('Bildirimler açık', 'Bu cihaz emir ve karar bildirimlerine kayıtlı.');
      } else {
        Alert.alert(
          'Bildirim açılamadı',
          'İzin verilmedi ya da bu cihaz push desteklemiyor (simülatörlerde çalışmaz). ' +
            'Daha önce reddettiysen cihaz ayarlarından açman gerekir.',
        );
      }
    } catch (e) {
      Alert.alert('Hata', String(e));
    } finally {
      setPushBusy(false);
    }
  };

  const handleTestPush = async () => {
    try {
      const result = await api.testNotification();
      Alert.alert('Gönderildi', `${result.sent} cihaza gönderildi — bildirimlerini kontrol et.`);
    } catch (e) {
      Alert.alert('Hata', String(e));
    }
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView contentContainerStyle={{ padding: 16 }}>
        <Text style={styles.heading}>Settings</Text>

        <Text style={styles.subheading}>Eval scorecard</Text>
        <View style={styles.evalCard}>
          {evalLoading ? (
            <Text style={styles.muted}>hesaplanıyor…</Text>
          ) : !evalData ? (
            <Text style={styles.muted}>Yeterli geçmiş yok — eval yeni başladı.</Text>
          ) : (
            <>
              <View style={styles.healthRow}>
                <Text style={styles.label}>Karar</Text>
                <View style={styles.verdictWrap}>
                  <Text style={[styles.verdict, { color: VERDICT_COLOR[evalData.verdict] ?? theme.textSecondary }]}>
                    {evalData.verdict}
                  </Text>
                  {evalData.verdict === 'TOO EARLY' && evalData.provisional_verdict ? (
                    <Text style={[styles.trend, { color: VERDICT_COLOR[evalData.provisional_verdict] ?? theme.textSecondary }]}>
                      eğilim: {evalData.provisional_verdict}
                    </Text>
                  ) : null}
                </View>
              </View>
              <View style={styles.evalGrid}>
                <EvalStat label="Sharpe" value={evalData.sharpe.toFixed(2)} gate={`>${evalData.gate_sharpe}`} />
                <EvalStat label="Sortino" value={evalData.sortino.toFixed(2)} gate="downside" />
                <EvalStat label="Max DD" value={`${evalData.max_dd_pct.toFixed(1)}%`} gate={`<${evalData.gate_max_dd_pct}%`} />
                <EvalStat label="Calmar" value={evalData.calmar.toFixed(2)} gate="getiri/DD" />
                <EvalStat label="Getiri" value={`${evalData.total_return_pct >= 0 ? '+' : ''}${evalData.total_return_pct.toFixed(1)}%`} gate={`${evalData.days}g`} />
                {evalData.spy_return_pct != null ? (
                  <EvalStat
                    label="α vs SPY"
                    value={`${evalData.total_return_pct - evalData.spy_return_pct >= 0 ? '+' : ''}${(evalData.total_return_pct - evalData.spy_return_pct).toFixed(1)}%`}
                    gate={`SPY ${evalData.spy_return_pct >= 0 ? '+' : ''}${evalData.spy_return_pct.toFixed(1)}%`}
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
                Every gate above is computed from the equity curve, which a
                book that stopped trading still has. When order flow says the
                book is frozen, that caveat belongs next to the verdict — not
                one screen away.
              */}
              {inertiaNote(flow) ? (
                <Text style={styles.flowCaveat}>⚠︎ {inertiaNote(flow)}</Text>
              ) : null}
            </>
          )}
        </View>

        <Text style={styles.subheading}>Kill switch</Text>
        <View style={styles.killRow}>
          {KILL_STATES.map((k) => {
            const active = ks?.state === k.state;
            return (
              <Pressable
                key={k.state}
                style={[styles.killChip, active && { backgroundColor: k.color }]}
                onPress={() => applyKill(k.state)}
                disabled={setKs.isPending}
                accessibilityRole="button"
                accessibilityLabel={killSwitchLabel(k.state)}
                accessibilityHint={k.desc}
                accessibilityState={{ selected: active, disabled: setKs.isPending }}
              >
                <Text style={[styles.killLabel, active && { color: theme.background }]}>{k.label}</Text>
              </Pressable>
            );
          })}
        </View>
        <Text style={styles.killDesc}>
          {KILL_STATES.find((k) => k.state === ks?.state)?.desc ?? 'durum yükleniyor…'}
        </Text>

        {/* Language and palette were both settable only by the device: the app
            followed the phone's locale and opened in whichever palette the
            build defaulted to. Both are reader choices, and both persist. */}
        <Text style={styles.subheading}>Görünüm</Text>
        <View style={styles.healthCard}>
          <Text style={styles.label}>Dil / Language</Text>
          <View style={styles.segment}>
            {(['tr', 'en'] as const).map((code) => (
              <Pressable
                key={code}
                style={[styles.segBtn, lang === code && styles.segBtnActive]}
                onPress={() => void setLanguage(code)}
                accessibilityRole="button"
                accessibilityLabel={code === 'tr' ? 'Türkçe' : 'English'}
                accessibilityState={{ selected: lang === code }}
              >
                <Text style={[styles.segLabel, lang === code && styles.segLabelActive]}>
                  {code === 'tr' ? 'Türkçe' : 'English'}
                </Text>
              </Pressable>
            ))}
          </View>

          <Text style={styles.label}>Tema</Text>
          <View style={styles.segment}>
            {(['modernist', 'dark'] as const).map((name: ThemeName) => (
              <Pressable
                key={name}
                style={[styles.segBtn, themeName === name && styles.segBtnActive]}
                onPress={() => setTheme(name)}
                accessibilityRole="button"
                accessibilityLabel={name === 'modernist' ? 'Açık tema' : 'Koyu tema'}
                accessibilityState={{ selected: themeName === name }}
              >
                <Text style={[styles.segLabel, themeName === name && styles.segLabelActive]}>
                  {name === 'modernist' ? 'Açık' : 'Koyu'}
                </Text>
              </Pressable>
            ))}
          </View>
        </View>

        <Text style={styles.subheading}>Sistem</Text>
        <View style={styles.healthCard}>
          <View style={styles.healthRow}>
            <Text style={styles.label}>Backend</Text>
            <Text style={{ color: healthError ? theme.accent : theme.textPrimary, fontWeight: '700' }}>
              {healthError ? '● offline' : health?.status === 'ok' ? '● online' : '…'}
            </Text>
          </View>
          <View style={styles.healthRow}>
            <Text style={styles.label}>Son ajan kararı</Text>
            <Text style={styles.muted}>{lastRun}</Text>
          </View>
        </View>

        <Text style={styles.subheading}>Emir gönderimi</Text>
        <View style={styles.healthCard}>
          <Text style={styles.label}>Günlük koşu otomatik gönderir (paper)</Text>
          <Text style={styles.muted}>
            Paper hesapta daily run, risk guard'lardan geçen emirleri bracket
            (stop + take-profit) ile otomatik gönderir. Live'a geçişte emirler
            mobil onaya düşer (hold → biometric approve, ADR-005). Bu davranış
            uygulamadan değiştirilemez — tek kontrol yukarıdaki kill switch.
          </Text>
        </View>

        <Text style={styles.subheading}>Bildirimler</Text>
        <View style={styles.healthCard}>
          <View style={styles.healthRow}>
            <Text style={styles.label}>Push izni</Text>
            <Text style={{ color: PERMISSION_COPY[permission ?? 'undetermined'].color, fontWeight: '600' }}>
              {permission ? PERMISSION_COPY[permission].text : '…'}
            </Text>
          </View>
          <Text style={styles.muted}>
            Onay bekleyen emirler ve gerçekleşen işlemler için bildirim gönderilir.
            İzin sadece buradan ya da ilk emir onaya düştüğünde istenir.
          </Text>
        </View>
        <Pressable
          style={styles.button}
          disabled={pushBusy}
          onPress={handleRegisterPush}
          accessibilityRole="button"
        >
          <Text style={styles.buttonText}>
            {pushBusy ? 'Kaydediliyor…' : permission === 'granted' ? 'Kaydı yenile' : 'Bildirimlere izin ver'}
          </Text>
        </Pressable>
        <Pressable
          style={[styles.button, styles.buttonSecondary]}
          onPress={() => router.push('/notifications' as never)}
          accessibilityRole="button"
        >
          <Text style={styles.buttonSecondaryText}>
            Bildirim geçmişi ({inbox.length}
            {unreadCount(inbox) > 0 ? ` · ${unreadCount(inbox)} okunmamış` : ''})
          </Text>
        </Pressable>
        <Pressable
          style={[styles.button, styles.buttonSecondary]}
          onPress={handleTestPush}
          accessibilityRole="button"
        >
          <Text style={styles.buttonSecondaryText}>Test bildirimi gönder</Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.background },
    heading: { color: t.textPrimary, fontSize: 24, fontWeight: '800', marginBottom: 16 },
    subheading: {
      color: t.textPrimary,
      fontSize: 11,
      fontWeight: '800',
      textTransform: 'uppercase',
      letterSpacing: 1,
      marginTop: 24,
      marginBottom: 8,
      borderBottomWidth: 2,
      borderBottomColor: t.textPrimary,
      paddingBottom: 6,
    },
    label: { color: t.textPrimary, fontSize: 15, fontWeight: '600' },
    muted: { color: t.textSecondary, fontSize: 13, lineHeight: 18 },
    // Collapsed-border strip, the same control shape as Orders and Charts.
    segment: { flexDirection: 'row' },
    segBtn: {
      flex: 1,
      paddingVertical: 10,
      borderWidth: 1,
      borderColor: t.textPrimary,
      marginRight: -1,
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: MIN_TOUCH_TARGET,
    },
    segBtnActive: { backgroundColor: t.textPrimary },
    segLabel: { color: t.textPrimary, fontSize: 14, fontWeight: '700' },
    segLabelActive: { color: t.background },
    killRow: { flexDirection: 'row' },
    killChip: {
      flex: 1,
      paddingVertical: 14,
      borderWidth: 1,
      borderColor: t.textPrimary,
      marginRight: -1,
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: MIN_TOUCH_TARGET,
    },
    killLabel: { color: t.textPrimary, fontSize: 14, fontWeight: '800', letterSpacing: 0.5 },
    killDesc: { color: t.textSecondary, fontSize: 12, marginTop: 8 },
    healthCard: { paddingVertical: 4, gap: 12 },
    healthRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
    evalCard: { paddingVertical: 4, gap: 12 },
    verdict: { fontSize: 16, fontWeight: '800', letterSpacing: 0.5 },
    verdictWrap: { alignItems: 'flex-end' },
    trend: { fontSize: 11, fontWeight: '700', marginTop: 1 },
    evalGrid: { flexDirection: 'row', flexWrap: 'wrap', rowGap: 12, justifyContent: 'space-between' },
    evalStat: { alignItems: 'flex-start' },
    evalStatLabel: { color: t.textSecondary, fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.8 },
    evalStatValue: { color: t.textPrimary, fontSize: 18, fontWeight: '800', marginTop: 2 },
    evalStatGate: { color: t.textSecondary, fontSize: 10, marginTop: 1 },
    evalReason: { color: t.warning, fontSize: 11 },
    flowCaveat: { color: t.warning, fontSize: 11, marginTop: 8, lineHeight: 16 },
    countdown: { color: t.textSecondary, fontSize: 12, fontWeight: '600' },
    gateList: { gap: 6, borderTopWidth: 1, borderTopColor: t.divider, paddingTop: 10 },
    gateRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    gateIcon: { fontSize: 13, fontWeight: '800', width: 16, textAlign: 'center' },
    gateName: { color: t.textPrimary, fontSize: 13, flex: 1 },
    gateDetail: { color: t.textSecondary, fontSize: 12 },
    button: {
      backgroundColor: t.textPrimary,
      padding: 14,
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: MIN_TOUCH_TARGET,
      marginTop: 8,
    },
    buttonText: { color: t.background, fontSize: 14, fontWeight: '800' },
    buttonSecondary: { backgroundColor: 'transparent', borderWidth: 1, borderColor: t.textPrimary },
    buttonSecondaryText: { color: t.textPrimary, fontSize: 13, fontWeight: '600' },
  });
