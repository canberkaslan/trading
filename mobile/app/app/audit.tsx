/**
 * Denetim kaydı — the prototype's AUDIT LOG screen, ported to Aurora.
 *
 * Who did what, when, and from which device, over the three actions that move
 * money or stop it moving: approve, reject, kill switch.
 *
 * ── The honest part ────────────────────────────────────────────────────────
 * The backend keeps an audit trail but does not yet publish it. Concretely,
 * as of this port:
 *
 *   • `kill_switch_events` is an append-only table (models.py) written by
 *     `POST /v1/orders/kill-switch` (actor + source + detail) and by the
 *     daily `kill_check.py` backstop. `DELETE /v1/me` deliberately keeps it —
 *     its test asserts "the audit trail itself must survive".
 *   • approvals / rejections / cancellations land in `order_updates` with a
 *     status and a timestamp.
 *   • no route in `agent/api/routes/` reads either of them back. The only
 *     kill-switch GET returns the CURRENT state (`{state}`) and nothing about
 *     how it got there; `GET /v1/orders` carries no actor, no device and no
 *     decided-at.
 *
 * So this screen draws the chrome and says so. It does not reconstruct a log
 * out of order rows: a list assembled from `submitted_at_utc` would show an
 * actor it never received, a device it cannot know, and no record at all of
 * the rejections and kill-switch flips — the three things the screen exists
 * to prove. A convincing but partial audit log is worse than an absent one,
 * because it is the screen an operator would point at to say "I did not do
 * that".
 *
 * Wiring it later is one function: fill {@link AUDIT_ENTRIES} from a hook over
 * the new endpoint. Everything below — filters, actor chips, row shape,
 * counts — already runs off that array, and the filter bar appears with the
 * first row.
 *
 * What IS live here is the header block: the current kill-switch state, the
 * trading mode and the signed-in identity, all from existing hooks. That is
 * the "who / where" the app can actually answer today, and it is the context
 * the log would be read against.
 *
 * Nothing in `src/utils/` or `src/api/` was changed for this screen.
 */

import { View, Text, StyleSheet, Pressable, ScrollView } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useMemo, useState } from 'react';

import { useKillSwitch, useReadiness } from '@/api/hooks';
import { useMe } from '@/api/useMe';
import { Card } from '@/components/Card';
import { ErrorState } from '@/components/ErrorState';
import { SectionHeader } from '@/components/SectionHeader';
import { StatCell } from '@/components/StatCell';
import { Tag, type TagVariant } from '@/components/Tag';
import { useTheme } from '@/theme/useTheme';
import { useShape, type Shape } from '@/theme/shape';
import { font, TYPE, TABULAR } from '@/theme/type';
import { hitSlopFor, killSwitchLabel, MIN_TOUCH_TARGET } from '@/utils/a11y';
import { parseUtc, relativeAgeTr } from '@/utils/format';

/** Who took the action. The prototype's `actorLabel` map, Turkish column. */
type AuditActor = 'operator' | 'system' | 'risk';

/** What was done. The prototype's `actionLabels` map, Turkish column. */
type AuditAction =
  | 'approve'
  | 'reject'
  | 'cancel'
  | 'kill'
  | 'setting'
  | 'login'
  | 'hold'
  | 'refuse'
  | 'report';

export interface AuditEntry {
  id: string;
  /** ISO-8601. Naive values are read as UTC — see `parseUtc`. */
  ts: string;
  actor: AuditActor;
  action: AuditAction;
  /** One line of what happened: "AAPL BUY 12 · onaylandı". */
  detail: string;
  /** Device + verification method, or the server component that acted. */
  where: string;
}

const ACTOR_LABEL_TR: Record<AuditActor, string> = {
  operator: 'operatör',
  system: 'sistem',
  risk: 'risk katmanı',
};

const ACTION_LABEL_TR: Record<AuditAction, string> = {
  approve: 'Onay',
  reject: 'Red',
  cancel: 'İptal',
  kill: 'Kill switch',
  setting: 'Ayar',
  login: 'Giriş',
  hold: 'Onaya düştü',
  refuse: 'Risk reddi',
  report: 'Rapor',
};

