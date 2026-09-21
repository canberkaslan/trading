/**
 * Ajanlar — the decision list, and a pointer to the run it came out of.
 *
 * Ported to Aurora. The structure the prototype's AGENTS section draws:
 *
 *   1. title + subtitle, with a "Canlı koşu" pill on the right;
 *   2. "Son koşu" — the run's own figures, one card;
 *   3. the day's calls as a horizontally scrolling strip of chips;
 *   4. the decision list: one clipped card, one row per decision, each row
 *      expanding in place to the council.
 *
 * Two things carried over from the pre-Aurora screen because they are product
 * rules rather than decoration:
 *
 *  - Two separate affordances on one row, because they do two different things:
 *    tapping the row OPENS the decision (`/trade/<ticker>`), and the "N ajan +"
 *    target on the right EXPANDS the agent summaries in place. They are
 *    SIBLINGS rather than one nested in the other — a Pressable is `accessible`
 *    by default, so nesting the expand button inside the row button hides it
 *    from VoiceOver/TalkBack entirely.
 *  - The missing-output warning. A decision logged with no agent output, or
 *    with no PM text, is the one row in this list that genuinely warrants a
 *    mark: the pipeline wrote a row it could not fill. It used to live on the
 *    embedded run log; with the log gone it moved onto the decision row itself,
 *    which is where the reader can act on it.
 *
 * The run log is NOT embedded here any more. The stage log, its timings and the
 * cost of a run belong to the Canlı koşu screen; what this screen keeps is the
 * summary it can honestly derive from the decisions themselves — how many names
 * the last run ruled on, what the agents spent, how long they worked. There is
 * still no runs endpoint (`src/api/endpoints.ts` exposes exactly two agent
 * routes, `/v1/agents/decisions` and `/decisions/{id}`), so nothing on this
 * screen claims a stage, a dollar cost or a next-run time.
 */

import { View, Text, StyleSheet, ScrollView, RefreshControl, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useState, useCallback, useMemo } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';
import Svg, { Path } from 'react-native-svg';

import type { AgentDecision } from '@/api/types';
import { useDecisions } from '@/api/hooks';
import { todaysCalls, summaryLine } from '@/utils/todaysCalls';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { TYPE, TABULAR, font } from '@/theme/type';
import { ratingVariant, modelBadge } from '@/theme/rating';
import { Card } from '@/components/Card';
import { Tag } from '@/components/Tag';
import { StatCell } from '@/components/StatCell';
import { SectionHeader } from '@/components/SectionHeader';
import { ErrorState } from '@/components/ErrorState';
import { EmptyState } from '@/components/EmptyState';
import { formatUsd, parseUtc } from '@/utils/format';
import { formatTokens, formatLatency, councilChips } from '@/utils/decision';
import { formatOrderDate } from '@/utils/orders';
import { hitSlopFor, MIN_TOUCH_TARGET } from '@/utils/a11y';

type Palette = ReturnType<typeof useTheme>;

/** How many decisions the screen asks `/v1/agents/decisions` for. */
const DECISION_LIMIT = 25;

/**
 * The floating tab bar's height plus air. Stated here rather than imported from
 * `_layout.tsx` because a route module's exports are the router's namespace,
 * not a place to hang shared constants.
 */
const TAB_BAR_CLEARANCE = 72;

/** The Canlı koşu screen — the prototype's `openRun`. */
const RUN_ROUTE = '/run';

/** Every token this decision's agents burned, in and out. */
function decisionTokens(d: AgentDecision): number {
  return (d.reasoning ?? []).reduce((sum, r) => sum + (r.tokens_in ?? 0) + (r.tokens_out ?? 0), 0);
}

/**
 * Summed agent latency — deliberately labelled "ajan süresi" rather than run
 * duration wherever it is shown. The agents run concurrently, so this is the
 * work done, not the wall clock, and the two are not the same number.
 */
function decisionLatency(d: AgentDecision): number {
  return (d.reasoning ?? []).reduce((sum, r) => sum + (r.latency_ms ?? 0), 0);
}

/**
 * What the pipeline failed to write for this decision, in TR, or `null` when
 * the row is complete. Same rule the run log used to apply; it now marks the
 * decision row itself.
 */
function decisionGap(d: AgentDecision): string | null {
  const missing: string[] = [];
  if ((d.reasoning?.length ?? 0) === 0) missing.push('ajan analizi yok');
  if (!d.final_decision_text) missing.push('PM metni yok');
  return missing.length ? missing.join(' · ') : null;
}

