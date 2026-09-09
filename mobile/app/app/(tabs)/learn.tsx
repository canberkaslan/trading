import { useState, useCallback, useMemo } from 'react';
import { View, Text, StyleSheet, ScrollView, Pressable, LayoutAnimation, Platform, UIManager } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';

import { LESSONS, type LessonText } from '@/content/lessons';
import { useTheme } from '@/theme/useTheme';
import { MIN_TOUCH_TARGET } from '@/utils/a11y';

if (Platform.OS === 'android' && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}

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
  const lang: 'tr' | 'en' = i18n.language?.startsWith('tr') ? 'tr' : 'en';
  const [openId, setOpenId] = useState<string | null>(null);
  const [answered, setAnswered] = useState<Record<string, number>>({});

  const toggle = useCallback((id: string) => {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    setOpenId((cur) => (cur === id ? null : id));
  }, []);

  const done = Object.keys(answered).filter((id) => {
    const lesson = LESSONS.find((l) => l.id === id);
    return lesson && answered[id] === lesson[lang].quizAnswer;
  }).length;

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView contentContainerStyle={styles.scroll}>
        <Text style={styles.heading}>{t('learn.title')}</Text>
        <Text style={styles.subheading}>{t('learn.subtitle')}</Text>

        <View style={styles.progressRow}>
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
            picked={answered[lesson.id]}
            onToggle={() => toggle(lesson.id)}
            onPick={(i) => setAnswered((a) => ({ ...a, [lesson.id]: i }))}
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
        <View style={[styles.dot, solved && styles.dotDone]} />
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
                accessibilityLabel={opt}
              >
                <Text style={styles.optionText}>{opt}</Text>
              </Pressable>
            );
          })}

          {picked !== undefined ? (
            <View style={styles.explain}>
              {/* Not the accounting scheme: a right answer is not a gain, so it
                  keeps the conventional green (`upAlt`) rather than rendering as
                  plain ink, which here would be indistinguishable from body copy. */}
              <Text style={[styles.explainHead, { color: solved ? theme.upAltText ?? theme.up : theme.downText ?? theme.down }]}>
                {solved ? correctLabel : wrongLabel}
              </Text>
              <Text style={styles.explainText}>{text.quizExplain}</Text>
            </View>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.background },
    scroll: { paddingHorizontal: 16, paddingTop: 20, paddingBottom: 24, gap: 0 },
    heading: { color: t.textPrimary, fontSize: 24, fontWeight: '800' },
    subheading: { color: t.textSecondary, fontSize: 13 },

    progressRow: { flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 16, marginBottom: 12 },
    // Square track, ink fill — a rule that fills rather than a pill.
    progressTrack: { flex: 1, height: 6, borderWidth: 1, borderColor: t.textPrimary, overflow: 'hidden' },
    progressFill: { height: 4, backgroundColor: t.textPrimary },
    progressText: { color: t.textSecondary, fontSize: 12, fontVariant: ['tabular-nums'], fontWeight: '700' },

    // Ruled rows: each lesson is separated by a hairline, not floated on a card.
    card: { paddingVertical: 16, borderBottomWidth: 1, borderBottomColor: t.divider },
    cardHead: { flexDirection: 'row', alignItems: 'flex-start', gap: 12, minHeight: MIN_TOUCH_TARGET },
    dot: { width: 8, height: 8, borderWidth: 1, borderColor: t.textPrimary, marginTop: 6 },
    dotDone: { backgroundColor: t.textPrimary },
    cardTitle: { color: t.textPrimary, fontSize: 16, fontWeight: '800' },
    cardSummary: { color: t.textSecondary, fontSize: 12.5, marginTop: 3, lineHeight: 17 },
    chevron: { color: t.textPrimary, fontSize: 20, fontWeight: '700', width: 20, textAlign: 'center' },

    body: { marginTop: 14, gap: 12, borderTopWidth: 1, borderTopColor: t.divider, paddingTop: 14 },
    para: { color: t.textSecondary, fontSize: 13.5, lineHeight: 20 },

    takeaway: { backgroundColor: t.surface, padding: 12, gap: 4, borderLeftWidth: 3, borderLeftColor: t.textPrimary },
    takeawayLabel: { color: t.textSecondary, fontSize: 10.5, fontWeight: '800', letterSpacing: 1, textTransform: 'uppercase' },
    takeawayText: { color: t.textPrimary, fontSize: 13.5, lineHeight: 19 },

    quizQ: { color: t.textPrimary, fontSize: 14, fontWeight: '700', marginTop: 4, lineHeight: 20 },
    option: {
      minHeight: MIN_TOUCH_TARGET,
      justifyContent: 'center',
      borderWidth: 1,
      borderColor: t.neutral400 ?? t.textSecondary,
      paddingHorizontal: 14,
      paddingVertical: 10,
      marginBottom: -1,
    },
    // Marked by a heavier rule rather than a wash: the two tints this replaced
    // were rgba literals of the DARK palette's green and red, which on the light
    // ground rendered as two barely-distinguishable greys.
    optionRight: { borderWidth: 2, borderColor: t.upAlt ?? t.up, zIndex: 1 },
    optionWrong: { borderWidth: 2, borderColor: t.accent, backgroundColor: t.accent100 ?? 'transparent', zIndex: 1 },
    optionText: { color: t.textPrimary, fontSize: 13.5, lineHeight: 19 },

    explain: { gap: 4, marginTop: 12 },
    explainHead: { fontSize: 12, fontWeight: '800', letterSpacing: 0.4 },
    explainText: { color: t.textSecondary, fontSize: 13, lineHeight: 19 },

    disclaimer: { color: t.textSecondary, fontSize: 11, paddingVertical: 20, textAlign: 'center' },
  });