/**
 * Chip tone per actor, as the prototype tones it: the operator is brand-soft
 * (a person did this), the risk layer is rose-soft (something was refused),
 * the system is the neutral ground. `Tag`'s variants fall back to `surface`
 * on a palette without Aurora's soft grounds, so the meaning survives in the
 * text colour.
 */
const ACTOR_VARIANT: Record<AuditActor, TagVariant> = {
  operator: 'brand',
  risk: 'down',
  system: 'neutral',
};

type AuditFilter = 'all' | 'approve' | 'reject' | 'kill' | 'refuse' | 'system';

const FILTERS: { key: AuditFilter; label: string }[] = [
  { key: 'all', label: 'Hepsi' },
  { key: 'approve', label: ACTION_LABEL_TR.approve },
  { key: 'reject', label: ACTION_LABEL_TR.reject },
  { key: 'kill', label: 'Kill' },
  { key: 'refuse', label: 'Risk' },
  { key: 'system', label: 'Sistem' },
];

/**
 * The prototype's filter predicate, verbatim in behaviour: "system" filters by
 * actor, every other key filters by action, "all" passes everything.
 */
function matchesFilter(entry: AuditEntry, filter: AuditFilter): boolean {
  if (filter === 'all') return true;
  if (filter === 'system') return entry.actor === 'system';
  return entry.action === filter;
}

/**
 * The log rows. Empty, and deliberately so — see the file header. Replace this
 * with the query result when `agent/api/routes/` grows an audit read endpoint;
 * nothing else on this screen needs to change.
 */
const AUDIT_ENTRIES: AuditEntry[] = [];

/** The prototype's chips are 34pt; `hitSlopFor` carries them to 44. */
const CHIP_HEIGHT = 34;

