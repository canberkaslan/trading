/**
 * Notification inbox.
 *
 * Everything the backend has pushed, kept locally: tap a row to land on the
 * same screen the push banner would have opened. Opening the inbox clears the
 * springboard badge and marks the list read.
 */

import { View, Text, StyleSheet, Pressable, ScrollView, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useEffect, useMemo } from 'react';

import { EmptyState } from '@/components/EmptyState';
import { clearBadge } from '@/notifications';
import { useInboxStore } from '@/stores/notifications';
import { useTheme } from '@/theme/useTheme';
import { formatInboxDate, typeLabelTr, type InboxItem } from '@/utils/inbox';
import { font } from '@/theme/type';

// Palette-bound: under Modernist a filled order is ink, not green, and only
// the two states that need a response carry colour.
const tone = (t: Palette): Record<string, string> => ({
  decision_pending: t.warning,
  order_submitted: t.textSecondary,
  order_filled: t.textPrimary,
  order_rejected: t.accent700 ?? t.down,
  eval_report: t.textSecondary,
});

export default function NotificationsScreen() {
  const router = useRouter();
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);
  const TONE = useMemo(() => tone(theme), [theme]);
  const items = useInboxStore((s) => s.items);
  const hydrated = useInboxStore((s) => s.hydrated);
  const markAllRead = useInboxStore((s) => s.markAllRead);
  const clear = useInboxStore((s) => s.clear);

  useEffect(() => {
    markAllRead();
    void clearBadge();
  }, [markAllRead]);

  const confirmClear = () => {
    if (items.length === 0) return;
    Alert.alert('Bildirimleri temizle?', 'Yerel bildirim geçmişi silinir. Emirler etkilenmez.', [
      { text: 'Vazgeç', style: 'cancel' },
      { text: 'Temizle', style: 'destructive', onPress: clear },
    ]);
  };

  const open = (item: InboxItem) => {
    if (!item.route) return;
    router.push(item.route as never);
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <Pressable
          style={styles.headerBtn}
          onPress={() => router.back()}
          accessibilityRole="button"
          accessibilityLabel="Geri"
        >
          <Text style={styles.headerBtnText}>← Geri</Text>
        </Pressable>
        <Pressable
          style={styles.headerBtn}
          onPress={confirmClear}
          accessibilityRole="button"
          accessibilityLabel="Bildirim geçmişini temizle"
        >
          <Text style={[styles.headerBtnText, items.length === 0 && styles.headerBtnDisabled]}>Temizle</Text>
        </Pressable>
      </View>

      <ScrollView contentContainerStyle={styles.scroll}>
        <Text style={styles.heading}>Bildirimler</Text>
        {/* The count was interpolated as an empty string when the inbox is
            empty, leaving "son  bildirim" with a double space and no number. */}
        <Text style={styles.subheading}>
          {items.length ? `Bu cihaza gelen son ${items.length} bildirim` : 'Bu cihaza gelen bildirimler'}
        </Text>

        {!hydrated ? (
          <Text style={styles.muted}>Yükleniyor…</Text>
        ) : items.length === 0 ? (
          <EmptyState
            title="Henüz bildirim yok"
            hint="Bir emir onaya düştüğünde ya da gerçekleştiğinde buraya düşer."
          />
        ) : (
          items.map((item) => (
            <Pressable
              key={item.id}
              style={[styles.card, !item.route && styles.cardFlat]}
              onPress={() => open(item)}
              disabled={!item.route}
              accessibilityRole={item.route ? 'button' : 'text'}
              accessibilityLabel={`${typeLabelTr(item.type)}: ${item.title}`}
            >
              <View style={styles.row}>
                <Text style={[styles.type, { color: TONE[item.type] ?? theme.textSecondary }]}>
                  {typeLabelTr(item.type)}
                </Text>
                <Text style={styles.time}>{formatInboxDate(item.receivedAt)}</Text>
              </View>
              <Text style={styles.title}>{item.title}</Text>
              {item.body ? <Text style={styles.body}>{item.body}</Text> : null}
              {item.route ? <Text style={styles.tapHint}>Aç →</Text> : null}
            </Pressable>
          ))
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.background },
    header: { flexDirection: 'row', justifyContent: 'space-between', paddingHorizontal: 4 },
    // 44pt minimum touch target.
    headerBtn: { minHeight: 44, minWidth: 44, justifyContent: 'center', paddingHorizontal: 12 },
    headerBtnText: { color: t.textPrimary, fontSize: 15, ...font(800) },
    headerBtnDisabled: { color: t.textSecondary },
    scroll: { paddingHorizontal: 16, paddingTop: 8, paddingBottom: 24 },
    heading: { color: t.textPrimary, fontSize: 24, ...font(800) },
    subheading: { color: t.textSecondary, fontSize: 13, marginBottom: 8 },
    muted: { color: t.textSecondary, fontSize: 13 },
    // Ruled rows: a notification list is a log, and a log reads as one column.
    card: { paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: t.divider },
    cardFlat: { opacity: 0.7 },
    row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
    type: { fontSize: 10, ...font(800), letterSpacing: 0.8, textTransform: 'uppercase' },
    time: { color: t.textSecondary, fontSize: 12 },
    title: { color: t.textPrimary, fontSize: 16, ...font(800), marginTop: 6 },
    body: { color: t.textSecondary, fontSize: 13, marginTop: 4, lineHeight: 18 },
    tapHint: { color: t.accent700 ?? t.accent, fontSize: 12, marginTop: 8, ...font(600) },
  });
