/**
 * Persisted notification inbox.
 *
 * Backed by expo-secure-store — the only storage module already linked into the
 * native build, so this ships over-the-air. All data rules live in
 * `@/utils/inbox`; this store is just state + a write-behind cache.
 *
 * A storage failure is never fatal: the inbox degrades to memory-only for the
 * session rather than blocking the UI.
 */

import { create } from 'zustand';
import * as SecureStore from 'expo-secure-store';

import {
  markAllRead as markAllReadItems,
  markRead as markReadItem,
  mergeInbox,
  parseInbox,
  serializeInbox,
  unreadCount,
  type InboxItem,
} from '@/utils/inbox';

const STORAGE_KEY = 'notif_inbox_v1';

async function persist(items: InboxItem[]): Promise<void> {
  try {
    await SecureStore.setItemAsync(STORAGE_KEY, serializeInbox(items));
  } catch {
    // Memory-only for this session — an inbox is not worth an error dialog.
  }
}

interface InboxState {
  items: InboxItem[];
  /** False until storage has been read once; the screen shows a spinner. */
  hydrated: boolean;
  hydrate: () => Promise<void>;
  push: (item: InboxItem) => void;
  markRead: (id: string) => void;
  markAllRead: () => void;
  clear: () => void;
}

export const useInboxStore = create<InboxState>((set, get) => ({
  items: [],
  hydrated: false,

  hydrate: async () => {
    if (get().hydrated) return;
    let stored: string | null = null;
    try {
      stored = await SecureStore.getItemAsync(STORAGE_KEY);
    } catch {
      stored = null;
    }
    // Anything that arrived while storage was being read wins the merge.
    set((s) => ({ items: mergeInbox(parseInbox(stored), s.items), hydrated: true }));
  },

  push: (item) => {
    const items = mergeInbox(get().items, [item]);
    set({ items });
    void persist(items);
  },

  markRead: (id) => {
    const items = markReadItem(get().items, id);
    set({ items });
    void persist(items);
  },

  markAllRead: () => {
    const items = markAllReadItems(get().items);
    set({ items });
    void persist(items);
  },

  clear: () => {
    set({ items: [] });
    void persist([]);
  },
}));

/** Unread count for the header bell. */
export function useUnreadCount(): number {
  return useInboxStore((s) => unreadCount(s.items));
}
