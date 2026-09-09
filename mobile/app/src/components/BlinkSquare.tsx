/**
 * The 10px accent square that blinks while something is running.
 *
 * `step` timing, not a fade: the handoff calls for a 0.8–1s step blink, and the
 * whole system has no eased transitions. A pulsing opacity would read as a
 * different design language than everything around it.
 */

import { View, StyleSheet } from 'react-native';
import { useEffect, useState } from 'react';

import { useTheme } from '@/theme/useTheme';

const PERIOD_MS = 900;

export function BlinkSquare({ size = 10, color }: { size?: number; color?: string }) {
  const t = useTheme();
  const [on, setOn] = useState(true);

  useEffect(() => {
    const id = setInterval(() => setOn((v) => !v), PERIOD_MS);
    return () => clearInterval(id);
  }, []);

  return (
    <View
      style={[
        styles.square,
        { width: size, height: size, backgroundColor: color ?? t.accent, opacity: on ? 1 : 0 },
      ]}
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
    />
  );
}

const styles = StyleSheet.create({ square: {} });
