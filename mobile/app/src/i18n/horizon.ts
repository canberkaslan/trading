/**
 * `time_horizon` is free text written by the portfolio-manager LLM in English
 * ("6-12 months", "3–6 months", "short-term"). Shown raw, it put English into
 * the Turkish UI (VADE "6-12 months"). The shapes the model actually produces
 * are few, so they are parsed and re-rendered in the UI language; anything
 * unrecognised is still shown as written rather than hidden.
 */

export type HorizonUnit = 'day' | 'week' | 'month' | 'year';
export type HorizonTerm = 'short' | 'medium' | 'long';

export type ParsedHorizon =
  | { kind: 'range'; from: number; to: number | null; unit: HorizonUnit }
  | { kind: 'term'; term: HorizonTerm };

const UNIT_RE = /^(\d+(?:\.\d+)?)(?:\s*(?:-|–|—|to)\s*(\d+(?:\.\d+)?))?\+?\s*(day|week|month|year)s?$/i;
const TERM_RE = /^(short|medium|mid|long)(?:[\s-]*term)?$/i;

export function parseHorizon(raw: string | null | undefined): ParsedHorizon | null {
  if (!raw) return null;
  const s = raw.trim().replace(/\.$/, '');
  const m = UNIT_RE.exec(s);
  if (m) {
    return {
      kind: 'range',
      from: Number(m[1]),
      to: m[2] != null ? Number(m[2]) : null,
      unit: m[3]!.toLowerCase() as HorizonUnit,
    };
  }
  const t = TERM_RE.exec(s);
  if (t) {
    const w = t[1]!.toLowerCase();
    return { kind: 'term', term: w === 'mid' ? 'medium' : (w as HorizonTerm) };
  }
  return null;
}

type Translate = (key: string, options?: Record<string, unknown>) => string;

/** The horizon in the UI language, the raw text when unparseable, '—' when absent. */
export function formatHorizon(raw: string | null | undefined, t: Translate): string {
  if (!raw || !raw.trim()) return '—';
  const p = parseHorizon(raw);
  if (!p) return raw.trim();
  if (p.kind === 'term') return t(`horizon.term.${p.term}`);
  const count = p.to ?? p.from;
  const unit = t(`horizon.unit.${p.unit}`, { count });
  return p.to != null ? `${p.from}–${p.to} ${unit}` : `${p.from} ${unit}`;
}
