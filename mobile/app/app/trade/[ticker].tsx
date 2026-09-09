/**
 * Karar detayı — one decision, in full.
 *
 * Reached by tapping a row on Ajanlar (or "Tam detay →" from Sor / Emirler).
 * Read-only by design: approving or rejecting happens on Emirler, where the
 * order — not the decision — lives.
 *
 * What changed against the handoff:
 *
 *  - The stat block is the six-cell ruled grid the design specifies (Giriş,
 *    Stop, Kâr al, Hedef, Vade, Boyut) between two 2px rules, not four figures
 *    in a row with Vade and Boyut trailing underneath as prose. Six cells on a
 *    three-column grid is the same shape the order detail uses, so the numbers
 *    a trader compares sit in the same places on both screens.
 *  - The council row was missing entirely.
 *  - The screen padding is the mobile 20 / 16 the system uses everywhere else;
 *    this one was on 24 all round.
 *
 * Every figure goes through `@/utils/format`, every token/latency through
 * `@/utils/decision`, and the rating and model marks through `@/theme/rating`.
 * Nothing here re-states a rule one of those already encodes.
 */

import { View, Text, StyleSheet, ScrollView, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import { useLocalSearchParams, useRouter } from 'expo-router';

import { useMemo } from 'react';

import { useDecisions } from '@/api/hooks';
import { useTheme } from '@/theme/useTheme';
import { ratingChip, modelBadge } from '@/theme/rating';
import { Tag } from '@/components/Tag';
import { formatUsd, formatPct } from '@/utils/format';
import { formatTokens, formatLatency, debateEntries, debateRoleLabel,
  councilChips,
} from '@/utils/decision';
import { formatOrderDate } from '@/utils/orders';
import { MIN_TOUCH_TARGET } from '@/utils/a11y';
import { font, TABULAR } from '@/theme/type';

type Palette = ReturnType<typeof useTheme>;

/**
 * Which models ruled on this name, and how many agents each carried.
 *
 * The prototype chips a per-model VOTE here off a `council: {votes, chair,
 * confidence}` block. `AgentDecision` carries no such field — the council is
 * opt-in, off by default, and only the chair's rating reaches the wire — so
 * these chips report participation, labelled "ajan", and never imply a second
 * opinion the backend did not send. Same derivation as the Ajanlar list, which
 * is why it wants to live in `src/utils/decision.ts` rather than in two route
 * files (see `needs`).
 */
export default function DecisionDetailScreen() {
  const { t } = useTranslation();
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);
  const { ticker } = useLocalSearchParams<{ ticker: string }>();
  const router = useRouter();
  const { data, isLoading } = useDecisions({ ticker, limit: 1 });

  const decision = data?.[0];
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

  const chip = decision ? ratingChip(theme, decision.rating) : null;

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView contentContainerStyle={styles.scroll}>
        <Pressable
          onPress={() => router.back()}
          style={styles.back}
          accessibilityRole="button"
          accessibilityLabel="Ajanlar listesine dön"
        >
          <Text style={styles.backText}>← Ajanlar</Text>
        </Pressable>

        {isLoading || !decision || !chip ? (
          <>
            <Text style={styles.title}>{ticker}</Text>
            <Text style={styles.muted}>{isLoading ? 'Yükleniyor…' : 'Karar bulunamadı.'}</Text>
          </>
        ) : (
          <>
            <View style={styles.titleRow}>
              <Text style={styles.title}>{decision.ticker}</Text>
              {/* The rating used to render as plain body ink here, so the one
                  screen that shows the FULL reasoning was also the one that
                  dropped the buy/hold/sell encoding. Same chip as everywhere. */}
              {/* `ratingChip` returns `background`, not `backgroundColor` —
                  a CSS name, because the web prototype it was ported from
                  writes CSS. Spreading it straight into a View style dropped
                  the fill on native (RN ignores the unknown key), and a Buy
                  chip draws its text in `t.background`, so the rating rendered
                  as ground-on-ground: invisible. Mapped explicitly, the way
                  orders.tsx and approve/[orderId].tsx already do it. */}
              <View
                style={[
                  styles.ratingChip,
                  { backgroundColor: chip.background, borderColor: chip.borderColor ?? 'transparent' },
                ]}
              >
                <Text style={[styles.ratingText, { color: chip.color }]}>{decision.rating}</Text>
              </View>
            </View>

            <Text style={styles.timestamp}>{formatOrderDate(decision.timestamp_utc)}</Text>

            <View style={styles.grid}>
              {cells.map(([label, value]) => (
                <View key={label} style={styles.cell}>
                  <Text style={styles.cellLabel}>{label}</Text>
                  <Text style={styles.cellValue}>{value}</Text>
                </View>
              ))}
            </View>

            <View style={styles.council}>
              <Text style={styles.councilLabel}>Konsey</Text>
              {councilChips(decision.reasoning, (m) => modelBadge(theme, m).label).map((label) => (
                <Tag key={label} label={label} variant="neutral" />
              ))}
            </View>

            {decision.reasoning?.length ? (
              <>
                <View style={styles.sectionHead}>
                  <Text style={styles.section}>Ajan analizleri</Text>
                  <Text style={styles.sectionMeta}>~{formatTokens(totalTokens)} token</Text>
                </View>
                {decision.reasoning.map((r, i) => {
                  const badge = modelBadge(theme, r.model);
                  return (
                    <View key={`${decision.decision_id}-${i}`} style={styles.agentCard}>
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
              </>
            ) : null}

            {debate.length ? (
              <>
                <View style={styles.sectionHead}>
                  <Text style={styles.section}>Tartışma</Text>
                </View>
                {debate.map((entry) => (
                  <View key={entry.role} style={styles.debateCard}>
                    <Text style={styles.debateRole}>{debateRoleLabel(entry.role)}</Text>
                    <Text style={styles.body}>{entry.text}</Text>
                  </View>
                ))}
              </>
            ) : null}

            <View style={styles.sectionHead}>
              <Text style={styles.section}>Portföy yöneticisi çıktısı</Text>
            </View>
            <Text style={styles.body}>{decision.final_decision_text ?? '(PM metni yok)'}</Text>

            <Text style={styles.note}>
              Onay/red, emir bekleyen listeye düştüğünde Emirler'den yapılır.
            </Text>
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

    // 44pt, per the handoff — the back button on a money screen is a real target.
    back: { alignSelf: 'flex-start', minHeight: MIN_TOUCH_TARGET, justifyContent: 'center' },
    backText: { color: t.textPrimary, fontSize: 13, ...font(800) },

    titleRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
    // 34px, the size the mobile prototype gives this heading — between h2 and
    // the hero figure, because the ticker IS the screen here.
    title: { color: t.textPrimary, fontSize: 34, ...font(800) },
    ratingChip: { paddingHorizontal: 10, paddingVertical: 4, borderWidth: 1, borderColor: 'transparent' },
    ratingText: { fontSize: 13, ...font(800), letterSpacing: 0.4 },
    timestamp: { color: t.textSecondary, fontSize: 11, marginTop: 4, ...font(400) },

    // The six-cell grid, ruled top and bottom at 2px. Percentage widths rather
    // than flex, so cell four sits under cell one whatever the label lengths.
    grid: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      rowGap: 12,
      marginTop: 14,
      paddingVertical: 14,
      borderTopWidth: 2,
      borderBottomWidth: 2,
      borderColor: t.divider,
    },
    cell: { width: '33.333%', paddingRight: 8 },
    cellLabel: { color: t.textSecondary, fontSize: 11, ...font(400) },
    cellValue: { color: t.textPrimary, fontSize: 15, marginTop: 2, ...font(800), ...TABULAR },

    council: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      alignItems: 'center',
      gap: 6,
      marginTop: 14,
    },
    councilLabel: { color: t.textSecondary, fontSize: 11, ...font(400) },

    // The rule belongs to the ROW, not to the label: inside a row a Text sizes
    // to its content, so an underline on the label stops at the last letter
    // while every other section rule on the screen spans the column.
    sectionHead: {
      flexDirection: 'row',
      alignItems: 'baseline',
      justifyContent: 'space-between',
      marginTop: 24,
      marginBottom: 8,
      borderBottomWidth: 2,
      borderBottomColor: t.divider,
      paddingBottom: 6,
    },
    section: { color: t.textPrimary, fontSize: 15, ...font(800) },
    sectionMeta: { color: t.textSecondary, fontSize: 11, ...TABULAR, ...font(400) },

    agentCard: { paddingVertical: 12, gap: 6, borderBottomWidth: 1, borderBottomColor: t.divider },
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

    debateCard: { paddingVertical: 12, gap: 4, borderBottomWidth: 1, borderBottomColor: t.divider },
    debateRole: { color: t.textPrimary, fontSize: 13, ...font(800) },
    body: { color: t.textSecondary, fontSize: 13, lineHeight: 20, ...font(400) },

    muted: { color: t.textSecondary, fontSize: 13, marginTop: 8, ...font(400) },
    note: { color: t.textSecondary, fontSize: 11, lineHeight: 16, marginTop: 16, ...font(400) },
    disclaimer: {
      color: t.textSecondary,
      fontSize: 11,
      paddingVertical: 20,
      textAlign: 'center',
      ...font(400),
    },
  });