/**
 * Epoch millis, via `parseUtc`.
 *
 * Slicing and string-comparing `timestamp_utc` restated a rule `@/utils/format`
 * already owns: decisions come out of SQLite naive (`2026-09-08T21:30:04.47`)
 * while broker-sourced rows end in `Z`, and the two do not sort against each
 * other as text — `.` sorts before `Z`, so a naive row and a zoned row one
 * second apart can come back in the wrong order and pick the wrong "latest
 * run". `parseUtc` is the one place that knows a missing suffix means UTC.
 * Unparseable timestamps sort last (NaN-free) rather than to the epoch.
 */
function decisionTime(d: AgentDecision): number {
  return parseUtc(d.timestamp_utc)?.getTime() ?? Number.NEGATIVE_INFINITY;
}

/** The calendar day a decision belongs to, or '' when its stamp is unusable. */
function runDay(iso: string): string {
  return parseUtc(iso)?.toISOString().slice(0, 10) ?? '';
}

/** The decisions that belong to the most recent run, oldest first. */
function lastRunDecisions(decisions: AgentDecision[]): AgentDecision[] {
  if (decisions.length === 0) return [];
  const latest = decisions.reduce((a, b) => (decisionTime(a) >= decisionTime(b) ? a : b));
  const day = runDay(latest.timestamp_utc);
  // No usable timestamp anywhere means there is no run to summarise. Grouping
  // the unparseable rows together would invent a run out of bad data.
  if (!day) return [];
  return decisions
    .filter((d) => runDay(d.timestamp_utc) === day)
    .sort((a, b) => decisionTime(a) - decisionTime(b));
}

/** The prototype's play triangle, at the pill's text colour. */
function PlayMark({ color }: { color: string }) {
  return (
    <Svg width={11} height={11} viewBox="0 0 24 24" fill={color}>
      <Path d="M6 3 20 12 6 21V3z" />
    </Svg>
  );
}

/**
 * The model stamp on an agent's analysis.
 *
 * An outline rather than a `<Tag>` variant: the colour comes from `modelBadge`,
 * which is the single source of "Opus is the brand colour, everything else is
 * secondary ink", and a Tag variant would restate that mapping a second time
 * where the two could drift. The pill radius is still the system's.
 */
function ModelTag({ model }: { model: string }) {
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const badge = modelBadge(t, model);
  return (
    <View style={[styles.modelTag, { borderColor: badge.color }]}>
      <Text style={[styles.modelTagText, { color: badge.color }]}>{badge.label}</Text>
    </View>
  );
}

