/**
 * Order-flow ("did the agent still have a choice?") presentation helpers —
 * pure, unit-tested, RN-free.
 *
 * Why this card exists: every performance number the app shows is computed
 * from equity, and a fully-invested basket that no longer passes its own
 * sizing caps still has an equity curve. So "the strategy is working" and
 * "the strategy is frozen" looked identical on the scorecard — the GO badge
 * kept rendering while nothing reached the broker for days.
 *
 * GET /v1/diagnostics/actionability answers the missing question from the
 * order rows, and these helpers turn it into labels. Two rules are carried
 * over from the backend deliberately and must not be softened here:
 *  - `submitted` means the broker acknowledged the order. `risk_approved` is
 *    the agent's own note about its intentions, and counting intent as action
 *    is exactly the optimism this card exists to catch.
 *  - Zero orders in the window is 'idle' (the run may simply not have
 *    happened), never 'inert' — that would blame the strategy for a missing
 *    cron.
 */

import { parseUtc, relativeAgeTr } from './format';
import { rejectionReasonTr } from './orders';

export type ActionabilityTone = 'up' | 'warning' | 'muted';

export interface ActionabilityReportLike {
  verdict: string;
  orders: number;
  submitted: number;
  refused: number;
  by_reason: Record<string, number>;
  dominant_reason: string | null;
  inert_run_days: number;
  run_days: number;
  inert_threshold_run_days: number;
  last_submitted_at_utc: string | null;
  window_days: number;
}

export interface VerdictMeta {
  label: string;
  tone: ActionabilityTone;
}

const EM_DASH = '—';

/**
 * Verdict -> TR label + tone. An unknown verdict renders verbatim and muted:
 * a backend that grew a fourth state should show it, not be flattened into one
 * of the three we happen to know about.
 */
export function actionabilityVerdictMeta(verdict: string | null | undefined): VerdictMeta {
  switch ((verdict ?? '').toLowerCase()) {
    case 'active':
      return { label: 'Aktif', tone: 'up' };
    case 'inert':
      return { label: 'Donmuş', tone: 'warning' };
    case 'idle':
      return { label: 'Veri yok', tone: 'muted' };
    default:
      return { label: verdict ? verdict : EM_DASH, tone: 'muted' };
  }
}

/**
 * Share of produced orders that actually reached the broker, 0..1.
 * Null with no orders at all — 0/0 is not "0% submitted", it is "nothing was
 * attempted", and the two mean opposite things about the strategy.
 */
export function submitRate(
  report: Pick<ActionabilityReportLike, 'orders' | 'submitted'> | null | undefined,
): number | null {
  if (!report || report.orders <= 0) return null;
  return report.submitted / report.orders;
}

/** "6 / 234" with an em dash when the window produced nothing. */
export function submitRatioLabel(
  report: Pick<ActionabilityReportLike, 'orders' | 'submitted'> | null | undefined,
): string {
  if (!report || report.orders <= 0) return EM_DASH;
  return `${report.submitted} / ${report.orders}`;
}

export interface ReasonRow {
  /** Raw normalized key from the backend, kept for React keys + a11y. */
  reason: string;
  count: number;
  /** TR sentence, numbers preserved verbatim. */
  label: string;
  /** 0..1 bar width relative to the most frequent reason in the window. */
  share: number;
}

/**
 * Refusal reasons, most frequent first.
 *
 * `share` is relative to the TOP reason, not to `refused`: one order can be
 * refused for several reasons at once, so the counts sum to >= refused and a
 * refused-denominated share would produce bars past 100%. Ties break on the
 * reason name so the list does not reshuffle between polls.
 */
export function topReasons(
  byReason: Record<string, number> | null | undefined,
  limit = 4,
): ReasonRow[] {
  const entries = Object.entries(byReason ?? {}).filter(
    ([, count]) => Number.isFinite(count) && count > 0,
  );
  if (entries.length === 0) return [];
  entries.sort((a, b) => (b[1] - a[1]) || a[0].localeCompare(b[0]));
  const top = entries[0]?.[1] ?? 0;
  return entries.slice(0, Math.max(limit, 0)).map(([reason, count]) => ({
    reason,
    count,
    label: rejectionReasonTr(reason),
    share: top > 0 ? count / top : 0,
  }));
}

/** How long ago something last reached the broker. "hiç" = never, in window. */
export function lastSubmitLabel(
  lastSubmittedAtUtc: string | null | undefined,
  now: Date,
): string {
  if (!lastSubmittedAtUtc) return 'hiç';
  const then = parseUtc(lastSubmittedAtUtc);
  if (!then) return 'bilinmiyor';
  return relativeAgeTr(now.getTime() - then.getTime());
}

/**
 * The one-line honest reading under the card. Ordered so the sentence that
 * most changes how the scorecard should be read comes first.
 *
 * Counted in RUN days, not calendar days — the agent produces no rows at the
 * weekend, so a calendar count would report two days of inertia every Monday.
 */
export function inertiaNote(report: ActionabilityReportLike | null | undefined): string | null {
  if (!report) return null;
  if (report.verdict === 'idle') {
    return `Son ${report.window_days} günde hiç emir üretilmemiş — günlük çalışma hiç yapılmamış olabilir.`;
  }
  if (report.verdict === 'inert') {
    return (
      `${report.inert_run_days} çalışma günüdür broker'a hiçbir emir ulaşmadı ` +
      `(eşik ${report.inert_threshold_run_days}). Karne açık pozisyonların değerlemesini ölçüyor, ` +
      `yeni karar akışını değil.`
    );
  }
  return null;
}

/**
 * Short qualifier for the eval verdict badge, e.g. next to "GO".
 *
 * Returns null while the book is still acting: a badge that always carries a
 * warning teaches people to ignore the warning.
 */
export function verdictQualifier(
  report: Pick<ActionabilityReportLike, 'verdict' | 'inert_run_days'> | null | undefined,
): string | null {
  if (!report || report.verdict !== 'inert') return null;
  return `donmuş kitap · ${report.inert_run_days}g`;
}
