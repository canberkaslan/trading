/**
 * Notification inbox — screen 10.
 *
 * Everything the backend has pushed, kept locally: tap a row to land on the
 * same screen the push banner would have opened.
 *
 * Unread is a state the operator clears, not one the screen clears for them.
 * Opening the inbox used to call `markAllRead()` on mount, which meant the
 * accent squares were wiped in the same frame they were first drawn — the one
 * mark that says "you have not looked at this one yet" could never be seen.
 * Now a row is marked read when that row is opened, and the bulk clear is the
 * explicit "Tümünü okundu işaretle" button. The springboard badge is a
 * different fact ("something arrived while you were away"), so it still clears
 * on mount.
 *
 * All label/tone/date rules come from `@/utils/inbox` unchanged; the only
 * decision made here is which chip variant carries which kind of row.
 */

import { View, Text, StyleSheet, Pressable, ScrollView } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useEffect, useMemo, useState } from 'react';

import { EmptyState } from '@/components/EmptyState';
import { Sheet } from '@/components/Sheet';
import { Tag, type TagVariant } from '@/components/Tag';
import { clearBadge } from '@/notifications';
import { useInboxStore, useUnreadCount } from '@/stores/notifications';
import { toast } from '@/stores/toast';
import { useTheme } from '@/theme/useTheme';
import { font, TYPE } from '@/theme/type';
import { MIN_TOUCH_TARGET } from '@/utils/a11y';
import { formatInboxDate, typeLabelTr, type InboxItem } from '@/utils/inbox';

/**
 * Chip tone per payload kind. The *text* is `typeLabelTr` — this map only
 * decides which of the four `.tag` variants carries it: a row that is waiting
 * on the operator is outlined, a row reporting something that went wrong is
 * accent, everything else is just so.
 *
 * Keyed only on kinds the sender actually emits (`notifications/sender.py`:
 * decision_pending, order_submitted, order_filled, order_rejected). The
 * prototype's table also tones an `inert_alert` accent, but nothing pushes that
 * type and `typeLabelTr` has no word for it — the entry could only ever paint
 * the fallback "Bildirim" chip alarm-red, which is a louder claim than the row
 * can back up. When the sender starts emitting it, it gets a label and a tone
 * in the same change.
 */
const TAG_VARIANT: Record<string, TagVariant> = {
  decision_pending: 'outline',
  order_rejected: 'accent',
};

const tagVariant = (type: string): TagVariant => TAG_VARIANT[type] ?? 'neutral';

export default function NotificationsScreen() {
  const router = useRouter();
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);

  const items = useInboxStore((s) => s.items);
  const hydrated = useInboxStore((s) => s.hydrated);
  const markRead = useInboxStore((s) => s.markRead);
  const markAllRead = useInboxStore((s) => s.markAllRead);
  const clear = useInboxStore((s) => s.clear);
  const unread = useUnreadCount();

  const [confirmClear, setConfirmClear] = useState(false);

  // The OS badge means "push arrived while the app was closed" — seeing the
  // list answers that. The per-row unread square is a separate promise and is
  // deliberately left alone.
  useEffect(() => {
    void clearBadge();
  }, []);

  const doClear = () => {
    setConfirmClear(false);
    clear();
    toast('Yerel bildirim geçmişi temizlendi');
  };

  /** Opening a row is what marks that row read — one row, not the list. */
  const open = (item: InboxItem) => {
    if (!item.read) markRead(item.id);
    if (item.route) router.push(item.route as never);
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <Pressable
          style={styles.backBtn}
          onPress={() => router.back()}
          accessibilityRole="button"
          accessibilityLabel="Geri"
        >
          <Text style={styles.backText}>← Geri</Text>
        </Pressable>

        <View style={styles.headerActions}>
          <Pressable
            style={styles.ghostBtn}
            onPress={markAllRead}
            disabled={unread === 0}
            accessibilityRole="button"
            accessibilityLabel="Tümünü okundu işaretle"
            accessibilityState={{ disabled: unread === 0 }}
          >
            <Text style={[styles.ghostText, unread === 0 && styles.ghostDisabled]} numberOfLines={1}>
              Tümünü okundu işaretle
            </Text>
          </Pressable>
          <Pressable
            style={styles.ghostBtn}
            onPress={() => setConfirmClear(true)}
            disabled={items.length === 0}
            accessibilityRole="button"
            accessibilityLabel="Bildirim geçmişini temizle"
            accessibilityState={{ disabled: items.length === 0 }}
          >
            <Text style={[styles.ghostText, items.length === 0 && styles.ghostDisabled]} numberOfLines={1}>
              Temizle
            </Text>
          </Pressable>
        </View>
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
          // EmptyState carries its own 16px gutter, for a parent that has none.
          // This scroll already pads 16, so without the reset the empty block's
          // 2px rules would sit a gutter inside the column the list's own 2px
          // rule draws on. Same reset risk.tsx uses for the same reason.
          <View style={styles.gutterReset}>
            <EmptyState title="Henüz bildirim yok" hint="Bir emir onaya düştüğünde buraya düşer." />
          </View>
        ) : (
          <View style={styles.list}>
            {items.map((item) => (
              <Pressable
                key={item.id}
                style={styles.row}
                onPress={() => open(item)}
                accessibilityRole="button"
                accessibilityLabel={[
                  item.read ? null : 'okunmadı',
                  typeLabelTr(item.type),
                  item.title,
                  formatInboxDate(item.receivedAt),
                ]
                  .filter(Boolean)
                  .join(', ')}
                // A row whose payload maps to no screen is still pressable —
                // that press is what clears its unread square — so it says so
                // rather than announcing as a button that does nothing.
                accessibilityHint={item.route ? 'İlgili ekranı açar' : 'Okundu olarak işaretler'}
              >
                {/* 10px accent square, unread only. The cell stays either way so
                    every title starts on the same vertical line. */}
                <View style={[styles.dot, !item.read && styles.dotUnread]} />
                <View style={styles.rowBody}>
                  <View style={styles.rowHead}>
                    <Tag label={typeLabelTr(item.type)} variant={tagVariant(item.type)} />
                    <Text style={styles.time}>{formatInboxDate(item.receivedAt)}</Text>
                  </View>
                  <Text style={[styles.title, !item.read && styles.titleUnread]}>{item.title}</Text>
                  {item.body ? <Text style={styles.body}>{item.body}</Text> : null}
                  {item.route ? <Text style={styles.openHint}>Aç →</Text> : null}
                </View>
              </Pressable>
            ))}
          </View>
        )}
      </ScrollView>

      <Sheet
        visible={confirmClear}
        title="Bildirimleri temizle?"
        message="Yerel bildirim geçmişi silinir. Emirler etkilenmez."
        onDismiss={() => setConfirmClear(false)}
        actions={[
          { label: 'Vazgeç', onPress: () => setConfirmClear(false) },
          { label: 'Temizle', onPress: doClear, destructive: true },
        ]}
      />
    </SafeAreaView>
  );
}

