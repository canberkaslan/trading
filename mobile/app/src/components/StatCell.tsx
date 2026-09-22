/**
 * One figure with a label over it and a qualifier under it.
 *
 * The prototype uses this shape at three sizes and never varies the order:
 * an 11px uppercase kicker, the number, then the thing that makes the number
 * mean something ("< 15%", "18 Eyl 21:34", "4/4 gate geçti"). The Eval grid,
 * the sheet's three-up summary and the Bugün two-up are all the same cell.
 *
 * The qualifier is not optional decoration: a Sharpe of 0.8 is only legible
 * beside the gate it has to clear. That is why it sits under the value rather
 * than being dropped when space is tight.
 */

import { View, Text, StyleSheet, type StyleProp, type ViewStyle } from 'react-native';
import { useMemo } from 'react';

import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { TYPE, TABULAR, font } from '@/theme/type';

type Palette = ReturnType<typeof useTheme>;

export type StatSize = 'sm' | 'md' | 'lg';

const VALUE_SIZE: Record<StatSize, number> = { sm: 13, md: 18, lg: 26 };

export interface StatCellProps {
  /** The 11px uppercase kicker. */
  label: string;
  value: string;
  /** What makes the value mean something: a gate, a unit, a timestamp. */
  hint?: string;
  /** Overrides the ink. Pass a palette colour, never a literal. */
  valueColor?: string;
  size?: StatSize;
  align?: 'left' | 'right';
  style?: StyleProp<ViewStyle>;
  /** Read out instead of "label value hint" when that would be clumsy. */
  accessibilityLabel?: string;
}

export function StatCell({
  label,
  value,
  hint,
  valueColor,
  size = 'md',
  align = 'left',
  style,
  accessibilityLabel,
}: StatCellProps) {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);

  return (
    <View
      style={[styles.cell, align === 'right' && styles.right, style]}
      accessible
      accessibilityLabel={accessibilityLabel ?? [label, value, hint].filter(Boolean).join(', ')}
    >
      <Text style={styles.label} numberOfLines={1}>
        {label}
      </Text>
      <Text
        style={[styles.value, { fontSize: VALUE_SIZE[size] }, valueColor ? { color: valueColor } : null]}
        numberOfLines={1}
        adjustsFontSizeToFit={size === 'lg'}
        minimumFontScale={0.8}
      >
        {value}
      </Text>
      {hint ? (
        <Text style={styles.hint} numberOfLines={2}>
          {hint}
        </Text>
      ) : null}
    </View>
  );
}

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    cell: { minWidth: 0, gap: 2 },
    right: { alignItems: 'flex-end' },
    label: { ...TYPE.kicker, color: t.ink3 ?? t.textMuted },
    value: { ...font(800), ...TABULAR, letterSpacing: -0.4, color: t.textPrimary },
    hint: { ...TYPE.helper, fontSize: 12, color: t.ink2 ?? t.textSecondary, marginTop: sh.space[0] / 2 },
  });
