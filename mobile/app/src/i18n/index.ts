import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import * as Localization from 'expo-localization';
import * as SecureStore from 'expo-secure-store';

import en from './en.json';
import tr from './tr.json';

const detected = Localization.getLocales()[0]?.languageCode ?? 'en';

// `i18n.use(...)` is i18next's documented entrypoint. The import plugin warns
// because i18next also exposes a named `use` export, but the default export's
// method is what is wanted here.
// eslint-disable-next-line import/no-named-as-default-member
i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, tr: { translation: tr } },
  lng: detected === 'tr' ? 'tr' : 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
});

const STORAGE_KEY = 'lang_v1';

export type Language = 'tr' | 'en';

/** The device language, used until the reader picks one explicitly. */
export const deviceLanguage: Language = detected === 'tr' ? 'tr' : 'en';

/** Switch language and remember it across launches. */
export async function setLanguage(lang: Language): Promise<void> {
  // eslint-disable-next-line import/no-named-as-default-member
  await i18n.changeLanguage(lang);
  try {
    await SecureStore.setItemAsync(STORAGE_KEY, lang);
  } catch {
    // Session-only; the device language wins again on next launch.
  }
}

/**
 * Restore an explicit choice. Only an explicit one is stored, so a reader who
 * never picked keeps following their device — changing the phone's language
 * still changes the app's.
 */
export async function hydrateLanguage(): Promise<void> {
  try {
    const saved = await SecureStore.getItemAsync(STORAGE_KEY);
    if (saved === 'tr' || saved === 'en') {
      // eslint-disable-next-line import/no-named-as-default-member
      await i18n.changeLanguage(saved);
    }
  } catch {
    // Keep the detected language.
  }
}

export default i18n;
