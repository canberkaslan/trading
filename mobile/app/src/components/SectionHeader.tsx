/**
 * `<h2>` + count + the affordance on its right.
 *
 * The prototype repeats this exact row above every list:
 *
 *     <div style="display:flex;justify-content:space-between;align-items:baseline;margin:22px 0 8px">
 *       <h2>Onay bekleyen <span>· 3</span></h2>
 *       <button>Kuyruğu aç →</button>
 *     </div>
 *
 * The count is deliberately a separate, lighter span rather than part of the
 * title: it is data, and it changes. Baking it into the heading string makes
 * "Onay bekleyen · 0" read as a different section from "Onay bekleyen · 3".
 */

import { View, Text, Pressable, StyleSheet, type StyleProp, type ViewStyle } from 'react-native';
import { useMemo } from 'react';

import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { TYPE, TABULAR, font } from '@/theme/type';
import { hitSlopFor } from '@/utils/a11y';

type Palette = ReturnType<typeof useTheme>;

/**
 * The link's own height. 24pt of text is not a touch target; `hitSlopFor`
 * grows it to 44 without boxing the label off the heading's baseline.
 */
const ACTION_HEIGHT = 24;

export interface SectionHeaderProps {
  title: string;
  /** Rendered as "· n" beside the title, in secondary ink. */
  count?: number | string | null;
  /** The right-hand link. Rendered only with `onAction`. */
  actionLabel?: string;
  onAction?: () => void;
  actionAccessibilityLabel?: string;
  actionAccessibilityHint?: string;
  style?: StyleProp<ViewStyle>;
}

export function SectionHeader({
  title,
  count,
  actionLabel,
  onAction,
  actionAccessibilityLabel,
  actionAccessibilityHint,
  style,
}: SectionHeaderProps) {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);

  return (
    <View style={[styles.row, style]}>
      <Text style={styles.title} accessibilityRole="header">
        {title}
        {count != null ? <Text style={styles.count}>{`  · ${count}`}</Text> : null}
      </Text>
      {actionLabel && onAction ? (
        <Pressable
          onPress={onAction}
          hitSlop={hitSlopFor(ACTION_HEIGHT)}
          accessibilityRole="button"
          accessibilityLabel={actionAccessibilityLabel ?? actionLabel}
          accessibilityHint={actionAccessibilityHint}
          style={styles.action}
        >
          <Text style={styles.actionLabel}>{`${actionLabel} →`}</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    row: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'baseline',
      marginTop: sh.space[4],
      marginBottom: sh.space[1],
      gap: sh.space[1],
    },
    title: { ...TYPE.section, color: t.textPrimary, flexShrink: 1 },
    count: { ...TYPE.body, ...TABULAR, ...font(400), color: t.textMuted },
    action: { paddingVertical: 6, minHeight: ACTION_HEIGHT },
    actionLabel: { ...TYPE.body, ...font(600), color: t.brand ?? t.accent },
  });
