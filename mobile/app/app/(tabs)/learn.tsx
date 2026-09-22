import { useState, useCallback, useEffect, useMemo } from 'react';
import { View, Text, StyleSheet, ScrollView, Pressable, LayoutAnimation, Platform, UIManager } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import { create } from 'zustand';
import Svg, { Path } from 'react-native-svg';
import * as SecureStore from '@/utils/storage';

import { LESSONS, type LessonText } from '@/content/lessons';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { Card } from '@/components/Card';
import { MIN_TOUCH_TARGET } from '@/utils/a11y';
import { font, TABULAR, TYPE } from '@/theme/type';

if (Platform.OS === 'android' && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}

/**
 * The floating tab bar's height plus air — the bar is a pill with a shadow,
 * not a flush strip, so the last lesson has to clear it. Same constant as the
 * Bugün screen; a route module's exports are the router's namespace, not a
 * place to hang shared values, so it is restated rather than imported.
 */
const TAB_BAR_CLEARANCE = 72;

/* -------------------------------------------------------------------------- */
/* Persisted quiz answers                                                     */
/* -------------------------------------------------------------------------- */

/**
 * A solved lesson has to stay solved. The progress rule reads "n/8 of the
 * things this panel assumes you know" — a counter that resets to 0/8 on every
 * cold start is not a progress bar, it is a decoration.
 *
 * Backed by expo-secure-store, the same write-behind pattern as the
 * notification inbox: it is the only storage module linked into the native
 * build, so this ships over-the-air. A storage failure is never fatal — the
 * quiz degrades to memory-only for the session rather than blocking the tab.
 */

const STORAGE_KEY = 'lesson_answers_v1';

type Lang = 'tr' | 'en';

/** `${lang}:${lessonId}` -> index the reader picked in that language's `quizOptions`. */
type LessonAnswers = Record<string, number>;

/**
 * The stored value is an index into `quizOptions`, and the two languages do not
 * order their options the same way — `go_live_gates` answers 2 in Turkish and 1
 * in English. An index is only meaningful against the list it was picked from,
 * so it is stored under that language. Keyed by lesson id alone, flipping the
 * language in Settings would paint the wrong-answer border on a sentence the
 * reader never chose and quietly drop a solved lesson out of the n/8 count.
 */
function answerKey(lang: Lang, lessonId: string): string {
  return `${lang}:${lessonId}`;
}

/**
 * Storage is untrusted input: it survives app upgrades, so it can hold ids that
 * no longer exist and indexes into an option list that has since been rewritten.
 * Anything that is not a plain non-negative integer is dropped rather than
 * rendered as a phantom selection.
 */
function parseAnswers(raw: string | null): LessonAnswers {
  if (!raw) return {};
  try {
    const data: unknown = JSON.parse(raw);
    if (!data || typeof data !== 'object' || Array.isArray(data)) return {};
    const out: LessonAnswers = {};
    for (const [id, value] of Object.entries(data as Record<string, unknown>)) {
      if (typeof value === 'number' && Number.isInteger(value) && value >= 0) out[id] = value;
    }
    return out;
  } catch {
    return {};
  }
}

async function persist(answers: LessonAnswers): Promise<void> {
  try {
    await SecureStore.setItemAsync(STORAGE_KEY, JSON.stringify(answers));
  } catch {
    // Memory-only for this session — a quiz answer is not worth an error dialog.
  }
}

interface LessonAnswerState {
  answers: LessonAnswers;
  /** False until storage has been read once. */
  hydrated: boolean;
  hydrate: () => Promise<void>;
  /** `key` is an {@link answerKey}, not a bare lesson id. */
  pick: (key: string, option: number) => void;
}

const useLessonAnswers = create<LessonAnswerState>((set, get) => ({
  answers: {},
  hydrated: false,

  hydrate: async () => {
    if (get().hydrated) return;
    let stored: string | null = null;
    try {
      stored = await SecureStore.getItemAsync(STORAGE_KEY);
    } catch {
      stored = null;
    }
    // Anything answered while storage was being read wins the merge.
    const pending = get().answers;
    const merged = { ...parseAnswers(stored), ...pending };
    set({ answers: merged, hydrated: true });
    // That early answer has already written itself to storage as the WHOLE set,
    // on top of a set it had not read yet — so every previously solved lesson
    // is sitting only in `stored` at this point. Write the merge back or the
    // next cold start comes up with one answer and no history.
    if (Object.keys(pending).length > 0) void persist(merged);
  },

  pick: (key, option) => {
    // The first answer stands; the options go read-only once one is revealed.
    if (get().answers[key] !== undefined) return;
    const answers = { ...get().answers, [key]: option };
    set({ answers });
    void persist(answers);
  },
}));

