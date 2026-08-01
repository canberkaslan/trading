/**
 * Notification inbox — pure helpers.
 *
 * Push notifications were fire-and-forget: the listeners in the root layout
 * deep-linked on tap and then the message was gone forever. Miss the banner
 * (phone in a pocket, notification swiped) and there was no way to learn a
 * decision had been pushed. This module holds the data rules for a persisted
 * inbox; the store and screen stay dumb around it.
 *
 * Design constraints:
 * - Deep-link targets MUST match the tap handler exactly, so `routeForType`
 *   is the single source of truth for both (notifications/index.ts imports it).
 * - Persistence is expo-secure-store (the only storage module already in the
 *   native build — AsyncStorage would need a rebuild and break OTA). SecureStore
 *   warns above ~2KB per value, so `serializeInbox` drops the oldest entries
 *   until the payload fits a byte budget instead of writing an oversized blob.
 */

export type InboxItem = {
  /** Expo notification identifier — dedup key (a tap re-delivers the same id). */
  id: string;
  type: string;
  title: string;
  body: string;
  /** ISO-8601 UTC. */
  receivedAt: string;
  /** In-app deep link, or null when the payload maps to no screen. */
  route: string | null;
  read: boolean;
};

/** Newest-N kept in memory; older entries fall off. */
export const INBOX_CAP = 30;
/** Serialized-payload ceiling — SecureStore warns past 2048 bytes. */
export const INBOX_BYTE_BUDGET = 1800;

const TITLE_MAX = 60;
const BODY_MAX = 120;

/**
 * Deep-link target for a push payload. Mirrors the backend's `data.type`
 * contract; unknown types return null (shown in the inbox, not tappable).
 */
export function routeForType(data: Record<string, unknown> | undefined | null): string | null {
  if (!data) return null;
  const type = typeof data.type === 'string' ? data.type : '';
  switch (type) {
    case 'decision_pending':
      return typeof data.order_id === 'string' && data.order_id ? `/approve/${data.order_id}` : '/(tabs)/orders';
    case 'order_submitted':
    case 'order_rejected':
      return '/(tabs)/orders';
    case 'order_filled':
      return '/(tabs)/portfolio';
    default:
      return null;
  }
}

/** Short Turkish label for the notification kind — the inbox row's badge. */
export function typeLabelTr(type: string): string {
  switch (type) {
    case 'decision_pending':
      return 'Onay bekliyor';
    case 'order_submitted':
      return 'Emir gönderildi';
    case 'order_filled':
      return 'Emir gerçekleşti';
    case 'order_rejected':
      return 'Emir reddedildi';
    case 'eval_report':
      return 'Eval raporu';
    default:
      return 'Bildirim';
  }
}

function trim(value: unknown, max: number): string {
  if (typeof value !== 'string') return '';
  const clean = value.replace(/\s+/g, ' ').trim();
  return clean.length > max ? `${clean.slice(0, max - 1)}…` : clean;
}

/**
 * Normalize a raw expo-notifications payload into a storable row.
 * Truncates text so the persisted blob stays inside the byte budget.
 */
export function toInboxItem(raw: {
  id: string;
  title?: string | null;
  body?: string | null;
  data?: Record<string, unknown> | null;
  receivedAt: string;
}): InboxItem {
  const data = raw.data ?? undefined;
  const type = typeof data?.type === 'string' ? data.type : 'unknown';
  return {
    id: raw.id,
    type,
    title: trim(raw.title, TITLE_MAX) || typeLabelTr(type),
    body: trim(raw.body, BODY_MAX),
    receivedAt: raw.receivedAt,
    route: routeForType(data),
    read: false,
  };
}

/**
 * Merge incoming rows into the inbox: newest first, deduped by id (an existing
 * row keeps its `read` flag so a re-delivery can't resurrect the unread badge),
 * capped.
 */
