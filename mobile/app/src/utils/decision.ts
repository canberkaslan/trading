/**
 * Decision-detail presentation helpers — pure, unit-tested.
 *
 * The trade/[ticker] screen renders the latest AgentDecision in full: per-agent
 * reasoning (model / token spend / latency) and the multi-round debate
 * transcript. These helpers turn raw counters + the transcript Record into
 * TR-facing strings so the screen stays a thin View over already-fetched data
 * (item 7 detail slice, read-only, OTA-safe).
 */

const EM_DASH = '—';

/**
 * The detail route for ONE decision.
 *
 * The screen used to be reached by ticker alone and then loaded the newest
 * decision for that symbol, so tapping yesterday's NVDA row (or the order under
 * approval) showed today's rating, prices and reasoning. Every caller that holds
 * a decision id passes it; the ticker stays in the path for the header and as
 * the fallback for callers that have none.
 */
export function decisionRoute(ticker: string, decisionId?: string | null): string {
  const base = `/trade/${encodeURIComponent(ticker)}`;
  return decisionId ? `${base}?decisionId=${encodeURIComponent(decisionId)}` : base;
}

/**
 * Compact a token count for a badge (1234 -> "1.2k", 980 -> "980"). Null/NaN
 * render as an em dash so a decision logged before token accounting existed
 * doesn't show "NaN".
 */
export function formatTokens(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n) || n < 0) return EM_DASH;
  if (n < 1000) return String(Math.round(n));
  return `${(n / 1000).toFixed(1)}k`;
}

/**
 * Humanize a latency in milliseconds. Sub-second stays in ms ("850 ms"),
 * anything >= 1s flips to seconds with one decimal ("4.2 sn"). Null/NaN/neg
 * render as an em dash.
 */
export function formatLatency(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms) || ms < 0) return EM_DASH;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} sn`;
}

/**
 * A counter as the pipeline wrote it, or null when it was never measured.
 *
 * The pipeline writes every per-agent `tokens_in`, `tokens_out` and
 * `latency_ms` as 0 — it has no per-agent meter. No LLM call takes 0 ms or 0
 * tokens, so a 0 is "not measured", and rendering it ("0 ms", "Token 0")
 * presents a placeholder as a measurement.
 */
export function measured(n: number | null | undefined): number | null {
  return n != null && Number.isFinite(n) && n > 0 ? n : null;
}

/** Sum of the measured values, or null when none of them was measured. */
export function sumMeasured(values: readonly (number | null | undefined)[]): number | null {
  let total: number | null = null;
  for (const v of values) {
    const m = measured(v);
    if (m != null) total = (total ?? 0) + m;
  }
  return total;
}

interface Metered {
  tokens_in?: number | null;
  tokens_out?: number | null;
  reasoning?: readonly {
    tokens_in?: number | null;
    tokens_out?: number | null;
    latency_ms?: number | null;
  }[];
}

/**
 * Tokens one decision burned, in + out. The decision-level total is what the
 * usage callback actually metered for the whole council, so it wins; the
 * per-agent sum is the fallback for rows that carry one. Null = not measured.
 */
export function decisionTokenTotal(d: Metered): number | null {
  const top = sumMeasured([d.tokens_in, d.tokens_out]);
  if (top != null) return top;
  return sumMeasured((d.reasoning ?? []).flatMap((r) => [r.tokens_in, r.tokens_out]));
}

/** Summed per-agent latency, or null when no agent's latency was measured. */
export function decisionLatencyTotal(d: Metered): number | null {
  return sumMeasured((d.reasoning ?? []).map((r) => r.latency_ms));
}

/**
 * The "12.3k↓ / 1.1k↑ token · 4.2 sn" line under one agent's analysis, or null
 * when none of the three was measured — the line is then left out, rather than
 * printing "0↓ / 0↑ token · 0 ms" as if the agent had been timed.
 */
export function agentMetaLine(r: {
  tokens_in?: number | null;
  tokens_out?: number | null;
  latency_ms?: number | null;
}): string | null {
  const tin = measured(r.tokens_in);
  const tout = measured(r.tokens_out);
  const ms = measured(r.latency_ms);
  if (tin == null && tout == null && ms == null) return null;
  return `${formatTokens(tin)}↓ / ${formatTokens(tout)}↑ token · ${formatLatency(ms)}`;
}

export interface DebateEntry {
  role: string;
  text: string;
}

/**
 * Turn the debate_transcript Record<role, text> into an ordered, render-ready
 * list. Empty/whitespace-only entries are dropped (a role that never spoke
 * shouldn't render a blank block). Order is stable: any roles named in
 * PREFERRED_ORDER come first in that order, the rest follow in insertion order.
 */
const PREFERRED_ORDER = [
  'bull',
  'bear',
  'bull_researcher',
  'bear_researcher',
  'research_manager',
  'trader',
  'risk_manager',
  'portfolio_manager',
];

export function debateEntries(
  transcript: Record<string, string> | null | undefined,
): DebateEntry[] {
  if (!transcript) return [];
  const entries = Object.entries(transcript)
    .filter(([, text]) => typeof text === 'string' && text.trim().length > 0)
    .map(([role, text]) => ({ role, text: text.trim() }));

  return entries.sort((a, b) => {
    const ia = PREFERRED_ORDER.indexOf(a.role);
    const ib = PREFERRED_ORDER.indexOf(b.role);
    if (ia === -1 && ib === -1) return 0; // both unknown → keep insertion order
    if (ia === -1) return 1;
    if (ib === -1) return -1;
    return ia - ib;
  });
}

/**
 * Prettify a transcript role key ("bull_researcher" -> "Bull Researcher") for a
 * heading. Unknown keys are title-cased on underscores rather than hidden.
 */
export function debateRoleLabel(role: string): string {
  return role
    .split('_')
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

/**
 * "Opus · 4 ajan" chips summarising which models sat on the council.
 *
 * Lived byte-for-byte in both agents.tsx and trade/[ticker].tsx. It is a pure
 * function over `reasoning[]`, so it belongs here beside formatTokens and
 * formatLatency where it can be unit-tested — the two copies could otherwise
 * have counted differently and nothing would have caught it.
 *
 * Takes the label resolver rather than the palette so this module stays free of
 * theme imports; callers pass `(m) => modelBadge(theme, m).label`.
 */
export function councilChips(
  reasoning: { model: string }[] | null | undefined,
  labelFor: (model: string) => string,
): string[] {
  const counts = new Map<string, number>();
  for (const r of reasoning ?? []) {
    const label = labelFor(r.model);
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return Array.from(counts.entries()).map(([label, n]) => `${label} · ${n} ajan`);
}
