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
