/**
 * Karar detayı — one decision, in full, in Aurora.
 *
 * Reached by tapping a row on Ajanlar (or "Tam detay →" from Sor / Emirler).
 * Read-only by design: approving or rejecting happens on Emirler, where the
 * order — not the decision — lives.
 *
 * What the Aurora port changed, and what it deliberately did not.
 *
 * Changed — the DRAWING only. The six figures that were two 2px rules with
 * bare cells between them are now the prototype's ruled card: one `Card` with
 * a three-column grid of `StatCell`s, the same cell the Bugün hero and the
 * approve sheet use, so a number sits in the same shape wherever it appears.
 * Agent analyses and the debate move onto cards as well; the PM output becomes
 * the one inverted slab on the screen, which is how the prototype marks the
 * sentence that actually decided something. Headings go through
 * `SectionHeader`, chips through `Tag`, radius/spacing through `useShape`.
 *
 * Not changed — the RULES. Every figure still goes through `@/utils/format`,
 * every token/latency through `@/utils/decision`, the council chips through
 * `councilChips`, and the rating/model marks through `@/theme/rating`. The
 * error branch is still separate from the empty branch: "Karar bulunamadı." is
 * an assertion that the council never rated this ticker, and a refused read
 * must not be allowed to manufacture it.
 *
 * Two affordances the prototype has and this screen did not: the chart button
 * beside the ticker and "Yeniden analiz et" under the PM output. Both are pure
 * navigation to routes that already take `?ticker=` (`/(tabs)/charts`,
 * `/(tabs)/ask`) — no new rule, no new request shape.
 */

