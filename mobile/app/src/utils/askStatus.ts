/**
 * What the operator reads while a council runs.
 *
 * The screen used to say only "ajanlar tartışıyor… (~5-10 dk)" — one fixed
 * sentence for ten minutes, with no elapsed time and no stage. A screen that
 * cannot distinguish itself from a hung one will be read as hung, and it was.
 * Measured runs take about ten minutes, so the operator waits past the stated
 * window and concludes it failed.
 *
 * Pure, and separate from the screen, so the two things that actually matter
 * here can be tested: that the elapsed count is right, and that every graph
 * node the pipeline emits has a Turkish phrase.
 */

import type { AnalyzeStatus } from '@/api/types';

const QUEUED_TR = 'sıraya alındı…';
// Measured, repeatedly: a council is about ten minutes, not five. An
// optimistic estimate is worse than none — the operator waits past it and
// concludes the thing is broken.
const RUNNING_TR = 'ajanlar tartışıyor… (~10 dk)';

/** Graph node names, as the operator would say them. */
export const PHASE_TR: Record<string, string> = {
  'Market Analyst': 'piyasa analizi',
  'Sentiment Analyst': 'duyarlılık analizi',
  'News Analyst': 'haber taraması',
  'Fundamentals Analyst': 'temel analiz',
  'Bull Researcher': 'boğa tezi',
  'Bear Researcher': 'ayı tezi',
  'Research Manager': 'araştırma sentezi',
  'Trader': 'işlem planı',
  'Aggressive Analyst': 'agresif risk görüşü',
  'Neutral Analyst': 'nötr risk görüşü',
  'Conservative Analyst': 'muhafazakâr risk görüşü',
  'Portfolio Manager': 'portföy kararı',
};

/** "4 dk 12 sn" — the number that proves the job is alive. */
export function elapsedTr(since: string | undefined, now: number): string {
  if (!since) return '';
  const ms = now - Date.parse(since);
  if (!Number.isFinite(ms) || ms < 0) return '';
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const sec = total % 60;
  return m > 0 ? `${m} dk ${sec} sn` : `${sec} sn`;
}

/**
 * What the operator reads while a council runs.
 *
 * It used to say only "ajanlar tartışıyor… (~5-10 dk)" — one fixed sentence
 * for ten minutes, with no elapsed time and no stage. A screen that cannot
 * distinguish itself from a hung one will be read as hung, and it was:
 * measured runs take about ten minutes, so the operator waits past the stated
 * window and concludes it failed.
 *
 * The elapsed count is the part that proves it is alive; the phase says which
 * of the eighteen steps is running, so the wait has a shape.
 */
export function statusLine(
  ticker: string,
  status: AnalyzeStatus | undefined,
  phase: string | null | undefined,
  since: string | undefined,
  now: number,
): string {
  if (status !== 'running') return ticker ? `${ticker}: ${QUEUED_TR}` : QUEUED_TR;
  const step = phase ? (PHASE_TR[phase] ?? phase) : null;
  const el = elapsedTr(since, now);
  const parts = [step ?? RUNNING_TR, el].filter(Boolean);
  const body = parts.join(' · ');
  return ticker ? `${ticker}: ${body}` : body;
}
