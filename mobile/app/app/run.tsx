/**
 * Canlı koşu — the run log, ported to Aurora from the prototype's LIVE RUN
 * block.
 *
 * WHAT THIS SCREEN IS, AND WHAT IT IS NOT
 *
 * The prototype plays a 15-stage pipeline out of a hard-coded `runStages`
 * table: fifteen literal agent names, fifteen literal durations, a fixed
 * ticker (NVDA), fixed council copy, and a hard-coded "Opus 4.8 · Sonnet 4.6 ·
 * Haiku 4.5" line under the progress bar. None of that exists on the server.
 * `src/api/endpoints.ts` exposes exactly two agent routes — `/v1/agents/
 * decisions` and `/decisions/{id}` — and neither reports a run, a stage, a
 * wall-clock start, a cost or a next-run time. There is no runs endpoint.
 *
 * So this screen does not pretend to watch a run happen. It REPLAYS a run that
 * has already been logged, from the only record of it that exists: the
 * decision. Every stage on the timeline is a real row the pipeline wrote —
 *
 *   1. one stage per `reasoning[]` entry (agent, model, summary, tokens in/out,
 *      latency), in the order the server returned them;
 *   2. one stage per `debate_transcript` role, ordered by `debateEntries`
 *      (bull → bear → research manager → trader → risk → PM), which is the
 *      same ordering the decision detail screen uses;
 *   3. a closing stage for `final_decision_text` when there is one.
 *
 * — and the numbers beside it are that row's own numbers. Nothing is invented.
 * A run that logged nine agents draws nine stages, not fifteen.
 *
 * The consequence, stated on the screen itself rather than buried here: the
 * clock is SUMMED AGENT LATENCY, not the wall clock of the run. The agents run
 * concurrently, so the two are different numbers, and the screen says "ajan
 * süresi" everywhere it shows one — the same wording `agents.tsx` settled on.
 * Play/pause therefore steps through a recording at a fixed tick; it is not a
 * live tail, and the note under the hero says so.
 *
 * NO DATA SOURCE (drawn as an em dash, never as a guess):
 *   - cost in dollars. The prototype has an `X.cost` label; the API reports no
 *     price per token, and multiplying tokens by a rate hard-coded in the app
 *     would be a number the operator could not audit.
 *   - wall-clock run duration, start time, and "next run 22:30 UTC".
 *   - the three-model council line. `councilChips` derives the real one from
 *     `reasoning[]`, so the screen shows what actually ran instead.
 *
 * Aurora shape: the hero is `surfaceElevated` with the brand glow (the page
 * ground is already the darkest surface, so the prototype's near-black slab
 * inverts — the same call the Bugün hero makes), the timeline rail is `line`,
 * stage cards are `Card`, and pending stages sit at 0.55 opacity exactly as the
 * prototype dims them.
 */

import { View, Text, StyleSheet, ScrollView, Pressable, RefreshControl } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter } from 'expo-router';
import Svg, { Path, Rect, Defs, RadialGradient, Stop, Circle } from 'react-native-svg';

import type { AgentDecision } from '@/api/types';
import { useDecisions } from '@/api/hooks';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { TYPE, TABULAR, font } from '@/theme/type';
import { modelBadge } from '@/theme/rating';
import { Card } from '@/components/Card';
import { Tag } from '@/components/Tag';
import { StatCell } from '@/components/StatCell';
import { BlinkSquare } from '@/components/BlinkSquare';
import { ErrorState } from '@/components/ErrorState';
import { EmptyState } from '@/components/EmptyState';
import {
  formatTokens,
  formatLatency,
  councilChips,
  debateEntries,
  debateRoleLabel,
} from '@/utils/decision';
import { formatOrderDate } from '@/utils/orders';
import { parseUtc } from '@/utils/format';
import { hitSlopFor, MIN_TOUCH_TARGET } from '@/utils/a11y';

type Palette = ReturnType<typeof useTheme>;

/** How many decisions to ask for; the same window `agents.tsx` uses. */
const DECISION_LIMIT = 25;

/** One stage per tick. The prototype plays a stage a second; so does this. */
const TICK_MS = 1000;

