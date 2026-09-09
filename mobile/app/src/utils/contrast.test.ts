import { describe, it, expect } from '@jest/globals';

import { readFileSync, readdirSync, statSync } from 'fs';
import { join } from 'path';

import { contrastRatio, luminance, meetsAA } from './contrast';
import { colors, modernist } from '../theme/colors';

/** Every .tsx the app actually ships, so the guard sees screens and not just tokens. */
function tsxFiles(): string[] {
  const walk = (dir: string): string[] =>
    readdirSync(dir).flatMap((entry) => {
      const full = join(dir, entry);
      return statSync(full).isDirectory()
        ? walk(full)
        : full.endsWith('.tsx')
          ? [full]
          : [];
    });
  return [join(__dirname, '..', '..', 'app'), join(__dirname, '..', 'components')].flatMap(walk);
}

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

describe('screens do not reintroduce off-token colours', () => {
  // The token suite above only sees colors.ts, so a screen that writes a literal
  // is invisible to it. That is what happened: seven screens styled the legal
  // disclaimer `color: '#555'` — 2.66:1 — and the login screen wrote
  // `placeholderTextColor="#666"` at 3.12:1 on its input.
  //
  // The first version of this guard matched only `color: '<hex>'`, so it caught
  // the disclaimers and walked straight past the placeholder. A guard that
  // covers one spelling of the same mistake reads as coverage it does not have,
  // so it now matches any prop that ends in `color` (case-insensitive), in both
  // the `x: '#hex'` and `x="#hex"` forms.
  const BAD_HEX = /#(?:555|666|555555|666666)\b/i;
  const COLOUR_PROP = /([A-Za-z]*[Cc]olor)\s*[:=]\s*['"{]?\s*['"]?(#[0-9a-fA-F]{3,6})['"]?/g;

  it('the greys that shipped are genuine failures, not near misses', () => {
    expect(meetsAA('#555', colors.background)).toBe(false);
    expect(contrastRatio('#555', colors.background)).toBeLessThan(3);
    expect(meetsAA('#666', colors.surface)).toBe(false);
  });

  it('luminance handles the shorthand these literals are written in', () => {
    // It used to throw on three-digit hex, which is exactly how the offending
    // values are spelled — the guard could not measure what it was guarding.
    expect(luminance('#555')).toBeCloseTo(luminance('#555555'), 10);
    expect(luminance('fff')).toBeCloseTo(1, 5);
  });

  it('textMuted is a legitimate replacement on every surface', () => {
    for (const bg of [colors.background, colors.surface, colors.surfaceElevated]) {
      expect(meetsAA(colors.textMuted, bg)).toBe(true);
    }
  });

  it('no screen hard-codes one of those greys on any colour prop', () => {
    const hits: string[] = [];
    for (const file of tsxFiles()) {
      const src = readFileSync(file, 'utf8');
      for (const m of src.matchAll(COLOUR_PROP)) {
        const [, prop, hex] = m;
        if (hex && BAD_HEX.test(hex)) hits.push(`${file}: ${prop} ${hex}`);
      }
    }
    expect(hits).toEqual([]);
  });
});

describe('white-on-fill chips meet AA', () => {
  // colors.down is tuned to be read AS text on a dark ground. Inverted — white
  // text on a down-coloured fill — it only reaches 3.76:1, and the chip that
  // uses that inversion is the one announcing real-money mode.
  it('the LIVE mode chip is legible', () => {
    expect(meetsAA(colors.textPrimary, colors.dangerDeep)).toBe(true);
  });

  it('and borrowing the P&L red for it would not be', () => {
    expect(meetsAA(colors.textPrimary, colors.down)).toBe(false);
  });
});

describe('modernist palette', () => {
  // The light system inverts the ground, so every ratio has to be re-derived
  // rather than assumed to carry over. Two of its choices are deliberate and
  // pinned here so they read as decisions rather than oversights.
  const grounds = [modernist.background, modernist.surface, modernist.surfaceElevated];

  it('body ink and secondary ink clear AA on every ground', () => {
    for (const bg of grounds) {
      expect(meetsAA(modernist.textPrimary, bg)).toBe(true);
      expect(meetsAA(modernist.textSecondary, bg)).toBe(true);
    }
  });

  it('textMuted is BELOW AA — it is a rule colour, not a text colour', () => {
    // neutral-500 at 3.4:1. Kept in the palette because the design uses it for
    // hairlines and disabled marks; anything that has to be read uses
    // textSecondary. Pinned so a future screen cannot adopt it as body copy
    // believing the palette vouched for it.
    expect(meetsAA(modernist.textMuted, modernist.background)).toBe(false);
    expect(meetsAA(modernist.textSecondary, modernist.background)).toBe(true);
  });

  it('the base accent is a fill, and accent700 is what small red text uses', () => {
    expect(meetsAA(modernist.accent, modernist.background)).toBe(false);
    expect(meetsAA(modernist.accent700, modernist.background)).toBe(true);
  });

  it('the LIVE strip inverts legibly', () => {
    // The handoff specifies the base accent for this fill, which puts the page
    // ground at 3.76:1 on top — the same number the dark palette's LIVE chip
    // failed at. liveStrip is accent-700, 6.41:1, and the deviation is
    // deliberate: this strip's entire job is to be read.
    expect(meetsAA(modernist.background, modernist.liveStrip)).toBe(true);
    expect(meetsAA(modernist.background, modernist.accent)).toBe(false);
  });

  it('P&L is encoded as ink vs accent, not as two hues', () => {
    // The accounting convention: a gain is simply body text. If `up` ever stops
    // equalling the ink, the palette has quietly become conventional again.
    expect(modernist.up).toBe(modernist.textPrimary);
    expect(modernist.down).toBe(modernist.accent);
  });
});
