/**
 * Shared error state for a failed data fetch.
 *
 * Replaces three copies of an inline "Backend unreachable" block that leaked
 * developer CLI commands (uvicorn / python -m scripts.trade) straight into the
 * end-user's error screen — noise to a user, and a small info-leak about the
 * backend layout. This shows an honest TR message + an optional retry, and
 * keeps the raw error one line, muted, for support without shouting a shell
 * command at the user.
 */

import { View, Text, StyleSheet, Pressable } from 'react-native';

import { colors } from '@/theme/colors';

type Props = {
  /** Short headline; defaults to a generic connection message. */
  title?: string;
  /** Underlying error, rendered muted + truncated. Optional. */
  detail?: unknown;
  /** When provided, renders a "Tekrar dene" button. */
  onRetry?: () => void;
};

export function ErrorState({ title, detail, onRetry }: Props) {
  return (
    <View style={styles.container} accessibilityRole="alert">
      <Text style={styles.title}>{title ?? "Sunucuya ulaşılamıyor"}</Text>
      <Text style={styles.hint}>Bağlantını kontrol edip tekrar dene.</Text>
      {detail != null ? (
        <Text style={styles.detail} numberOfLines={2}>
          {String(detail)}
        </Text>
      ) : null}
      {onRetry ? (
        <Pressable
          style={styles.retry}
          onPress={onRetry}
          accessibilityRole="button"
          accessibilityLabel="Tekrar dene"
        >
          <Text style={styles.retryText}>Tekrar dene</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  title: { color: colors.danger, fontSize: 16, fontWeight: '600', marginBottom: 6 },
  hint: { color: colors.textSecondary, fontSize: 13, textAlign: 'center' },
  detail: { color: colors.textMuted, fontSize: 11, marginTop: 12, textAlign: 'center' },
  retry: {
    marginTop: 20,
    backgroundColor: colors.surfaceElevated,
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 10,
  },
  retryText: { color: colors.textPrimary, fontSize: 14, fontWeight: '600' },
});
