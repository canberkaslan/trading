/**
 * Ajanlar — the decision list, and the run it came out of.
 *
 * Three things the handoff asks for that the old screen did not have:
 *
 *  - The row is a summary a trader can act on without opening anything: ticker,
 *    rating chip, Giriş / Stop / Hedef as money, and a "tarih · vade" line. The
 *    money used to render as `Entry ${d.entry_price ?? '—'}` — a raw float with
 *    a dollar glued in front, so `342.1` printed as "$342.1" and a null printed
 *    as "$—". It goes through `formatUsd` now, like every other figure.
 *  - Two separate affordances on one row, because they do two different things:
 *    tapping the row OPENS the decision (`/trade/<ticker>`), and the "N ajan +"
 *    target on the right EXPANDS the agent summaries in place. The old row did
 *    only the second, so the detail screen was reachable only after expanding.
 *    They are siblings rather than one nested in the other — a Pressable is
 *    `accessible` by default, so nesting the expand button inside the row
 *    button hid it from VoiceOver/TalkBack entirely.
 *  - The per-agent meta line ("6.1k↓ / 410↑ token · 5.2 sn"). The counters were
 *    already on the wire and already formatted by `@/utils/decision`; the list
 *    simply dropped them, which hid what a run costs.
 *
 * The "Çalışma günlüğü" here is NOT the prototype's 11-stage pipeline log.
 * There is no runs/log endpoint — `src/api/endpoints.ts` exposes exactly two
 * agent routes (`/v1/agents/decisions` and `/decisions/{id}`) and the FastAPI
 * side has no others — so the stage names, the $ cost and "sonraki koşu" have
 * nothing behind them. Inventing them would put fiction on a money screen.
 * What the decisions genuinely carry IS a log of the last run: one row per name
 * the run ruled on, in the order it ruled, with the agent count, token spend
 * and latency it actually recorded. A row that came back without agent output
 * or without a PM text is the real warning case, and gets the accent square.
 */

import { View, Text, StyleSheet, ScrollView, RefreshControl, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useState, useCallback, useMemo } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';

import type { AgentDecision } from '@/api/types';
import { useDecisions } from '@/api/hooks';
import { useTheme } from '@/theme/useTheme';
import { ratingChip, modelBadge } from '@/theme/rating';
import { Tag } from '@/components/Tag';
import { ErrorState } from '@/components/ErrorState';
import { EmptyState } from '@/components/EmptyState';
import { formatUsd, parseUtc } from '@/utils/format';
import { formatTokens, formatLatency,
  councilChips,
} from '@/utils/decision';
import { formatOrderDate } from '@/utils/orders';
import { hitSlopFor, MIN_TOUCH_TARGET } from '@/utils/a11y';
import { font, TABULAR } from '@/theme/type';

type Palette = ReturnType<typeof useTheme>;

/** How many decisions the screen asks `/v1/agents/decisions` for. */
const DECISION_LIMIT = 25;

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
 * The council row.
 *
 * The prototype chips a per-model VOTE ("DeepSeek V3 · Overweight") off a
 * `council: {votes, chair, confidence}` block. `AgentDecision` has no such
 * field — the council is opt-in and off by default, and when it does convene
 * only the chair's rating survives onto the wire. So the chips say what is
 * actually knowable from `reasoning[]`: which models ruled on this name and how
 * many agents each one carried. Labelled "ajan", never as a vote, so a reader
 * cannot mistake a participation count for a second opinion.
 */
interface RunRow {
  key: string;
  time: string;
  stage: string;
  detail: string;
  warn: boolean;
}

/**
 * HH:MM, taken off the shared date formatter rather than re-derived: every row
 * in a run log falls on the same day, so repeating "8 Eyl" eleven times is
 * noise. The date lives once, in the section header.
 */
