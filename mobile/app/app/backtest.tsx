/**
 * Backtest lab — the prototype's BACKTEST block, ported to Aurora.
 *
 * The prototype runs a fake: `btRun()` ticks a progress bar to 100% and then
 * synthesises a row out of `this.rng(hash(strategy+window+universe+Date.now()))`
 * — Sharpe, Sortino, max drawdown, return, benchmark and trade count, all made
 * up. That is fine for a design file and unacceptable in the app: a fabricated
 * Sharpe on a money screen is indistinguishable from a real one.
 *
 * The backend does not serve backtests. `agent/api/main.py` mounts thirteen
 * routers (portfolio, orders, agents, analyze, prices, learn, eval,
 * notifications, trades, diagnostics, risk, tickers, market) and none of them
 * is a backtest router. The engine itself exists — `tradingagents_us.backtest`
 * with `run_signal_backtest`/`BacktestConfig`, plus `backtest.strategies` —
 * it is simply not reachable over HTTP.
 *
 * So this screen ports everything the prototype's *configuration* card does,
 * because those choices are real and the picker is the part a backend will
 * need, and it states plainly in the results panel that the server does not
 * serve this yet. It invents no numbers and shows no progress bar for work
 * that is not happening. The run button is present but disabled, because the
 * affordance is the honest thing to show: the screen is waiting on the API,
 * not on the operator.
 *
 * The one piece of the prototype kept verbatim is the leakage note, which is
 * not decoration — it is the reason an LLM backtest is "indicative, not the
 * scorecard", and it is the sentence that has to survive the port even when
 * the numbers do not.
 */

import { View, Text, StyleSheet, Pressable, ScrollView } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useMemo, useState } from 'react';

import { Card } from '@/components/Card';
import { SectionHeader } from '@/components/SectionHeader';
import { Seg, type SegOption } from '@/components/Seg';
import { Tag } from '@/components/Tag';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { TYPE, font } from '@/theme/type';
import { hitSlopFor, MIN_TOUCH_TARGET } from '@/utils/a11y';

/** The prototype's three strategies, keys and all. */
type StrategyKey = 'sma' | 'llm' | 'council';

const STRATEGIES: readonly SegOption<StrategyKey>[] = [
  { value: 'sma', label: 'SMA(10/30)', accessibilityLabel: 'SMA 10/30 kural stratejisi' },
  { value: 'llm', label: 'Önbellekli LLM', accessibilityLabel: 'Önbellekli LLM stratejisi' },
  { value: 'council', label: 'Council v2', accessibilityLabel: 'Council v2 stratejisi' },
];

const WINDOWS = ['2024', '2025', '2026 Ç2'] as const;
const UNIVERSES = ['AAPL', 'SPY+10', '11 isim'] as const;

/**
 * Which strategies carry information leakage. `sma` is a deterministic rule
 * evaluated bar by bar, so it cannot have seen the future; anything that
 * replays a language model's decisions over a window the model was trained
 * through can have. This is the distinction the whole screen exists to draw.
 */
const leaks = (s: StrategyKey) => s !== 'sma';

const COST_NOTE: Record<'rule' | 'llm', string> = {
  rule: 'Deterministik kural — sızıntısız, ücretsiz.',
  llm:
    'LLM backtest yalnız gösterge niteliğindedir: model test penceresini görmüş olabilir ' +
    '(bilgi sızıntısı). Önbellekli kararlar kullanılır, ek LLM maliyeti yok.',
};

/** The prototype's pill chips are 32pt; hitSlop carries them to 44. */
const CHIP_HEIGHT = 32;
/** The prototype's run button. */
const RUN_HEIGHT = 50;

/**
 * The columns a result row will carry once the API serves one. Listed by name
 * with no value beside them: the panel can say what it will measure without
 * pretending it has measured anything.
 */
const RESULT_FIELDS: readonly { label: string; hint: string }[] = [
  { label: 'Sharpe', hint: 'Birim oynaklık başına getiri' },
  { label: 'Sortino', hint: 'Yalnız aşağı yönlü oynaklığa göre' },
  { label: 'Maks. düşüş', hint: 'Tepe noktadan en derin kayıp' },
  { label: 'α', hint: 'Pencere getirisi eksi ölçüt' },
];

