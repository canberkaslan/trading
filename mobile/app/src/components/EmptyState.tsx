/**
 * Shared empty state for a successful fetch that returned no rows.
 *
 * Replaces inline "No pending orders / No decisions yet" blocks that told the
 * user to run a `python -m scripts.trade …` command — a dev instruction that
 * makes no sense on a phone. Empty is a normal state, not an error, so this is
 * quiet and reassuring, with an optional secondary line for context.
 */

import { View, Text, StyleSheet } from 'react-native';

import { colors } from '@/theme/colors';

type Props = {
  title: string;
  /** Optional supporting line, e.g. "Yeni kararlar günlük çalışmada oluşur." */
  hint?: string;
};

export function EmptyState({ title, hint }: Props) {
  return (
    <View style={styles.container}>
      <Text style={styles.title}>{title}</Text>
      {hint ? <Text style={styles.hint}>{hint}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { margin: 24, padding: 24, backgroundColor: colors.surface, borderRadius: 12, alignItems: 'center' },
  title: { color: colors.textSecondary, fontSize: 15, fontWeight: '600', textAlign: 'center' },
  hint: { color: colors.textMuted, fontSize: 13, marginTop: 8, textAlign: 'center' },
});