type Palette = ReturnType<typeof useTheme>;

const DOT = 10;

const makeStyles = (t: Palette) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.background },
    // Screen padding is 20/16; the buttons carry 8 of the horizontal inset so
    // their labels still land on the 16px column.
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 8,
      paddingHorizontal: 8,
      paddingTop: 8,
    },
    backBtn: {
      minHeight: MIN_TOUCH_TARGET,
      justifyContent: 'center',
      paddingHorizontal: 8,
    },
    backText: { color: t.textPrimary, fontSize: 15, ...font(800) },
    headerActions: { flexDirection: 'row', alignItems: 'center', flexShrink: 1 },
    // `.btn-ghost` — accent text, no fill. accent-700 is the ramp step for
    // small red type; the base accent is a fill colour.
    ghostBtn: {
      minHeight: MIN_TOUCH_TARGET,
      justifyContent: 'center',
      paddingHorizontal: 8,
      flexShrink: 1,
    },
    ghostText: { color: t.accent700 ?? t.accent, fontSize: 12, ...font(600) },
    ghostDisabled: { color: t.textMuted },

    scroll: { paddingHorizontal: 16, paddingTop: 12, paddingBottom: 24 },
    heading: { color: t.textPrimary, ...TYPE.h2 },
    subheading: { color: t.textSecondary, ...TYPE.body, marginTop: 2, marginBottom: 12 },
    muted: { color: t.textSecondary, ...TYPE.body },
    gutterReset: { marginHorizontal: -16 },

    // A notification list is a log, and a log reads as one ruled column:
    // 2px where the section starts, 1px between rows.
    list: { borderTopWidth: 2, borderTopColor: t.divider },
    row: {
      flexDirection: 'row',
      alignItems: 'flex-start',
      gap: 12,
      minHeight: MIN_TOUCH_TARGET,
      paddingVertical: 12,
      borderBottomWidth: 1,
      borderBottomColor: t.divider,
    },
    dot: { width: DOT, height: DOT, marginTop: 6, backgroundColor: 'transparent' },
    dotUnread: { backgroundColor: t.accent },
    rowBody: { flex: 1 },
    rowHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 8 },
    time: { color: t.textSecondary, ...TYPE.helper },
    // Read rows sit at the 600 step, unread at 800 — the weight is the second
    // half of the unread mark, so it survives the square being missed.
    title: { color: t.textPrimary, fontSize: 15, ...font(600), marginTop: 6 },
    titleUnread: { ...font(800) },
    body: { color: t.textSecondary, ...TYPE.body, marginTop: 2, lineHeight: 18 },
    openHint: { color: t.accent700 ?? t.accent, ...TYPE.helper, ...font(600), marginTop: 6 },
  });