/** Absolute stamp for a row, in the operator's own zone. */
function stampTr(iso: string): string {
  const d = parseUtc(iso);
  if (!d) return '—';
  return d.toLocaleString('tr-TR', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function ageTr(iso: string): string {
  const d = parseUtc(iso);
  if (!d) return '—';
  return relativeAgeTr(Date.now() - d.getTime());
}

export default function AuditScreen() {
  const router = useRouter();
  const t = useTheme();
  const sh = useShape();
  const styles = useMemo(() => makeStyles(t, sh), [t, sh]);

  const [filter, setFilter] = useState<AuditFilter>('all');

  const kill = useKillSwitch();
  const readiness = useReadiness();
  const me = useMe();

  const rows = useMemo(
    () => AUDIT_ENTRIES.filter((entry) => matchesFilter(entry, filter)),
    [filter],
  );

  const killState = kill.data?.state;
  // Same three-way tone as the risk screen: RUN is the healthy state, PAUSE is
  // a warning, FLATTEN is the loud one.
  const killColor =
    killState === 'FLATTEN_ALL'
      ? t.danger
      : killState === 'PAUSE_NEW'
        ? t.warning
        : killState === 'RUN'
          ? (t.upAltText ?? t.up)
          : (t.ink3 ?? t.textMuted);

  const mode = readiness.data?.trading_mode;
  const modeLabel = mode === 'live' ? 'LIVE' : mode === 'paper' ? 'PAPER' : '—';

  const who = me.data?.uid ?? '—';
  const whoHint = me.isLoading
    ? 'Oturum okunuyor…'
    : me.data?.is_admin
      ? 'yönetici — onay ve kill switch yetkisi var'
      : me.data
        ? 'salt okunur oturum'
        : 'oturum bilinmiyor';

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <ScrollView contentContainerStyle={styles.content}>
        <Pressable
          style={styles.backBtn}
          onPress={() => router.back()}
          accessibilityRole="button"
          accessibilityLabel="Geri"
        >
          <Text style={styles.backText}>← Geri</Text>
        </Pressable>

        <View style={styles.head}>
          <Text style={styles.title} accessibilityRole="header">
            Denetim kaydı{' '}
            <Text style={styles.titleCount}>{AUDIT_ENTRIES.length}</Text>
          </Text>
          <Text style={styles.subtitle}>Kim, ne zaman, hangi cihazdan</Text>
        </View>

        {/* What the app can answer today: the state the log would explain. */}
        {kill.isError ? (
          <ErrorState
            title="Kill switch durumu okunamadı"
            detail={kill.error}
            onRetry={() => void kill.refetch()}
          />
        ) : (
          <Card tone="recessed" style={styles.nowCard}>
            <View style={styles.nowRow}>
              <StatCell
                label="Kill switch"
                value={kill.isLoading ? '…' : (killState ?? '—')}
                hint={killState ? killSwitchLabel(killState) : 'durum bilinmiyor'}
                valueColor={killColor}
                accessibilityLabel={
                  killState
                    ? `Kill switch durumu: ${killSwitchLabel(killState)}`
                    : 'Kill switch durumu bilinmiyor'
                }
              />
              <StatCell
                label="Mod"
                value={readiness.isLoading ? '…' : modeLabel}
                hint={mode === 'live' ? 'gerçek para' : mode === 'paper' ? 'sanal hesap' : 'sunucu yanıt vermedi'}
                accessibilityLabel={`İşlem modu: ${modeLabel}`}
              />
            </View>
            <View style={styles.nowDivider} />
            <StatCell
              label="Bu oturum"
              value={who}
              hint={whoHint}
              size="sm"
              accessibilityLabel={`Oturum: ${who}, ${whoHint}`}
            />
          </Card>
        )}

        <SectionHeader
          title="Kayıt"
          count={AUDIT_ENTRIES.length ? rows.length : null}
          style={styles.section}
        />

        {/* The filter bar only exists once there is something to filter —
            six chips over an empty list are six controls that do nothing. */}
        {AUDIT_ENTRIES.length > 0 ? (
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.chips}
            style={styles.chipScroll}
          >
            {FILTERS.map((f) => {
              const active = filter === f.key;
              return (
                <Pressable
                  key={f.key}
                  style={[styles.chip, active && styles.chipActive]}
                  hitSlop={hitSlopFor(CHIP_HEIGHT)}
                  onPress={() => setFilter(f.key)}
                  accessibilityRole="button"
                  accessibilityState={{ selected: active }}
                  accessibilityLabel={`${f.label} filtresi`}
                >
                  <Text style={[styles.chipText, active && styles.chipTextActive]} numberOfLines={1}>
                    {f.label}
                  </Text>
                </Pressable>
              );
            })}
          </ScrollView>
        ) : null}

        {AUDIT_ENTRIES.length === 0 ? (
          <Card tone="dashed" style={styles.slot}>
            <Text style={styles.slotTitle}>Kayıt uygulamaya açık değil</Text>
            <Text style={styles.slotText}>
              Onaylar, redler ve kill switch değişiklikleri sunucuda tutuluyor, ancak sunucu bu kaydı
              okumak için bir uç nokta sunmuyor. Emir listesinden kim ve hangi cihaz bilgisi
              çıkarılamadığı için burada uydurma satır gösterilmiyor.
            </Text>
          </Card>
        ) : rows.length === 0 ? (
          <Card tone="dashed" style={styles.slot}>
            <Text style={styles.slotTitle}>Bu filtrede kayıt yok</Text>
            <Text style={styles.slotText}>Başka bir filtre dene veya “Hepsi”ne dön.</Text>
          </Card>
        ) : (
          <Card padded={false} style={styles.list}>
            {rows.map((entry, i) => (
              <View
                key={entry.id}
                style={[styles.row, i < rows.length - 1 && styles.rowDivider]}
                accessible
                accessibilityLabel={[
                  ACTOR_LABEL_TR[entry.actor],
                  ACTION_LABEL_TR[entry.action],
                  entry.detail,
                  `nerede: ${entry.where}`,
                  ageTr(entry.ts),
                ].join(', ')}
              >
                <View style={styles.rowHead}>
                  <Tag
                    label={ACTOR_LABEL_TR[entry.actor]}
                    variant={ACTOR_VARIANT[entry.actor]}
                    size="sm"
                  />
                  <Text style={styles.rowAction} numberOfLines={1}>
                    {ACTION_LABEL_TR[entry.action]}
                  </Text>
                  <Text style={styles.rowWhen}>{stampTr(entry.ts)}</Text>
                </View>
                <Text style={styles.rowDetail}>{entry.detail}</Text>
                <Text style={styles.rowWhere}>
                  Nerede: {entry.where} · {ageTr(entry.ts)}
                </Text>
              </View>
            ))}
          </Card>
        )}

        <Text style={styles.note}>
          Her onay, red ve kill switch değişikliği cihaz + doğrulama yöntemiyle kaydedilir. Kayıt
          sunucuda tutulur; uygulamadan silinemez.
        </Text>
      </ScrollView>
    </SafeAreaView>
  );
}

