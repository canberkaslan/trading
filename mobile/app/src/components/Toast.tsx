/**
 * The toast surface: ink fill, 13px, 2.6s, sitting just above the tab bar.
 *
 * Rendered once by the tab layout rather than per screen, because the outcomes
 * it reports ("reddedildi — bugün yeniden önerilmez") often land as the screen
 * that triggered them is being popped.
 *
 * No entrance animation. Both handoffs are explicit that there are no
 * transitions anywhere except the toggle knob and the status square's blink, so
 * this appears and disappears outright.
 *
 * `bottomOffset` exists because Aurora's tab bar FLOATS: it is a pill sitting
 * on the page with air under it, so a toast docked to the screen edge lands
 * behind it. The shell knows the bar's height and passes it; every other caller
 * keeps the old edge-docked behaviour.
 */

import { View, Text, StyleSheet } from 'react-native';
import { useEffect, useMemo } from 'react';

import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { font } from '@/theme/type';
import { useToastStore, TOAST_MS } from '@/stores/toast';

export function Toast({ bottomOffset = 0 }: { bottomOffset?: number }) {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const message = useToastStore((s) => s.message);
  const seq = useToastStore((s) => s.seq);
  const hide = useToastStore((s) => s.hide);

  useEffect(() => {
    if (!message) return;
    // Keyed on `seq` as well as `message`, so raising the same text twice
    // restarts the clock instead of letting the first timer close the second.
    const id = setTimeout(hide, TOAST_MS);
    return () => clearTimeout(id);
  }, [message, seq, hide]);

  if (!message) return null;

  return (
    <View style={[styles.dock, { bottom: sh.space[1] + bottomOffset }]} pointerEvents="none">
      <View style={styles.toast} accessibilityRole="alert" accessibilityLiveRegion="polite">
        <Text style={styles.text}>{message}</Text>
      </View>
    </View>
  );
}

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    dock: { position: 'absolute', left: sh.space[3], right: sh.space[3] },
    toast: {
      backgroundColor: t.textPrimary,
      paddingHorizontal: 14,
      paddingVertical: 11,
      borderRadius: sh.radius,
      alignSelf: 'center',
      maxWidth: '100%',
    },
    text: { color: t.background, fontSize: 13, lineHeight: 18, ...font(600) },
  });
