/**
 * Sor — a conversation, not a form.
 *
 * The screen used to be a single input with one result card underneath, so the
 * second question erased the answer to the first. The handoff specifies a chat:
 * the questions asked stay on screen as right-aligned surface boxes, each answer
 * opens with the PM kicker, and the composer lives at the bottom under a 2px
 * rule. Nothing about the request changes — `useStartAnalysis` fires the job and
 * `useAnalysisJob` polls it exactly as before; the terminal state is appended to
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
import { useRouter, useLocalSearchParams } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { HTTPError } from 'ky';

import { useStartAnalysis, useAnalysisJob } from '@/api/hooks';
import type { AgentDecision, AnalyzeStatus } from '@/api/types';
import { useTheme } from '@/theme/useTheme';
import { ratingChip, modelBadge } from '@/theme/rating';
import { font, TABULAR, TYPE } from '@/theme/type';
import { formatUsd } from '@/utils/format';
import { MIN_TOUCH_TARGET, hitSlopFor } from '@/utils/a11y';
import { BlinkSquare } from '@/components/BlinkSquare';

/** The six symbols the prototype offers before the first question. */
const CHIPS = ['AAPL', 'NVDA', 'MSFT', 'GOOGL', 'AMZN', 'AVGO'] as const;

/** The link back to the full decision is 13px of text; slop it up to 44. */
const LINK_HEIGHT = 20;

const QUEUED_TR = 'sıraya alındı…';
const RUNNING_TR = 'ajanlar tartışıyor… (~5-10 dk)';

