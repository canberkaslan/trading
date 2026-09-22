/**
 * Sor — a conversation, not a form.
 *
 * The screen used to be a single input with one result card underneath, so the
 * second question erased the answer to the first. The handoff specifies a chat:
 * the questions asked stay on screen as right-aligned ink pills, each answer
 * opens with the PM kicker on its own card, and the composer lives at the
 * bottom under the section rule. Nothing about the request changes —
 * `useStartAnalysis` fires the job and `useAnalysisJob` polls it as before; the terminal state is appended to
 * the stream instead of replacing the one card the screen could hold.
 *
 * State is the handoff's `chat{messages,input,busy,status}`. `busy`/`status` are
 * mirrored from the query rather than owned here, so the indicator cannot drift
 * from what the poller actually knows.
 */

import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  Pressable,
  ScrollView,
  Keyboard,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import Svg, { Path } from 'react-native-svg';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { HTTPError } from 'ky';

import { useStartAnalysis, useAnalysisJob } from '@/api/hooks';
import { useIsAdmin } from '@/api/useMe';
import {
  useMarketMovers,
  useTickerSearch,
  type MoverSort,
  type MoverUniverse,
} from '@/api/useTickerSearch';
import { ErrorState } from '@/components/ErrorState';
import { Card } from '@/components/Card';
import { DataRow } from '@/components/DataRow';
import { StatCell } from '@/components/StatCell';
import { statusLine } from '@/utils/askStatus';
import type { AgentDecision, } from '@/api/types';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { ratingChip, modelBadge } from '@/theme/rating';
import { font, TABULAR, TYPE } from '@/theme/type';
import { formatPct, formatUsd } from '@/utils/format';
import { MIN_TOUCH_TARGET, hitSlopFor } from '@/utils/a11y';
import { BlinkSquare } from '@/components/BlinkSquare';

/** The six symbols the prototype offers before the first question. */
const CHIPS = ['AAPL', 'NVDA', 'MSFT', 'GOOGL', 'AMZN', 'AVGO'] as const;

/** The link back to the full decision is 13px of text; slop it up to 44. */
const LINK_HEIGHT = 20;



/**
 * Why the job never started. A 422 is the backend ANSWERING — it takes only
 * letters, at most six of them — so reporting it as "backend unreachable"
 * blames the network for something the user can fix in the field. Same
 * `HTTPError` check the approve screen uses for its risk-guard refusal.
 */
function startErrorTr(error: unknown, ticker: string): string {
  if (error instanceof HTTPError && error.response.status === 422) {
    return `${ticker} kabul edilmedi — sembol yalnızca harf, en fazla 6 karakter olabilir.`;
  }
  return "İstek başarısız — backend'e ulaşılamadı.";
}

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  /** Present on an answer that carried a decision; drives the strip + detail link. */
  decision?: AgentDecision | null;
  /** A transport/pipeline failure rather than an answer — no PM byline. */
  failed?: boolean;
}

interface ChatState {
  messages: ChatMessage[];
  input: string;
  busy: boolean;
  status: string;
}

const EMPTY_CHAT: ChatState = { messages: [], input: '', busy: false, status: '' };

/**
 * Which model signed the answer — or nothing, if the payload does not say.
 *
 * `final_decision_text` is the portfolio manager's output, so only the
 * `portfolio_manager` reasoning entry can name the model that wrote the
 * paragraph below the byline. There is no sane fallback: the pipeline appends
 * the council chair and one entry per council member AFTER the PM, so "the
 * last entry" is usually a council voter, and the first is a data analyst.
 * Stamping either model under "PORTFOLIO MANAGER" would be a fabricated
 * attribution, so a decision without a PM entry gets the byline alone.
 */
function pmModel(decision: AgentDecision | null | undefined): string | null {
  const pm = decision?.reasoning?.find((r) => r.agent.toLowerCase().includes('portfolio'));
  return pm?.model ?? null;
}

