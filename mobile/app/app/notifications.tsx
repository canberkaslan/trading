/**
 * Notification inbox — screen 10, ported to Aurora.
 *
 * Everything the backend has pushed, kept locally: tap a row to land on the
 * same screen the push banner would have opened.
 *
 * Unread is a state the operator clears, not one the screen clears for them.
 * Opening the inbox used to call `markAllRead()` on mount, which meant the
 * accent marks were wiped in the same frame they were first drawn — the one
 * mark that says "you have not looked at this one yet" could never be seen.
 * Now a row is marked read when that row is opened, and the bulk clear is the
 * explicit "Tümünü okundu işaretle" button. The springboard badge is a
 * different fact ("something arrived while you were away"), so it still clears
 * on mount.
 *
 * All label/tone/date rules come from `@/utils/inbox` unchanged; the only
 * decision made here is which chip variant carries which kind of row.
 *
 * Aurora shape, from the prototype's NOTIFICATIONS block: the log becomes a
 * stack of 16px cards with 8pt between them rather than one ruled column, the
 * unread mark is a brand dot rather than an accent square, and the two bulk
 * actions are outlined pills beside the title instead of ghost text.
 */

import { View, Text, StyleSheet, Pressable, ScrollView } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useEffect, useMemo, useState } from 'react';

import { Card } from '@/components/Card';
import { Sheet } from '@/components/Sheet';
import { Tag, type TagVariant } from '@/components/Tag';
import { clearBadge } from '@/notifications';
import { useInboxStore, useUnreadCount } from '@/stores/notifications';
import { toast } from '@/stores/toast';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { font, TYPE } from '@/theme/type';
import { hitSlopFor, MIN_TOUCH_TARGET } from '@/utils/a11y';
import { formatInboxDate, typeLabelTr, type InboxItem } from '@/utils/inbox';

/**
 * Chip tone per payload kind. The *text* is `typeLabelTr` — this map only
 * decides which `.tag` variant carries it. The prototype tones the chip by
 * ground rather than by outline: a row waiting on the operator is brand-soft,
 * a row reporting something that went wrong is rose-soft, everything else is
 * the neutral ground. The variants are the shared `Tag`'s, so on a palette
 * without Aurora's soft grounds they fall back to `surface` and the meaning
 * survives in the text colour alone.
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
  decision_pending: 'brand',
  order_rejected: 'down',
};

const tagVariant = (type: string): TagVariant => TAG_VARIANT[type] ?? 'neutral';

/** The prototype's pill buttons are 34pt tall; hitSlop carries them to 44. */
const PILL_HEIGHT = 34;
/** The prototype's unread dot. */
const DOT = 8;