/** The prototype's timeline dot, and the rail it hangs off. */
const DOT = 14;
const RAIL_GUTTER = 26;

/** The play/pause control. 40 in the prototype, carried to 44 by hitSlop. */
const CTRL_HEIGHT = 40;

/** Chip in the ticker strip. */
const CHIP_HEIGHT = 34;

/** A stage of the run, built only from what the decision actually logged. */
interface RunStage {
  key: string;
  /** Who did the work: an agent name, or a debate role. */
  agent: string;
  /** The model badge label, or null when the row does not name a model. */
  model: string | null;
  summary: string;
  /** Tokens in + out, or null when the row predates token accounting. */
  tokens: number | null;
  /** This stage's own latency, or null when the row does not carry one. */
  latencyMs: number | null;
  /**
   * The prototype marks two stages warn-coloured: the risk layer and order
   * submission — the stages that can silently change or stop an order. Here
   * the equivalent is a stage the pipeline wrote but could not fill.
   */
  warn: boolean;
}

/** Epoch millis for sorting, via `parseUtc`; unusable stamps sort last. */
function decisionTime(d: AgentDecision): number {
  return parseUtc(d.timestamp_utc)?.getTime() ?? Number.NEGATIVE_INFINITY;
}

/** The calendar day a decision belongs to, or '' when its stamp is unusable. */
function runDay(iso: string): string {
  return parseUtc(iso)?.toISOString().slice(0, 10) ?? '';
}

/**
 * The decisions that belong to the most recent run, oldest first.
 *
 * Same rule as `agents.tsx`: a run is a calendar day's worth of decisions, and
 * a set of rows with no parseable stamp is not a run — grouping those together
 * would invent one out of bad data.
 */
function lastRunDecisions(decisions: AgentDecision[]): AgentDecision[] {
  if (decisions.length === 0) return [];
  const latest = decisions.reduce((a, b) => (decisionTime(a) >= decisionTime(b) ? a : b));
  const day = runDay(latest.timestamp_utc);
  if (!day) return [];
  return decisions
    .filter((d) => runDay(d.timestamp_utc) === day)
    .sort((a, b) => decisionTime(a) - decisionTime(b));
}

/**
 * The stage log for one decision — the whole of this screen's content model.
 *
 * Reasoning rows first (they are the analysts, and the server returns them in
 * pipeline order), then the debate, then the written decision. A row with no
 * summary still becomes a stage: the fact that the pipeline ran an agent and
 * stored nothing is exactly the kind of gap the operator opened this screen to
 * find, so it is drawn and marked rather than dropped.
 */
function buildStages(d: AgentDecision | null, labelFor: (m: string) => string): RunStage[] {
  if (!d) return [];

  const stages: RunStage[] = (d.reasoning ?? []).map((r, i) => {
    const summary = (r.summary ?? '').trim();
    return {
      key: `agent-${i}-${r.agent}`,
      agent: r.agent,
      model: r.model ? labelFor(r.model) : null,
      summary: summary || 'Bu aşama için özet yazılmadı.',
      tokens: (r.tokens_in ?? 0) + (r.tokens_out ?? 0) || null,
      latencyMs: r.latency_ms ?? null,
      warn: summary.length === 0,
    };
  });

  for (const entry of debateEntries(d.debate_transcript)) {
    stages.push({
      key: `debate-${entry.role}`,
      agent: debateRoleLabel(entry.role),
      // The transcript is a Record<role, text>; it does not say which model
      // spoke, and guessing from the role name would be a fabrication.
      model: null,
      summary: entry.text,
      tokens: null,
      latencyMs: null,
      warn: false,
    });
  }

  const finalText = (d.final_decision_text_tr ?? d.final_decision_text ?? '').trim();
  if (finalText) {
    stages.push({
      key: 'final',
      agent: 'Karar metni',
      model: null,
      summary: finalText,
      tokens: null,
      latencyMs: null,
      warn: false,
    });
  }

  return stages;
}

/** The prototype's play triangle. */
function PlayMark({ color }: { color: string }) {
  return (
    <Svg width={13} height={13} viewBox="0 0 24 24" fill={color}>
      <Path d="M6 3 20 12 6 21V3z" />
    </Svg>
  );
}