/* -------------------------------------------------------------------------- */
/* Copy that has no i18n key                                                  */
/* -------------------------------------------------------------------------- */

/**
 * The prototype labels the progress row ("İlerleme" / "Progress"). There is no
 * `learn.progress` key in `src/i18n`, and the i18n catalogues are not this
 * task's files to edit — so the one missing string lives here, keyed the same
 * way the quiz answers are. Flagged in the report: it belongs in tr.json/en.json.
 */
const PROGRESS_LABEL: Record<Lang, string> = { tr: 'İlerleme', en: 'Progress' };

/** Spoken-only; the visible marks are the ✓ / ✗ glyphs. */
const SOLVED_HINT: Record<Lang, string> = { tr: 'çözüldü', en: 'solved' };

/* -------------------------------------------------------------------------- */
/* Screen                                                                     */
/* -------------------------------------------------------------------------- */

/**
 * Öğren — the prototype's LEARN screen.
 *
 * Four things, in the prototype's order: the title block, a progress card
 * (label · filled rule · n / 8), eight accordion lesson cards, and inside an
 * open one the two paragraphs, the brand-tinted takeaway box, the question and
 * its three options.
 *
 * The lessons explain this system's own metrics — Sharpe, drawdown, the
 * go-live gates — so the numbers on the other tabs mean something. Content is
 * bundled rather than fetched: it is small, it never changes between releases,
 * and a reader who has lost connectivity is exactly the reader with time to
 * read it. That is why this screen has no loading or error state to draw: the
 * only thing it waits on is the answer store, and the copy renders without it.
 */
export default function LearnScreen() {
  const { t: tr, i18n } = useTranslation();
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const lang: Lang = i18n.language?.startsWith('tr') ? 'tr' : 'en';
  const [openId, setOpenId] = useState<string | null>(null);

  const answered = useLessonAnswers((s) => s.answers);
  const pick = useLessonAnswers((s) => s.pick);

  // Read once on mount. The lesson text needs no storage, so the tab renders
  // immediately and the solved marks arrive a frame or two later rather than
  // holding readable copy behind a spinner.
  useEffect(() => {
    void useLessonAnswers.getState().hydrate();
  }, []);

  const toggle = useCallback((id: string) => {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    setOpenId((cur) => (cur === id ? null : id));
  }, []);

  const done = LESSONS.filter((l) => answered[answerKey(lang, l.id)] === l[lang].quizAnswer).length;
  const pct = LESSONS.length > 0 ? (done / LESSONS.length) * 100 : 0;

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.titleBlock}>
          <Text style={styles.heading} accessibilityRole="header">
            {tr('learn.title')}
          </Text>
          <Text style={styles.subheading}>{tr('learn.subtitle')}</Text>
        </View>

        <Card
          style={styles.progressCard}
          accessibilityRole="summary"
          accessibilityLabel={`${PROGRESS_LABEL[lang]}: ${done} / ${LESSONS.length}`}
        >
          <Text style={styles.progressLabel}>{PROGRESS_LABEL[lang]}</Text>
          {/*
           * The bar itself carries the progressbar role so a screen reader gets
           * the value, while the card above gives the spoken sentence. Aurora
           * fills the rule in `brand`; the track is the raised surface, which
           * is the only ground in this palette a 6px sliver stays visible on.
           */}
          <View
            style={styles.progressTrack}
            accessibilityRole="progressbar"
            accessibilityValue={{ min: 0, max: LESSONS.length, now: done }}
          >
            <View style={[styles.progressFill, { width: `${pct}%` }]} />
          </View>
          <Text style={styles.progressText}>{`${done} / ${LESSONS.length}`}</Text>
        </Card>

        <View style={styles.list}>
          {LESSONS.map((lesson, i) => (
            <LessonCard
              key={lesson.id}
              index={i}
              text={lesson[lang]}
              open={openId === lesson.id}
              picked={answered[answerKey(lang, lesson.id)]}
              onToggle={() => toggle(lesson.id)}
              onPick={(option) => pick(answerKey(lang, lesson.id), option)}
              correctLabel={tr('learn.correct')}
              wrongLabel={tr('learn.wrong')}
              takeawayLabel={tr('learn.takeaway')}
              solvedHint={SOLVED_HINT[lang]}
            />
          ))}
        </View>

        <Text style={styles.disclaimer}>{tr('disclaimer.short')}</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

/* -------------------------------------------------------------------------- */
/* One lesson                                                                 */
/* -------------------------------------------------------------------------- */

