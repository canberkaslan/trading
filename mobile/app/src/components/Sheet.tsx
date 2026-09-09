/**
 * The bottom sheet the approve / reject / cancel flows are specified to use.
 *
 * They used `Alert.alert` — OS chrome, rounded, in the system font, and on iOS
 * it cannot show a spinner, which is why the "Doğrulanıyor…" step of the
 * approval flow simply did not exist. A sheet is also the only surface in the
 * system allowed to cast a shadow (`0 12px 32px rgba(45,43,43,.22)`), which is
 * what tells the operator the screen behind it is no longer live.
 *
 * Deliberately built on React Native's own `Modal`: it is part of core, so this
 * ships over-the-air with no native module to link.
 */

import { View, Text, Modal, Pressable, StyleSheet, ActivityIndicator } from 'react-native';
import { useMemo, type ReactNode } from 'react';

import { useTheme } from '@/theme/useTheme';
import { font } from '@/theme/type';
import { MIN_TOUCH_TARGET } from '@/utils/a11y';

export interface SheetAction {
  label: string;
  onPress: () => void;
  /** Ink fill. One per sheet — the thing the sheet is asking you to decide. */
  primary?: boolean;
  /** Accent fill, for the irreversible one (Reddet, İptal et, FLATTEN). */
  destructive?: boolean;
  disabled?: boolean;
  accessibilityHint?: string;
}

export function Sheet({
  visible,
  title,
  message,
  busy,
  busyLabel,
  actions,
  onDismiss,
  children,
}: {
  visible: boolean;
  title: string;
  message?: string;
  /** Replaces the actions with a spinner while an action is in flight. */
  busy?: boolean;
  busyLabel?: string;
  actions?: SheetAction[];
  onDismiss?: () => void;
  children?: ReactNode;
}) {
  const t = useTheme();
  const styles = useMemo(() => makeStyles(t), [t]);

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onDismiss} statusBarTranslucent>
      {/* The scrim is dismiss-on-tap only when the sheet has a way out. A sheet
          mid-verification must not be dismissible by a stray tap. */}
      <Pressable
        style={styles.scrim}
        onPress={busy ? undefined : onDismiss}
        accessibilityRole="button"
        accessibilityLabel="Kapat"
        accessibilityElementsHidden={busy}
      />
      <View style={styles.dock} pointerEvents="box-none">
        <View style={styles.sheet} accessibilityViewIsModal accessibilityRole="alert">
          <Text style={styles.title}>{title}</Text>
          {message ? <Text style={styles.message}>{message}</Text> : null}
          {children}

          {busy ? (
            <View style={styles.busy}>
              <ActivityIndicator size="small" color={t.textPrimary} />
              <Text style={styles.busyLabel}>{busyLabel ?? 'Gönderiliyor…'}</Text>
            </View>
          ) : (
            <View style={styles.actions}>
              {(actions ?? []).map((a) => (
                <Pressable
                  key={a.label}
                  onPress={a.onPress}
                  disabled={a.disabled}
                  style={[
                    styles.btn,
                    a.primary && { backgroundColor: t.textPrimary, borderColor: t.textPrimary },
                    a.destructive && { backgroundColor: t.accent, borderColor: t.accent },
                    a.disabled && styles.btnDisabled,
                  ]}
                  accessibilityRole="button"
                  accessibilityLabel={a.label}
                  accessibilityHint={a.accessibilityHint}
                  accessibilityState={{ disabled: !!a.disabled }}
                >
                  <Text style={[styles.btnLabel, (a.primary || a.destructive) && { color: t.background }]}>
                    {a.label}
                  </Text>
                </Pressable>
              ))}
            </View>
          )}
        </View>
      </View>
    </Modal>
  );
}

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette) =>
  StyleSheet.create({
    scrim: { ...StyleSheet.absoluteFillObject, backgroundColor: 'rgba(32,30,29,0.45)' },
    dock: { flex: 1, justifyContent: 'flex-end' },
    sheet: {
      backgroundColor: t.surfaceElevated,
      borderTopWidth: 2,
      borderTopColor: t.divider,
      paddingHorizontal: 16,
      paddingTop: 20,
      paddingBottom: 32,
      gap: 10,
      // The system's only elevation, and only here.
      shadowColor: t.shadowColor,
      shadowOpacity: 0.22,
      shadowRadius: 32,
      shadowOffset: { width: 0, height: 12 },
      elevation: 12,
    },
    title: { color: t.textPrimary, fontSize: 18, ...font(800) },
    message: { color: t.textSecondary, fontSize: 13, lineHeight: 19 },
    actions: { flexDirection: 'row', gap: 8, marginTop: 8 },
    btn: {
      flex: 1,
      height: 48,
      borderWidth: 1,
      borderColor: t.divider,
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: MIN_TOUCH_TARGET,
    },
    btnDisabled: { opacity: 0.45 },
    btnLabel: { color: t.textPrimary, fontSize: 14, ...font(800) },
    busy: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 12, minHeight: 48 },
    busyLabel: { color: t.textSecondary, fontSize: 13, ...font(600) },
  });