function statusLine(ticker: string, status: AnalyzeStatus | undefined): string {
  const phase = status === 'running' ? RUNNING_TR : QUEUED_TR;
  return ticker ? `${ticker}: ${phase}` : phase;
}

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
  const { t } = useTranslation();
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);
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
  const busy = start.isPending || polling;

  // Mirror the poller into the handoff's chat shape.
  useEffect(() => {
    setChat((c) => {
      const status = busy ? statusLine(job?.ticker ?? askedTicker.current, job?.status) : '';
      if (c.busy === busy && c.status === status) return c;
      return { ...c, busy, status };
    });
  }, [busy, job?.ticker, job?.status]);

  // Terminal state -> one assistant message.
  useEffect(() => {
    if (!jobId) return;
    const append = (id: string, message: Omit<ChatMessage, 'id'>) =>
      setChat((c) => ({ ...c, messages: [...c.messages, { id, ...message }] }));

    if (job?.status === 'done' || job?.status === 'error') {
      if (settled.current === jobId) return;
      settled.current = jobId;
      if (job.status === 'done') {
        const text = job.decision?.final_decision_text?.trim();
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
        status: statusLine(ticker, 'queued'),
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
    <SafeAreaView style={styles.container} edges={['top']}>
      {/*
        No `keyboardVerticalOffset`: the tab bar is a flex SIBLING of the
        screen (bottom-tabs renders it below the scene container, not over it),
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
          <Text style={styles.heading}>Sor</Text>
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
            <View style={styles.chips}>
              {CHIPS.map((c) => (
                <Pressable
                  key={c}
                  style={styles.chip}
                  onPress={() => runAnalysis(c)}
                  accessibilityRole="button"
                  accessibilityLabel={`${c} analiz et`}
                >
                  <Text style={styles.chipText}>{c}</Text>
                </Pressable>
              ))}
            </View>
          ) : null}

          {chat.messages.map((m) =>
            m.role === 'user' ? (
              <View key={m.id} style={styles.userRow}>
                <View style={styles.userBubble}>
                  <Text style={styles.userText}>{m.text}</Text>
                </View>
              </View>
            ) : m.failed ? (
              <Text key={m.id} style={styles.errorLine}>
                {m.text}
              </Text>
            ) : (
              <AgentMessage
                key={m.id}
                message={m}
                styles={styles}
                theme={theme}
                onOpenDetail={(ticker) => router.push(`/trade/${ticker}` as never)}
              />
            ),
          )}

          {chat.busy ? (
            <View style={styles.running} accessibilityLiveRegion="polite">
              <BlinkSquare />
              <Text style={styles.runningText}>{chat.status}</Text>
            </View>
          ) : null}
        </ScrollView>

        {/* 2px rule, then the composer — the one control the screen always shows. */}
        <View style={styles.composer}>
          <View style={styles.inputRow}>
            <TextInput
              style={styles.input}
              value={chat.input}
              onChangeText={(v) => setChat((c) => ({ ...c, input: v }))}
              placeholder="AAPL"
              placeholderTextColor={theme.textSecondary}
              autoCapitalize="characters"
              autoCorrect={false}
              maxLength={6}
              returnKeyType="send"
              onSubmitEditing={() => runAnalysis(chat.input)}
              editable={!chat.busy}
              accessibilityLabel="Analiz edilecek sembol"
            />
            <Pressable
              style={[styles.btn, chat.busy && styles.btnDisabled]}
              onPress={() => runAnalysis(chat.input)}
              disabled={chat.busy}
              accessibilityRole="button"
              accessibilityLabel="Girilen sembolü analiz et"
              accessibilityState={{ disabled: chat.busy, busy: chat.busy }}
            >
              <Text style={styles.btnText}>Analiz et</Text>
            </Pressable>
          </View>
          <Text style={styles.disclaimer}>{t('disclaimer.short')}</Text>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

type Palette = ReturnType<typeof useTheme>;
type Styles = ReturnType<typeof makeStyles>;

/**
 * One answer: the PM byline, the decision strip between two 2px rules, the
 * paragraph, and the way back to the full record.
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

  return (
    <View style={styles.agentBlock}>
      <Text style={styles.kicker}>
        PORTFOLIO MANAGER
        {badge ? (
          <Text style={{ color: badge.color }}>{` · ${badge.label.toUpperCase()}`}</Text>
        ) : null}
      </Text>

      {decision && chip ? (
        <View style={styles.strip}>
          <View style={styles.stripHead}>
            <Text style={styles.stripTicker}>{decision.ticker}</Text>
            <View
              style={[
                styles.ratingTag,
                { backgroundColor: chip.background, borderColor: chip.borderColor ?? 'transparent' },
              ]}
            >
              <Text style={[styles.ratingText, { color: chip.color }]}>{decision.rating}</Text>
            </View>
          </View>
          <View style={styles.stripGrid}>
            <StripField label="Giriş" value={formatUsd(decision.entry_price)} styles={styles} />
            <StripField label="Stop" value={formatUsd(decision.stop_loss)} styles={styles} />
            <StripField label="Hedef" value={formatUsd(decision.price_target)} styles={styles} />
            <StripField label="Vade" value={decision.time_horizon ?? '—'} styles={styles} />
          </View>
        </View>
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
  );
}

function StripField({
  label,
  value,
  styles,
}: {
  label: string;
  value: string;
  styles: Styles;
}) {
  return (
    <View style={styles.stripField}>
      <Text style={styles.stripLabel}>{label}</Text>
      <Text style={styles.stripValue} numberOfLines={1}>
        {value}
      </Text>
    </View>
  );
}

const makeStyles = (t: Palette) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.background },
    flex: { flex: 1 },

    header: { paddingTop: 20, paddingHorizontal: 16 },
    heading: { color: t.textPrimary, ...TYPE.h2 },
    subheading: { color: t.textSecondary, ...TYPE.body, marginTop: 4 },

    stream: { paddingHorizontal: 16, paddingTop: 16, paddingBottom: 16, gap: 16 },

    // The question, kept on screen: a surface box hugging the right edge.
    userRow: { alignItems: 'flex-end' },
    userBubble: { backgroundColor: t.surface, paddingHorizontal: 12, paddingVertical: 8 },
    userText: { color: t.textPrimary, fontSize: 13, ...font(800), letterSpacing: 0.52 },

    agentBlock: { gap: 10 },
    kicker: { color: t.accent700 ?? t.accent, ...TYPE.kicker },

    // The decision, banded between two section rules — the only 2px in a message.
    strip: {
      borderTopWidth: 2,
      borderBottomWidth: 2,
      borderColor: t.divider,
      paddingVertical: 10,
    },
    stripHead: { flexDirection: 'row', alignItems: 'center', gap: 10 },
    stripTicker: { color: t.textPrimary, fontSize: 18, ...font(800) },
    // `.tag` metrics; the colours come from ratingChip so the buy/hold/sell
    // encoding stays in one place.
    ratingTag: { paddingHorizontal: 10, paddingVertical: 3, borderWidth: 1, alignSelf: 'flex-start' },
    ratingText: { fontSize: 11, letterSpacing: 0.22, ...font(600) },
    stripGrid: { flexDirection: 'row', gap: 8, marginTop: 8 },
    stripField: { flex: 1 },
    stripLabel: { color: t.textSecondary, ...TYPE.helper },
    stripValue: { color: t.textPrimary, fontSize: 13, ...font(800), ...TABULAR, marginTop: 2 },

    pmText: { color: t.textPrimary, ...TYPE.body, lineHeight: 20 },
    detailLink: { alignSelf: 'flex-start' },
    detailLinkText: { color: t.accent700 ?? t.accent, fontSize: 13, ...font(600) },

    errorLine: { color: t.accent700 ?? t.danger, ...TYPE.body, lineHeight: 20 },

    running: { flexDirection: 'row', alignItems: 'center', gap: 10 },
    runningText: { color: t.textSecondary, ...TYPE.body, flexShrink: 1 },

    chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
    chip: {
      paddingHorizontal: 18,
      borderWidth: 1,
      borderColor: t.divider,
      minHeight: MIN_TOUCH_TARGET,
      alignItems: 'center',
      justifyContent: 'center',
    },
    chipText: { color: t.textPrimary, fontSize: 14, ...font(800), letterSpacing: 0.5 },

    composer: {
      borderTopWidth: 2,
      borderTopColor: t.divider,
      paddingHorizontal: 16,
      paddingTop: 12,
      paddingBottom: 12,
    },
    // Square field + square ink button, borders collapsed onto one baseline.
    inputRow: { flexDirection: 'row' },
    input: {
      flex: 1,
      backgroundColor: t.surfaceElevated,
      color: t.textPrimary,
      borderWidth: 1,
      borderColor: t.textPrimary,
      paddingHorizontal: 14,
      minHeight: 48,
      fontSize: 16,
      ...font(800),
      letterSpacing: 1.28,
    },
    btn: {
      backgroundColor: t.textPrimary,
      paddingHorizontal: 20,
      justifyContent: 'center',
      alignItems: 'center',
      minHeight: 48,
      marginLeft: -1,
    },
    btnDisabled: { opacity: 0.45 },
    btnText: { color: t.background, fontSize: 15, ...font(800), letterSpacing: 0.5 },

    disclaimer: { color: t.textSecondary, ...TYPE.helper, marginTop: 8 },
  });
