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
    <View style={[styles.seg, block && styles.segBlock, style]}>
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
              block && styles.optBlockPadding,
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
    /*
     * `block` used to set flex:1 on the options while the container kept
     * alignSelf:'flex-start', so the row never grew — the options divided an
     * intrinsic width instead of the screen, and the longer label ("Onay
     * bekleyen · 0") truncated to "Onay bekley…" while the short one had room
     * to spare. The container has to stretch for flex:1 to mean anything.
     */
    segBlock: { alignSelf: 'stretch' },
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
    // Half a phone width is not much for "Onay bekleyen · 12"; the fixed
    // padding is what pushes it over. Blocked options are already as wide as
    // they will get, so they do not need it.
    optBlockPadding: { paddingHorizontal: 6 },
    disabled: { opacity: 0.45 },
    label: { color: t.textPrimary, fontSize: 13, ...font(600) },
  });
