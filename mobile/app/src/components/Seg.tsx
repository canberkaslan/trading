/**
 * `.seg` — the segmented control.
 *
 * One outlined strip with the borders collapsed (`marginRight: -1`), the
 * selected option filled. Five screens had grown their own copy of this shape
 * with three different active treatments; the handoff specifies exactly one:
 * accent fill, ground-coloured label.
 *
 * `tone` exists for the kill switch, where the selected option's fill carries
 * the meaning of the state (RUN is ink, FLATTEN is the accent) rather than
 * always being the accent.
 */

import { View, Text, Pressable, StyleSheet, type StyleProp, type ViewStyle } from 'react-native';
import { useMemo } from 'react';

import { useTheme } from '@/theme/useTheme';
import { font } from '@/theme/type';
import { MIN_TOUCH_TARGET } from '@/utils/a11y';

export interface SegOption<T extends string> {
  value: T;
  label: string;
  /** Overrides the accent fill when this option is the selected one. */
  fill?: string;
  accessibilityLabel?: string;
  accessibilityHint?: string;
}

export function Seg<T extends string>({
  options,
  value,
  onChange,
  disabled,
  block,
  style,
}: {
  options: readonly SegOption<T>[];
  value: T | undefined;
  onChange: (next: T) => void;
  disabled?: boolean;
  /** Stretch each option to fill the row. Default is intrinsic width. */
  block?: boolean;
  style?: StyleProp<ViewStyle>;
}) {
  const t = useTheme();
  const styles = useMemo(() => makeStyles(t), [t]);

  return (
    <View style={[styles.seg, style]}>
      {options.map((o) => {
        const active = o.value === value;
        const fill = o.fill ?? t.accent;
        return (
          <Pressable
            key={o.value}
            onPress={() => onChange(o.value)}
            disabled={disabled}
            style={[
              styles.opt,
              block && styles.optBlock,
              active && { backgroundColor: fill, borderColor: fill },
              disabled && styles.disabled,
            ]}
            accessibilityRole="tab"
            accessibilityLabel={o.accessibilityLabel ?? o.label}
            accessibilityHint={o.accessibilityHint}
            accessibilityState={{ selected: active, disabled: !!disabled }}
          >
            <Text style={[styles.label, active && { color: t.background }]} numberOfLines={1}>
              {o.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette) =>
  StyleSheet.create({
    seg: { flexDirection: 'row', alignSelf: 'flex-start' },
    opt: {
      paddingHorizontal: 14,
      paddingVertical: 8,
      borderWidth: 1,
      borderColor: t.divider,
      // Collapse the shared edge so the strip reads as one control.
      marginRight: -1,
      minHeight: MIN_TOUCH_TARGET,
      alignItems: 'center',
      justifyContent: 'center',
    },
    optBlock: { flex: 1 },
    disabled: { opacity: 0.45 },
    label: { color: t.textPrimary, fontSize: 13, ...font(600) },
  });