function runClock(iso: string): string {
  const parts = formatOrderDate(iso).split(' ');
  return parts.length > 1 ? (parts[parts.length - 1] ?? '—') : '—';
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
  // No usable timestamp anywhere means there is no run to log. Grouping the
  // unparseable rows together would invent a run out of bad data.
  if (!day) return [];
  return decisions
    .filter((d) => runDay(d.timestamp_utc) === day)
    .sort((a, b) => decisionTime(a) - decisionTime(b));
}

function toRunRow(d: AgentDecision): RunRow {
  const agents = d.reasoning?.length ?? 0;
  // A decision logged with no agent output, or with no PM text, is the one
  // thing in this list that genuinely warrants a mark: the pipeline wrote a
  // row it could not fill.
  const missing: string[] = [];
  if (agents === 0) missing.push('ajan analizi yok');
  if (!d.final_decision_text) missing.push('PM metni yok');

  const detail = missing.length
    ? `${d.rating} · ${missing.join(' · ')}`
    : `${d.rating} · ${agents} ajan · ${formatTokens(decisionTokens(d))} token · ${formatLatency(
        decisionLatency(d),
      )}`;

  return {
    key: d.decision_id,
    time: runClock(d.timestamp_utc),
    stage: d.ticker,
    detail,
    warn: missing.length > 0,
  };
}

