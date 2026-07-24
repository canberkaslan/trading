import { describe, it, expect } from '@jest/globals';

import { contrastRatio, luminance, meetsAA } from './contrast';
import { colors } from '../theme/colors';

describe('contrast helpers', () => {
  it('computes known luminance anchors', () => {
    expect(luminance('#000000')).toBeCloseTo(0, 5);
    expect(luminance('#ffffff')).toBeCloseTo(1, 5);
  });

  it('gives 21:1 for black on white', () => {
    expect(contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 1);
  });

  it('is order-independent', () => {
    expect(contrastRatio('#8a8a8a', '#0a0a0a')).toBeCloseTo(
      contrastRatio('#0a0a0a', '#8a8a8a'),
      6,
    );
  });

  it('tolerates missing # and rejects garbage', () => {
    expect(() => luminance('0a0a0a')).not.toThrow();
    expect(() => luminance('nope')).toThrow();
  });

  it('flags the old muted grey as an AA failure (regression guard)', () => {
    expect(meetsAA('#666666', colors.background)).toBe(false);
  });
});

describe('theme text tokens meet WCAG AA on app backgrounds', () => {
  const backgrounds = [colors.background, colors.surface, colors.surfaceElevated];
  const textTokens: Array<[string, string]> = [
    ['textPrimary', colors.textPrimary],
    ['textSecondary', colors.textSecondary],
    ['textMuted', colors.textMuted],
  ];

  for (const bg of backgrounds) {
    for (const [name, fg] of textTokens) {
      it(`${name} on ${bg} passes AA (4.5:1)`, () => {
        expect(meetsAA(fg, bg)).toBe(true);
      });
    }
  }
});