function LessonCard({
  index,
  text,
  open,
  picked,
  onToggle,
  onPick,
  correctLabel,
  wrongLabel,
  takeawayLabel,
  solvedHint,
}: {
  index: number;
  text: LessonText;
  open: boolean;
  picked: number | undefined;
  onToggle: () => void;
  onPick: (i: number) => void;
  correctLabel: string;
  wrongLabel: string;
  takeawayLabel: string;
  solvedHint: string;
}) {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);

  const reveal = picked !== undefined;
  const solved = picked === text.quizAnswer;
  // The prototype's `markColor`: up for a correct answer, down for a wrong one.
  const markColor = solved ? t.up : t.down;
  const n = String(index + 1).padStart(2, '0');

  return (
    /*
     * Only the HEAD toggles. The whole card used to be the Pressable, with the
     * quiz options nested inside it — which works on native (the responder
     * system gives the touch to the innermost handler) and silently misbehaves
     * on web, where the DOM event bubbles and answering a question collapses
     * the lesson you were reading. A header-only target is also simply right:
     * selecting body text should not close the section.
     */
    <Card padded={false} clip>
      <Pressable
        style={({ pressed }) => [styles.head, pressed && styles.headPressed]}
        onPress={onToggle}
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        accessibilityLabel={reveal && solved ? `${n}. ${text.title}, ${solvedHint}` : `${n}. ${text.title}`}
      >
        {/*
         * The prototype's numbered disc: a ring that fills with `brand` once
         * the lesson is solved. `radiusPill` on a 28pt box is a circle under
         * Aurora and a square under Modernist, which is that system's rule.
         */}
        <View style={[styles.disc, solved && styles.discSolved]}>
          <Text style={[styles.discText, solved && styles.discTextSolved]}>{n}</Text>
        </View>

        <View style={styles.headText}>
          <Text style={styles.cardTitle}>{text.title}</Text>
          <Text style={styles.cardSummary}>{text.summary}</Text>
        </View>

        {reveal ? <Text style={[styles.mark, { color: markColor }]}>{solved ? '✓' : '✗'}</Text> : null}

        {/* The prototype's lucide chevron, rotated when the section is open. */}
        <View style={[styles.chevron, open && styles.chevronOpen]}>
          <Svg width={16} height={16} viewBox="0 0 24 24" fill="none">
            <Path
              d="m6 9 6 6 6-6"
              stroke={t.ink3 ?? t.textMuted}
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </Svg>
        </View>
      </Pressable>

      {open ? (
        <View style={styles.body}>
          {/*
           * The prototype has exactly two paragraphs, the second in `ink2`.
           * The content file is an array, so the rule generalises: the opening
           * paragraph is the statement and everything after it is the gloss.
           */}
          {text.body.map((p, i) => (
            <Text key={i} style={i === 0 ? styles.para : styles.paraMuted}>
              {p}
            </Text>
          ))}

          <View style={styles.takeaway}>
            <Text style={styles.takeawayLabel}>{takeawayLabel}</Text>
            <Text style={styles.takeawayText}>{text.takeaway}</Text>
          </View>

          <Text style={styles.quizQ}>{text.quizQ}</Text>

          <View style={styles.options}>
            {text.quizOptions.map((opt, i) => {
              const isPicked = picked === i;
              const isRight = i === text.quizAnswer;
              return (
                <Pressable
                  key={i}
                  style={({ pressed }) => [
                    styles.option,
                    reveal && isRight && styles.optionRight,
                    reveal && isPicked && !isRight && styles.optionWrong,
                    pressed && !reveal && styles.optionPressed,
                  ]}
                  onPress={() => onPick(i)}
                  disabled={reveal}
                  accessibilityRole="button"
                  accessibilityState={{ disabled: reveal, selected: isPicked }}
                  accessibilityLabel={opt}
                  accessibilityHint={
                    reveal ? undefined : `${text.quizQ} — ${i + 1}. seçeneği işaretle`
                  }
                >
                  <Text style={styles.optionText}>{opt}</Text>
                </Pressable>
              );
            })}
          </View>

          {reveal ? (
            /*
             * One line, as the prototype has it: verdict word, em dash,
             * explanation. Aurora spends real colour here — the verdict takes
             * the same up/down pair as the ✓ / ✗ mark in the header, so the
             * two readings of the same fact agree.
             */
            <Text style={styles.explainText}>
              <Text style={[styles.explainHead, { color: markColor }]}>
                {solved ? correctLabel : wrongLabel}
              </Text>
              {' — '}
              {text.quizExplain}
            </Text>
          ) : null}
        </View>
      ) : null}
    </Card>
  );
}

type Palette = ReturnType<typeof useTheme>;

