/**
 * Font loading, kept out of the layout file so the map of faces sits next to
 * `FONT_FAMILIES` rather than in a component.
 *
 * Only the three ramp weights are loaded. The package ships nine plus italics;
 * shipping the ones the design does not use would add ~700KB to every OTA
 * bundle for faces nothing can select.
 */

import {
  Archivo_400Regular,
  Archivo_600SemiBold,
  Archivo_800ExtraBold,
} from '@expo-google-fonts/archivo';

export const FONT_ASSETS = {
  Archivo_400Regular,
  Archivo_600SemiBold,
  Archivo_800ExtraBold,
} as const;
