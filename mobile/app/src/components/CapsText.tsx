import { Text, type TextProps } from 'react-native';

import { useCaps } from '@/i18n/useCaps';

/**
 * A kicker, stamp or any other all-caps label. Casing is done in JS with the
 * string's language (see `i18n/caps.ts`) — never with `textTransform`, which
 * the platform applies with the device locale. Screen readers get the original
 * casing, so a word is not spelled out letter by letter as an acronym.
 */
export function CapsText({
  children,
  lang,
  accessibilityLabel,
  ...rest
}: Omit<TextProps, 'children'> & { children: string; lang?: string }) {
  const caps = useCaps();
  return (
    <Text accessibilityLabel={accessibilityLabel ?? children} {...rest}>
      {caps(children, lang)}
    </Text>
  );
}
