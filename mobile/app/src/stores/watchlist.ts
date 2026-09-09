/**
 * The watchlist — the handoff's `watch[]`, persisted.
 *
 * Backed by expo-secure-store, which is the only storage module already linked
 * into the native build, so this ships over-the-air. The write path is the same
 * write-behind shape as `@/stores/notifications`: state changes immediately, the
 * disk write is fire-and-forget, and a storage failure degrades the list to
 * memory-only for the session rather than blocking the UI. A watchlist is never
 * worth an error dialog.
 *
 * Everything except the zustand store is a pure function, so the rules that
 * matter (what counts as a symbol, what the stored blob may contain) are
 * testable without a renderer.
 */

import { create } from 'zustand';
import * as SecureStore from 'expo-secure-store';

const STORAGE_KEY = 'watchlist_v1';

/**
 * What a first run starts with — `WATCH_DEFAULT` from the handoff's
 * `trader-core.js`, verbatim and in its order.
 *
 * It seeds ONLY when storage has never been written. A user who removes every
 * row has expressed a preference, and the empty state ("Liste boş…") is a real
 * screen in the design — re-seeding on the next launch would silently undo them.
 */
export const WATCH_DEFAULT = ['SPY', 'AVGO', 'TSLA', 'LLY', 'COST', 'CRM'] as const;

/**
 * Ceiling on the list.
 *
 * Not a storage limit — 30 symbols serialize to well under SecureStore's 2KB
 * warning threshold — and NOT a claim about the daily run: the run's universe
 * is fixed server-side (`US_UNIVERSE`), and no endpoint accepts a watchlist, so
 * this list never leaves the device. The cost it bounds is the screen's own:
 * each row opens its own `GET /v1/prices/{ticker}`, and that route proxies a
 * 5-request-per-minute upstream, so an unbounded list is an unbounded burst of
 * requests on every cold open.
 */
export const WATCH_CAP = 30;

/**
 * What the price endpoint will accept.
 *
 * `GET /v1/prices/{ticker}` answers 422 for anything that is not 1–6 ASCII
 * letters (`sym.isalpha() and len(sym) <= 6`), so accepting a dotted class like
 * `BRK.B` here would only add a row that can never show a price. The input
 * validates against the same rule the server enforces.
 */
const TICKER_PATTERN = /^[A-Z]{1,6}$/;

/**
 * Clean an operator's typing into a ticker, or reject it.
 *
 * Whitespace is forgiven; nothing else is. Stripping stray characters instead
 * would quietly turn `BRK.B` into `BRKB` — a different, real, wrong symbol —
 * so anything that is not letters is rejected and the screen says so.
 */
export function normalizeTicker(raw: string | null | undefined): string | null {
  if (typeof raw !== 'string') return null;
  const cleaned = raw.replace(/\s+/g, '').toUpperCase();
  return TICKER_PATTERN.test(cleaned) ? cleaned : null;
}

/**
 * Read the stored blob.
 *
 * Distinguishes "never written" from "written as empty": null means the seed
 * applies, `[]` means the user emptied the list on purpose. Corrupt storage is
 * treated as never-written — a garbled blob is not evidence of an intent.
 */
export function parseWatch(raw: string | null | undefined): string[] | null {
  if (raw == null) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!Array.isArray(parsed)) return null;
  return dedupe(parsed.map((v) => (typeof v === 'string' ? normalizeTicker(v) : null)));
}

export function serializeWatch(tickers: string[]): string {
  return JSON.stringify(tickers.slice(0, WATCH_CAP));
}

/** Drop nulls and repeats, keep first-seen order, apply the cap. */
function dedupe(values: (string | null)[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const v of values) {
    if (!v || seen.has(v)) continue;
    seen.add(v);
    out.push(v);
    if (out.length >= WATCH_CAP) break;
  }
  return out;
}

/**
 * Fold whatever arrived while storage was being read into what was on disk.
 * Disk order wins; anything added in the meantime lands after it.
 */
export function mergeWatch(stored: string[], pending: string[]): string[] {
  return dedupe([...stored, ...pending]);
}

export type AddOutcome = 'added' | 'duplicate' | 'invalid' | 'full';

/** The outcome plus the cleaned symbol, so the screen can name it in a toast. */
export interface AddResult {
  outcome: AddOutcome;
  ticker: string | null;
}

async function persist(tickers: string[]): Promise<void> {
  try {
    await SecureStore.setItemAsync(STORAGE_KEY, serializeWatch(tickers));
  } catch {
    // Memory-only for this session.
  }
}

interface WatchState {
  tickers: string[];
  /** False until storage has been read once; the screen holds the list back. */
  hydrated: boolean;
  hydrate: () => Promise<void>;
  add: (raw: string) => AddResult;
  remove: (ticker: string) => void;
}

/**
 * The read in flight, if any.
 *
 * `hydrated` alone only rejects a SECOND call after the first has resolved —
 * two callers on a cold start (the screen on mount, a shell that adds the
 * startup call) both see `false` and both read storage. The reads themselves
 * are harmless, but the later one would then see the seeded list as in-flight
 * state and write it to disk, turning "never written" into "written" and
 * quietly disabling the seed for every future launch. One promise, shared.
 */
let hydrating: Promise<void> | null = null;

export const useWatchStore = create<WatchState>((set, get) => ({
  tickers: [],
  hydrated: false,

  hydrate: async () => {
    if (get().hydrated) return;
    if (!hydrating) {
      hydrating = (async () => {
        let stored: string | null = null;
        try {
          stored = await SecureStore.getItemAsync(STORAGE_KEY);
        } catch {
          stored = null;
        }
        const disk = parseWatch(stored) ?? [...WATCH_DEFAULT];
        // Anything added while storage was being read has already persisted
        // itself — as a list of just that symbol, because that WAS the whole
        // list at the time. Merging it into memory is not enough: disk still
        // holds the short version, so the next launch would read one symbol
        // back and the seed (or a saved list) would be gone without anyone
        // having removed it. Write the merge back when there was something to
        // merge, and only then.
        const inFlight = get().tickers;
        const merged = mergeWatch(disk, inFlight);
        set({ tickers: merged, hydrated: true });
        if (inFlight.length > 0) void persist(merged);
      })().finally(() => {
        hydrating = null;
      });
    }
    await hydrating;
  },

  add: (raw) => {
    const ticker = normalizeTicker(raw);
    if (!ticker) return { outcome: 'invalid', ticker: null };
    const current = get().tickers;
    if (current.includes(ticker)) return { outcome: 'duplicate', ticker };
    if (current.length >= WATCH_CAP) return { outcome: 'full', ticker };
    const tickers = [...current, ticker];
    set({ tickers });
    void persist(tickers);
    return { outcome: 'added', ticker };
  },

  remove: (ticker) => {
    const current = get().tickers;
    if (!current.includes(ticker)) return;
    const tickers = current.filter((t) => t !== ticker);
    set({ tickers });
    void persist(tickers);
  },
}));

/**
 * Hydrate from outside a component.
 *
 * The screen calls `hydrate()` on mount, which is what actually runs today —
 * the shell (`app/_layout.tsx`) hydrates the inbox, the theme and the language
 * at startup but not this store. This export is what it would call to join
 * them; until it does, nothing is missing, because a concurrent or repeat call
 * shares the single in-flight read above.
 */
export function hydrateWatchlist(): Promise<void> {
  return useWatchStore.getState().hydrate();
}
