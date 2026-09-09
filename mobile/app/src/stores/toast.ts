/**
 * Toast state.
 *
 * A store rather than a context so a mutation callback can raise one without
 * the screen threading a prop down to wherever the mutation lives — the reject
 * and cancel flows both fire from inside `onSuccess`, two components deep.
 *
 * Deliberately single-slot: the handoff specifies one toast, 2.6s, above the
 * tab bar. A queue would let two outcomes stack up and be read as one.
 */

import { create } from 'zustand';

/** 2.6 seconds, per the handoff. */
export const TOAST_MS = 2600;

interface ToastState {
  message: string | null;
  /** Bumped on every show so the renderer can restart its timer on a repeat. */
  seq: number;
  show: (message: string) => void;
  hide: () => void;
}

export const useToastStore = create<ToastState>((set, get) => ({
  message: null,
  seq: 0,
  show: (message) => set({ message, seq: get().seq + 1 }),
  hide: () => set({ message: null }),
}));

/** Raise a toast from outside a component (mutation callbacks, helpers). */
export function toast(message: string): void {
  useToastStore.getState().show(message);
}