type Palette = ReturnType<typeof useTheme>;

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

    head: { paddingTop: sh.space[1], paddingBottom: sh.space[2] },
    title: { ...TYPE.h2, fontSize: 26, letterSpacing: -0.5, color: t.textPrimary },
    // The count is data beside the heading, not part of it.
    titleCount: { ...font(600), color: t.ink3 ?? t.textMuted, ...TABULAR },
    subtitle: { ...TYPE.body, color: t.ink2 ?? t.textSecondary, marginTop: sh.space[0] },

    nowCard: { marginTop: sh.space[0] },
    nowRow: { flexDirection: 'row', gap: sh.space[3] },
    nowDivider: {
      height: sh.hairline,
      backgroundColor: t.line ?? t.divider,
      marginVertical: sh.space[2],
    },

    section: { marginTop: sh.space[4], marginBottom: sh.space[1] },

    // Full-bleed chip rail: it scrolls past the page gutter like the prototype's.
    chipScroll: { marginHorizontal: -sh.space[3], marginBottom: sh.space[1] },
    chips: { gap: 6, paddingHorizontal: sh.space[3], paddingBottom: sh.space[0] / 2 },
    chip: {
      height: CHIP_HEIGHT,
      justifyContent: 'center',
      paddingHorizontal: sh.space[2],
      borderRadius: sh.radiusPill,
      borderWidth: sh.hairline,
      borderColor: t.line2 ?? t.divider,
    },
    chipActive: { backgroundColor: t.textPrimary, borderColor: t.textPrimary },
    chipText: { fontSize: 12, ...font(800), color: t.textPrimary },
    chipTextActive: { color: t.background },

    slot: { alignItems: 'center', paddingVertical: sh.space[4] },
    slotTitle: { ...TYPE.section, color: t.textPrimary, textAlign: 'center' },
    slotText: {
      ...TYPE.body,
      color: t.ink2 ?? t.textSecondary,
      textAlign: 'center',
      marginTop: sh.space[1],
      lineHeight: 19,
    },

    list: { paddingHorizontal: sh.space[3] },
    row: { paddingVertical: 11 },
    rowDivider: { borderBottomWidth: sh.hairline, borderBottomColor: t.line ?? t.divider },
    rowHead: { flexDirection: 'row', alignItems: 'center', gap: sh.space[1] },
    rowAction: { fontSize: 13, ...font(800), color: t.textPrimary, flexShrink: 1 },
    rowWhen: { ...TYPE.helper, color: t.ink3 ?? t.textMuted, marginLeft: 'auto', ...TABULAR },
    rowDetail: { fontSize: 13, ...font(400), color: t.textPrimary, marginTop: 5, ...TABULAR },
    rowWhere: { ...TYPE.helper, color: t.ink3 ?? t.textMuted, marginTop: 2 },

    note: {
      ...TYPE.helper,
      color: t.ink3 ?? t.textMuted,
      lineHeight: 17,
      marginTop: sh.space[2],
    },
  });
