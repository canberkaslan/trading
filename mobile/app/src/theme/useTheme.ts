/**
 * Which palette a screen draws with.
 *
 * A zustand store rather than React context: the palette is read inside
 * StyleSheet factories and helper functions that are not always under a
 * provider, and a store can be read from either place with the same call.
 *
 * Migration is per screen. Screens that still `import { colors }` get the dark
 * palette regardless of what is selected here — they are simply not migrated
 * yet, and that is visible rather than silently half-themed.
 */

import { create } from 'zustand';

import { type Colors, type ThemeName, themes } from './colors';

interface ThemeState {
  name: ThemeName;
  setTheme: (name: ThemeName) => void;
}

const useThemeStore = create<ThemeState>((set) => ({
  // Modernist is the design the handoff specifies, so it is what the migrated
  // screens open in. Flipping this back to 'dark' should leave every migrated
  // screen coherent — if it does not, a hard-coded colour has crept in.
  name: 'modernist',
  setTheme: (name) => set({ name }),
}));

/** The active palette. */
export function useTheme(): Colors & Record<string, string> {
  const name = useThemeStore((s) => s.name);
  return themes[name] as Colors & Record<string, string>;
}

export function useThemeName(): ThemeName {
  return useThemeStore((s) => s.name);
}

export function useSetTheme(): (name: ThemeName) => void {
  return useThemeStore((s) => s.setTheme);
}

/** Read the palette outside a component (helpers, style factories). */
export function getTheme(): Colors & Record<string, string> {
  return themes[useThemeStore.getState().name] as Colors & Record<string, string>;
}