export default function BacktestScreen() {
  const router = useRouter();
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);

  // Local, because there is nothing to send them to yet. When a backtest
  // endpoint lands these become the request body and nothing else on the
  // screen has to move.
  const [strategy, setStrategy] = useState<StrategyKey>('sma');
  const [windowKey, setWindowKey] = useState<string>(WINDOWS[0]);
  const [universe, setUniverse] = useState<string>(UNIVERSES[0]);

  const leaky = leaks(strategy);

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <ScrollView contentContainerStyle={styles.content}>
        <Pressable
          style={styles.backBtn}
          onPress={() => router.back()}
          accessibilityRole="button"
          accessibilityLabel="Geri"
        >
          <Text style={styles.backText}>← Geri</Text>
        </Pressable>

        <View style={styles.head}>
          <Text style={styles.title} accessibilityRole="header">
            Backtest lab
          </Text>
          <Text style={styles.subtitle}>Kural tabanlı ve LLM backtestleri — karne değil, gösterge</Text>
        </View>

        <Card>
          <View style={styles.kickerRow}>
            <Text style={styles.kicker}>Strateji</Text>
            {leaky ? <Tag label="sızıntı riski" variant="warn" size="sm" /> : null}
          </View>
          <Seg options={STRATEGIES} value={strategy} onChange={setStrategy} block style={styles.seg} />

          <View style={styles.pickerGrid}>
            <View style={styles.picker}>
              <Text style={styles.kicker}>Pencere</Text>
              <View style={styles.chipRow}>
                {WINDOWS.map((w) => (
                  <Chip
                    key={w}
                    label={w}
                    selected={w === windowKey}
                    onPress={() => setWindowKey(w)}
                    accessibilityLabel={`Pencere ${w}`}
                  />
                ))}
              </View>
            </View>

            <View style={styles.picker}>
              <Text style={styles.kicker}>Evren</Text>
              <View style={styles.chipRow}>
                {UNIVERSES.map((u) => (
                  <Chip
                    key={u}
                    label={u}
                    selected={u === universe}
                    onPress={() => setUniverse(u)}
                    accessibilityLabel={`Evren ${u}`}
                  />
                ))}
              </View>
            </View>
          </View>

          <Text style={styles.note}>{COST_NOTE[leaky ? 'llm' : 'rule']}</Text>

          {/* The prototype's full-width ink button, kept — and disabled, because
              nothing on the server answers it. A button that pretended to run
              would have to invent a result to show afterwards. */}
          <Pressable
            style={styles.run}
            disabled
            accessibilityRole="button"
            accessibilityLabel="Backtest’i çalıştır"
            accessibilityHint="Sunucu backtest sunmadığı için şu an kullanılamıyor"
            accessibilityState={{ disabled: true }}
          >
            <Text style={styles.runText}>Backtest’i çalıştır</Text>
          </Pressable>
          <Text style={styles.runNote}>Sunucu backtest uç noktası sunana kadar kapalı.</Text>
        </Card>

        <SectionHeader title="Sonuçlar" />

        {/* Empty, and explicit about why. A dashed slot rather than a filled
            card: there is no result here, and a card with nothing in it reads
            as a result that failed to load. */}
        <Card tone="dashed" style={styles.slot}>
          <Text style={styles.slotTitle}>Backtest sonucu yok</Text>
          <Text style={styles.slotText}>
            Sunucu henüz backtest sunmuyor. Motor depoda hazır ama API’ye bağlanmadı — bağlandığı anda koşular
            burada listelenir.
          </Text>

          <View style={styles.fields}>
            {RESULT_FIELDS.map((f) => (
              <View key={f.label} style={styles.field}>
                <Text style={styles.fieldLabel}>{f.label}</Text>
                <Text style={styles.fieldHint}>{f.hint}</Text>
              </View>
            ))}
          </View>
        </Card>

        <Text style={styles.footNote}>
          Backtest bir karne değil. Geçmiş pencerede iyi görünen bir strateji, canlıda aynı sonucu vermez; LLM
          koşularında model test penceresini görmüş olabilir.
        </Text>
      </ScrollView>
    </SafeAreaView>
  );
}

