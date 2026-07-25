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
