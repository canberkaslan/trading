/**
 * `.tag` — the one chip shape in the system.
 *
 * Four variants, straight from the handoff's styles.css:
 *   accent   — accent-100 fill, accent-800 text. A thing that went wrong.
 *   neutral  — neutral-100 fill, neutral-800 text. A thing that is just so.
 *   outline  — 1px accent border, accent text. A thing awaiting a decision.
 *   outlineMuted — 1px secondary-ink border. A thing the app does not recognise;
 *                  it must NOT wear the accent, or API drift reads as a warning.
 *   ink      — ink fill, ground text. The strongest mark; ratings use it.
 *
 * It exists because five screens were each drawing their own chip with slightly
 * different padding and their own idea of which colour meant what — the status
 * tag on Emirler and the model badge on Ajanlar were the same idea rendered two
 * ways. Square by definition: `borderRadius` is not a prop.
 */

import { View, Text, StyleSheet, type StyleProp, type ViewStyle } from 'react-native';
import { useMemo } from 'react';

import { useTheme } from '@/theme/useTheme';
import type { Palette } from '@/theme/colors';
import { font } from '@/theme/type';

export type TagVariant = 'accent' | 'neutral' | 'outline' | 'outlineMuted' | 'ink';

export function tagColors(t: Palette, variant: TagVariant) {
  switch (variant) {
    case 'accent':
      return { backgroundColor: t.accent100 ?? t.surface, color: t.accent800 ?? t.danger, borderColor: 'transparent' };
    case 'neutral':
      return { backgroundColor: t.neutral100 ?? t.surface, color: t.neutral800 ?? t.textSecondary, borderColor: 'transparent' };
    case 'outline':
      return { backgroundColor: 'transparent', color: t.accent700 ?? t.accent, borderColor: t.accent };
    case 'outlineMuted':
      return { backgroundColor: 'transparent', color: t.textSecondary, borderColor: t.textSecondary };
    case 'ink':
      return { backgroundColor: t.textPrimary, color: t.background, borderColor: 'transparent' };
  }
}

export function Tag({
  label,
  variant = 'neutral',
  style,
}: {
  label: string;
  variant?: TagVariant;
  style?: StyleProp<ViewStyle>;
}) {
  const t = useTheme();
  const styles = useMemo(() => makeStyles(), []);
  const c = tagColors(t, variant);
  return (
    <View style={[styles.tag, { backgroundColor: c.backgroundColor, borderColor: c.borderColor }, style]}>
      <Text style={[styles.label, { color: c.color }]}>{label}</Text>
    </View>
  );
}

const makeStyles = () =>
  StyleSheet.create({
    tag: { paddingHorizontal: 10, paddingVertical: 3, borderWidth: 1, alignSelf: 'flex-start' },
    label: { fontSize: 11, letterSpacing: 0.22, ...font(600) },
  });
