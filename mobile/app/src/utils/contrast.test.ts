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
  const textTokens: [string, string][] = [
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

describe('screens do not reintroduce off-token text colours', () => {
  // The token suite above only sees colors.ts, so a screen that writes a literal
  // is invisible to it. That is exactly what happened: seven screens styled the
  // legal disclaimer `color: '#555'` — 2.66:1 on the app background, well under
  // the bar textMuted was raised to clear, and the one piece of text on those
  // screens that most needs to be readable.
  const OFFENDERS = ['#555', '#555555', '#666', '#666666'];

  it('the disclaimer grey that shipped is genuinely a failure, not a near miss', () => {
    expect(meetsAA('#555555', colors.background)).toBe(false);
    expect(contrastRatio('#555555', colors.background)).toBeLessThan(3);
  });

  it('textMuted is a legitimate replacement for it', () => {
    expect(meetsAA(colors.textMuted, colors.background)).toBe(true);
    expect(meetsAA(colors.textMuted, colors.surface)).toBe(true);
    expect(meetsAA(colors.textMuted, colors.surfaceElevated)).toBe(true);
  });

  it('no screen hard-codes one of those greys as a text colour', () => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { readFileSync, readdirSync, statSync } = require('fs');
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { join } = require('path');

    const walk = (dir: string): string[] =>
      readdirSync(dir).flatMap((entry: string) => {
        const full = join(dir, entry);
        if (statSync(full).isDirectory()) return walk(full);
        return full.endsWith('.tsx') ? [full] : [];
      });

    const roots = [join(__dirname, '..', '..', 'app'), join(__dirname, '..', 'components')];
    const hits: string[] = [];
    for (const root of roots) {
      for (const file of walk(root)) {
        const src = readFileSync(file, 'utf8');
        for (const bad of OFFENDERS) {
          if (src.includes(`color: '${bad}'`)) hits.push(`${file}: color: '${bad}'`);
        }
      }
    }
    expect(hits).toEqual([]);
  });
});
