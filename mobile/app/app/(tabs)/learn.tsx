import { useState, useCallback } from 'react';
import { View, Text, StyleSheet, ScrollView, Pressable, LayoutAnimation, Platform, UIManager } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';

import { LESSONS, type LessonText } from '@/content/lessons';
import { colors } from '@/theme/colors';
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
  const solved = picked === text.quizAnswer;

  return (
    <Pressable
      style={styles.card}
      onPress={onToggle}
      accessibilityRole="button"
      accessibilityState={{ expanded: open }}
      accessibilityLabel={text.title}
    >
      <View style={styles.cardHead}>
        <View style={[styles.dot, solved && styles.dotDone]} />
        <View style={{ flex: 1 }}>
          <Text style={styles.cardTitle}>{text.title}</Text>
          <Text style={styles.cardSummary}>{text.summary}</Text>
        </View>
        <Text style={styles.chevron}>{open ? '−' : '+'}</Text>
      </View>

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
              <Text style={[styles.explainHead, { color: solved ? colors.up : colors.down }]}>
                {solved ? correctLabel : wrongLabel}
              </Text>
              <Text style={styles.explainText}>{text.quizExplain}</Text>
            </View>
          ) : null}
        </View>
      ) : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  scroll: { padding: 24, gap: 12 },
  heading: { color: colors.textPrimary, fontSize: 28, fontWeight: '700' },
  subheading: { color: colors.textSecondary, fontSize: 13 },

  progressRow: { flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 4 },
  progressTrack: { flex: 1, height: 4, borderRadius: 2, backgroundColor: colors.surfaceElevated, overflow: 'hidden' },
  progressFill: { height: 4, borderRadius: 2, backgroundColor: colors.accent },
  progressText: { color: colors.textMuted, fontSize: 12, fontVariant: ['tabular-nums'] },

  card: { backgroundColor: colors.surface, borderRadius: 14, padding: 16 },
  cardHead: { flexDirection: 'row', alignItems: 'flex-start', gap: 12 },
  dot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.surfaceElevated, marginTop: 6 },
  dotDone: { backgroundColor: colors.up },
  cardTitle: { color: colors.textPrimary, fontSize: 16, fontWeight: '700' },
  cardSummary: { color: colors.textSecondary, fontSize: 12.5, marginTop: 3, lineHeight: 17 },
  chevron: { color: colors.textMuted, fontSize: 20, fontWeight: '600', width: 20, textAlign: 'center' },

  body: { marginTop: 14, gap: 12, borderTopWidth: 1, borderTopColor: colors.surfaceElevated, paddingTop: 14 },
  para: { color: colors.textSecondary, fontSize: 13.5, lineHeight: 20 },

  takeaway: { backgroundColor: colors.surfaceElevated, borderRadius: 10, padding: 12, gap: 4 },
  takeawayLabel: { color: colors.textMuted, fontSize: 10.5, fontWeight: '700', letterSpacing: 1, textTransform: 'uppercase' },
  takeawayText: { color: colors.textPrimary, fontSize: 13.5, lineHeight: 19 },

  quizQ: { color: colors.textPrimary, fontSize: 14, fontWeight: '600', marginTop: 4, lineHeight: 20 },
  option: {
    minHeight: MIN_TOUCH_TARGET,
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: colors.surfaceElevated,
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  optionRight: { borderColor: colors.up, backgroundColor: 'rgba(34,197,94,0.10)' },
  optionWrong: { borderColor: colors.down, backgroundColor: 'rgba(239,68,68,0.10)' },
  optionText: { color: colors.textPrimary, fontSize: 13.5, lineHeight: 19 },

  explain: { gap: 4, marginTop: 4 },
  explainHead: { fontSize: 12, fontWeight: '700' },
  explainText: { color: colors.textSecondary, fontSize: 13, lineHeight: 19 },

  disclaimer: { color: colors.textMuted, fontSize: 11, paddingVertical: 20, fontStyle: 'italic', textAlign: 'center' },
});