/**
 * The prototype's outlined pill. Selected fills with ink and inverts, which is
 * the same mark the tab bar uses for the active tab — one selection language
 * across the app rather than two.
 */
function Chip({
  label,
  selected,
  onPress,
  accessibilityLabel,
}: {
  label: string;
  selected: boolean;
  onPress: () => void;
  accessibilityLabel?: string;
}) {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeChipStyles(t, sh), [t, sh]);

  return (
    <Pressable
      onPress={onPress}
      hitSlop={hitSlopFor(CHIP_HEIGHT)}
      style={({ pressed }) => [styles.chip, selected && styles.chipOn, pressed && !selected && styles.chipPressed]}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? label}
      accessibilityState={{ selected }}
    >
      <Text style={[styles.chipText, selected && styles.chipTextOn]} numberOfLines={1}>
        {label}
      </Text>
    </Pressable>
  );
}

type Palette = ReturnType<typeof useTheme>;

const makeChipStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    chip: {
      height: CHIP_HEIGHT,
      justifyContent: 'center',
      paddingHorizontal: sh.space[2],
      borderRadius: sh.radiusPill,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
    },
    chipOn: { backgroundColor: t.textPrimary, borderColor: t.textPrimary },
    // No hover on a phone; the prototype's border brighten lands on press.
    chipPressed: { borderColor: t.brand ?? t.accent },
    chipText: { fontSize: 12, ...font(800), color: t.textPrimary },
    chipTextOn: { color: t.inkInv ?? t.background },
  });

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    screen: { flex: 1, backgroundColor: t.background },
    content: {
      paddingHorizontal: sh.space[3],
      paddingTop: sh.space[1],
      paddingBottom: sh.space[5],
    },

    backBtn: { minHeight: MIN_TOUCH_TARGET, justifyContent: 'center', alignSelf: 'flex-start' },
    backText: { ...TYPE.bodyStrong, fontSize: 15, ...font(800), color: t.textPrimary },

    head: { paddingTop: sh.space[1], paddingBottom: sh.space[2] },
    title: { ...TYPE.h2, fontSize: 26, letterSpacing: -0.5, color: t.textPrimary },
    subtitle: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, marginTop: sh.space[0] },

    kickerRow: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1] },
    kicker: { ...TYPE.kicker, color: t.ink3 ?? t.textMuted },
    seg: { marginTop: sh.space[1] },

    pickerGrid: { flexDirection: 'row', gap: sh.space[2], marginTop: sh.space[3] },
    picker: { flex: 1, minWidth: 0 },
    chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: sh.space[1] },

    note: { ...TYPE.body, fontSize: 12, lineHeight: 18, color: t.ink2 ?? t.textSecondary, marginTop: sh.space[3] },

    run: {
      height: RUN_HEIGHT,
      marginTop: sh.space[3],
      borderRadius: sh.radius,
      alignItems: 'center',
      justifyContent: 'center',
      // Disabled, and it says so by sitting one step below the page's ink:
      // the outlined well rather than the filled slab the live button will be.
      backgroundColor: t.surface2 ?? t.surfaceElevated,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
    },
    runText: { fontSize: 14, ...font(800), color: t.ink3 ?? t.textMuted },
    runNote: { ...TYPE.helper, color: t.ink3 ?? t.textMuted, marginTop: sh.space[1], textAlign: 'center' },

    slot: { paddingVertical: sh.space[4], marginTop: sh.space[1] },
    slotTitle: { ...TYPE.section, color: t.textPrimary },
    slotText: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, marginTop: sh.space[1], lineHeight: 19 },

    fields: { marginTop: sh.space[3], gap: sh.space[1] },
    field: { flexDirection: 'row', alignItems: 'baseline', gap: sh.space[2] },
    fieldLabel: { ...TYPE.bodyStrong, ...font(800), color: t.textPrimary, width: 92 },
    fieldHint: { ...TYPE.helper, color: t.ink3 ?? t.textMuted, flex: 1 },

    footNote: {
      ...TYPE.helper,
      color: t.ink3 ?? t.textMuted,
      lineHeight: 17,
      marginTop: sh.space[3],
    },
  });
