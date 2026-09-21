/**
 * Radius and spacing — the half of a design system that is not colour.
 *
 * These could not go in `colors.ts`: the `Palette` contract is strings, and a
 * radius is a number. They could not be constants either, because the two
 * systems disagree about them completely. Modernist is square by rule — "every
 * radius is 0" is the first thing its handoff says — and Aurora is rounded
 * everywhere, with pill-shaped chips. A screen that hard-codes either one is
 * only correct under one palette, which is exactly the drift `useTheme` exists
 * to prevent.
 *
 * Read it the same way as the palette: `useShape()` in a component,
 * `getShape()` in a style factory.
 */

import { useThemeName } from './useTheme';
import type { ThemeName } from './colors';

export interface Shape {
  /** Cards, sheets, inputs. */
  radius: number;
  /** Chips, tags, segmented controls — fully rounded under Aurora. */
  radiusPill: number;
  /** Small marks: swatches, dots, badges. */
  radiusSmall: number;
  /** Hairline between rows. */
  hairline: number;
  /** The heavier rule between sections. */
  rule: number;
  /** 4 · 8 · 12 · 16 · 24 · 32, shared by both systems. */
  space: readonly [4, 8, 12, 16, 24, 32];
}

const SPACE = [4, 8, 12, 16, 24, 32] as const;

/** Square by rule. */
const flat: Shape = {
  radius: 0,
  radiusPill: 0,
  radiusSmall: 0,
  hairline: 1,
  rule: 2,
  space: SPACE,
};

/** Rounded everywhere; 99 is the prototype's pill. */
const rounded: Shape = {
  radius: 16,
  radiusPill: 99,
  radiusSmall: 8,
  hairline: 1,
  rule: 1,
  space: SPACE,
};

const SHAPES: Record<ThemeName, Shape> = {
  dark: rounded,
  modernist: flat,
  aurora: rounded,
};

export function useShape(): Shape {
  return SHAPES[useThemeName()];
}

export function getShape(name: ThemeName): Shape {
  return SHAPES[name];
}
