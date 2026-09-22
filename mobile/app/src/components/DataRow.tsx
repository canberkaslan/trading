/**
 * The list row, which is most of the prototype.
 *
 * Every list in Trader.dc.html — pending approvals, the risk/flow strip, the
 * menu, the order history — is the same 64pt row: an optional leading mark, a
 * title with an optional chip beside it, a 12px meta line under it, and an
 * optional right column that is a figure over a hint. Screens kept re-deriving
 * it, and they disagreed about the gap and about whether the meta line was
 * `--ink2` or `--ink3`.
 *
 * Two things it does NOT do, on purpose:
 *  - it does not decide what the row means. The chip is passed in, so the
 *    rating/status helpers stay the single source of that decision.
 *  - it does not own its divider. A row inside a clipped Card draws the rule
 *    only between rows, which only the parent knows; pass `divider` on all but
 *    the last.
 */

import { View, Text, Pressable, StyleSheet, type StyleProp, type ViewStyle } from 'react-native';
import { useMemo, type ReactNode } from 'react';
import Svg, { Path } from 'react-native-svg';

import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { TYPE, TABULAR, font } from '@/theme/type';
import { MIN_TOUCH_TARGET } from '@/utils/a11y';

type Palette = ReturnType<typeof useTheme>;

function Chevron({ color }: { color: string }) {
  return (
    <Svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round">
      <Path d="m9 18 6-6-6-6" />
    </Svg>
  );
}

export interface DataRowProps {
  /** A square mark, a dot, or an icon. Rendered as given. */
  leading?: ReactNode;
  title: string;
  /** Sits beside the title — a `<Tag>`, usually. */
  titleAfter?: ReactNode;
  /** The 12px meta line under the title. */
  subtitle?: string;
  /** Right column, top line. A figure; rendered tabular. */
  value?: string;
  /** Right column, bottom line. Age, count, unit. */
  valueHint?: string;
  valueColor?: string;
  /** Anything else for the right edge (a switch, a chip). Wins over `value`. */
  trailing?: ReactNode;
  onPress?: () => void;
  /** Draw the disclosure caret. Implied by `onPress` unless set false. */
  chevron?: boolean;
  /** Hairline along the bottom edge, for rows inside one clipped card. */
  divider?: boolean;
  style?: StyleProp<ViewStyle>;
  accessibilityLabel?: string;
  accessibilityHint?: string;
}

export function DataRow({
  leading,
  title,
  titleAfter,
  subtitle,
  value,
  valueHint,
  valueColor,
  trailing,
  onPress,
  chevron,
  divider,
  style,
  accessibilityLabel,
  accessibilityHint,
}: DataRowProps) {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const showChevron = chevron ?? !!onPress;

  const body = (
    <>
      {leading}
      <View style={styles.main}>
        <View style={styles.titleRow}>
          <Text style={styles.title} numberOfLines={1}>
            {title}
          </Text>
          {titleAfter}
        </View>
        {subtitle ? (
          <Text style={styles.subtitle} numberOfLines={2}>
            {subtitle}
          </Text>
        ) : null}
      </View>
      {trailing ??
        (value ? (
          <View style={styles.valueCol}>
            <Text style={[styles.value, valueColor ? { color: valueColor } : null]} numberOfLines={1}>
              {value}
            </Text>
            {valueHint ? (
              <Text style={styles.valueHint} numberOfLines={1}>
                {valueHint}
              </Text>
            ) : null}
          </View>
        ) : null)}
      {showChevron ? <Chevron color={t.ink3 ?? t.textMuted} /> : null}
    </>
  );

  const skin = [styles.row, divider && styles.divider, style];

  if (!onPress) return <View style={skin}>{body}</View>;

  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [skin, pressed && styles.pressed]}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? title}
      accessibilityHint={accessibilityHint}
    >
      {body}
    </Pressable>
  );
}

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    row: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: sh.space[2],
      paddingHorizontal: sh.space[3],
      paddingVertical: sh.space[2],
      // The prototype's rows are 64px; 44 is the floor the platform asks for
      // and the one that survives a row with no subtitle.
      minHeight: MIN_TOUCH_TARGET,
    },
    divider: { borderBottomWidth: sh.hairline, borderBottomColor: t.line ?? t.divider },
    pressed: { backgroundColor: t.surface2 ?? t.surfaceElevated },
    main: { flex: 1, minWidth: 0 },
    titleRow: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1], flexShrink: 1 },
    title: { ...TYPE.bodyStrong, fontSize: 15, color: t.textPrimary, flexShrink: 1 },
    subtitle: { ...TYPE.helper, fontSize: 12, ...TABULAR, color: t.ink2 ?? t.textSecondary, marginTop: 2 },
    valueCol: { alignItems: 'flex-end' },
    value: { ...TYPE.bodyStrong, fontSize: 14, ...TABULAR, color: t.textPrimary },
    valueHint: { ...TYPE.helper, ...font(400), color: t.ink3 ?? t.textMuted, marginTop: 1 },
  });
