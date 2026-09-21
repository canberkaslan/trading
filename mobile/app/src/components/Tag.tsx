/**
 * `.tag` — the one chip shape in the system.
 *
 * Variants, straight from the handoffs:
 *   accent   — accent-100 fill, accent-800 text. A thing that went wrong.
 *   neutral  — neutral-100 fill, neutral-800 text. A thing that is just so.
 *   outline  — 1px accent border, accent text. A thing awaiting a decision.
 *   outlineMuted — 1px secondary-ink border. A thing the app does not recognise;
 *                  it must NOT wear the accent, or API drift reads as a warning.
 *   ink      — ink fill, ground text. The strongest mark; ratings use it.
 *
 * Aurora adds four TONE variants, because it has soft grounds that Modernist
 * does not (`--upSoft`, `--downSoft`, `--warnSoft`, `--brandSoft`) and uses them
 * to carry direction: the BUY/SELL square on a pending row is a green or rose
 * tint, not the ink chip. On a palette without those grounds they fall back to
 * `surface` and the meaning survives in the text colour alone, which is the
 * failure mode that keeps a half-migrated screen readable.
 *
 * It exists because five screens were each drawing their own chip with slightly
 * different padding and their own idea of which colour meant what. Its radius
 * is NOT a prop: it is `shape.radiusPill`, which is 0 under Modernist and a
 * pill under Aurora, and a screen that wants a different one is wrong.
 */

import { View, Text, StyleSheet, type StyleProp, type ViewStyle } from 'react-native';
import { useMemo } from 'react';

import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import type { Palette } from '@/theme/colors';
import { font, TABULAR } from '@/theme/type';

export type TagVariant =
  | 'accent'
  | 'neutral'
  | 'outline'
  | 'outlineMuted'
  | 'ink'
  | 'up'
  | 'down'
  | 'warn'
  | 'brand';

/**
 * The Aurora-only grounds, declared optional so `tagColors` still takes a plain
 * `Palette` (which is what the rating test passes) while being able to reach
 * for a soft fill when the active palette has one.
 */
type TagPalette = Palette &
  Partial<Record<'upSoft' | 'downSoft' | 'warnSoft' | 'brandSoft' | 'brand', string>>;

export function tagColors(t: TagPalette, variant: TagVariant) {
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
    case 'up':
      return { backgroundColor: t.upSoft ?? t.surface, color: t.upAltText ?? t.up, borderColor: 'transparent' };
    case 'down':
      return { backgroundColor: t.downSoft ?? t.surface, color: t.downText ?? t.down, borderColor: 'transparent' };
    case 'warn':
      return { backgroundColor: t.warnSoft ?? t.surface, color: t.warning, borderColor: 'transparent' };
    case 'brand':
      return { backgroundColor: t.brandSoft ?? t.surface, color: t.brand ?? t.accent, borderColor: 'transparent' };
  }
}

export type TagSize = 'sm' | 'md';

export function Tag({
  label,
  variant = 'neutral',
  size = 'md',
  /** Uppercase + tracked, for rating and status stamps. */
  caps,
  /** Tabular figures, for a chip whose text is mostly a number. */
  numeric,
  style,
}: {
  label: string;
  variant?: TagVariant;
  size?: TagSize;
  caps?: boolean;
  numeric?: boolean;
  style?: StyleProp<ViewStyle>;
}) {
  const t = useTheme();
  const sh = useShape();
  // `font()` resolves the family at call time against the ACTIVE palette, so
  // this memo has to depend on the palette — frozen at `[]` it would keep
  // Modernist's Archivo after a switch to Aurora. The palette is passed in
  // (rather than merely listed as a dep) so that dependency is real and
  // exhaustive-deps agrees with it; the colours themselves are applied inline,
  // because they depend on the variant.
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const c = tagColors(t, variant);
  return (
    <View
      style={[styles.tag, size === 'sm' && styles.tagSm, { backgroundColor: c.backgroundColor, borderColor: c.borderColor }, style]}
    >
      <Text
        style={[
          styles.label,
          size === 'sm' && styles.labelSm,
          caps && styles.caps,
          numeric && TABULAR,
          { color: c.color },
        ]}
      >
        {label}
      </Text>
    </View>
  );
}

const makeStyles = (_palette: TagPalette, sh: Shape) =>
  StyleSheet.create({
    tag: {
      paddingHorizontal: 10,
      paddingVertical: 3,
      borderWidth: sh.hairline,
      borderRadius: sh.radiusPill,
      alignSelf: 'flex-start',
    },
    tagSm: { paddingHorizontal: 6, paddingVertical: 2 },
    label: { fontSize: 11, letterSpacing: 0.22, ...font(600) },
    labelSm: { fontSize: 10 },
    caps: { textTransform: 'uppercase', letterSpacing: 0.6 },
  });
