/**
 * Shared empty state for a successful fetch that returned no rows.
 *
 * Replaces inline "No pending orders / No decisions yet" blocks that told the
 * user to run a `python -m scripts.trade …` command — a dev instruction that
 * makes no sense on a phone. Empty is a normal state, not an error, so this is
 * quiet and reassuring, with an optional secondary line for context.
 */

import { View, Text, StyleSheet } from 'react-native';

import { useMemo } from 'react';

import { useTheme } from '@/theme/useTheme';

type Props = {
  title: string;
  /** Optional supporting line, e.g. "Yeni kararlar günlük çalışmada oluşur." */
  hint?: string;
};

export function EmptyState({ title, hint }: Props) {
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);
  return (
    <View style={styles.container}>
      <Text style={styles.title}>{title}</Text>
      {hint ? <Text style={styles.hint}>{hint}</Text> : null}
    </View>
  );
}

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette) =>
  StyleSheet.create({
    // A ruled block on the page ground, not a filled card: an empty list should
    // recede, and a dark slab on a light page reads as an error.
    container: {
      marginHorizontal: 16,
      marginVertical: 24,
      paddingVertical: 24,
      paddingHorizontal: 16,
      borderTopWidth: 2,
      borderBottomWidth: 2,
      borderColor: t.divider,
      alignItems: 'center',
    },
    title: { color: t.textPrimary, fontSize: 15, fontWeight: '800', textAlign: 'center' },
    hint: { color: t.textSecondary, fontSize: 13, marginTop: 8, textAlign: 'center' },
  });
