import { describe, it, expect } from '@jest/globals';

import { ratingChip, ratingVariant, RATINGS } from './rating';
import { tagColors } from '../components/Tag';
import { modernist, dark } from './colors';

/**
 * `ratingVariant` and `ratingChip` are two spellings of one decision — a Tag
 * variant and a raw colour pair. They were written separately, so this pins
 * that they agree. Without it the chip and the Tag drift apart silently and a
 * Sell renders one way on Emirler and another on İzleme.
 */
describe('ratingVariant and ratingChip agree', () => {
  for (const palette of [modernist, dark]) {
    for (const rating of [...RATINGS, 'Something New']) {
      it(`${rating} matches in ${palette === modernist ? 'modernist' : 'dark'}`, () => {
        const chip = ratingChip(palette, rating);
        const tag = tagColors(palette, ratingVariant(rating));
        expect(tag.backgroundColor).toBe(chip.background);
        expect(tag.color).toBe(chip.color);
      });
    }
  }
});

describe('the rating encoding itself', () => {
  it('collapses five ratings to three filled buckets plus an outline', () => {
    expect(ratingVariant('Buy')).toBe('ink');
    expect(ratingVariant('Overweight')).toBe('ink');
    expect(ratingVariant('Hold')).toBe('neutral');
    expect(ratingVariant('Underweight')).toBe('accent');
    expect(ratingVariant('Sell')).toBe('accent');
  });

  it('outlines an unrecognised rating instead of borrowing Sell', () => {
    // The web dashboard's own fallthrough paints an unknown value red, so API
    // drift there reads as a sell signal. Pinned so this one cannot follow.
    expect(ratingVariant('Strong Buy')).toBe('outlineMuted');
    expect(ratingVariant('')).toBe('outlineMuted');
    // Muted rather than the accent outline: that outline is the warning mark,
    // so an unknown rating wearing it would turn schema drift into an alarm.
    expect(ratingVariant('Strong Buy')).not.toBe('outline');
  });

  it('a gain is never the accent — the accent means loss', () => {
    expect(ratingChip(modernist, 'Buy').background).toBe(modernist.textPrimary);
    expect(ratingChip(modernist, 'Buy').background).not.toBe(modernist.accent);
  });
});
