/**
 * Static guards for the two ways mixed-language / mis-cased copy shipped:
 *
 *  1. `textTransform: 'uppercase'` — the platform cases with the DEVICE
 *     locale, so "Giriş" became "GIRIŞ" on an English phone and "Underweight"
 *     became "UNDERWEİGHT" on a Turkish one. All-caps copy goes through
 *     `CapsText` / `useCaps` / `Tag caps`, which case in JS with the string's
 *     own language.
 *  2. Hard-coded strings in caps positions and keys missing from a catalogue,
 *     which is how a Turkish label reached the English UI and vice versa.
 */
import { describe, it, expect } from '@jest/globals';
import * as fs from 'fs';
import * as path from 'path';

import en from './en.json';
import tr from './tr.json';
import { RATINGS } from '@/theme/rating';

const ROOT = path.resolve(__dirname, '..', '..');

function sourceFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return sourceFiles(p);
    return /\.(ts|tsx)$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [p] : [];
  });
}

/** Comments document the rule and quote the banned style; only code counts. */
function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`])\/\/[^\n]*/g, '$1');
}

const FILES = [...sourceFiles(path.join(ROOT, 'app')), ...sourceFiles(path.join(ROOT, 'src'))].map((f) => ({
  file: path.relative(ROOT, f),
  code: stripComments(fs.readFileSync(f, 'utf8')),
}));

type Tree = { [k: string]: string | Tree };
function flatten(tree: Tree, prefix = ''): string[] {
  return Object.entries(tree).flatMap(([k, v]) =>
    typeof v === 'string' ? [`${prefix}${k}`] : flatten(v, `${prefix}${k}.`),
  );
}
const EN = new Set(flatten(en as Tree));
const TR = new Set(flatten(tr as Tree));
const hasKey = (set: Set<string>, key: string) => set.has(key) || set.has(`${key}_one`) || set.has(`${key}_other`);

describe('i18n guard', () => {
  it('scans a real tree', () => {
    expect(FILES.length).toBeGreaterThan(40);
  });

  it('never uses textTransform uppercase (it cases with the device locale)', () => {
    const offenders = FILES.filter((f) => /textTransform\s*:\s*['"]uppercase['"]/.test(f.code)).map((f) => f.file);
    expect(offenders).toEqual([]);
  });

  it('never uppercases translated copy with a locale-blind toUpperCase', () => {
    const offenders = FILES.filter((f) => /\bt[r]?\([^()]*\)\s*\.\s*toUpperCase\(/.test(f.code)).map((f) => f.file);
    expect(offenders).toEqual([]);
  });

  it('keeps en and tr catalogues in step', () => {
    expect([...EN].filter((k) => !TR.has(k))).toEqual([]);
    expect([...TR].filter((k) => !EN.has(k))).toEqual([]);
  });

  it('resolves every static key the code names', () => {
    const namespaces = Object.keys(en).join('|');
    const re = new RegExp(`['"]((?:${namespaces})\\.[A-Za-z0-9_.]+)['"]`, 'g');
    const missing: string[] = [];
    for (const f of FILES) {
      for (const m of f.code.matchAll(re)) {
        const key = m[1]!;
        if (!hasKey(EN, key) || !hasKey(TR, key)) missing.push(`${f.file}: ${key}`);
      }
    }
    expect(missing).toEqual([]);
  });

  it('translates every rating, horizon unit and term the app maps', () => {
    const needed = [
      ...RATINGS.map((r) => `rating.${r}`),
      ...['day', 'week', 'month', 'year'].map((u) => `horizon.unit.${u}`),
      ...['short', 'medium', 'long'].map((t) => `horizon.term.${t}`),
    ];
    expect(needed.filter((k) => !hasKey(EN, k) || !hasKey(TR, k))).toEqual([]);
  });

  it('has no hard-coded copy in caps positions (StatCell label, Tag caps, CapsText)', () => {
    const offenders: string[] = [];
    for (const f of FILES) {
      for (const m of f.code.matchAll(/<StatCell\b[^>]*?\blabel="[^"]*"/gs)) offenders.push(`${f.file}: ${m[0].slice(-40)}`);
      for (const m of f.code.matchAll(/<Tag\b[^>]*?\/>/gs)) {
        if (/\bcaps\b/.test(m[0]) && /\blabel=["']/.test(m[0])) offenders.push(`${f.file}: ${m[0].slice(0, 60)}`);
      }
      for (const m of f.code.matchAll(/<CapsText\b[^>]*>([^<{]*[^\s<{][^<{]*)<\/CapsText>/gs)) {
        offenders.push(`${f.file}: CapsText "${m[1]!.trim()}"`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
