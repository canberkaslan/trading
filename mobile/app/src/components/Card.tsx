/**
 * The surface every screen sits on.
 *
 * The Aurora prototype draws 923 inline styles and almost no classes, but it
 * repeats exactly one rectangle: `background:var(--surface)`, a hairline of
 * `--line`, and a 16px radius. Six screens had each written that rectangle out
 * by hand with slightly different padding, which is how the shell and the
 * Orders list ended up a pixel apart.
 *
 * Five tones, because the prototype uses five grounds and they mean different
 * things:
 *   surface   — the default card. A thing.
 *   raised    — a card ON a card (`--surface2`). A thing inside a thing.
 *   recessed  — the page ground inside a card (`--paper`). A well, e.g. the
 *               three-up summary grid in a sheet.
 *   outline   — no fill, one rule. A card that must not compete.
 *   dashed    — the empty slot. Never filled: an empty list should read as an
 *               absence, not as a card with nothing in it.
 *
 * `onPress` turns it into a pressable without changing anything else, because
 * in the prototype every card that navigates is a `<button>` with card styling
 * rather than a different shape.
 */

import { View, Pressable, StyleSheet, type StyleProp, type ViewStyle } from 'react-native';
import { useMemo, type ReactNode } from 'react';

import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';

export type CardTone = 'surface' | 'raised' | 'recessed' | 'outline' | 'dashed';

type Palette = ReturnType<typeof useTheme>;

export function cardSurface(t: Palette, tone: CardTone) {
  switch (tone) {
    case 'raised':
      return { backgroundColor: t.surfaceElevated, borderColor: t.line2 ?? t.divider, borderStyle: 'solid' as const };
    case 'recessed':
      return { backgroundColor: t.paper ?? t.background, borderColor: t.line ?? t.divider, borderStyle: 'solid' as const };
    case 'outline':
      return { backgroundColor: 'transparent', borderColor: t.line2 ?? t.divider, borderStyle: 'solid' as const };
    case 'dashed':
      return { backgroundColor: 'transparent', borderColor: t.line2 ?? t.divider, borderStyle: 'dashed' as const };
    case 'surface':
    default:
      return { backgroundColor: t.surface, borderColor: t.line ?? t.divider, borderStyle: 'solid' as const };
  }
}

export interface CardProps {
  children?: ReactNode;
  tone?: CardTone;
  /** Turns the card into a pressable. Everything else is unchanged. */
  onPress?: () => void;
  /** Drop the built-in padding — for a card whose rows draw their own. */
  padded?: boolean;
  /** Clip children to the radius. Needed when rows paint their own fill. */
  clip?: boolean;
  style?: StyleProp<ViewStyle>;
  accessibilityLabel?: string;
  accessibilityHint?: string;
  accessibilityRole?: 'button' | 'summary' | 'none';
  /**
   * Pass `false` when the card CONTAINS its own buttons.
   *
   * React Native's Pressable is `accessible` by default, which makes the whole
   * card one element and hides every control inside it from VoiceOver and
   * TalkBack. A card that is merely selectable can stay accessible; a card that
   * wraps Onayla / Reddet must not, or those buttons cannot be reached at all.
   */
  accessible?: boolean;
}

export function Card({
  children,
  tone = 'surface',
  onPress,
  padded = true,
  clip,
  style,
  accessibilityLabel,
  accessibilityHint,
  accessibilityRole,
  accessible,
}: CardProps) {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);

  const skin = [styles.card, cardSurface(t, tone), padded && styles.padded, clip && styles.clip, style];

  if (!onPress) {
    return (
      <View
        style={skin}
        accessibilityRole={accessibilityRole === 'button' ? undefined : accessibilityRole}
        accessibilityLabel={accessibilityLabel}
      >
        {children}
      </View>
    );
  }

  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [skin, pressed && styles.pressed]}
      accessible={accessible}
      accessibilityRole={accessibilityRole ?? 'button'}
      accessibilityLabel={accessibilityLabel}
      accessibilityHint={accessibilityHint}
    >
      {children}
    </Pressable>
  );
}

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    card: { borderRadius: sh.radius, borderWidth: sh.hairline },
    padded: { paddingHorizontal: sh.space[3], paddingVertical: sh.space[2] },
    clip: { overflow: 'hidden' },
    // The prototype's hover is `border-color:--line2`. A phone has no hover, so
    // the same intent lands on press: the card brightens its own edge rather
    // than dimming, which on a dark ground reads as "received" instead of
    // "disabled".
    pressed: { borderColor: t.line2 ?? t.textSecondary },
  });
