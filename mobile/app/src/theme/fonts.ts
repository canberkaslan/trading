/**
 * Font loading, kept out of the layout file so the map of faces sits next to
 * `FONT_FAMILIES` rather than in a component.
 *
 * Two families, because the two design systems do not share one: Modernist is
 * Archivo 400/600/800, Aurora is Plus Jakarta Sans on the same three steps.
 * Both are loaded at startup rather than on theme change — a face that arrives
 * after the first paint reflows every heading, and switching palettes is not a
 * moment anyone wants to watch that happen.
 *
 * Only the ramp weights are loaded. Each package ships nine plus italics;
 * shipping faces nothing can select would add ~700KB per family to every OTA
 * bundle.
 *
 * Adding a family is OTA-safe: `@expo-google-fonts/*` are asset modules and
 * `useFonts` needs only expo-font, which is already in the native build. No new
 * native module means no new binary.
 */

import {
  Archivo_400Regular,
  Archivo_600SemiBold,
  Archivo_800ExtraBold,
} from '@expo-google-fonts/archivo';
import {
  PlusJakartaSans_400Regular,
  PlusJakartaSans_600SemiBold,
  PlusJakartaSans_800ExtraBold,
} from '@expo-google-fonts/plus-jakarta-sans';

export const FONT_ASSETS = {
  Archivo_400Regular,
  Archivo_600SemiBold,
  Archivo_800ExtraBold,
  PlusJakartaSans_400Regular,
  PlusJakartaSans_600SemiBold,
  PlusJakartaSans_800ExtraBold,
} as const;