export default function NotificationsScreen() {
  const router = useRouter();
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);

  const items = useInboxStore((s) => s.items);
  const hydrated = useInboxStore((s) => s.hydrated);
  const markRead = useInboxStore((s) => s.markRead);
  const markAllRead = useInboxStore((s) => s.markAllRead);
  const clear = useInboxStore((s) => s.clear);
  const unread = useUnreadCount();

  const [confirmClear, setConfirmClear] = useState(false);

  // The OS badge means "push arrived while the app was closed" — seeing the
  // list answers that. The per-row unread dot is a separate promise and is
  // deliberately left alone.
  useEffect(() => {
    void clearBadge();
  }, []);

  const doClear = () => {
    setConfirmClear(false);
    clear();
    toast('Yerel bildirim geçmişi temizlendi');
  };

  /**
   * The prototype confirms the bulk clear with a toast, which is the only
   * feedback a mark-all gives: the dots vanish, and on a list scrolled past the
   * first row nothing visibly happens at all.
   */
  const doMarkAllRead = () => {
    if (unread === 0) return;
    markAllRead();
    toast('Tümü okundu işaretlendi');
  };

  /** Opening a row is what marks that row read — one row, not the list. */
  const open = (item: InboxItem) => {
    if (!item.read) markRead(item.id);
    if (item.route) router.push(item.route as never);
  };

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <ScrollView contentContainerStyle={styles.content}>
        <Pressable
          style={styles.backBtn}
          onPress={() => router.back()}
          accessibilityRole="button"
          accessibilityLabel="Geri"
        >
          <Text style={styles.backText}>← Geri</Text>
        </Pressable>

        {/* Title block and the two bulk actions share one baseline row, as in
            the prototype: the actions belong to the list, not to a toolbar. */}
        <View style={styles.headRow}>
          <View style={styles.headText}>
            <Text style={styles.title} accessibilityRole="header">
              Bildirimler
            </Text>
            {/* The count was interpolated as an empty string when the inbox is
                empty, leaving "son  bildirim" with a double space and no
                number. */}
            <Text style={styles.subtitle}>
              {items.length ? `Bu cihaza gelen son ${items.length} bildirim` : 'Bu cihaza gelen bildirimler'}
            </Text>
          </View>

          <View style={styles.headActions}>
            <Pressable
              style={({ pressed }) => [
                styles.pill,
                unread === 0 && styles.pillDisabled,
                pressed && unread > 0 && styles.pillPressed,
              ]}
              hitSlop={hitSlopFor(PILL_HEIGHT)}
              onPress={doMarkAllRead}
              disabled={unread === 0}
              accessibilityRole="button"
              accessibilityLabel="Tümünü okundu işaretle"
              accessibilityState={{ disabled: unread === 0 }}
            >
              <Text style={[styles.pillText, unread === 0 && styles.pillTextDisabled]} numberOfLines={1}>
                Tümünü okundu işaretle
              </Text>
            </Pressable>

            <Pressable
              style={({ pressed }) => [
                styles.pill,
                items.length === 0 && styles.pillDisabled,
                pressed && items.length > 0 && styles.pillPressed,
              ]}
              hitSlop={hitSlopFor(PILL_HEIGHT)}
              onPress={() => setConfirmClear(true)}
              disabled={items.length === 0}
              accessibilityRole="button"
              accessibilityLabel="Bildirim geçmişini temizle"
              accessibilityState={{ disabled: items.length === 0 }}
            >
              <Text
                style={[styles.pillText, styles.pillDanger, items.length === 0 && styles.pillTextDisabled]}
                numberOfLines={1}
              >
                Temizle
              </Text>
            </Pressable>
          </View>
        </View>

        {!hydrated ? (
          // Storage is still being read. The dashed slot is the prototype's
          // empty shape, reused so the list does not jump a card's worth of
          // height when the rows arrive.
          <Card tone="dashed" style={styles.slot}>
            <Text style={styles.slotText}>Yükleniyor…</Text>
          </Card>
        ) : items.length === 0 ? (
          <Card tone="dashed" style={styles.slot}>
            <Text style={styles.slotTitle}>Henüz bildirim yok</Text>
            <Text style={styles.slotText}>Bir emir onaya düştüğünde buraya düşer.</Text>
          </Card>
        ) : (
          <View style={styles.list}>
            {items.map((item) => (
              <Card
                key={item.id}
                padded={false}
                style={styles.row}
                onPress={() => open(item)}
                accessibilityLabel={[
                  item.read ? null : 'okunmadı',
                  typeLabelTr(item.type),
                  item.title,
                  formatInboxDate(item.receivedAt),
                ]
                  .filter(Boolean)
                  .join(', ')}
                // A row whose payload maps to no screen is still pressable —
                // that press is what clears its unread dot — so it says so
                // rather than announcing as a button that does nothing.
                accessibilityHint={item.route ? 'İlgili ekranı açar' : 'Okundu olarak işaretler'}
              >
                {/* The unread dot. The cell stays either way so every chip
                    starts on the same vertical line. */}
                <View style={[styles.dot, !item.read && styles.dotUnread]} />
                <View style={styles.rowBody}>
                  <View style={styles.rowHead}>
                    <Tag label={typeLabelTr(item.type)} variant={tagVariant(item.type)} size="sm" />
                    <Text style={styles.when}>{formatInboxDate(item.receivedAt)}</Text>
                  </View>
                  <Text style={[styles.rowTitle, !item.read && styles.rowTitleUnread]}>{item.title}</Text>
                  {item.body ? <Text style={styles.rowText}>{item.body}</Text> : null}
                  {item.route ? <Text style={styles.openHint}>Aç →</Text> : null}
                </View>
              </Card>
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

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    screen: { flex: 1, backgroundColor: t.background },
    content: {
      paddingHorizontal: sh.space[3],
      paddingTop: sh.space[1],
      paddingBottom: sh.space[5],
    },

    backBtn: { minHeight: MIN_TOUCH_TARGET, justifyContent: 'center', alignSelf: 'flex-start' },
    backText: { ...TYPE.bodyStrong, fontSize: 15, ...font(800), color: t.textPrimary },

    headRow: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'flex-end',
      gap: sh.space[1],
      paddingTop: sh.space[1],
      paddingBottom: sh.space[2],
    },
    headText: { flexShrink: 1 },
    title: { ...TYPE.h2, fontSize: 26, letterSpacing: -0.5, color: t.textPrimary },
    subtitle: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, marginTop: sh.space[0] },

    headActions: { flexDirection: 'row', alignItems: 'center', gap: 6, flexShrink: 1 },
    pill: {
      height: PILL_HEIGHT,
      justifyContent: 'center',
      paddingHorizontal: sh.space[2],
      borderRadius: sh.radiusPill,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
      flexShrink: 1,
    },
    // No hover on a phone, so the prototype's border brighten lands on press.
    pillPressed: { borderColor: t.brand ?? t.accent },
    pillDisabled: { borderColor: t.line ?? t.divider },
    pillText: { fontSize: 11, ...font(800), color: t.textPrimary },
    pillDanger: { color: t.downText ?? t.down },
    pillTextDisabled: { color: t.ink3 ?? t.textMuted },

    // The empty/loading slot: dashed, never filled — an absent list should read
    // as an absence, not as a card with nothing in it.
    slot: { alignItems: 'center', paddingVertical: sh.space[4], marginTop: sh.space[1] },
    slotTitle: { ...TYPE.section, color: t.textPrimary, textAlign: 'center' },
    slotText: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, textAlign: 'center', marginTop: sh.space[0] },

    list: { gap: sh.space[1], marginTop: sh.space[1] },
    row: {
      flexDirection: 'row',
      alignItems: 'flex-start',
      gap: sh.space[2],
      minHeight: MIN_TOUCH_TARGET,
      paddingVertical: sh.space[2],
      paddingHorizontal: sh.space[3],
    },
    // Round under Aurora, square under Modernist — the same mark either way.
    dot: {
      width: DOT,
      height: DOT,
      borderRadius: sh.radiusPill,
      marginTop: 6,
      backgroundColor: 'transparent',
    },
    dotUnread: { backgroundColor: t.brand ?? t.accent },
    rowBody: { flex: 1, minWidth: 0 },
    rowHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: sh.space[1] },
    when: { ...TYPE.helper, color: t.ink3 ?? t.textMuted },
    // Read rows sit at the 600 step, unread at 800 — the weight is the second
    // half of the unread mark, so it survives the dot being missed.
    rowTitle: { fontSize: 14, ...font(600), color: t.textPrimary, marginTop: 6 },
    rowTitleUnread: { ...font(800) },
    rowText: { fontSize: 12, ...font(400), color: t.ink2 ?? t.textSecondary, marginTop: 2, lineHeight: 18 },
    openHint: { fontSize: 12, ...font(800), color: t.brand ?? t.accent, marginTop: 6 },
  });
