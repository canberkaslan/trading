/**
 * Learn-tab content, bundled rather than fetched.
 *
 * `lessons.json` is a copy of `agent/api/static/lessons.json`, which is what
 * the web dashboard reads from `/v1/learn`. The agent test suite fails if the
 * two files drift, so there is one authored source even though each app ships
 * its own copy — the mobile one has to be in the bundle so the tab still works
 * with no connectivity, which is exactly when someone has time to read it.
 */

import data from './lessons.json';

export interface LessonText {
  title: string;
  summary: string;
  body: string[];
  takeaway: string;
  quizQ: string;
  quizOptions: string[];
  /** 0-based index into quizOptions. */
  quizAnswer: number;
  quizExplain: string;
}

export interface Lesson {
  id: string;
  tr: LessonText;
  en: LessonText;
}

export const LESSONS: Lesson[] = data as Lesson[];
