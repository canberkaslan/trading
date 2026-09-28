/**
 * No route may start with the web base path.
 *
 * app.config.ts sets `experiments.baseUrl` so the web build can live under a
 * sub-path. expo-router strips that base from every path it resolves, in native
 * builds too, with an unanchored regex (`stripBaseUrl` in expo-router's
 * getStateFromPath-forks: `^\/?<baseUrl>`, no segment boundary). A top-level
 * route whose first URL segment merely starts with the base's segment loses those
 * characters: under '/app', `/approve/o-42` became `rove/o-42` and matched
 * nothing. That made order approval unreachable in every native production build
 * from 2026-09-14 to 2026-09-28, and it never showed in development, because the
 * strip is skipped when NODE_ENV is development.
 *
 * Route groups like `(tabs)` do not appear in URLs, so a file inside one is a
 * top-level URL all the same. This walks app/ the way the router does.
 */
import { describe, expect, it } from '@jest/globals';
import fs from 'fs';
import path from 'path';

const ROOT = path.join(__dirname, '..', '..');
const APP_DIR = path.join(ROOT, 'app');

function baseSegment(): string | null {
  const config = fs.readFileSync(path.join(ROOT, 'app.config.ts'), 'utf8');
  const m = config.match(/baseUrl:\s*['"]\/?([^'"/]+)/);
  return m?.[1] ?? null;
}

function firstUrlSegments(dir: string, prefix: string[] = []): { file: string; first: string }[] {
  const out: { file: string; first: string }[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const name = entry.name;
    if (name.startsWith('_') || name.startsWith('+') || name.startsWith('.')) continue;
    const isGroup = /^\(.*\)$/.test(name);
    if (entry.isDirectory()) {
      out.push(...firstUrlSegments(path.join(dir, name), isGroup ? prefix : [...prefix, name]));
      continue;
    }
    if (!/\.(tsx?|jsx?)$/.test(name)) continue;
    const stem = name.replace(/\.(tsx?|jsx?)$/, '');
    const segments = stem === 'index' ? prefix : [...prefix, stem];
    const first = segments[0];
    if (first !== undefined) out.push({ file: path.relative(ROOT, path.join(dir, name)), first });
  }
  return out;
}

describe('routes and the web base path', () => {
  const base = baseSegment();

  it('reads the base path from app.config.ts', () => {
    expect(base).toBe('app');
  });

  it('has no top-level route whose first segment starts with the base segment', () => {
    const clashes = firstUrlSegments(APP_DIR).filter(r => base && r.first.startsWith(base));
    expect(clashes).toEqual([]);
  });

  it('would have caught the route that broke', () => {
    expect('approve'.startsWith(base as string)).toBe(true);
  });
});
