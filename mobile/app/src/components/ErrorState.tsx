/**
 * Shared error state for a failed data fetch.
 *
 * Replaces three copies of an inline "Backend unreachable" block that leaked
 * developer CLI commands (uvicorn / python -m scripts.trade) straight into the
 * end-user's error screen — noise to a user, and a small info-leak about the
 * backend layout. This shows an honest TR message + an optional retry, and
 * keeps the raw error one line, muted, for support without shouting a shell
 * command at the user.
 *
 * It now also distinguishes WHY the fetch failed, because one generic line was
 * actively misleading. An operator opening the app without a bearer saw
 * "Sunucuya ulaşılamıyor — bağlantını kontrol et" over a `Tekrar dene` button,
 * while the server was up, answering in milliseconds, and refusing them for
 * want of a token. They were sent to debug their network, and the only button
 * on screen could not do anything but reproduce the same 401.
 *
 * So an auth failure gets its own copy and its own action: say which of the
 * two token problems it is, and offer the screen that fixes it. Everything
 * else keeps the connection message and the retry, which is the right pair
 * when nothing answered.
 *
 * The raw error is deliberately suppressed for auth failures. ky's HTTPError
 * message embeds the full request URL, so rendering it puts the API host and
 * path on screen to say something the headline already says better.
 */

import { View, Text, StyleSheet, Pressable } from 'react-native';
import { useRouter } from 'expo-router';

import { useMemo } from 'react';

import { useApiTokenStore } from '@/stores/apiToken';
import { useTheme } from '@/theme/useTheme';
import { MIN_TOUCH_TARGET } from '@/utils/a11y';
import { authErrorKind } from '@/utils/apiError';
import { font } from '@/theme/type';

type Props = {
  /** Short headline; defaults to a generic connection message. */
  title?: string;
  /** Underlying error, rendered muted + truncated. Optional. */
  detail?: unknown;
  /** When provided, renders a "Tekrar dene" button. */
  onRetry?: () => void;
};

/**
 * Copy per auth failure. Both send the operator to the same field; they differ
 * in what they claim happened, because "you never entered one" and "the one you
 * entered was refused" lead to different next moves.
 */
const AUTH_COPY = {
  missing: {
    title: 'Kimlik doğrulama gerekli',
    hint: 'Bu cihaz sunucuya erişim yetkisine sahip değil. Lütfen giriş yapın.',
  },
  invalid: {
    title: 'Kimlik doğrulama başarısız',
    hint: 'Oturumunuz geçersiz. Lütfen çıkış yapıp yeniden giriş yapın.',
  },
} as const;

export function ErrorState({ title, detail, onRetry }: Props) {
  const theme = useTheme();
  const router = useRouter();
  const styles = useMemo(() => makeStyles(theme), [theme]);

  // Whether a token is stored is what separates "never entered" from
  // "rejected", and the store already knows it without a round trip.
  const hasToken = useApiTokenStore((s) => s.token != null);
  const kind = authErrorKind(detail, hasToken);

  if (kind) {
    const copy = AUTH_COPY[kind];
    return (
      <View style={styles.container} accessibilityRole="alert">
        <Text style={styles.title}>{copy.title}</Text>
        <Text style={styles.hint}>{copy.hint}</Text>
        <Pressable
          style={styles.retry}
          onPress={() => router.push('/(tabs)/settings' as never)}
          accessibilityRole="button"
          accessibilityLabel="Ayarlara git"
          accessibilityHint="Sunucu token’ının girildiği ekranı açar"
        >
          <Text style={styles.retryText}>Ayarlara git</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.container} accessibilityRole="alert">
      <Text style={styles.title}>{title ?? 'Sunucuya ulaşılamıyor'}</Text>
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
