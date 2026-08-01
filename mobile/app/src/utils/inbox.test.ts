import {
  INBOX_CAP,
  badgeLabel,
  formatInboxDate,
  markAllRead,
  markRead,
  mergeInbox,
  parseInbox,
  routeForType,
  serializeInbox,
  toInboxItem,
  typeLabelTr,
  unreadCount,
  type InboxItem,
} from './inbox';

function item(over: Partial<InboxItem> = {}): InboxItem {
  return {
    id: 'n1',
    type: 'order_filled',
    title: 'AAPL alındı',
    body: '10 lot',
    receivedAt: '2026-08-01T10:00:00.000Z',
    route: '/(tabs)/portfolio',
    read: false,
    ...over,
  };
}

describe('routeForType', () => {
  it('deep-links a pending decision to its approve screen', () => {
    expect(routeForType({ type: 'decision_pending', order_id: 'o-42' })).toBe('/approve/o-42');
  });

  it('falls back to the orders tab when the decision push has no order id', () => {
    expect(routeForType({ type: 'decision_pending' })).toBe('/(tabs)/orders');
  });

  it('maps order lifecycle types to their tabs', () => {
    expect(routeForType({ type: 'order_submitted' })).toBe('/(tabs)/orders');
    expect(routeForType({ type: 'order_rejected' })).toBe('/(tabs)/orders');
    expect(routeForType({ type: 'order_filled' })).toBe('/(tabs)/portfolio');
  });

  it('returns null for unknown or missing payloads', () => {
    expect(routeForType({ type: 'something_new' })).toBeNull();
    expect(routeForType(undefined)).toBeNull();
    expect(routeForType({})).toBeNull();
  });
});

describe('toInboxItem', () => {
  it('normalizes an expo payload', () => {
    const it = toInboxItem({
      id: 'abc',
      title: '  Onay   bekliyor ',
      body: 'NVDA BUY 10',
      data: { type: 'decision_pending', order_id: 'o-7' },
      receivedAt: '2026-08-01T09:00:00.000Z',
    });
    expect(it).toEqual({
      id: 'abc',
      type: 'decision_pending',
      title: 'Onay bekliyor',
      body: 'NVDA BUY 10',
      receivedAt: '2026-08-01T09:00:00.000Z',
      route: '/approve/o-7',
      read: false,
    });
  });

  it('falls back to a Turkish type label when the push has no title', () => {
    const it = toInboxItem({ id: 'a', data: { type: 'order_filled' }, receivedAt: '2026-08-01T09:00:00.000Z' });
    expect(it.title).toBe('Emir gerçekleşti');
    expect(it.body).toBe('');
  });

  it('truncates long text so the persisted blob stays small', () => {
    const it = toInboxItem({
      id: 'a',
      title: 'x'.repeat(200),
      body: 'y'.repeat(400),
      data: { type: 'order_filled' },
      receivedAt: '2026-08-01T09:00:00.000Z',
    });
    expect(it.title.length).toBeLessThanOrEqual(60);
    expect(it.body.length).toBeLessThanOrEqual(120);
    expect(it.title.endsWith('…')).toBe(true);
  });

  it('tolerates a payload with no data at all', () => {
    const it = toInboxItem({ id: 'a', title: 'Merhaba', receivedAt: '2026-08-01T09:00:00.000Z' });
    expect(it.type).toBe('unknown');
    expect(it.route).toBeNull();
  });
});

describe('mergeInbox', () => {
  it('keeps newest first', () => {
    const older = item({ id: 'a', receivedAt: '2026-08-01T08:00:00.000Z' });
    const newer = item({ id: 'b', receivedAt: '2026-08-01T12:00:00.000Z' });
    expect(mergeInbox([older], [newer]).map((i) => i.id)).toEqual(['b', 'a']);
  });

  it('dedupes a re-delivered notification by id', () => {
    const one = item({ id: 'dup' });
    expect(mergeInbox([one], [item({ id: 'dup' })])).toHaveLength(1);
  });

  it('never flips a read row back to unread', () => {
    const stored = item({ id: 'dup', read: true });
    const redelivered = item({ id: 'dup', read: false });
    expect(mergeInbox([stored], [redelivered])[0].read).toBe(true);
  });

  it('promotes an unread row to read when the tap copy arrives', () => {
    const stored = item({ id: 'dup', read: false });
    const tapped = item({ id: 'dup', read: true });
    expect(mergeInbox([stored], [tapped])[0].read).toBe(true);
  });

  it('caps the inbox', () => {
    const many = Array.from({ length: INBOX_CAP + 10 }, (_, i) =>
      item({ id: `n${i}`, receivedAt: `2026-08-01T${String(i % 24).padStart(2, '0')}:00:00.000Z` }),
    );
    expect(mergeInbox([], many)).toHaveLength(INBOX_CAP);
  });
});