export default function AgentsScreen() {
  const { t: tr } = useTranslation();
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const router = useRouter();

  const { data, isLoading, isError, error, refetch } = useDecisions({ limit: DECISION_LIMIT });
  const queryClient = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const decisions = useMemo(() => data ?? [], [data]);
  const today = useMemo(() => todaysCalls(data), [data]);

  const lastRun = useMemo(() => {
    const rows = lastRunDecisions(decisions);
    if (rows.length === 0) return null;
    return {
      count: rows.length,
      when: rows[rows.length - 1]?.timestamp_utc ?? null,
      tokens: rows.reduce((sum, d) => sum + decisionTokens(d), 0),
      latency: rows.reduce((sum, d) => sum + decisionLatency(d), 0),
      gaps: rows.filter((d) => decisionGap(d) !== null).length,
    };
  }, [decisions]);

  const toggle = (id: string) => setExpanded((e) => ({ ...e, [id]: !e[id] }));
  const openDetail = useCallback(
    (ticker: string) => router.push(`/trade/${ticker}` as never),
    [router],
  );
  const openRun = useCallback(() => router.push(RUN_ROUTE as never), [router]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await queryClient.invalidateQueries({ queryKey: ['agents'] });
    } finally {
      setRefreshing(false);
    }
  }, [queryClient]);

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={t.textSecondary} />
        }
      >
        <View style={styles.head}>
          <View style={styles.headText}>
            <Text style={styles.title} accessibilityRole="header">
              Ajan kararları
            </Text>
            {/* "son N karar", not "N karar": the list is a page of
                DECISION_LIMIT, so a bare count reports the page size as if it
                were how many decisions exist. Nothing returns a total. */}
            <Text style={styles.sub}>
              Portfolio Manager + LLM konseyi · son {decisions.length} karar
            </Text>
          </View>
          <Pressable
            onPress={openRun}
            style={({ pressed }) => [styles.runPill, pressed && styles.runPillPressed]}
            accessibilityRole="button"
            accessibilityLabel="Canlı koşu"
            accessibilityHint="Koşunun aşama günlüğünü açar"
          >
            <PlayMark color={t.brand ?? t.accent} />
            <Text style={styles.runPillLabel}>Canlı koşu</Text>
          </Pressable>
        </View>

        {/* The run, summarised — not logged. The stage-by-stage log lives on
            the Canlı koşu screen; these three figures are the only ones the
            decision rows themselves can back up. */}
        {lastRun ? (
          <>
            <SectionHeader
              title="Son koşu"
              count={lastRun.when ? formatOrderDate(lastRun.when) : undefined}
              actionLabel="Koşuyu aç"
              onAction={openRun}
              actionAccessibilityLabel="Canlı koşu ekranını aç"
              actionAccessibilityHint="Aşama günlüğü ve zamanlamalar"
              style={styles.firstSection}
            />
            <Card
              tone="raised"
              onPress={openRun}
              accessibilityLabel={`Son koşu: ${lastRun.count} karar, ${formatTokens(
                lastRun.tokens,
              )} token, ${formatLatency(lastRun.latency)} ajan süresi. Canlı koşu ekranını aç.`}
              accessibilityHint="Aşama günlüğü ve zamanlamalar"
            >
              <View style={styles.statRow}>
                <StatCell
                  label="Karar"
                  value={String(lastRun.count)}
                  hint="isim üzerine hüküm"
                  style={styles.stat}
                />
                <StatCell
                  label="Token"
                  value={`~${formatTokens(lastRun.tokens)}`}
                  hint="girdi + çıktı"
                  style={styles.stat}
                />
                <StatCell
                  label="Ajan süresi"
                  value={formatLatency(lastRun.latency)}
                  hint="eş zamanlı, duvar saati değil"
                  style={styles.stat}
                />
              </View>
              {lastRun.gaps > 0 ? (
                <View style={styles.gapRow}>
                  <Tag label={`${lastRun.gaps} eksik kayıt`} variant="warn" size="sm" numeric />
                </View>
              ) : null}
              <Text style={styles.cardNote}>
                Aşama günlüğü, koşu maliyeti ve sonraki koşu saati Canlı koşu ekranında; buradaki
                özet son koşuda üretilen kararlardan hesaplanır.
              </Text>
            </Card>
          </>
        ) : null}

        {/* Today's run, lifted out of the page of 25. The question an operator
            opens the app with is "what did the agents say today", and that was
            answerable only by reading timestamps down a list. Rendered only
            when today has produced something: before the run fires, "nothing
            yet" and "nothing decided" are different facts and a "0 karar" row
            would collapse them. */}
        {today.items.length > 0 ? (
          <>
            <SectionHeader title="Bugünün önerileri" count={summaryLine(today)} />
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              style={styles.strip}
              contentContainerStyle={styles.stripContent}
            >
              {today.items.map((d) => (
                <Card
                  key={`today-${d.decision_id}`}
                  onPress={() => openDetail(d.ticker)}
                  style={styles.todayCard}
                  accessibilityLabel={`${d.ticker}, ${d.rating}`}
                  accessibilityHint="Karar detayını açar"
                >
                  <Text style={styles.todayTicker}>{d.ticker}</Text>
                  <Tag label={d.rating} variant={ratingVariant(d.rating)} size="sm" caps />
                </Card>
              ))}
            </ScrollView>
          </>
        ) : null}

        <SectionHeader
          title="Kararlar"
          count={decisions.length > 0 ? decisions.length : undefined}
          style={today.items.length > 0 || lastRun ? undefined : styles.firstSection}
        />

        {isLoading ? (
          <Card tone="dashed" style={styles.slot}>
            <Text style={styles.slotText}>Yükleniyor…</Text>
          </Card>
        ) : isError ? (
          <ErrorState detail={error} onRetry={refetch} />
        ) : decisions.length === 0 ? (
          <EmptyState
            title="Henüz karar yok"
            hint="Ajan kararları günlük çalışmada üretilir ve burada listelenir."
          />
        ) : (
          <Card padded={false} clip>
            {decisions.map((d, index) => {
              const isOpen = !!expanded[d.decision_id];
              const agents = d.reasoning?.length ?? 0;
              const gap = decisionGap(d);
              const last = index === decisions.length - 1;
              return (
                <View
                  key={d.decision_id}
                  style={!last && !isOpen ? styles.decisionDivider : undefined}
                >
                  {/* Tapping the row opens the decision; the "N ajan +" target
                      expands it in place. The two are SIBLINGS, not nested: a
                      Pressable is `accessible` by default, so a button inside
                      one is swallowed into the parent's a11y element and
                      VoiceOver/TalkBack can reach only "open". Side by side,
                      both are real targets to touch and to a screen reader. */}
                  <View style={styles.rowInner}>
                    <Pressable
                      style={({ pressed }) => [styles.rowBody, pressed && styles.rowPressed]}
                      onPress={() => openDetail(d.ticker)}
                      accessibilityRole="button"
                      accessibilityLabel={`${d.ticker} kararı, ${d.rating}`}
                      accessibilityHint="Karar detayını açar"
                    >
                      <View style={styles.rowHead}>
                        <Text style={styles.ticker}>{d.ticker}</Text>
                        <Tag label={d.rating} variant={ratingVariant(d.rating)} size="sm" caps />
                        {gap ? <Tag label="eksik" variant="warn" size="sm" /> : null}
                      </View>

                      <View style={styles.moneyRow}>
                        <Text style={styles.money}>
                          <Text style={styles.moneyLabel}>Giriş </Text>
                          {formatUsd(d.entry_price)}
                        </Text>
                        <Text style={styles.money}>
                          <Text style={styles.moneyLabel}>Stop </Text>
                          {formatUsd(d.stop_loss)}
                        </Text>
                        <Text style={styles.money}>
                          <Text style={styles.moneyLabel}>Hedef </Text>
                          {formatUsd(d.price_target)}
                        </Text>
                      </View>

                      <Text style={styles.rowMeta}>
                        {formatOrderDate(d.timestamp_utc)} · {d.time_horizon ?? '—'}
                        {gap ? ` · ${gap}` : ''}
                      </Text>
                    </Pressable>

                    <Pressable
                      style={styles.expandBtn}
                      onPress={() => toggle(d.decision_id)}
                      accessibilityRole="button"
                      accessibilityLabel={`${d.ticker}, ${agents} ajan gerekçesi`}
                      accessibilityState={{ expanded: isOpen }}
                    >
                      <Text style={styles.expandHint}>
                        {agents} ajan {isOpen ? '−' : '+'}
                      </Text>
                    </Pressable>
                  </View>

                  {isOpen ? (
                    <View style={styles.expandBox}>
                      <Card tone="recessed" style={styles.council}>
                        <View style={styles.councilChips}>
                          <Text style={styles.councilLabel}>Konsey</Text>
                          {councilChips(d.reasoning, (m) => modelBadge(t, m).label).map((label) => (
                            <Tag key={label} label={label} variant="neutral" size="sm" numeric />
                          ))}
                        </View>

                        {agents === 0 ? (
                          <Text style={styles.agentBody}>
                            Bu karar ajan analizi olmadan kaydedilmiş.
                          </Text>
                        ) : (
                          (d.reasoning ?? []).map((r, i) => (
                            <View key={`${d.decision_id}-${i}`} style={styles.agent}>
                              <View style={styles.agentHead}>
                                <Text style={styles.agentName}>{r.agent}</Text>
                                <ModelTag model={r.model} />
                              </View>
                              <Text style={styles.agentBody}>{r.summary}</Text>
                              <Text style={styles.agentMeta}>
                                {formatTokens(r.tokens_in)}↓ / {formatTokens(r.tokens_out)}↑ token ·{' '}
                                {formatLatency(r.latency_ms)}
                              </Text>
                            </View>
                          ))
                        )}
                      </Card>

                      <Pressable
                        onPress={() => openDetail(d.ticker)}
                        style={styles.detailBtn}
                        hitSlop={hitSlopFor(24)}
                        accessibilityRole="button"
                        accessibilityLabel={`${d.ticker} kararının tam detayı`}
                      >
                        <Text style={styles.detailLink}>Tam detay →</Text>
                      </Pressable>
                    </View>
                  ) : null}
                </View>
              );
            })}
          </Card>
        )}

        <Text style={styles.disclaimer}>{tr('disclaimer.short')}</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    screen: { flex: 1, backgroundColor: t.background },
    content: { paddingHorizontal: sh.space[3], paddingBottom: TAB_BAR_CLEARANCE },

    head: {
      flexDirection: 'row',
      alignItems: 'flex-end',
      justifyContent: 'space-between',
      gap: sh.space[2],
      paddingTop: sh.space[1],
      paddingBottom: sh.space[2],
    },
    headText: { flex: 1, minWidth: 0 },
    title: { ...TYPE.h2, fontSize: 26, letterSpacing: -0.5, color: t.textPrimary },
    sub: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, marginTop: sh.space[0] },

    // The prototype's brandSoft pill. 36px tall there; 44 here, because it is a
    // touch target and not a hover target.
    runPill: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      minHeight: MIN_TOUCH_TARGET,
      paddingHorizontal: sh.space[2],
      borderRadius: sh.radiusPill,
      borderWidth: sh.hairline,
      borderColor: t.brandSoft ?? t.divider,
      backgroundColor: t.brandSoft ?? t.surfaceElevated,
    },
    runPillPressed: { borderColor: t.brand ?? t.accent },
    runPillLabel: { ...TYPE.helper, fontSize: 12, ...font(600), color: t.brand ?? t.accent },

    // SectionHeader's own top margin is the gap between sections; the first one
    // on the page sits right under the title and does not need it.
    firstSection: { marginTop: sh.space[1] },

    statRow: { flexDirection: 'row', gap: sh.space[2], paddingVertical: sh.space[0] },
    stat: { flex: 1 },
    gapRow: { flexDirection: 'row', marginTop: sh.space[1] },
    cardNote: {
      ...TYPE.helper,
      color: t.ink3 ?? t.textMuted,
      lineHeight: 16,
      marginTop: sh.space[1],
    },

    // Bleeds to both page edges, as the prototype's `margin:0 -16px` does, so
    // the strip reads as scrollable rather than as a clipped row.
    strip: { marginHorizontal: -sh.space[3] },
    stripContent: { paddingHorizontal: sh.space[3], gap: sh.space[1], paddingVertical: 2 },
    todayCard: { minWidth: 104, gap: sh.space[1] },
    todayTicker: { ...TYPE.bodyStrong, fontSize: 15, ...font(800), color: t.textPrimary },

    decisionDivider: { borderBottomWidth: sh.hairline, borderBottomColor: t.line ?? t.divider },
    rowInner: { flexDirection: 'row', alignItems: 'flex-start' },
    rowBody: {
      flex: 1,
      minWidth: 0,
      gap: sh.space[0] + 2,
      paddingLeft: sh.space[3],
      paddingRight: sh.space[1],
      paddingVertical: sh.space[2],
      minHeight: MIN_TOUCH_TARGET,
    },
    rowPressed: { backgroundColor: t.surface2 ?? t.surfaceElevated },
    rowHead: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1], flexWrap: 'wrap' },
    ticker: { ...font(800), fontSize: 17, letterSpacing: -0.2, color: t.textPrimary },

    moneyRow: { flexDirection: 'row', gap: sh.space[2], flexWrap: 'wrap' },
    money: { ...TYPE.bodyStrong, ...TABULAR, color: t.textPrimary },
    moneyLabel: { ...font(400), color: t.ink2 ?? t.textSecondary },
    rowMeta: { ...TYPE.helper, color: t.ink3 ?? t.textMuted },

    // A real 44pt target rather than a hitSlop'd label: it sits beside the row
    // instead of inside it, so it has room to be one. `paddingTop` matches the
    // body's own top padding, which puts the label on the ticker's line.
    expandBtn: {
      minHeight: MIN_TOUCH_TARGET,
      paddingTop: sh.space[2],
      paddingLeft: sh.space[1],
      paddingRight: sh.space[3],
    },
    expandHint: { ...TYPE.helper, fontSize: 12, ...font(600), color: t.brand ?? t.accent },

    expandBox: {
      paddingHorizontal: sh.space[3],
      paddingBottom: sh.space[2],
      gap: sh.space[1],
    },
    council: { gap: sh.space[2] },
    councilChips: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: sh.space[0] + 2 },
    councilLabel: { ...TYPE.helper, color: t.ink3 ?? t.textMuted },
    agent: { gap: 3 },
    agentHead: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1], flexWrap: 'wrap' },
    agentName: { ...TYPE.bodyStrong, ...font(800), color: t.textPrimary },
    modelTag: {
      borderWidth: sh.hairline,
      borderRadius: sh.radiusPill,
      paddingHorizontal: 7,
      paddingVertical: 1,
    },
    modelTagText: { fontSize: 10, ...font(600), letterSpacing: 0.2 },
    agentBody: { ...TYPE.body, lineHeight: 19, color: t.ink2 ?? t.textSecondary },
    agentMeta: { ...TYPE.helper, ...TABULAR, color: t.ink3 ?? t.textMuted },

    detailBtn: { alignSelf: 'flex-start', paddingTop: sh.space[0] },
    detailLink: { ...TYPE.bodyStrong, ...font(600), color: t.brand ?? t.accent },

    slot: { paddingVertical: sh.space[4], alignItems: 'center' },
    slotText: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, textAlign: 'center' },

    disclaimer: {
      ...TYPE.helper,
      color: t.ink3 ?? t.textMuted,
      textAlign: 'center',
      lineHeight: 16,
      marginTop: sh.space[4],
    },
  });