/** The numbered disc in the accordion head, and the progress rule's height. */
const DISC = 28;
const BAR = 6;

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    screen: { flex: 1, backgroundColor: t.background },
    content: { paddingHorizontal: sh.space[3], paddingBottom: TAB_BAR_CLEARANCE },

    titleBlock: { paddingTop: sh.space[1], paddingBottom: sh.space[2] },
    heading: { ...TYPE.h2, fontSize: 26, letterSpacing: -0.52, color: t.textPrimary },
    subheading: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, marginTop: sh.space[0] },

    progressCard: { flexDirection: 'row', alignItems: 'center', gap: sh.space[2] },
    progressLabel: { ...TYPE.helper, fontSize: 12, color: t.ink2 ?? t.textSecondary },
    progressTrack: {
      flex: 1,
      height: BAR,
      borderRadius: sh.radiusPill,
      backgroundColor: t.surface2 ?? t.surfaceElevated,
      overflow: 'hidden',
    },
    progressFill: { height: BAR, borderRadius: sh.radiusPill, backgroundColor: t.brand ?? t.accent },
    progressText: { ...TYPE.body, ...font(800), ...TABULAR, color: t.textPrimary },

    list: { gap: sh.space[1], marginTop: sh.space[2] },

    head: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: sh.space[2],
      paddingHorizontal: sh.space[3],
      paddingVertical: sh.space[2],
      minHeight: Math.max(MIN_TOUCH_TARGET, 56),
    },
    // No hover on a phone: the prototype's head hover lands on press, and it
    // brightens rather than dims — on this ground that reads as "received".
    headPressed: { backgroundColor: t.surface2 ?? t.surfaceElevated },

    disc: {
      width: DISC,
      height: DISC,
      borderRadius: sh.radiusPill,
      borderWidth: 2,
      borderColor: t.line2 ?? t.divider,
      alignItems: 'center',
      justifyContent: 'center',
    },
    discSolved: { backgroundColor: t.brand ?? t.accent, borderColor: t.brand ?? t.accent },
    discText: { fontSize: 11, ...font(800), ...TABULAR, color: t.ink2 ?? t.textSecondary },
    // On the filled disc the digits sit on `brand`, which is a light indigo in
    // this palette — the inverted ink is what stays readable there.
    discTextSolved: { color: t.inkInv ?? t.background },

    headText: { flex: 1 },
    cardTitle: { ...TYPE.body, ...font(800), color: t.textPrimary },
    cardSummary: { ...TYPE.helper, color: t.ink2 ?? t.textSecondary, marginTop: 3, lineHeight: 16 },
    mark: { fontSize: 14, ...font(800) },
    chevron: { width: 16, height: 16, alignItems: 'center', justifyContent: 'center' },
    chevronOpen: { transform: [{ rotate: '180deg' }] },

    body: { paddingHorizontal: sh.space[3], paddingBottom: sh.space[3], gap: sh.space[2] },
    para: { ...TYPE.body, color: t.textPrimary, lineHeight: 21 },
    paraMuted: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, lineHeight: 21 },

    /*
     * The one tinted block on the screen. `brandSoft` is a deep indigo ground
     * in Aurora and absent from the other two palettes, where it falls back to
     * the raised surface and the box survives as a plain well.
     */
    takeaway: {
      borderRadius: sh.radius,
      backgroundColor: t.brandSoft ?? t.surfaceElevated,
      paddingVertical: sh.space[2],
      paddingHorizontal: sh.space[2],
    },
    takeawayLabel: { ...TYPE.kicker, fontSize: 10, color: t.brand ?? t.accent },
    takeawayText: { ...TYPE.body, ...font(800), color: t.brand ?? t.accent, marginTop: 2, lineHeight: 19 },

    quizQ: { ...TYPE.body, ...font(800), color: t.textPrimary, lineHeight: 19 },
    options: { gap: sh.space[1] },
    option: {
      minHeight: MIN_TOUCH_TARGET,
      justifyContent: 'center',
      borderRadius: sh.radius,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
      backgroundColor: 'transparent',
      paddingHorizontal: sh.space[2],
      paddingVertical: sh.space[1],
    },
    optionPressed: { borderColor: t.brand ?? t.accent },
    // The two revealed states the prototype draws: the right answer in `up`
    // whether or not it was chosen, and the chosen-but-wrong one in `down`.
    optionRight: { borderColor: t.up, backgroundColor: t.upSoft ?? 'transparent' },
    optionWrong: { borderColor: t.down, backgroundColor: t.downSoft ?? 'transparent' },
    optionText: { ...TYPE.body, ...font(600), color: t.textPrimary, lineHeight: 19 },

    explainHead: { ...font(800) },
    explainText: { ...TYPE.helper, fontSize: 12, color: t.ink2 ?? t.textSecondary, lineHeight: 18 },

    disclaimer: {
      ...TYPE.helper,
      color: t.ink3 ?? t.textMuted,
      textAlign: 'center',
      lineHeight: 16,
      marginTop: sh.space[4],
    },
  });
