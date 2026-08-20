/**
 * Money / percentage formatting — single source of truth.
 *
 * Replaces three near-identical `formatUsd` copies (orders, portfolio, approve)
 * and two `formatPct` copies that had drifted (one handled null, the others
 * crashed on it). Everything routes through here so currency/percent rendering
 * stays consistent across screens.
 */

const EM_DASH = '—';

type UsdOptions = {
  /** Prefix positive values with '+' (e.g. daily P&L deltas). Default false. */
  signed?: boolean;
};

/**
 * Format a USD amount with 2 decimals. Null/undefined render as an em dash so
 * missing fields (e.g. an order with no stop) don't show "$NaN".
 */
export function formatUsd(n: number | null | undefined, opts: UsdOptions = {}): string {
  if (n == null || Number.isNaN(n)) return EM_DASH;
  const body = `$${Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
  if (n < 0) return `-${body}`;
  return opts.signed ? `+${body}` : body;
}

/**
 * Format a fraction (0.0754 -> "7.54%"). Pass `signed` for deltas that should
 * carry an explicit '+' when positive.
 */
export function formatPct(n: number | null | undefined, opts: UsdOptions = {}): string {
  if (n == null || Number.isNaN(n)) return EM_DASH;
  const pct = (n * 100).toFixed(2);
  return opts.signed && n > 0 ? `+${pct}%` : `${pct}%`;
}

/**
 * Elapsed milliseconds -> short TR age ("az önce", "12 dk önce", "3 sa önce",
 * "9 gün önce"). One ladder, shared by every freshness label on the app, so a
 * "3 sa önce" on one card means the same thing as on another.
 *
 * A negative age (device clock ahead of the server) reads as "az önce" rather
 * than "-2 sa önce": the clock skew is ours, and a negative age on a money
 * screen looks like a data bug.
 */
export function relativeAgeTr(ms: number): string {
  if (!Number.isFinite(ms)) return EM_DASH;
  if (ms < 60_000) return 'az önce';
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 60) return `${minutes} dk önce`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} sa önce`;
  return `${Math.floor(hours / 24)} gün önce`;
}

/**
 * Parse a backend timestamp as UTC even when it carries no zone suffix.
 *
 * SQLite-backed rows come back naive (`2026-08-11T23:30:04.470376`) while
 * broker-sourced ones end in `Z`. Letting the device parse a naive value as
 * local time shifts every age by the timezone offset — in Istanbul that is 3
 * hours, enough to flip a staleness flag either way. Returns null when the
 * value is missing or unparseable, so callers say "unknown" instead of "now".
 */
export function parseUtc(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso);
  const d = new Date(hasZone ? iso : `${iso}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}
