import { useTranslation } from 'react-i18next';
import type { StyleProp, ViewStyle } from 'react-native';

import type { Rating } from '@/api/types';
import { ratingVariant } from '@/theme/rating';
import { ratingLabel } from '@/i18n/labels';

import { Tag, type TagSize } from './Tag';

/**
 * The rating stamp. The API sends the rating as an English enum; this is the
 * one place it becomes UI copy, so every screen shows it in the UI language and
 * cased for that language. An unrecognised value is shown as sent, cased as the
 * English it is.
 */
export function RatingTag({
  rating,
  size = 'md',
  style,
}: {
  rating: Rating | string;
  size?: TagSize;
  style?: StyleProp<ViewStyle>;
}) {
  const { t, i18n } = useTranslation();
  const known = i18n.exists(`rating.${rating}`);
  return (
    <Tag
      label={ratingLabel(t, rating)}
      labelLang={known ? undefined : 'en'}
      variant={ratingVariant(rating)}
      size={size}
      caps
      style={style}
    />
  );
}