export default function AgentsScreen() {
  const { t } = useTranslation();
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);
  const router = useRouter();
  const { data, isLoading, isError, refetch } = useDecisions({ limit: DECISION_LIMIT });
  const queryClient = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const decisions = useMemo(() => data ?? [], [data]);
  const runRows = useMemo(() => lastRunDecisions(decisions).map(toRunRow), [decisions]);
  const lastRun = useMemo(() => {
    if (decisions.length === 0) return null;
    return decisions.reduce((a, b) => (decisionTime(a) >= decisionTime(b) ? a : b)).timestamp_utc;
  }, [decisions]);

  const runTotals = useMemo(() => {
    const rows = lastRunDecisions(decisions);
    return {
      tokens: rows.reduce((sum, d) => sum + decisionTokens(d), 0),
      latency: rows.reduce((sum, d) => sum + decisionLatency(d), 0),
    };
  }, [decisions]);

  const toggle = (id: string) => setExpanded((e) => ({ ...e, [id]: !e[id] }));
  const openDetail = (ticker: string) => router.push(`/trade/${ticker}` as never);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await queryClient.invalidateQueries({ queryKey: ['agents'] });
    setRefreshing(false);
  }, [queryClient]);

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={theme.textPrimary} />
        }
      >
        <Text style={styles.heading}>Ajan kararları</Text>
        {/* "son N karar", not "N karar": the list is a page of DECISION_LIMIT,
            so a bare count reports the page size as if it were how many
            decisions exist. Nothing returns a total. */}
        <Text style={styles.subheading}>
          Portfolio Manager + LLM konseyi · son {decisions.length} karar
          {lastRun ? ` · son koşu ${formatOrderDate(lastRun)}` : ''}
        </Text>

        {isLoading ? (
          <Text style={styles.muted}>Yükleniyor…</Text>
        ) : isError ? (
          <ErrorState onRetry={refetch} />
        ) : decisions.length === 0 ? (
          <EmptyState
            title="Henüz karar yok"
            hint="Ajan kararları günlük çalışmada üretilir ve burada listelenir."
          />
        ) : (
          <>
            {decisions.map((d) => {
              const isOpen = !!expanded[d.decision_id];
              const chip = ratingChip(theme, d.rating);
              const agents = d.reasoning?.length ?? 0;
              return (
                <View key={d.decision_id} style={styles.row}>
                  {/* Tapping the row opens the decision; the "N ajan +" target
                      expands it in place. The two are SIBLINGS, not nested: a
                      Pressable is `accessible` by default, so a button inside
                      one is swallowed into the parent's a11y element and
                      VoiceOver/TalkBack can reach only "open". Side by side,
                      both are real targets to touch and to a screen reader. */}
                  <View style={styles.rowInner}>
                    <Pressable
                      style={styles.rowBody}
                      onPress={() => openDetail(d.ticker)}
                      accessibilityRole="button"
                      accessibilityLabel={`${d.ticker} kararı, ${d.rating}`}
                      accessibilityHint="Karar detayını açar"
                    >
                      <View style={styles.rowHead}>
                        <Text style={styles.ticker}>{d.ticker}</Text>
                        {/* `ratingChip` returns `background`, a CSS name the
                            web prototype uses. Spread straight into a View
                            style, RN ignores it and the chip loses its fill —
                            and a Buy chip draws its text in `t.background`, so
                            the rating came out ground-on-ground: invisible.
                            Mapped explicitly, as orders.tsx already does. */}
                        <View
                          style={[
                            styles.ratingChip,
                            {
                              backgroundColor: chip.background,
                              borderColor: chip.borderColor ?? 'transparent',
                            },
                          ]}
                        >
                          <Text style={[styles.rating, { color: chip.color }]}>{d.rating}</Text>
                        </View>
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
                      <View style={styles.council}>
                        <Text style={styles.councilLabel}>Konsey</Text>
                        {councilChips(d.reasoning, (m) => modelBadge(theme, m).label).map((label) => (
                          <Tag key={label} label={label} variant="neutral" />
                        ))}
                      </View>

                      {(d.reasoning ?? []).map((r, i) => {
                        const badge = modelBadge(theme, r.model);
                        return (
                          <View key={`${d.decision_id}-${i}`} style={styles.agent}>
                            <View style={styles.agentHead}>
                              <Text style={styles.agentName}>{r.agent}</Text>
                              <Text style={[styles.badge, { color: badge.color, borderColor: badge.color }]}>
                                {badge.label}
                              </Text>
                            </View>
                            <Text style={styles.agentBody}>{r.summary}</Text>
                            <Text style={styles.agentMeta}>
                              {formatTokens(r.tokens_in)}↓ / {formatTokens(r.tokens_out)}↑ token ·{' '}
                              {formatLatency(r.latency_ms)}
                            </Text>
                          </View>
                        );
                      })}

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

            {runRows.length ? (
              <View style={styles.runBlock}>
                <View style={styles.runHead}>
                  <Text style={styles.section}>Çalışma günlüğü</Text>
                  <Text style={styles.runHeadMeta}>{lastRun ? formatOrderDate(lastRun) : '—'}</Text>
                </View>

                {runRows.map((r) => (
                  <View key={r.key} style={styles.runRow}>
                    <View
                      style={[
                        styles.runDot,
                        { backgroundColor: r.warn ? theme.accent : theme.neutral300 ?? theme.divider },
                      ]}
                    />
                    <View style={styles.runText}>
                      <Text style={styles.runStage}>
                        <Text style={styles.runTime}>{r.time} · </Text>
                        {r.stage}
                      </Text>
                      <Text style={styles.runDetail}>{r.detail}</Text>
                    </View>
                  </View>
                ))}

                <Text style={styles.runTotals}>
                  ~{formatTokens(runTotals.tokens)} token · {formatLatency(runTotals.latency)} ajan süresi
                </Text>
                <Text style={styles.runNote}>
                  Aşama günlüğü, koşu maliyeti ve sonraki koşu saati için bir koşu uç noktası yok; bu
                  liste son koşuda üretilen kararlardır.
                </Text>
              </View>
            ) : null}
          </>
        )}

        <Text style={styles.disclaimer}>{t('disclaimer.short')}</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

const makeStyles = (t: Palette) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.background },
    scroll: { paddingHorizontal: 16, paddingTop: 20, paddingBottom: 24 },

    heading: { color: t.textPrimary, fontSize: 24, ...font(800) },
    subheading: { color: t.textSecondary, fontSize: 13, marginTop: 4, marginBottom: 12, ...font(400) },

    row: { borderBottomWidth: 1, borderBottomColor: t.divider },
    rowInner: { flexDirection: 'row', alignItems: 'flex-start' },
    rowBody: { flex: 1, paddingVertical: 14, gap: 6, minHeight: MIN_TOUCH_TARGET },
    rowHead: { flexDirection: 'row', alignItems: 'center', gap: 10 },
    ticker: { color: t.textPrimary, fontSize: 18, ...font(800) },
    // A filled chip, not tinted text: on a light ground a coloured word carries
    // far less than a filled block, and buy/hold/sell is the distinction that
    // actually drives a decision.
    ratingChip: { paddingHorizontal: 8, paddingVertical: 3, borderWidth: 1, borderColor: 'transparent' },
    rating: { fontSize: 11, ...font(800), letterSpacing: 0.3 },
    // A real 44pt target rather than a hitSlop'd label: it now sits beside the
    // row instead of inside it, so it has room to be one. `paddingTop` matches
    // the body's own top padding, which puts the label on the ticker's line.
    expandBtn: { minHeight: MIN_TOUCH_TARGET, paddingTop: 14, paddingLeft: 12 },
    expandHint: { color: t.accent700 ?? t.accent, fontSize: 11, ...font(600) },

    moneyRow: { flexDirection: 'row', gap: 14, flexWrap: 'wrap' },
    money: { color: t.textPrimary, fontSize: 13, ...font(600), ...TABULAR },
    moneyLabel: { color: t.textSecondary, ...font(400) },
    rowMeta: { color: t.textSecondary, fontSize: 11, ...font(400) },

    expandBox: { paddingBottom: 16, gap: 12 },
    council: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 6 },
    councilLabel: { color: t.textSecondary, fontSize: 11, ...font(400) },
    agent: { gap: 4 },
    agentHead: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    agentName: { color: t.textPrimary, fontSize: 13, ...font(800) },
    // Model tag: outlined, so it reads as metadata rather than as a rating.
    badge: {
      fontSize: 10,
      ...font(800),
      borderWidth: 1,
      paddingHorizontal: 6,
      paddingVertical: 1,
      overflow: 'hidden',
    },
    agentBody: { color: t.textSecondary, fontSize: 13, lineHeight: 19, ...font(400) },
    agentMeta: { color: t.textSecondary, fontSize: 11, ...TABULAR, ...font(400) },
    detailBtn: { alignSelf: 'flex-start' },
    detailLink: { color: t.accent700 ?? t.accent, fontSize: 13, ...font(600) },

    runBlock: { marginTop: 20 },
    // 2px between sections, 1px between rows.
    runHead: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'baseline',
      borderBottomWidth: 2,
      borderBottomColor: t.divider,
      paddingBottom: 6,
      marginBottom: 4,
    },
    section: { color: t.textPrimary, fontSize: 15, ...font(800) },
    runHeadMeta: { color: t.textSecondary, fontSize: 11, ...font(400) },
    runRow: {
      flexDirection: 'row',
      gap: 10,
      paddingVertical: 8,
      borderBottomWidth: 1,
      borderBottomColor: t.divider,
    },
    runDot: { width: 10, height: 10, marginTop: 4 },
    runText: { flex: 1, gap: 2 },
    runStage: { color: t.textPrimary, fontSize: 13, ...font(600) },
    runTime: { color: t.textSecondary, ...font(400), ...TABULAR },
    runDetail: { color: t.textSecondary, fontSize: 11, lineHeight: 16, ...font(400) },
    runTotals: { color: t.textSecondary, fontSize: 11, marginTop: 8, ...TABULAR, ...font(600) },
    runNote: { color: t.textSecondary, fontSize: 11, lineHeight: 16, marginTop: 4, ...font(400) },

    muted: { color: t.textSecondary, fontSize: 13, ...font(400) },
    disclaimer: {
      color: t.textSecondary,
      fontSize: 11,
      paddingVertical: 20,
      textAlign: 'center',
      ...font(400),
    },
  });