export function mergeInbox(existing: InboxItem[], incoming: InboxItem[], cap = INBOX_CAP): InboxItem[] {
  const byId = new Map<string, InboxItem>();
  for (const item of [...incoming, ...existing]) {
    const prev = byId.get(item.id);
    if (prev) {
      // Same notification seen twice — keep the first (newest) copy but never
      // flip a read row back to unread.
      if (item.read) byId.set(item.id, { ...prev, read: true });
      continue;
    }
    byId.set(item.id, item);
  }
  return [...byId.values()]
    .sort((a, b) => b.receivedAt.localeCompare(a.receivedAt))
    .slice(0, cap);
}

export function unreadCount(items: InboxItem[]): number {
  return items.reduce((n, item) => (item.read ? n : n + 1), 0);
}

/** Badge text; caps at "9+" so the pill keeps a fixed width. */
export function badgeLabel(count: number): string | null {
  if (!Number.isFinite(count) || count <= 0) return null;
  return count > 9 ? '9+' : String(count);
}

export function markAllRead(items: InboxItem[]): InboxItem[] {
  if (items.every((i) => i.read)) return items;
  return items.map((i) => (i.read ? i : { ...i, read: true }));
}

export function markRead(items: InboxItem[], id: string): InboxItem[] {
  return items.map((i) => (i.id === id && !i.read ? { ...i, read: true } : i));
}

/**
 * Serialize for SecureStore, dropping the oldest rows until the JSON fits
 * `budget` bytes. Returns "[]" if even one row is too large (never writes an
 * oversized value).
 */
export function serializeInbox(items: InboxItem[], budget = INBOX_BYTE_BUDGET): string {
  let kept = items;
  let json = JSON.stringify(kept);
  while (byteLength(json) > budget && kept.length > 0) {
    kept = kept.slice(0, kept.length - 1);
    json = JSON.stringify(kept);
  }
  return json;
}

/** UTF-8 byte count — Turkish copy is 2 bytes/char, so .length would lie. */
function byteLength(value: string): number {
  let bytes = 0;
  for (const char of value) {
    const cp = char.codePointAt(0) ?? 0;
    if (cp < 0x80) bytes += 1;
    else if (cp < 0x800) bytes += 2;
    else if (cp < 0x10000) bytes += 3;
    else bytes += 4;
  }
  return bytes;
}


/** Tolerant reader — malformed storage must never crash startup. */
export function parseInbox(raw: string | null | undefined): InboxItem[] {
  if (!raw) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  const items: InboxItem[] = [];
  for (const row of parsed) {
    if (!row || typeof row !== 'object') continue;
    const r = row as Record<string, unknown>;
    if (typeof r.id !== 'string' || typeof r.receivedAt !== 'string') continue;
    items.push({
      id: r.id,
      type: typeof r.type === 'string' ? r.type : 'unknown',
      title: typeof r.title === 'string' ? r.title : '',
      body: typeof r.body === 'string' ? r.body : '',
      receivedAt: r.receivedAt,
      route: typeof r.route === 'string' ? r.route : null,
      read: r.read === true,
    });
  }
  return items.slice(0, INBOX_CAP);
}

/** Relative Turkish timestamp: "az önce", "12 dk", "3 sa", "2 gün", then a date. */
export function formatInboxDate(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  const ms = then.getTime();
  if (!Number.isFinite(ms)) return '—';
  const diffMin = Math.floor((now.getTime() - ms) / 60_000);
  if (diffMin < 0) return 'az önce';
  if (diffMin < 1) return 'az önce';
  if (diffMin < 60) return `${diffMin} dk`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour} sa`;
  const diffDay = Math.floor(diffHour / 24);
  if (diffDay < 7) return `${diffDay} gün`;
  const dd = String(then.getUTCDate()).padStart(2, '0');
  const mm = String(then.getUTCMonth() + 1).padStart(2, '0');
  return `${dd}.${mm}.${then.getUTCFullYear()}`;
}