export default function AskScreen() {
  // `t` is the palette everywhere in this file, as in every other Aurora
  // screen; the translator takes the short name instead.
  const { t: tr } = useTranslation();
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);
  const router = useRouter();
  const params = useLocalSearchParams<{ ticker?: string }>();

  const [chat, setChat] = useState<ChatState>(EMPTY_CHAT);
  const [jobId, setJobId] = useState<string | null>(null);
  const start = useStartAnalysis();
  const jobQuery = useAnalysisJob(jobId);
  const job = jobQuery.data;

  const lastDeepLink = useRef<string | null>(null);
  /** The ticker of the request in flight, for the status line before the first poll lands. */
  const askedTicker = useRef('');
  /** Job ids whose terminal state is already in the stream, so it is appended once. */
  const settled = useRef<string | null>(null);
  /**
   * Job ids that have already reported a poll failure. Tracked apart from
   * `settled` because a failed poll is not a terminal state: the query keeps
   * retrying every 3s, so the job can still finish and must still be able to
   * append its answer.
   */
  const notified = useRef<string | null>(null);
  const scrollRef = useRef<ScrollView>(null);

  // A started job is still "running" in the window between the mutation
  // settling and the first poll returning — without this the blinker would
  // blink off for a beat right after every question.
  const polling =
    !!jobId &&
    !jobQuery.isError &&
    (!job || job.status === 'queued' || job.status === 'running');
  const isAdmin = useIsAdmin();
  const { data: hits } = useTickerSearch(chat.input);
  const [browseOpen, setBrowseOpen] = useState(false);
  const [sort, setSort] = useState<MoverSort>('volume');
  const [universe, setUniverse] = useState<MoverUniverse>('sp500');
  const {
    data: movers,
    isLoading: moversLoading,
    error: moversError,
    refetch: refetchMovers,
  } = useMarketMovers(sort, universe, browseOpen);
  // Hidden once the field holds an exact symbol from the list: the operator has
  // chosen, and a list still offering that same choice reads as not having
  // registered the tap.
  const suggestions = (hits ?? []).filter(
    (h) => h.ticker !== chat.input.trim().toUpperCase(),
  ).slice(0, 6);
  // Starting an analysis spends ~$1.61 of measured model time, so it is an
  // administrator action even though it never touches the broker.
  const busy = start.isPending || polling;

  // A clock, ticking only while something is running. Without it the elapsed
  // count would freeze between polls and the screen would look stuck again —
  // the exact thing this is here to disprove.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!busy) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [busy]);

  // Mirror the poller into the handoff's chat shape.
  useEffect(() => {
    setChat((c) => {
      const status = busy
        ? statusLine(
            job?.ticker ?? askedTicker.current,
            job?.status,
            job?.phase,
            job?.created_utc,
            now,
          )
        : '';
      if (c.busy === busy && c.status === status) return c;
      return { ...c, busy, status };
    });
  }, [busy, job?.ticker, job?.status, job?.phase, job?.created_utc, now]);

  // Terminal state -> one assistant message.
  useEffect(() => {
    if (!jobId) return;
    const append = (id: string, message: Omit<ChatMessage, 'id'>) =>
      setChat((c) => ({ ...c, messages: [...c.messages, { id, ...message }] }));

    if (job?.status === 'done' || job?.status === 'error') {
      if (settled.current === jobId) return;
      settled.current = jobId;
      if (job.status === 'done') {
        // Turkish when it exists, English otherwise. The English text stays
        // authoritative — a missing translation shows the original rather than
        // an empty report, because absence of a translation is not absence of
        // an answer.
        const text = (
          job.decision?.final_decision_text_tr ?? job.decision?.final_decision_text
        )?.trim();
        append(jobId, {
          role: 'assistant',
          text: text || 'Gerekçe metni gelmedi — ajan analizleri tam karar detayında.',
          decision: job.decision,
        });
      } else {
        append(jobId, {
          role: 'assistant',
          text: job.error?.trim() || 'Analiz tamamlanamadı.',
          failed: true,
        });
      }
    } else if (jobQuery.isError && notified.current !== jobId) {
      // Said once, and it does not close the job out: the poll keeps retrying,
      // and if it recovers the indicator comes back and the answer still lands
      // under this line.
      notified.current = jobId;
      append(`${jobId}:poll-error`, {
        role: 'assistant',
        text: 'Analiz durumu alınamadı — bağlantıyı kontrol et.',
        failed: true,
      });
    }
  }, [jobId, job, jobQuery.isError]);

  const runAnalysis = useCallback(
    (raw: string) => {
      const ticker = raw.trim().toUpperCase();
      if (!ticker || busy) return;
      Keyboard.dismiss();
      askedTicker.current = ticker;
      setChat((c) => ({
        messages: [...c.messages, { id: `ask-${Date.now()}`, role: 'user', text: ticker }],
        input: '',
        busy: true,
        status: statusLine(ticker, 'queued', null, undefined, Date.now()),
      }));
      start.mutate(ticker, {
        onSuccess: (j) => setJobId(j.job_id),
        onError: (e) =>
          setChat((c) => ({
            ...c,
            busy: false,
            status: '',
            messages: [
              ...c.messages,
              {
                id: `err-${Date.now()}`,
                role: 'assistant',
                text: startErrorTr(e, ticker),
                failed: true,
              },
            ],
          })),
      });
    },
    [busy, start],
  );

  // Deep-link from Portfolio/Agents: /(tabs)/ask?ticker=NVDA auto-runs once.
  useEffect(() => {
    const dl = params.ticker;
    if (dl && dl !== lastDeepLink.current) {
      lastDeepLink.current = dl;
      runAnalysis(dl);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.ticker]);

  const isEmpty = chat.messages.length === 0 && !chat.busy;

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      {/*
        No `keyboardVerticalOffset`: the tab bar is a flex SIBLING of the
        screen (the shell renders it below the scene container, not over it),
        so this view's frame already ends at the bar's top edge and the padding
        RN computes — keyboard height minus that gap — is exactly right. A
        positive offset is ADDED to that padding, so passing the bar's height
        would float the composer a whole tab bar above the keyboard.
      */}
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <View style={styles.header}>
          <Text style={styles.heading} accessibilityRole="header">
            Sor
          </Text>
          <Text style={styles.subheading}>
            Bir hisse gir — 7 ajanlı pipeline analiz eder. Sadece analiz, emir göndermez.
          </Text>
        </View>

        <ScrollView
          ref={scrollRef}
          style={styles.flex}
          contentContainerStyle={styles.stream}
          keyboardShouldPersistTaps="handled"
          onContentSizeChange={() => scrollRef.current?.scrollToEnd({ animated: true })}
        >
          {isEmpty ? (
            <>
              <Text style={styles.emptyKicker}>Sık kullanılanlar</Text>
              <View style={styles.chips}>
                {CHIPS.map((c) => (
                  <Pressable
                    key={c}
                    style={({ pressed }) => [styles.chip, pressed && styles.chipPressed]}
                    onPress={() => runAnalysis(c)}
                    accessibilityRole="button"
                    accessibilityLabel={`${c} analiz et`}
                  >
                    <Text style={styles.chipText}>{c}</Text>
                  </Pressable>
                ))}
              </View>

              {/* The board, not a list of names. The first version showed
                  sixty symbols and their company names, which answers "which
                  stocks exist" rather than "what moved today" — the question
                  every market screen answers and the one that was asked.
                  Aurora draws the entry as the prototype's market card: the
                  brand-tinted glyph, the two-line label, a caret that turns. */}
              <Pressable
                onPress={() => setBrowseOpen((v) => !v)}
                style={({ pressed }) => [
                  styles.marketCard,
                  pressed && styles.marketCardPressed,
                ]}
                accessibilityRole="button"
                accessibilityLabel={browseOpen ? 'Piyasa tablosunu kapat' : 'Piyasa tablosunu aç'}
                accessibilityState={{ expanded: browseOpen }}
              >
                <View style={styles.marketIcon}>
                  <Svg
                    width={20}
                    height={20}
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke={t.brand ?? t.accent}
                    strokeWidth={2}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <Path d="M22 7 13.5 15.5 8.5 10.5 2 17" />
                    <Path d="M16 7h6v6" />
                  </Svg>
                </View>
                <View style={styles.marketText}>
                  <Text style={styles.marketTitle}>Piyasa tahtası</Text>
                  <Text style={styles.marketSub} numberOfLines={1}>
                    Yükselenler, düşenler ve en çok işlem görenler
                  </Text>
                </View>
                <View style={browseOpen ? styles.caretOpen : undefined}>
                  <Svg
                    width={16}
                    height={16}
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke={t.ink3 ?? t.textMuted}
                    strokeWidth={2}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <Path d="m9 18 6-6-6-6" />
                  </Svg>
                </View>
              </Pressable>

              {browseOpen ? (
                <View style={styles.board}>
                  <View style={styles.moverTabs}>
                    {(
                      [
                        ['volume', 'En çok işlem'],
                        ['gainers', 'Yükselenler'],
                        ['losers', 'Düşenler'],
                      ] as const
                    ).map(([key, label]) => (
                      <Pressable
                        key={key}
                        style={[styles.moverTab, sort === key && styles.moverTabOn]}
                        onPress={() => setSort(key)}
                        accessibilityRole="button"
                        accessibilityLabel={label}
                        accessibilityState={{ selected: sort === key }}
                      >
                        <Text style={[styles.moverTabText, sort === key && styles.moverTabTextOn]}>
                          {label}
                        </Text>
                      </Pressable>
                    ))}
                    {/* Defaults ON. Unfiltered, the gainers board is penny
                        stocks — a real session had an $8 name at +179% — and
                        a screen whose top row is that is one the operator
                        learns to ignore. */}
                    <Pressable
                      style={[styles.moverTab, universe === 'sp500' && styles.moverTabOn]}
                      onPress={() => setUniverse((u) => (u === 'sp500' ? 'all' : 'sp500'))}
                      accessibilityRole="button"
                      accessibilityLabel="Sadece S&P 500"
                      accessibilityState={{ selected: universe === 'sp500' }}
                    >
                      <Text
                        style={[
                          styles.moverTabText,
                          universe === 'sp500' && styles.moverTabTextOn,
                        ]}
                      >
                        S&P 500
                      </Text>
                    </Pressable>
                  </View>

                  {moversError ? (
                    <ErrorState
                      title="Piyasa verisi alınamadı"
                      detail={moversError}
                      onRetry={() => void refetchMovers()}
                    />
                  ) : moversLoading ? (
                    <Text style={styles.hint}>Yükleniyor…</Text>
                  ) : (movers ?? []).length === 0 ? (
                    <Text style={styles.hint}>Gösterilecek hareket yok.</Text>
                  ) : (
                    <Card padded={false} clip>
                      {(movers ?? []).map((m, i, arr) => (
                        <DataRow
                          key={m.ticker}
                          title={m.ticker}
                          subtitle={m.name}
                          divider={i < arr.length - 1}
                          onPress={() => runAnalysis(m.ticker)}
                          accessibilityLabel={`${m.ticker}, ${m.name}, ${m.change_pct.toFixed(2)} yüzde`}
                          accessibilityHint="Bu sembolü analiz eder"
                          trailing={
                            <View style={styles.moverRight}>
                              <Text style={styles.moverPrice}>{formatUsd(m.price)}</Text>
                              <Text
                                style={[
                                  styles.moverChange,
                                  {
                                    color:
                                      m.change_pct > 0
                                        ? t.up
                                        : m.change_pct < 0
                                          ? (t.downText ?? t.down)
                                          : (t.ink2 ?? t.textSecondary),
                                  },
                                ]}
                              >
                                {formatPct(m.change_pct / 100, { signed: true })}
                              </Text>
                            </View>
                          }
                        />
                      ))}
                    </Card>
                  )}
                </View>
              ) : null}

              {/* What a question costs, said before it is asked. */}
              <Card tone="recessed">
                <Text style={styles.noteText}>
                  Bir analiz yaklaşık 10 dakika sürer. Ajanların vardığı karar yalnız bilgi
                  amaçlıdır; emir üretmez.
                </Text>
              </Card>
            </>
          ) : null}

          {chat.messages.map((m) =>
            m.role === 'user' ? (
              <View key={m.id} style={styles.userRow}>
                <View style={styles.userBubble}>
                  <Text style={styles.userText}>{m.text}</Text>
                </View>
              </View>
            ) : m.failed ? (
              <Card key={m.id} tone="outline">
                <Text style={styles.errorLine}>{m.text}</Text>
              </Card>
            ) : (
              <AgentMessage
                key={m.id}
                message={m}
                styles={styles}
                theme={t}
                onOpenDetail={(ticker) => router.push(`/trade/${ticker}` as never)}
              />
            ),
          )}

          {chat.busy ? (
            <Card>
              <View style={styles.running} accessibilityLiveRegion="polite">
                <BlinkSquare />
                <Text style={styles.runningText}>{chat.status}</Text>
              </View>
            </Card>
          ) : null}
        </ScrollView>

        {/* The rule, then the composer — the one control the screen always shows. */}
        <View style={styles.composer}>
          {/* Matches from the whole US listing. Analysing any stock always
              worked; finding one did not, because this was a bare text box and
              the operator had to know the symbol already. Shown above the
              input so a tap lands where the thumb already is. */}
          {suggestions.length > 0 && !chat.busy ? (
            <Card padded={false} clip style={styles.suggestions}>
              {suggestions.map((hit, i) => (
                <DataRow
                  key={hit.ticker}
                  title={hit.ticker}
                  subtitle={hit.name}
                  chevron={false}
                  divider={i < suggestions.length - 1}
                  onPress={() => {
                    setChat((c) => ({ ...c, input: hit.ticker }));
                    void runAnalysis(hit.ticker);
                  }}
                  accessibilityLabel={`${hit.ticker}, ${hit.name}`}
                  accessibilityHint="Bu sembolü analiz eder"
                />
              ))}
            </Card>
          ) : null}
          <View style={styles.inputRow}>
            <TextInput
              style={styles.input}
              value={chat.input}
              onChangeText={(v) => setChat((c) => ({ ...c, input: v }))}
              placeholder="AAPL veya Apple"
              placeholderTextColor={t.ink3 ?? t.textSecondary}
              autoCapitalize="characters"
              // Name search needs more than six characters of room.
              autoCorrect={false}
              maxLength={8}
              returnKeyType="send"
              onSubmitEditing={() => runAnalysis(chat.input)}
              editable={!chat.busy}
              accessibilityLabel="Analiz edilecek sembol"
            />
            <Pressable
              style={[styles.btn, chat.busy && styles.btnDisabled]}
              onPress={() => runAnalysis(chat.input)}
              disabled={chat.busy || !isAdmin}
              accessibilityRole="button"
              accessibilityLabel="Girilen sembolü analiz et"
              accessibilityState={{ disabled: chat.busy, busy: chat.busy }}
            >
              <Text style={styles.btnText}>Analiz et</Text>
              <Svg
                width={16}
                height={16}
                viewBox="0 0 24 24"
                fill="none"
                stroke={t.inkInv ?? t.background}
                strokeWidth={2.2}
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <Path d="M5 12h14" />
                <Path d="m12 5 7 7-7 7" />
              </Svg>
            </Pressable>
          </View>
          <Text style={styles.disclaimer}>{tr('disclaimer.short')}</Text>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

type Palette = ReturnType<typeof useTheme>;
type Styles = ReturnType<typeof makeStyles>;

/**
 * One answer: the PM byline, the decision strip on its raised ground, the
 * paragraph, and the way back to the full record — the prototype's agent card.
 */
function AgentMessage({
  message,
  styles,
  theme,
  onOpenDetail,
}: {
  message: ChatMessage;
  styles: Styles;
  theme: Palette;
  onOpenDetail: (ticker: string) => void;
}) {
  const decision = message.decision ?? null;
  const model = pmModel(decision);
  const badge = model ? modelBadge(theme, model) : null;
  const chip = decision ? ratingChip(theme, decision.rating) : null;

  const { t: tr } = useTranslation();

  return (
    <Card>
      <View style={styles.agentBlock}>
        <Text style={styles.kicker}>
          PORTFOLIO MANAGER
          {badge ? (
            <Text style={{ color: badge.color }}>{` · ${badge.label.toUpperCase()}`}</Text>
          ) : null}
        </Text>

        {decision && chip ? (
          <>
            <View style={styles.stripHead}>
              <Text style={styles.stripTicker}>{decision.ticker}</Text>
              <View
                style={[
                  styles.ratingTag,
                  {
                    backgroundColor: chip.background,
                    borderColor: chip.borderColor ?? 'transparent',
                  },
                ]}
              >
                <Text style={[styles.ratingText, { color: chip.color }]}>{decision.rating}</Text>
              </View>
            </View>
            {/* The four figures that make the verdict actionable, on the
                prototype's raised ground rather than between two rules —
                Aurora bands by surface, Modernist banded by rule. */}
            <Card tone="raised" style={styles.strip}>
              <View style={styles.stripGrid}>
                <StatCell
                  size="sm"
                  label="Giriş"
                  value={formatUsd(decision.entry_price)}
                  style={styles.stripCell}
                />
                <StatCell
                  size="sm"
                  label="Stop"
                  value={formatUsd(decision.stop_loss)}
                  style={styles.stripCell}
                />
                <StatCell
                  size="sm"
                  label="Hedef"
                  value={formatUsd(decision.price_target)}
                  style={styles.stripCell}
                />
                <StatCell
                  size="sm"
                  label="Vade"
                  value={decision.time_horizon ?? '—'}
                  style={styles.stripCell}
                />
              </View>
            </Card>
            <Text style={styles.disclaimer}>{tr('disclaimer.short')}</Text>
          </>
        ) : null}

        <Text style={styles.pmText}>{message.text}</Text>

        {decision ? (
          <Pressable
            onPress={() => onOpenDetail(decision.ticker)}
            hitSlop={hitSlopFor(LINK_HEIGHT)}
            style={styles.detailLink}
            accessibilityRole="link"
            accessibilityLabel={`${decision.ticker} kararının tam detayı`}
          >
            <Text style={styles.detailLinkText}>Tam karar detayı →</Text>
          </Pressable>
        ) : null}
      </View>
    </Card>
  );
}

const makeStyles = (t: Palette, sh: Shape) =>
  StyleSheet.create({
    screen: { flex: 1, backgroundColor: t.background },
    flex: { flex: 1 },

    header: { paddingTop: sh.space[2], paddingHorizontal: sh.space[3] },
    heading: { ...TYPE.h2, fontSize: 26, letterSpacing: -0.52, color: t.textPrimary },
    subheading: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, marginTop: sh.space[0] },

    stream: {
      paddingHorizontal: sh.space[3],
      paddingTop: sh.space[3],
      paddingBottom: sh.space[3],
      gap: sh.space[3],
    },

    emptyKicker: { ...TYPE.kicker, color: t.ink3 ?? t.textMuted },

    chips: { flexDirection: 'row', flexWrap: 'wrap', gap: sh.space[1] },
    chip: {
      paddingHorizontal: sh.space[3],
      borderRadius: sh.radiusPill,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
      backgroundColor: t.surface,
      minHeight: MIN_TOUCH_TARGET,
      alignItems: 'center',
      justifyContent: 'center',
    },
    // The prototype's hover is `border-color:--ink`; a phone has no hover, so
    // the same intent lands on press.
    chipPressed: { borderColor: t.brand ?? t.accent },
    chipText: { color: t.textPrimary, fontSize: 13, ...font(800), letterSpacing: 0.5 },

    marketCard: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: sh.space[2],
      backgroundColor: t.surface,
      borderRadius: sh.radius,
      borderWidth: sh.hairline,
      borderColor: t.line ?? t.divider,
      paddingHorizontal: sh.space[3],
      paddingVertical: sh.space[2],
    },
    marketCardPressed: { borderColor: t.line2 ?? t.textSecondary },
    marketIcon: {
      width: 40,
      height: 40,
      borderRadius: sh.radiusSmall,
      backgroundColor: t.brandSoft ?? t.surfaceElevated,
      alignItems: 'center',
      justifyContent: 'center',
    },
    marketText: { flex: 1, minWidth: 0 },
    marketTitle: { ...TYPE.section, color: t.textPrimary },
    marketSub: { ...TYPE.helper, fontSize: 12, color: t.ink2 ?? t.textSecondary, marginTop: 2 },
    caretOpen: { transform: [{ rotate: '90deg' }] },

    board: { gap: sh.space[1] },
    // Selector chips, not a Seg: four independent choices where one is a
    // toggle, so a segmented control would imply they are mutually exclusive.
    moverTabs: { flexDirection: 'row', flexWrap: 'wrap', gap: sh.space[0] + 2 },
    moverTab: {
      borderRadius: sh.radiusPill,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
      paddingHorizontal: sh.space[2],
      minHeight: MIN_TOUCH_TARGET,
      justifyContent: 'center',
    },
    moverTabOn: {
      backgroundColor: t.ink ?? t.textPrimary,
      borderColor: t.ink ?? t.textPrimary,
    },
    moverTabText: { color: t.ink2 ?? t.textSecondary, fontSize: 12, ...font(600) },
    moverTabTextOn: { color: t.inkInv ?? t.background, ...font(800) },

    moverRight: { alignItems: 'flex-end' },
    // Tabular so the decimal points line up down the column; a price list
    // that jitters is harder to scan than one that does not.
    moverPrice: { color: t.textPrimary, fontSize: 14, ...font(600), ...TABULAR },
    moverChange: { fontSize: 12, ...font(800), ...TABULAR, marginTop: 2 },

    hint: { ...TYPE.body, color: t.ink2 ?? t.textSecondary },
    noteText: {
      ...TYPE.helper,
      fontSize: 12,
      color: t.ink2 ?? t.textSecondary,
      lineHeight: 18,
    },

    // The question, kept on screen: an ink pill hugging the right edge.
    userRow: { alignItems: 'flex-end' },
    userBubble: {
      backgroundColor: t.ink ?? t.textPrimary,
      borderRadius: sh.radiusPill,
      paddingHorizontal: sh.space[2] + 2,
      paddingVertical: sh.space[1],
      maxWidth: '85%',
    },
    userText: {
      color: t.inkInv ?? t.background,
      fontSize: 13,
      ...font(800),
      letterSpacing: 0.52,
    },

    agentBlock: { gap: sh.space[1] },
    kicker: { ...TYPE.kicker, color: t.brand ?? t.accent },

    stripHead: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1] },
    stripTicker: { color: t.textPrimary, fontSize: 20, letterSpacing: -0.2, ...font(800) },
    // `.tag` metrics; the colours come from ratingChip so the buy/hold/sell
    // encoding stays in one place, and the radius from `shape` so the chip is
    // square under Modernist and a pill under Aurora.
    ratingTag: {
      paddingHorizontal: 10,
      paddingVertical: 3,
      borderRadius: sh.radiusPill,
      borderWidth: sh.hairline,
      alignSelf: 'flex-start',
    },
    ratingText: { fontSize: 11, letterSpacing: 0.22, ...font(600) },
    strip: { marginTop: sh.space[0] },
    stripGrid: { flexDirection: 'row', gap: sh.space[1] },
    stripCell: { flex: 1 },

    pmText: { ...TYPE.body, color: t.textPrimary, lineHeight: 20 },
    detailLink: { alignSelf: 'flex-start' },
    detailLinkText: { color: t.brand ?? t.accent, fontSize: 13, ...font(800) },

    errorLine: { ...TYPE.body, color: t.danger, lineHeight: 20 },

    running: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1] },
    runningText: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, flexShrink: 1 },

    suggestions: {
      marginBottom: sh.space[1],
      // The prototype floats this list over the page with a drop shadow; it is
      // the same elevation the shell's bar and the Bugün hero use.
      shadowColor: t.shadowColor,
      shadowOpacity: 0.28,
      shadowRadius: 30,
      shadowOffset: { width: 0, height: 10 },
      elevation: 10,
    },

    composer: {
      borderTopWidth: sh.hairline,
      borderTopColor: t.line ?? t.divider,
      backgroundColor: t.background,
      paddingHorizontal: sh.space[3],
      paddingTop: sh.space[2],
      paddingBottom: sh.space[2],
    },
    inputRow: { flexDirection: 'row', gap: sh.space[1] },
    input: {
      flex: 1,
      minHeight: 52,
      borderRadius: sh.radius,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
      backgroundColor: t.surface,
      color: t.textPrimary,
      paddingHorizontal: sh.space[3],
      fontSize: 15,
      ...font(800),
      letterSpacing: 0.6,
    },
    btn: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: sh.space[1],
      minHeight: 52,
      paddingHorizontal: sh.space[3],
      borderRadius: sh.radius,
      backgroundColor: t.ink ?? t.textPrimary,
    },
    btnDisabled: { opacity: 0.45 },
    btnText: { color: t.inkInv ?? t.background, fontSize: 14, ...font(800) },

    disclaimer: { ...TYPE.helper, color: t.ink3 ?? t.textMuted, marginTop: sh.space[0] },
  });