import { View, Text, StyleSheet, ScrollView, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import { useLocalSearchParams, useRouter } from 'expo-router';
import Svg, { Path } from 'react-native-svg';

import { useMemo } from 'react';

import { useDecision, useDecisions } from '@/api/hooks';
import { ErrorState } from '@/components/ErrorState';
import { Card } from '@/components/Card';
import { SectionHeader } from '@/components/SectionHeader';
import { StatCell } from '@/components/StatCell';
import { Tag } from '@/components/Tag';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { ratingVariant, modelBadge } from '@/theme/rating';
import { formatUsd, formatPct } from '@/utils/format';
import {
  formatTokens,
  formatLatency,
  debateEntries,
  debateRoleLabel,
  councilChips,
} from '@/utils/decision';
import { formatOrderDate } from '@/utils/orders';
import { MIN_TOUCH_TARGET } from '@/utils/a11y';
import { TYPE, TABULAR, font } from '@/theme/type';

type Palette = ReturnType<typeof useTheme>;

/**
 * The floating tab bar's clearance. Stated here rather than imported from the
 * shell: a route module's exports are the router's namespace, not a place to
 * hang shared constants. Same number the Bugün screen uses.
 */
const TAB_BAR_CLEARANCE = 72;

export default function DecisionDetailScreen() {
  const { t: tr } = useTranslation();
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const { ticker } = useLocalSearchParams<{ ticker: string }>();
  const router = useRouter();
  const { data, isLoading, isError, error, refetch } = useDecisions({ ticker, limit: 1 });
  // The list carries previews; this screen is where the reasoning is actually
  // read, so it asks for the untrimmed record by id.
  const listed = data?.[0];
  const { data: full } = useDecision(listed?.decision_id);

  // Prefer the untrimmed record; fall back to the listed preview while it
  // loads, so the screen renders immediately rather than blanking and filling.
  const decision = full ?? listed;
  const debate = debateEntries(decision?.debate_transcript);
  const totalTokens = (decision?.reasoning ?? []).reduce(
    (sum, r) => sum + (r.tokens_in ?? 0) + (r.tokens_out ?? 0),
    0,
  );

  // The six cells, in the handoff's order. An array rather than six copies of
  // the same four lines of JSX — the grid is the point, not the markup.
  const cells: [string, string][] = decision
    ? [
        ['Giriş', formatUsd(decision.entry_price)],
        ['Stop', formatUsd(decision.stop_loss)],
        ['Kâr al', formatUsd(decision.take_profit)],
        ['Hedef', formatUsd(decision.price_target)],
        ['Vade', decision.time_horizon ?? '—'],
        ['Boyut', formatPct(decision.suggested_size_pct)],
      ]
    : [];

  const shown = decision?.ticker ?? ticker;

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <ScrollView contentContainerStyle={styles.content}>
        <Pressable
          onPress={() => router.back()}
          style={({ pressed }) => [styles.back, pressed && styles.backPressed]}
          accessibilityRole="button"
          accessibilityLabel="Ajanlar listesine dön"
        >
          <Text style={styles.backText}>← Ajanlar</Text>
        </Pressable>

        {isError ? (
          /* "Karar bulunamadı." is an assertion that the council never rated
             this ticker. On a 401 that assertion is manufactured out of a
             refused read — the app speaking for the agents about work it was
             not allowed to see. A failed read and an empty result must not
             share a branch here. */
          <>
            <Text style={styles.title} accessibilityRole="header">
              {ticker}
            </Text>
            <ErrorState
              title="Karar okunamadı"
              detail={error}
              onRetry={() => void refetch()}
            />
          </>
        ) : isLoading || !decision ? (
          <>
            <Text style={styles.title} accessibilityRole="header">
              {ticker}
            </Text>
            <Card tone="dashed" style={styles.slot}>
              <Text style={styles.slotText}>
                {isLoading ? 'Yükleniyor…' : 'Karar bulunamadı.'}
              </Text>
            </Card>
          </>
        ) : (
          <>
            <View style={styles.titleRow}>
              <Text style={styles.title} accessibilityRole="header">
                {shown}
              </Text>
              {/* The rating used to render as plain body ink here, so the one
                  screen that shows the FULL reasoning was also the one that
                  dropped the buy/hold/sell encoding. Same chip as everywhere —
                  and via `Tag`, so the fill/ink pairing lives in one place
                  rather than being mapped by hand on five screens. */}
              <Tag label={decision.rating} variant={ratingVariant(decision.rating)} caps />
              <Pressable
                onPress={() => router.push(`/(tabs)/charts?ticker=${shown}` as never)}
                style={({ pressed }) => [styles.iconButton, pressed && styles.iconButtonPressed]}
                accessibilityRole="button"
                accessibilityLabel={`${shown} grafiğini aç`}
              >
                <Svg width={18} height={18} viewBox="0 0 24 24" fill="none">
                  <Path
                    d="M3 3v18h18"
                    stroke={t.textPrimary}
                    strokeWidth={2}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                  <Path
                    d="M7 14l4-5 4 3 5-7"
                    stroke={t.textPrimary}
                    strokeWidth={2}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </Svg>
              </Pressable>
            </View>

            <Text style={styles.timestamp}>{formatOrderDate(decision.timestamp_utc)}</Text>

            {/* The prototype's ruled six-up, now a card: three columns, so cell
                four sits under cell one whatever the label lengths. Percentage
                widths rather than flex, for the same reason. */}
            <Card style={styles.gridCard}>
              <View style={styles.grid}>
                {cells.map(([label, value]) => (
                  <View key={label} style={styles.cell}>
                    <StatCell label={label} value={value} size="sm" />
                  </View>
                ))}
              </View>
            </Card>

            <View style={styles.council}>
              <Text style={styles.councilLabel}>Konsey</Text>
              {councilChips(decision.reasoning, (m) => modelBadge(t, m).label).map((label) => (
                <Tag key={label} label={label} variant="neutral" size="sm" numeric />
              ))}
            </View>

            {decision.reasoning?.length ? (
              <>
                <SectionHeader
                  title="Ajan analizleri"
                  count={`~${formatTokens(totalTokens)} token`}
                />
                <Card padded={false} style={styles.listCard}>
                  {decision.reasoning.map((r, i) => {
                    const badge = modelBadge(t, r.model);
                    const last = i === decision.reasoning.length - 1;
                    return (
                      <View
                        key={`${decision.decision_id}-${i}`}
                        style={[styles.agentRow, last && styles.rowLast]}
                      >
                        <View style={styles.agentHead}>
                          <Text style={styles.agentName}>{r.agent}</Text>
                          {/* Outlined, so the model reads as metadata rather
                              than as a second rating. */}
                          <View style={[styles.badge, { borderColor: badge.color }]}>
                            <Text style={[styles.badgeText, { color: badge.color }]}>
                              {badge.label}
                            </Text>
                          </View>
                        </View>
                        <Text style={styles.agentBody}>{r.summary}</Text>
                        <Text style={styles.agentMeta}>
                          {formatTokens(r.tokens_in)}↓ / {formatTokens(r.tokens_out)}↑ token ·{' '}
                          {formatLatency(r.latency_ms)}
                        </Text>
                      </View>
                    );
                  })}
                </Card>
              </>
            ) : null}

            {debate.length ? (
              <>
                <SectionHeader title="Tartışma" />
                <View style={styles.debateList}>
                  {debate.map((entry) => (
                    <Card key={entry.role}>
                      <Text style={styles.debateRole}>{debateRoleLabel(entry.role)}</Text>
                      <Text style={styles.body}>{entry.text}</Text>
                    </Card>
                  ))}
                </View>
              </>
            ) : null}

            <SectionHeader title="Portföy yöneticisi çıktısı" />
            {/* The one inverted slab on the screen. The prototype fills it with
                `--ink` and writes on it in `--inkInv`, which under Aurora is a
                light card with dark text on a dark page: the sentence that
                actually decided something, and the only one drawn that way. */}
            <View style={styles.pm}>
              <Text style={styles.pmText}>
                {decision.final_decision_text_tr ??
                  decision.final_decision_text ??
                  '(PM metni yok)'}
              </Text>
            </View>

            <Pressable
              onPress={() => router.push(`/(tabs)/ask?ticker=${shown}` as never)}
              style={({ pressed }) => [styles.askButton, pressed && styles.askPressed]}
              accessibilityRole="button"
              accessibilityLabel={`${shown} için yeniden analiz çalıştır`}
            >
              <Text style={styles.askLabel}>Yeniden analiz et</Text>
            </Pressable>

            <Text style={styles.note}>
              Onay / red, emir bekleyen listeye düştüğünde Emirler&apos;den yapılır.
            </Text>
          </>
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

    // 44pt, per the handoff — the back button on a money screen is a real target.
    back: {
      alignSelf: 'flex-start',
      minHeight: MIN_TOUCH_TARGET,
      justifyContent: 'center',
      paddingRight: sh.space[1],
    },
    backPressed: { opacity: 0.6 },
    backText: { ...TYPE.body, ...font(600), color: t.ink2 ?? t.textSecondary },

    titleRow: { flexDirection: 'row', alignItems: 'center', gap: sh.space[2] },
    // 34px, the size the prototype gives this heading — between h2 and the hero
    // figure, because the ticker IS the screen here.
    title: { ...TYPE.h2, fontSize: 34, letterSpacing: -1, color: t.textPrimary },
    // 40px in the prototype; 44 here, because it is a real tap target.
    iconButton: {
      marginLeft: 'auto',
      width: MIN_TOUCH_TARGET,
      height: MIN_TOUCH_TARGET,
      borderRadius: sh.radius,
      borderWidth: sh.hairline,
      borderColor: t.line ?? t.divider,
      backgroundColor: t.surface,
      alignItems: 'center',
      justifyContent: 'center',
    },
    iconButtonPressed: { borderColor: t.brand ?? t.accent },
    timestamp: { ...TYPE.helper, color: t.ink3 ?? t.textMuted, marginTop: 2 },

    gridCard: { marginTop: sh.space[2], paddingVertical: sh.space[2] },
    grid: { flexDirection: 'row', flexWrap: 'wrap', rowGap: sh.space[2] },
    cell: { width: '33.333%', paddingRight: sh.space[1] },

    council: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      alignItems: 'center',
      gap: sh.space[0] + 2,
      marginTop: sh.space[2],
    },
    councilLabel: { ...TYPE.helper, color: t.ink3 ?? t.textMuted },

    listCard: { paddingHorizontal: sh.space[3] },
    agentRow: {
      paddingVertical: sh.space[2],
      gap: sh.space[0] + 2,
      borderBottomWidth: sh.hairline,
      borderBottomColor: t.line ?? t.divider,
    },
    rowLast: { borderBottomWidth: 0 },
    agentHead: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1], flexWrap: 'wrap' },
    agentName: { ...TYPE.bodyStrong, ...font(800), color: t.textPrimary },
    badge: {
      borderWidth: sh.hairline,
      borderRadius: sh.radiusPill,
      paddingHorizontal: 7,
      paddingVertical: 1,
    },
    badgeText: { fontSize: 10, ...font(800) },
    agentBody: { ...TYPE.body, lineHeight: 20, color: t.ink2 ?? t.textSecondary },
    agentMeta: { ...TYPE.helper, ...TABULAR, color: t.ink3 ?? t.textMuted },

    debateList: { gap: sh.space[1] },
    // 11px uppercase kicker, as the prototype stamps the role.
    debateRole: { ...TYPE.kicker, color: t.ink3 ?? t.textMuted, marginBottom: sh.space[0] },
    body: { ...TYPE.body, lineHeight: 20, color: t.textPrimary },

    pm: {
      backgroundColor: t.ink ?? t.textPrimary,
      borderRadius: sh.radius,
      paddingHorizontal: sh.space[3],
      paddingVertical: sh.space[2] + 4,
    },
    pmText: { ...TYPE.body, lineHeight: 21, color: t.inkInv ?? t.background },

    askButton: {
      marginTop: sh.space[2],
      minHeight: MIN_TOUCH_TARGET,
      borderRadius: sh.radius,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
      backgroundColor: t.surface,
      alignItems: 'center',
      justifyContent: 'center',
    },
    askPressed: { borderColor: t.brand ?? t.accent },
    askLabel: { ...TYPE.bodyStrong, ...font(800), color: t.textPrimary },

    slot: { marginTop: sh.space[2], paddingVertical: sh.space[4], alignItems: 'center' },
    slotText: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, textAlign: 'center' },

    note: {
      ...TYPE.helper,
      lineHeight: 16,
      color: t.ink3 ?? t.textMuted,
      marginTop: sh.space[3],
    },
    disclaimer: {
      ...TYPE.helper,
      lineHeight: 16,
      color: t.ink3 ?? t.textMuted,
      textAlign: 'center',
      marginTop: sh.space[3],
    },
  });
