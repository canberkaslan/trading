/**
 * Portfolio concentration / sector-allocation presentation helpers — pure,
 * unit-tested, React/RN-free so they ship over-the-air with no native dep.
 *
 * Two sources feed the Portfolio risk card:
 *  - GET /v1/portfolio/concentration (HHI, effective_n, top-weight flags) —
 *    already computed server-side, we only tone + label it here.
 *  - The snapshot positions, from which we derive the sector split locally
 *    (Position.sector is a static GICS map on the backend; cash is the
 *    remainder up to total equity). Weights are relative to total equity so
 *    the sector slices + cash sum to 100% and agree with gross_exposure_pct.
 */

import type { Position } from '@/api/types';

export type Tone = 'up' | 'warning' | 'down';

const UNKNOWN_SECTOR_TR = 'Bilinmiyor';

/** Short TR labels for the 11 GICS sectors; unknown/unmapped fall back. */
const SECTOR_LABELS_TR: Record<string, string> = {
  'Information Technology': 'Teknoloji',
  'Financials': 'Finans',
  'Communication Services': 'İletişim',
  'Consumer Discretionary': 'Tüketici (Döngüsel)',
  'Consumer Staples': 'Tüketici (Temel)',
  'Health Care': 'Sağlık',
  'Energy': 'Enerji',
  'Industrials': 'Sanayi',
  'Materials': 'Malzeme',
  'Utilities': 'Kamu Hizmetleri',
  'Real Estate': 'Gayrimenkul',
};

/** GICS sector → short TR label. null / unmapped → "Bilinmiyor". */
export function sectorLabelTr(sector: string | null | undefined): string {
  if (!sector) return UNKNOWN_SECTOR_TR;
  return SECTOR_LABELS_TR[sector] ?? sector;
}

export interface SectorSlice {
  /** TR display label. */
  label: string;
  /** USD market value of the sector's positions. */
  value: number;
  /** Share of total equity, 0–100. */
  weightPct: number;
}

/**
 * Group snapshot positions into sector slices weighted by market value
 * (quantity × current price) as a share of total equity, sorted heaviest
 * first. Positions with a null sector collapse into "Bilinmiyor". Returns an
 * empty array when there are no positions or equity is non-positive, so the
 * caller renders an empty state instead of NaN weights.
 */
export function sectorAllocation(
  positions: Position[],
  totalEquityUsd: number,
): SectorSlice[] {
  if (!positions.length || !(totalEquityUsd > 0)) return [];

  const bySector = new Map<string, number>();
  for (const p of positions) {
    const value = p.quantity * p.current_price;
    if (!Number.isFinite(value) || value <= 0) continue;
    const label = sectorLabelTr(p.sector);
    bySector.set(label, (bySector.get(label) ?? 0) + value);
  }

  return Array.from(bySector.entries())
    .map(([label, value]) => ({
      label,
      value,
      weightPct: (value / totalEquityUsd) * 100,
    }))
    .sort((a, b) => b.weightPct - a.weightPct);
}

/**
 * Tone a single-name top weight against the 10%-per-name soft cap the backend
 * flags on: clear (≤ cap) is calm, a small breach warns, a large one (≥ 1.5×
 * cap) reads as a real concentration risk.
 */
export function topWeightTone(topWeightPct: number, capPct = 10): Tone {
  if (!Number.isFinite(topWeightPct) || topWeightPct <= capPct) return 'up';
  if (topWeightPct >= capPct * 1.5) return 'down';
  return 'warning';
}

/**
 * Diversification read from the HHI-derived effective number of names: fewer
 * than ~5 effective names is concentrated, ~5–8 is moderate, more is
 * diversified. effective_n comes from the backend (1 / Σ wᵢ²).
 */
export function diversificationLabel(effectiveN: number): {
  label: string;
  tone: Tone;
} {
  if (!Number.isFinite(effectiveN) || effectiveN <= 0)
    return { label: '—', tone: 'warning' };
  if (effectiveN < 5) return { label: 'Yoğunlaşmış', tone: 'down' };
  if (effectiveN < 8) return { label: 'Orta', tone: 'warning' };
  return { label: 'Dağıtılmış', tone: 'up' };
}
