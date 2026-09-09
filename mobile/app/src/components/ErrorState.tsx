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

import { useMemo } from 'react';

import { useTheme } from '@/theme/useTheme';
import { MIN_TOUCH_TARGET } from '@/utils/a11y';
import { font } from '@/theme/type';

type Props = {
  /** Short headline; defaults to a generic connection message. */
  title?: string;
  /** Underlying error, rendered muted + truncated. Optional. */
  detail?: unknown;
  /** When provided, renders a "Tekrar dene" button. */
  onRetry?: () => void;
};

export function ErrorState({ title, detail, onRetry }: Props) {
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);
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

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette) =>
  StyleSheet.create({
    // Matches EmptyState's block treatment: the same list in two states should
    // not change shape, only content. Previously one was a card and the other
    // was bare, so an error read as a different kind of surface.
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
    title: { color: t.accent700 ?? t.danger, fontSize: 15, ...font(800), marginBottom: 6 },
    hint: { color: t.textSecondary, fontSize: 13, textAlign: 'center' },
    detail: { color: t.textSecondary, fontSize: 11, marginTop: 12, textAlign: 'center' },
    retry: {
      marginTop: 20,
      borderWidth: 1,
      borderColor: t.textPrimary,
      paddingHorizontal: 20,
      paddingVertical: 10,
      minHeight: MIN_TOUCH_TARGET,
      justifyContent: 'center',
    },
    retryText: { color: t.textPrimary, fontSize: 14, ...font(800) },
  });
