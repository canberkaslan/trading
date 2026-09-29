import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';

import { toCaps } from './caps';

/**
 * `toCaps` bound to the active UI language — the right default for any string
 * that came out of `t()`. Pass `lang` for a string that did not, e.g. a raw
 * backend value that is always English.
 */
export function useCaps(): (text: string, lang?: string) => string {
  const { i18n } = useTranslation();
  const uiLang = i18n.language ?? 'en';
  return useCallback((text: string, lang?: string) => toCaps(text, lang ?? uiLang), [uiLang]);
}
