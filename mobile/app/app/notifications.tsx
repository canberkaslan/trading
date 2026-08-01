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
import { useEffect } from 'react';

import { EmptyState } from '@/components/EmptyState';
import { clearBadge } from '@/notifications';
import { useInboxStore } from '@/stores/notifications';
import { colors } from '@/theme/colors';
import { formatInboxDate, typeLabelTr, type InboxItem } from '@/utils/inbox';

const TONE: Record<string, string> = {
  decision_pending: colors.warning,
  order_submitted: colors.accent,
  order_filled: colors.up,
  order_rejected: colors.down,
  eval_report: colors.accent,
};

export default function NotificationsScreen() {
  const router = useRouter();
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
        <Text style={styles.subheading}>Bu cihaza gelen son {items.length ? items.length : ''} bildirim</Text>

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
                <Text style={[styles.type, { color: TONE[item.type] ?? colors.textMuted }]}>
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

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  header: { flexDirection: 'row', justifyContent: 'space-between', paddingHorizontal: 12 },
  // 44pt minimum touch target.
  headerBtn: { minHeight: 44, minWidth: 44, justifyContent: 'center', paddingHorizontal: 12 },
  headerBtnText: { color: colors.accent, fontSize: 15, fontWeight: '600' },
  headerBtnDisabled: { color: colors.textMuted },
  scroll: { padding: 24, paddingTop: 8, gap: 12 },
  heading: { color: colors.textPrimary, fontSize: 28, fontWeight: '700' },
  subheading: { color: colors.textSecondary, fontSize: 13, marginBottom: 8 },
  muted: { color: colors.textMuted, fontSize: 13 },
  card: { padding: 16, backgroundColor: colors.surface, borderRadius: 12 },
  cardFlat: { opacity: 0.75 },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  type: { fontSize: 12, fontWeight: '800', letterSpacing: 0.3 },
  time: { color: colors.textMuted, fontSize: 12 },
  title: { color: colors.textPrimary, fontSize: 16, fontWeight: '600', marginTop: 6 },
  body: { color: colors.textSecondary, fontSize: 13, marginTop: 4 },
  tapHint: { color: colors.accent, fontSize: 12, marginTop: 8 },
});
