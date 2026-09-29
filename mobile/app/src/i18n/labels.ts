/**
 * Backend enums → UI copy. The API speaks English identifiers; showing them
 * raw is how English leaked into the Turkish UI.
 */
type Translate = (key: string, options?: Record<string, unknown>) => string;

/** "Underweight" → "Ağırlık azalt" / "Underweight"; unknown values pass through. */
export function ratingLabel(t: Translate, rating: string): string {
  return t(`rating.${rating}`, { defaultValue: rating });
}

/**
 * A debate-transcript role as a kicker. Known roles are translated; an unknown
 * one is title-cased from its key and flagged English, so the caller cases it
 * as English ("SENTIMENT", never "SENTİMENT" on a Turkish UI).
 */
export function debateRoleText(
  t: Translate,
  exists: (key: string) => boolean,
  role: string,
  fallback: (role: string) => string,
): { label: string; lang?: 'en' } {
  const key = `debateRole.${role}`;
  return exists(key) ? { label: t(key) } : { label: fallback(role), lang: 'en' };
}
