// WCAG 2.1 relative-luminance + contrast-ratio helpers.
// Used to guard the dark-theme text tokens against the app backgrounds so a
// palette tweak can't silently regress legibility (AA = 4.5:1 normal text,
// 3:1 large text / non-text).

const AA_NORMAL = 4.5;
const AA_LARGE = 3.0;

function channelToLinear(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

/** Relative luminance (0..1) of a #rrggbb hex color per WCAG 2.1. */
export function luminance(hex: string): number {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m?.[1]) throw new Error(`invalid hex color: ${hex}`);
  const n = parseInt(m[1], 16);
  const r = channelToLinear((n >> 16) & 0xff);
  const g = channelToLinear((n >> 8) & 0xff);
  const b = channelToLinear(n & 0xff);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio between two hex colors (1..21), order-independent. */
export function contrastRatio(fg: string, bg: string): number {
  const l1 = luminance(fg);
  const l2 = luminance(bg);
  const [hi, lo] = l1 >= l2 ? [l1, l2] : [l2, l1];
  return (hi + 0.05) / (lo + 0.05);
}

/** True when fg-on-bg meets WCAG AA (large text relaxes 4.5:1 → 3:1). */
export function meetsAA(fg: string, bg: string, large = false): boolean {
  return contrastRatio(fg, bg) >= (large ? AA_LARGE : AA_NORMAL);
}
