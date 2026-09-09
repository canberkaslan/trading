import { useState, useCallback, useEffect, useMemo } from 'react';
import { View, Text, StyleSheet, ScrollView, Pressable, LayoutAnimation, Platform, UIManager } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import { create } from 'zustand';
import * as SecureStore from 'expo-secure-store';

import { LESSONS, type LessonText } from '@/content/lessons';
import { useTheme } from '@/theme/useTheme';
import { MIN_TOUCH_TARGET } from '@/utils/a11y';
import { font, TABULAR, TYPE } from '@/theme/type';

if (Platform.OS === 'android' && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}

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
/* Screen                                                                     */
/* -------------------------------------------------------------------------- */

/**
 * The lessons explain this system's own metrics — Sharpe, drawdown, the
 * go-live gates — so the numbers on the other tabs mean something. Content is
 * bundled rather than fetched: it is small, it never changes between releases,
 * and a reader who has lost connectivity is exactly the reader with time to
 * read it.
 */
export default function LearnScreen() {
  const { t, i18n } = useTranslation();
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);
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

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView contentContainerStyle={styles.scroll}>
        <Text style={styles.heading}>{t('learn.title')}</Text>
        <Text style={styles.subheading}>{t('learn.subtitle')}</Text>

        <View
          style={styles.progressRow}
          accessibilityRole="progressbar"
          accessibilityValue={{ min: 0, max: LESSONS.length, now: done }}
        >
          <View style={styles.progressTrack}>
            <View style={[styles.progressFill, { width: `${(done / LESSONS.length) * 100}%` }]} />
          </View>
          <Text style={styles.progressText}>
            {done}/{LESSONS.length}
          </Text>
        </View>

        {LESSONS.map((lesson) => (
          <LessonCard
            key={lesson.id}
            text={lesson[lang]}
            open={openId === lesson.id}
            picked={answered[answerKey(lang, lesson.id)]}
            onToggle={() => toggle(lesson.id)}
            onPick={(i) => pick(answerKey(lang, lesson.id), i)}
            correctLabel={t('learn.correct')}
            wrongLabel={t('learn.wrong')}
            takeawayLabel={t('learn.takeaway')}
          />
        ))}

        <Text style={styles.disclaimer}>{t('disclaimer.short')}</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