describe('unread helpers', () => {
  it('counts unread rows', () => {
    expect(unreadCount([item({ id: 'a' }), item({ id: 'b', read: true })])).toBe(1);
  });

  it('caps the badge label at 9+ and hides zero', () => {
    expect(badgeLabel(0)).toBeNull();
    expect(badgeLabel(-1)).toBeNull();
    expect(badgeLabel(NaN)).toBeNull();
    expect(badgeLabel(3)).toBe('3');
    expect(badgeLabel(42)).toBe('9+');
  });

  it('marks all read and returns the same array when nothing changed', () => {
    const alreadyRead = [item({ read: true })];
    expect(markAllRead(alreadyRead)).toBe(alreadyRead);
    expect(markAllRead([item()]).every((i) => i.read)).toBe(true);
  });

  it('marks a single row read by id', () => {
    const items = [item({ id: 'a' }), item({ id: 'b' })];
    const next = markRead(items, 'b');
    expect(next.find((i) => i.id === 'b')?.read).toBe(true);
    expect(next.find((i) => i.id === 'a')?.read).toBe(false);
  });
});

describe('serializeInbox', () => {
  it('drops the oldest rows until the payload fits the budget', () => {
    const items = Array.from({ length: 20 }, (_, i) =>
      item({ id: `n${i}`, receivedAt: `2026-08-01T${String(i).padStart(2, '0')}:00:00.000Z` }),
    );
    const json = serializeInbox(items, 600);
    const kept = JSON.parse(json) as InboxItem[];
    expect(kept.length).toBeGreaterThan(0);
    expect(kept.length).toBeLessThan(items.length);
    // Newest survive; the tail is what gets dropped.
    expect(kept[0].id).toBe('n0');
    expect(Buffer.byteLength(json, 'utf8')).toBeLessThanOrEqual(600);
  });

  it('counts Turkish characters as their real UTF-8 size', () => {
    const turkish = [item({ title: 'ĞÜŞİÖÇ'.repeat(10), body: 'ığüşöç'.repeat(10) })];
    const json = serializeInbox(turkish, 200);
    expect(Buffer.byteLength(json, 'utf8')).toBeLessThanOrEqual(200);
  });

  it('writes an empty array rather than an oversized value', () => {
    expect(serializeInbox([item()], 10)).toBe('[]');
  });
});

describe('parseInbox', () => {
  it('round-trips', () => {
    const items = [item({ id: 'a' }), item({ id: 'b', read: true })];
    expect(parseInbox(serializeInbox(items))).toEqual(items);
  });

  it('never throws on garbage storage', () => {
    expect(parseInbox(null)).toEqual([]);
    expect(parseInbox('')).toEqual([]);
    expect(parseInbox('not json')).toEqual([]);
    expect(parseInbox('{"a":1}')).toEqual([]);
    expect(parseInbox('[1,2,null]')).toEqual([]);
  });

  it('drops rows missing the identity fields and defaults the rest', () => {
    const raw = JSON.stringify([{ id: 'ok', receivedAt: '2026-08-01T00:00:00.000Z' }, { title: 'no id' }]);
    expect(parseInbox(raw)).toEqual([
      { id: 'ok', type: 'unknown', title: '', body: '', receivedAt: '2026-08-01T00:00:00.000Z', route: null, read: false },
    ]);
  });
});

describe('formatInboxDate', () => {
  const now = new Date('2026-08-01T12:00:00.000Z');

  it('renders relative Turkish ages', () => {
    expect(formatInboxDate('2026-08-01T11:59:40.000Z', now)).toBe('az önce');
    expect(formatInboxDate('2026-08-01T11:48:00.000Z', now)).toBe('12 dk');
    expect(formatInboxDate('2026-08-01T09:00:00.000Z', now)).toBe('3 sa');
    expect(formatInboxDate('2026-07-30T12:00:00.000Z', now)).toBe('2 gün');
  });

  it('falls back to a date past a week', () => {
    expect(formatInboxDate('2026-07-04T12:00:00.000Z', now)).toBe('04.07.2026');
  });

  it('handles clock skew and bad input', () => {
    expect(formatInboxDate('2026-08-01T12:05:00.000Z', now)).toBe('az önce');
    expect(formatInboxDate('nope', now)).toBe('—');
  });
});

describe('typeLabelTr', () => {
  it('labels known types and falls back for unknown ones', () => {
    expect(typeLabelTr('decision_pending')).toBe('Onay bekliyor');
    expect(typeLabelTr('order_rejected')).toBe('Emir reddedildi');
    expect(typeLabelTr('whatever')).toBe('Bildirim');
  });
});
