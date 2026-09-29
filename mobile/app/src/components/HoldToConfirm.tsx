/**
 * Press-and-hold confirmation — the design's no-device-lock approval.
 *
 * Used where the platform has no OS lock to ask (web). A tap does nothing; the
 * button has to be held for `HOLD_TO_CONFIRM_MS`, and a fill shows how far the
 * hold has got, so an approval can never be a stray click. Releasing early
 * resets it. Screen readers get the same action through `activate`, because a
 * timed gesture is not something assistive tech can perform.
 */

import { useMemo, useState } from 'react';
import { Animated, Easing, Pressable, StyleSheet, Text } from 'react-native';

import { HOLD_TO_CONFIRM_MS } from '@/auth/authPolicy';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { font } from '@/theme/type';
import { MIN_TOUCH_TARGET } from '@/utils/a11y';

export function HoldToConfirm({
  label,
  onConfirm,
  accessibilityLabel,
}: {
  label: string;
  onConfirm: () => void;
  accessibilityLabel?: string;
}) {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  // State, not a ref: the value is created once and read during render.
  const [progress] = useState(() => new Animated.Value(0));

  const start = () => {
    progress.setValue(0);
    Animated.timing(progress, {
      toValue: 1,
      duration: HOLD_TO_CONFIRM_MS,
      easing: Easing.linear,
      useNativeDriver: false,
    }).start();
  };
  const reset = () => {
    progress.stopAnimation();
    progress.setValue(0);
  };

  const width = useMemo(
    () => progress.interpolate({ inputRange: [0, 1], outputRange: ['0%', '100%'] }),
    [progress],
  );

  return (
    <Pressable
      onPressIn={start}
      onPressOut={reset}
      onLongPress={onConfirm}
      delayLongPress={HOLD_TO_CONFIRM_MS}
      style={styles.btn}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? label}
      accessibilityHint="Onaylamak için basılı tutun"
      accessibilityActions={[{ name: 'activate' }]}
      onAccessibilityAction={(e) => {
        if (e.nativeEvent.actionName === 'activate') onConfirm();
      }}
      testID="hold-to-confirm"
    >
      <Animated.View style={[styles.fill, { width }]} pointerEvents="none" />
      <Text style={styles.label}>{label}</Text>
    </Pressable>
  );
}

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    btn: {
      height: 50,
      minHeight: MIN_TOUCH_TARGET,
      marginTop: 8,
      borderRadius: sh.radius,
      backgroundColor: t.textPrimary,
      alignItems: 'center',
      justifyContent: 'center',
      overflow: 'hidden',
    },
    fill: {
      position: 'absolute',
      left: 0,
      top: 0,
      bottom: 0,
      // The page ground over the ink slab: the fill reads on every palette,
      // light or dark, without a literal colour.
      backgroundColor: t.background,
      opacity: 0.25,
    },
    label: { color: t.inkInv ?? t.background, fontSize: 14, ...font(800) },
  });