function LessonCard({
  text,
  open,
  picked,
  onToggle,
  onPick,
  correctLabel,
  wrongLabel,
  takeawayLabel,
}: {
  text: LessonText;
  open: boolean;
  picked: number | undefined;
  onToggle: () => void;
  onPick: (i: number) => void;
  correctLabel: string;
  wrongLabel: string;
  takeawayLabel: string;
}) {
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);
  const solved = picked === text.quizAnswer;

  return (
    /*
     * Only the HEAD toggles. The whole card used to be the Pressable, with the
     * quiz options nested inside it — which works on native (the responder
     * system gives the touch to the innermost handler) and silently misbehaves
     * on web, where the DOM event bubbles and answering a question collapses
     * the lesson you were reading. A header-only target is also simply right:
     * selecting body text should not close the section.
     */
    <View style={styles.card}>
      <Pressable
        style={styles.cardHead}
        onPress={onToggle}
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        accessibilityLabel={text.title}
      >
        <View style={styles.dotCell}>
          <View style={[styles.dot, solved && styles.dotDone]} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={styles.cardTitle}>{text.title}</Text>
          <Text style={styles.cardSummary}>{text.summary}</Text>
        </View>
        <Text style={styles.chevron}>{open ? '−' : '+'}</Text>
      </Pressable>

      {open ? (
        <View style={styles.body}>
          {text.body.map((p, i) => (
            <Text key={i} style={styles.para}>
              {p}
            </Text>
          ))}

          <View style={styles.takeaway}>
            <Text style={styles.takeawayLabel}>{takeawayLabel}</Text>
            <Text style={styles.takeawayText}>{text.takeaway}</Text>
          </View>

          <Text style={styles.quizQ}>{text.quizQ}</Text>
          {text.quizOptions.map((opt, i) => {
            const isPicked = picked === i;
            const isRight = i === text.quizAnswer;
            const reveal = picked !== undefined;
            return (
              <Pressable
                key={i}
                style={[
                  styles.option,
                  reveal && isRight && styles.optionRight,
                  reveal && isPicked && !isRight && styles.optionWrong,
                ]}
                onPress={() => onPick(i)}
                disabled={reveal}
                accessibilityRole="button"
                accessibilityState={{ disabled: reveal, selected: isPicked }}
                accessibilityLabel={opt}
              >
                <Text style={styles.optionText}>{opt}</Text>
              </Pressable>
            );
          })}

          {picked !== undefined ? (
            /*
             * One line, as the prototype has it: verdict word, em dash,
             * explanation. The verdict is the system's own two-colour logic —
             * ink for the expected case, accent for the exception — and not the
             * conventional green/red, which this palette does not contain.
             */
            <Text style={styles.explainText}>
              <Text style={[styles.explainHead, solved ? styles.verdictRight : styles.verdictWrong]}>
                {solved ? correctLabel : wrongLabel}
              </Text>
              {' — '}
              {text.quizExplain}
            </Text>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

type Palette = ReturnType<typeof useTheme>;

/** The dot column: a 10px square in a 14px cell, so the body copy lines up under it. */
const DOT_CELL = 14;
const DOT = 10;

const makeStyles = (t: Palette) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.background },
    scroll: { paddingHorizontal: 16, paddingTop: 20, paddingBottom: 24 },
    heading: { color: t.textPrimary, ...TYPE.h2 },
    subheading: { color: t.textSecondary, marginTop: 4, ...TYPE.helper },

    progressRow: { flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 14, marginBottom: 6 },
    // A rule that fills, not a pill: 4px of neutral-200 overwritten in ink.
    progressTrack: { flex: 1, height: 4, backgroundColor: t.neutral200 ?? t.surface },
    progressFill: { height: 4, backgroundColor: t.textPrimary },
    progressText: { color: t.textPrimary, ...TYPE.helper, ...font(800), ...TABULAR },

    // Ruled rows: each lesson is separated by a hairline, not floated on a card.
    card: { borderBottomWidth: 1, borderBottomColor: t.divider },
    cardHead: {
      flexDirection: 'row',
      alignItems: 'flex-start',
      gap: 12,
      paddingVertical: 14,
      minHeight: MIN_TOUCH_TARGET,
    },
    dotCell: { width: DOT_CELL },
    dot: { width: DOT, height: DOT, borderWidth: 2, borderColor: t.textPrimary, marginTop: 5 },
    dotDone: { backgroundColor: t.textPrimary },
    cardTitle: { color: t.textPrimary, ...TYPE.section },
    cardSummary: { color: t.textSecondary, marginTop: 3, lineHeight: 16, ...TYPE.helper },
    chevron: { color: t.textSecondary, fontSize: 20, ...font(800), width: 20, textAlign: 'center' },

    // Indented to the dot column so the opened lesson hangs off the marker.
    body: { paddingLeft: DOT_CELL + 12, paddingBottom: 18, gap: 12 },
    para: { color: t.textPrimary, lineHeight: 21, ...TYPE.body },

    takeaway: { backgroundColor: t.surface, paddingVertical: 10, paddingHorizontal: 14 },
    takeawayLabel: { color: t.accent700 ?? t.accent, ...TYPE.kicker },
    takeawayText: { color: t.textPrimary, marginTop: 4, lineHeight: 19, ...TYPE.bodyStrong },

    quizQ: { color: t.textPrimary, lineHeight: 19, ...TYPE.body, ...font(800) },
    option: {
      minHeight: MIN_TOUCH_TARGET,
      justifyContent: 'center',
      borderWidth: 1,
      borderColor: t.neutral300 ?? t.divider,
      paddingHorizontal: 12,
      paddingVertical: 10,
    },
    // The right answer is INK, not green: this palette spends colour on the
    // exception only, so the correct option is stated in the page's own voice
    // and the accent is reserved for the one that was wrong.
    optionRight: { borderColor: t.textPrimary, backgroundColor: t.neutral200 ?? t.surface },
    optionWrong: { borderColor: t.accent, backgroundColor: t.accent100 ?? 'transparent' },
    optionText: { color: t.textPrimary, lineHeight: 19, ...TYPE.body },

    explainHead: { ...font(800) },
    // accent-700 rather than the base accent: a 13px word has to be read, and
    // the base accent is a fill colour on this ground.
    verdictRight: { color: t.textPrimary },
    verdictWrong: { color: t.accent700 ?? t.downText },
    explainText: { color: t.textSecondary, lineHeight: 19, ...TYPE.body },

    disclaimer: { color: t.textSecondary, paddingVertical: 20, textAlign: 'center', ...TYPE.helper },
  });