/** The prototype's pause bars. */
function PauseMark({ color }: { color: string }) {
  return (
    <Svg width={13} height={13} viewBox="0 0 24 24" fill={color}>
      <Rect x={6} y={4} width={4} height={16} />
      <Rect x={14} y={4} width={4} height={16} />
    </Svg>
  );
}

export default function RunScreen() {
  const router = useRouter();
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);

  const { data, isLoading, isError, error, refetch } = useDecisions({ limit: DECISION_LIMIT });
  const [refreshing, setRefreshing] = useState(false);

  const decisions = useMemo(() => data ?? [], [data]);
  const run = useMemo(() => lastRunDecisions(decisions), [decisions]);

  /** Which name in the run is being replayed. The prototype fixes this to NVDA. */
  const [ticker, setTicker] = useState<string | null>(null);
  const selected = useMemo(
    () => run.find((d) => d.ticker === ticker) ?? run[run.length - 1] ?? null,
    [run, ticker],
  );

  const labelFor = useCallback((m: string) => modelBadge(t, m).label, [t]);
  const stages = useMemo(() => buildStages(selected, labelFor), [selected, labelFor]);
  const total = stages.length;

  /**
   * Playback position. `stage` counts COMPLETED stages, so `stage === total`
   * is "finished" and `stages[stage]` is the one currently playing — the same
   * indexing the prototype's `r.stage` uses.
   */
  const [stage, setStage] = useState(0);
  const [playing, setPlaying] = useState(false);

  // Switching names, or the query returning a different run, restarts the
  // replay at the top rather than leaving the cursor pointing into a log that
  // no longer has that many stages.
  useEffect(() => {
    setStage(total);
    setPlaying(false);
  }, [selected?.decision_id, total]);

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => {
      setStage((s) => {
        if (s + 1 >= total) {
          setPlaying(false);
          return total;
        }
        return s + 1;
      });
    }, TICK_MS);
    return () => clearInterval(id);
  }, [playing, total]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await refetch();
    } finally {
      setRefreshing(false);
    }
  }, [refetch]);

  const done = total > 0 && stage >= total;
  const playLabel = done ? 'Yeniden oynat' : stage > 0 ? 'Devam et' : 'Koşuyu oynat';
  const statusLabel = playing ? 'Koşu oynatılıyor' : done ? 'Koşu tamamlandı' : 'Duraklatıldı';

  const togglePlay = () => {
    if (playing) {
      setPlaying(false);
      return;
    }
    if (done) setStage(0);
    setPlaying(true);
  };

  /** Cumulative summed agent latency up to and including the current stage. */
  const elapsedMs = useMemo(
    () =>
      stages
        .slice(0, Math.min(stage, total))
        .reduce((sum, s) => sum + (s.latencyMs ?? 0), 0),
    [stages, stage, total],
  );

  /** Tokens burned by the whole logged run, across every name in it. */
  const runTokens = useMemo(
    () =>
      run.reduce(
        (sum, d) =>
          sum + (d.reasoning ?? []).reduce((s, r) => s + (r.tokens_in ?? 0) + (r.tokens_out ?? 0), 0),
        0,
      ),
    [run],
  );

  const council = useMemo(
    () => councilChips(selected?.reasoning, labelFor),
    [selected?.reasoning, labelFor],
  );

  const progress = total > 0 ? Math.min(stage, total) / total : 0;

  const header = (
    <>
      <Pressable
        style={styles.backBtn}
        hitSlop={hitSlopFor(MIN_TOUCH_TARGET)}
        onPress={() => router.back()}
        accessibilityRole="button"
        accessibilityLabel="Geri"
      >
        <Text style={styles.backText}>← Geri</Text>
      </Pressable>

      <View style={styles.headText}>
        <Text style={styles.title} accessibilityRole="header">
          Canlı koşu
        </Text>
        <Text style={styles.subtitle}>
          {selected
            ? `${formatOrderDate(selected.timestamp_utc)} · ${selected.ticker} · ${total} aşama`
            : 'Kayıtlı ajan koşusunun aşama günlüğü'}
        </Text>
      </View>
    </>
  );

  if (isLoading) {
    return (
      <SafeAreaView style={styles.screen} edges={['top']}>
        <ScrollView contentContainerStyle={styles.content}>
          {header}
          <Card tone="dashed" style={styles.slot}>
            <Text style={styles.slotText}>Koşu günlüğü yükleniyor…</Text>
          </Card>
        </ScrollView>
      </SafeAreaView>
    );
  }

  if (isError) {
    return (
      <SafeAreaView style={styles.screen} edges={['top']}>
        <ScrollView contentContainerStyle={styles.content}>
          {header}
          <ErrorState
            title="Koşu günlüğü yüklenemedi"
            detail={error instanceof Error ? error.message : undefined}
            onRetry={() => void refetch()}
          />
        </ScrollView>
      </SafeAreaView>
    );
  }

  if (!selected || total === 0) {
    return (
      <SafeAreaView style={styles.screen} edges={['top']}>
        <ScrollView
          contentContainerStyle={styles.content}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={t.textSecondary} />}
        >
          {header}
          <EmptyState
            title="Kayıtlı koşu yok"
            hint="Ajanlar bir karar yazdığında o koşunun aşama günlüğü burada belirir."
          />
        </ScrollView>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={t.textSecondary} />}
      >
        {header}

        {/* The hero. Ground is `surfaceElevated` rather than the prototype's
            near-black slab: Aurora's page is already the darkest surface, so
            the slab inverts to stay "the one rectangle that is not the page". */}
        <View style={styles.hero}>
          <Svg style={styles.glow} width="100%" height="100%" pointerEvents="none">
            <Defs>
              <RadialGradient id="runGlow" cx="50%" cy="50%" r="50%">
                <Stop offset="0%" stopColor={t.brand ?? t.accent} stopOpacity={0.5} />
                <Stop offset="70%" stopColor={t.brand ?? t.accent} stopOpacity={0} />
              </RadialGradient>
            </Defs>
            <Circle cx="90%" cy="-8%" r="115" fill="url(#runGlow)" />
          </Svg>

          <View style={styles.heroTopRow}>
            {/* The prototype pulses a dot while playing. `BlinkSquare` is the
                system's step blink — no fade animation exists here. */}
            {playing ? <BlinkSquare size={8} color={t.brand ?? t.accent} /> : null}
            <Text style={styles.heroLabel}>{statusLabel}</Text>
            <Text style={styles.heroElapsed} numberOfLines={1}>
              Ajan süresi {formatLatency(elapsedMs)}
            </Text>
          </View>

          <View
            style={styles.track}
            accessible
            accessibilityRole="progressbar"
            accessibilityLabel={`${Math.min(stage, total)} / ${total} aşama`}
            accessibilityValue={{ min: 0, max: total, now: Math.min(stage, total) }}
          >
            <View style={[styles.trackFill, { width: `${Math.round(progress * 100)}%` }]} />
          </View>

          <View style={styles.heroMetaRow}>
            <Text style={styles.heroMeta} numberOfLines={1}>
              {council.length ? council.join(' · ') : 'Model bilgisi yok'}
            </Text>
            <Text style={styles.heroMeta}>
              {Math.min(stage, total)} / {total} aşama
            </Text>
          </View>

          <View style={styles.statRow}>
            <StatCell
              size="sm"
              label="Ajan süresi"
              value={formatLatency(elapsedMs)}
              hint="toplam, eşzamanlı"
              accessibilityLabel={`Toplam ajan süresi ${formatLatency(elapsedMs)}, ajanlar eşzamanlı çalışır`}
            />
            <StatCell
              size="sm"
              label="Token"
              value={formatTokens(runTokens)}
              hint={`${run.length} karar`}
            />
            <StatCell
              size="sm"
              label="Maliyet"
              value="—"
              hint="sunucu bildirmiyor"
              accessibilityLabel="Maliyet bilinmiyor, sunucu koşu maliyeti bildirmiyor"
            />
          </View>

          <Pressable
            style={({ pressed }) => [styles.ctrl, pressed && styles.ctrlPressed]}
            hitSlop={hitSlopFor(CTRL_HEIGHT)}
            onPress={togglePlay}
            accessibilityRole="button"
            accessibilityLabel={playing ? 'Duraklat' : playLabel}
            accessibilityHint="Kayıtlı koşuyu aşama aşama oynatır"
          >
            {playing ? (
              <PauseMark color={t.background} />
            ) : (
              <PlayMark color={t.background} />
            )}
            <Text style={styles.ctrlText}>{playing ? 'Duraklat' : playLabel}</Text>
          </Pressable>
        </View>

        <Text style={styles.note}>
          Bu bir canlı yayın değil: kayıtlı koşunun aşama günlüğü saniyede bir aşama olarak
          oynatılır. Süreler ajanların kendi gecikmeleridir; ajanlar eşzamanlı çalıştığı için
          toplam, koşunun duvar saati süresi değildir.
        </Text>

        {/* More than one name in the run: pick which decision to replay. The
            prototype hard-codes NVDA; the real run rules on several names. */}
        {run.length > 1 ? (
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.chipStrip}
            style={styles.chipStripOuter}
          >
            {run.map((d) => {
              const active = d.decision_id === selected.decision_id;
              return (
                <Pressable
                  key={d.decision_id}
                  style={[styles.chip, active && styles.chipActive]}
                  hitSlop={hitSlopFor(CHIP_HEIGHT)}
                  onPress={() => setTicker(d.ticker)}
                  accessibilityRole="button"
                  accessibilityState={{ selected: active }}
                  accessibilityLabel={`${d.ticker} koşusunu göster`}
                >
                  <Text style={[styles.chipText, active && styles.chipTextActive]}>{d.ticker}</Text>
                </Pressable>
              );
            })}
          </ScrollView>
        ) : null}

        <View style={styles.timeline}>
          <View style={styles.rail} />
          {stages.map((s, i) => {
            const stageDone = i < stage;
            const active = i === stage && playing;
            const shown = stageDone || active;
            const dotColor = stageDone
              ? s.warn
                ? t.warning
                : (t.brand ?? t.accent)
              : active
                ? (t.brand ?? t.accent)
                : (t.line2 ?? t.divider);

            const meta = [
              s.tokens != null ? `${formatTokens(s.tokens)} token` : null,
              s.latencyMs != null ? formatLatency(s.latencyMs) : null,
            ].filter(Boolean);

            return (
              <View key={s.key} style={styles.stageRow}>
                <View style={[styles.dot, { backgroundColor: dotColor }]} />
                <Card
                  tone="surface"
                  style={[styles.stageCard, !shown && styles.stagePending]}
                  accessibilityLabel={[
                    `${i + 1}. aşama`,
                    s.agent,
                    s.model ?? null,
                    shown ? s.summary : 'henüz oynatılmadı',
                    meta.join(', ') || null,
                  ]
                    .filter(Boolean)
                    .join(', ')}
                >
                  <View style={styles.stageHead}>
                    <Text style={styles.stageAgent} numberOfLines={1}>
                      {s.agent}
                    </Text>
                    {s.model ? <Tag label={s.model} variant="neutral" size="sm" /> : null}
                    {s.warn ? <Tag label="eksik" variant="warn" size="sm" /> : null}
                    {meta.length ? (
                      <Text style={styles.stageMeta} numberOfLines={1}>
                        {meta.join(' · ')}
                      </Text>
                    ) : null}
                  </View>
                  {shown ? <Text style={styles.stageText}>{s.summary}</Text> : null}
                </Card>
              </View>
            );
          })}
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    screen: { flex: 1, backgroundColor: t.background },
    content: {
      paddingHorizontal: sh.space[3],
      paddingTop: sh.space[1],
      // The tab bar is a floating pill with a shadow, not a flush strip.
      paddingBottom: 72,
    },

    backBtn: { minHeight: MIN_TOUCH_TARGET, justifyContent: 'center', alignSelf: 'flex-start' },
    backText: { ...TYPE.bodyStrong, fontSize: 15, ...font(800), color: t.textPrimary },

    headText: { paddingTop: sh.space[1], paddingBottom: sh.space[2] },
    title: { ...TYPE.h2, fontSize: 26, letterSpacing: -0.5, color: t.textPrimary },
    subtitle: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, marginTop: sh.space[0] },

    slot: { marginTop: sh.space[2], alignItems: 'center', paddingVertical: sh.space[4] },
    slotText: { ...TYPE.body, color: t.ink2 ?? t.textSecondary },

    hero: {
      backgroundColor: t.surfaceElevated,
      borderRadius: sh.radius,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
      paddingHorizontal: sh.space[3],
      paddingVertical: sh.space[3],
      overflow: 'hidden',
    },
    glow: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 },

    heroTopRow: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1] },
    heroLabel: { ...TYPE.section, color: t.textPrimary },
    heroElapsed: {
      ...TYPE.helper,
      ...TABULAR,
      marginLeft: 'auto',
      color: t.ink2 ?? t.textSecondary,
    },

    track: {
      height: 6,
      borderRadius: sh.radiusPill,
      backgroundColor: t.line2 ?? t.divider,
      marginTop: sh.space[2],
      overflow: 'hidden',
    },
    trackFill: { height: 6, borderRadius: sh.radiusPill, backgroundColor: t.brand ?? t.accent },

    heroMetaRow: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      gap: sh.space[2],
      marginTop: sh.space[2],
    },
    heroMeta: { ...TYPE.helper, color: t.ink2 ?? t.textSecondary, flexShrink: 1 },

    statRow: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      gap: sh.space[2],
      marginTop: sh.space[3],
    },

    ctrl: {
      flexDirection: 'row',
      alignItems: 'center',
      alignSelf: 'flex-start',
      gap: sh.space[1],
      height: CTRL_HEIGHT,
      paddingHorizontal: sh.space[3],
      borderRadius: sh.radiusPill,
      backgroundColor: t.textPrimary,
      marginTop: sh.space[3],
    },
    ctrlPressed: { opacity: 0.85 },
    ctrlText: { ...TYPE.bodyStrong, ...font(800), color: t.background },

    note: {
      ...TYPE.helper,
      color: t.ink3 ?? t.textMuted,
      lineHeight: 16,
      marginTop: sh.space[2],
      marginBottom: sh.space[2],
    },

    chipStripOuter: { marginHorizontal: -sh.space[3], marginBottom: sh.space[2] },
    chipStrip: { paddingHorizontal: sh.space[3], gap: sh.space[1], paddingBottom: sh.space[0] },
    chip: {
      height: CHIP_HEIGHT,
      justifyContent: 'center',
      paddingHorizontal: sh.space[2],
      borderRadius: sh.radiusPill,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
    },
    chipActive: { backgroundColor: t.textPrimary, borderColor: t.textPrimary },
    chipText: { ...TYPE.helper, fontSize: 12, ...font(800), color: t.textPrimary },
    chipTextActive: { color: t.background },

    timeline: { paddingLeft: RAIL_GUTTER, gap: sh.space[1] },
    rail: {
      position: 'absolute',
      left: 8,
      top: sh.space[2],
      bottom: sh.space[2],
      width: 2,
      backgroundColor: t.line ?? t.divider,
    },

    stageRow: { position: 'relative' },
    dot: {
      position: 'absolute',
      left: -(RAIL_GUTTER - 2),
      top: 14,
      width: DOT,
      height: DOT,
      borderRadius: DOT / 2,
      borderWidth: 3,
      borderColor: t.background,
    },
    stageCard: { paddingHorizontal: sh.space[2], paddingVertical: sh.space[2] },
    stagePending: { opacity: 0.55 },

    stageHead: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1], flexWrap: 'wrap' },
    stageAgent: { ...TYPE.bodyStrong, ...font(700), color: t.textPrimary, flexShrink: 1 },
    stageMeta: {
      ...TYPE.helper,
      ...TABULAR,
      marginLeft: 'auto',
      color: t.ink3 ?? t.textMuted,
    },
    stageText: {
      ...TYPE.body,
      color: t.ink2 ?? t.textSecondary,
      lineHeight: 19,
      marginTop: sh.space[1],
    },
  });
