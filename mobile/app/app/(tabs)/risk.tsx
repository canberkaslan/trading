/**
 * Screen 7 — "Risk & uyarılar".
 *
 * The kill switch used to live buried at the bottom of Ayarlar, four scrolls
 * past the theme picker. It is the one control that stops a running strategy,
 * so it gets the top of its own screen, and the three things an operator needs
 * in order to decide whether to touch it sit underneath: which circuit breakers
 * have actually fired, where the book stands against its caps, and what the
 * risk layer has been complaining about.
 *
 * ── On the numbers ───────────────────────────────────────────────────────────
 * The design shows every meter as "şimdi / limit" with a bar. The backend does
 * not expose a risk-limit endpoint: `PortfolioLimits` lives server-side and is
 * never serialized, and the only thresholds that reach the wire are the ones
 * attached to a breach — `Concentration.flag_items[]` carries `{value, limit}`
 * for the caps it actually flagged.
 *
 * So this screen prints a limit ONLY where the server sent one. Everywhere else
 * it shows the live value, marks the threshold as unavailable, and draws no
 * bar — a bar with an invented denominator is a lie with a progress indicator
 * on it. Toning still goes through `topWeightTone`, which is the app's own
 * encoded rule and is not the same claim as printing a number.
 *
 * Circuit-breaker thresholds are not on the wire at all, so that section
 * reports what the breakers DID rather than where they stand: refusal counts
 * from `/v1/diagnostics/actionability`, labelled by `rejectionReasonTr` through
 * `topReasons`. A breaker that never tripped is genuinely nothing to show.
 *
 * The same rule governs the empty states. "Devre kesici tetiklenmedi" is an
 * all-clear, and an all-clear drawn because a request failed is the worst thing
 * this screen could say, so no section reaches its empty state until the query
 * behind it has actually answered: loading, failed and empty are three states,
 * not one.
 */

import { useMemo, useState } from 'react';
import { View, Text, ScrollView, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import {
  useActionability,
  useConcentration,
  useKillSwitch,
  useOrders,
  usePortfolio,
  useSetKillSwitch,
  useStopCoverage,
} from '@/api/hooks';
import type { Concentration, KillSwitchState,
  ConcentrationFlag,
} from '@/api/types';
import { EmptyState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
import { Seg, type SegOption } from '@/components/Seg';
import { Sheet } from '@/components/Sheet';
import { Tag } from '@/components/Tag';
import { toast } from '@/stores/toast';
import { font, TABULAR, TYPE } from '@/theme/type';
import { useTheme } from '@/theme/useTheme';
import { killSwitchLabel, MIN_TOUCH_TARGET } from '@/utils/a11y';
import { inertiaNote, topReasons } from '@/utils/actionability';
import { sectorAllocation, topWeightTone } from '@/utils/concentration';
import { formatPct, parseUtc } from '@/utils/format';
import { formatOrderDate, rejectionReasonTr } from '@/utils/orders';

type Palette = ReturnType<typeof useTheme>;

/**
 * The three states, once: chip label and description together, the prototype's
 * own `KILL` table verbatim. One table rather than three parallel maps, so the
 * label a toast reports cannot drift from the label the chip shows.
 *
 * The spoken label is NOT here — `killSwitchLabel` in utils/a11y owns it.
 */
const KILL: readonly { value: KillSwitchState; label: string; desc: string }[] = [
  { value: 'RUN', label: 'RUN', desc: 'Normal işlem' },
  { value: 'PAUSE_NEW', label: 'PAUSE', desc: 'Yeni giriş yok, mevcut yönetilir' },
  { value: 'FLATTEN_ALL', label: 'FLATTEN', desc: 'Tüm pozisyonları piyasa fiyatından kapat' },
];

function killEntry(state: KillSwitchState | undefined) {
  return KILL.find((k) => k.value === state);
}

/**
 * The deterministic breakers, as opposed to the portfolio caps.
 *
 * A breaker halts the run for a reason about the *account or the market*; a cap
 * is about the shape of the book and is answered by /portfolio/concentration
 * further down the screen. Splitting them here keeps one refusal from being
 * counted in both sections.
 */
const BREAKER_KEYS = new Set([
  'daily_drawdown',
  'consecutive_losses',
  'price_z_score',
  'api_error_rate',
  'kill_switch',
  'account_trading_blocked',
  'pattern_day_trader_active',
  'market_closed_until',
]);

/**
 * Mirrors the key extraction inside `rejectionReasonTr` — the reason arrives as
 * `position_pct=12.40% exceeds 10%` and the key is everything before the first
 * separator. Used ONLY to bucket a row into breaker-vs-cap; every label the
 * user reads still comes out of the util itself.
 */
function reasonKey(raw: string): string {
  return (raw.trim().split(/[=:\s]/, 1)[0] ?? '').toLowerCase();
}

function filterReasons(
  byReason: Record<string, number> | undefined,
  keep: (key: string) => boolean,
): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [reason, count] of Object.entries(byReason ?? {})) {
    if (keep(reasonKey(reason))) out[reason] = count;
  }
  return out;
}

/**
 * `flag_items` is the machine-readable twin of `Concentration.flags` and the
 * only place a real cap VALUE reaches the client — the sentences in `flags`
 * would otherwise have to be parsed. It is optional on the wire because a
 * deployment older than the field sends only the prose; this then yields no
 * limits and the screen says so rather than inventing a threshold.
 *
 * Still validated at the boundary rather than asserted: the type describes the
 * contract, the guard describes what actually arrived.
 */
function isConcentrationFlag(v: unknown): v is ConcentrationFlag {
  if (typeof v !== 'object' || v === null) return false;
  const o = v as Record<string, unknown>;
  return (
    typeof o.code === 'string' &&
    typeof o.value === 'number' &&
    typeof o.limit === 'number' &&
    (o.ticker === undefined || typeof o.ticker === 'string')
  );
}

function concentrationFlags(c: Concentration | undefined): ConcentrationFlag[] {
  const raw: unknown = c?.flag_items;
  return Array.isArray(raw) ? raw.filter(isConcentrationFlag) : [];
}

/**
 * One meter row. `limit` null means the server never told us the threshold —
 * the row then renders the live value alone, with no bar and no denominator.
 */
interface MeterRow {
  key: string;
  name: string;
  now: string;
  limit: string | null;
  /** 0..1 of the limit. Null whenever `limit` is null. */
  ratio: number | null;
  breached: boolean;
}

interface AlertRow {
  key: string;
  when: string;
  text: string;
  open: boolean;
  ts: number;
}

function Meter({ row, styles, t }: { row: MeterRow; styles: Styles; t: Palette }) {
  const valueColor = row.breached ? t.downText ?? t.down : t.textPrimary;
  return (
    <View style={styles.meter} accessibilityRole="text">
      <View style={styles.meterHead}>
        <Text style={styles.meterName} numberOfLines={2}>
          {row.name}
        </Text>
        <Text style={styles.meterValue}>
          <Text style={[styles.meterNow, { color: valueColor }]}>{row.now}</Text>
          <Text style={styles.meterLimit}>
            {row.limit ? ` / ${row.limit}` : ' · limit yok'}
          </Text>
        </Text>
      </View>
      {row.ratio == null ? null : (
        <View style={styles.barTrack}>
          <View
            style={[
              styles.barFill,
              {
                width: `${Math.min(100, Math.max(0, row.ratio * 100))}%`,
                backgroundColor: row.breached ? t.accent : t.textPrimary,
              },
            ]}
          />
        </View>
      )}
    </View>
  );
}

export default function RiskScreen() {
  const t = useTheme();
  const styles = useMemo(() => makeStyles(t), [t]);

  const { data: ks, isError: ksError } = useKillSwitch();
  const setKs = useSetKillSwitch();
  const [flattenOpen, setFlattenOpen] = useState(false);

  const portfolio = usePortfolio();
  const concentration = useConcentration();
  const flow = useActionability();
  const orders = useOrders();
  const coverage = useStopCoverage();

  const conc = concentration.data;
  const flags = useMemo(() => concentrationFlags(conc), [conc]);
  const sectors = useMemo(
    () =>
      portfolio.data
        ? sectorAllocation(portfolio.data.positions, portfolio.data.total_equity_usd)
        : [],
    [portfolio.data],
  );

  // The selected chip's fill carries the state: ink runs, accent-700 holds,
  // the accent itself is the one that closes the book.
  const killOptions: SegOption<KillSwitchState>[] = useMemo(() => {
    const fill: Record<KillSwitchState, string> = {
      RUN: t.textPrimary,
      PAUSE_NEW: t.warning,
      FLATTEN_ALL: t.accent,
    };
    return KILL.map((k) => ({
      value: k.value,
      label: k.label,
      fill: fill[k.value],
      accessibilityLabel: killSwitchLabel(k.value),
      accessibilityHint: k.value === 'FLATTEN_ALL' ? 'Onay ister' : undefined,
    }));
  }, [t]);

  const applyKill = (next: KillSwitchState) => {
    if (next === ks?.state) return;
    // FLATTEN_ALL closes the whole book at market. It is the one state that
    // asks first.
    if (next === 'FLATTEN_ALL') {
      setFlattenOpen(true);
      return;
    }
    setKs.mutate(next, {
      onSuccess: () => toast(`Kill switch → ${killEntry(next)?.label ?? next}`),
      onError: () => toast('Kill switch değiştirilemedi — sunucuya ulaşılamadı'),
    });
  };

  const confirmFlatten = () => {
    setKs.mutate('FLATTEN_ALL', {
      onSuccess: () => {
        setFlattenOpen(false);
        toast('FLATTEN_ALL gönderildi — pozisyonlar piyasa fiyatından kapatılıyor');
      },
      onError: () => {
        setFlattenOpen(false);
        toast('FLATTEN_ALL gönderilemedi — sunucuya ulaşılamadı');
      },
    });
  };

  // ── Devre kesiciler ───────────────────────────────────────────────────────
  // No threshold reaches the wire, so this reports trips, not headroom.
  const breakerTrips = useMemo(
    () => topReasons(filterReasons(flow.data?.by_reason, (k) => BREAKER_KEYS.has(k)), 6),
    [flow.data],
  );

  const dailyDd = portfolio.data?.max_drawdown_today;

  // ── Portföy limitleri ─────────────────────────────────────────────────────
  const limitRows = useMemo<MeterRow[]>(() => {
    const rows: MeterRow[] = [];

    const singleName = flags.find((f) => f.code === 'single_name_cap');
    if (singleName) {
      rows.push({
        key: 'single_name_cap',
        name: 'Tek isim',
        now: `${formatPct(singleName.value / 100)}${singleName.ticker ? ` (${singleName.ticker})` : ''}`,
        limit: formatPct(singleName.limit / 100),
        ratio: singleName.limit > 0 ? singleName.value / singleName.limit : null,
        breached: true,
      });
    } else if (conc) {
      // Nothing flagged, so the cap itself never came over the wire — show the
      // live top weight, toned by the util, with the threshold left blank.
      rows.push({
        key: 'top_weight',
        name: 'Tek isim · en yüksek',
        now: formatPct(conc.top_weight_pct / 100),
        limit: null,
        ratio: null,
        breached: topWeightTone(conc.top_weight_pct) !== 'up',
      });
    }

    const lowN = flags.find((f) => f.code === 'low_effective_n');
    if (lowN) {
      rows.push({
        key: 'low_effective_n',
        name: 'Etkin isim sayısı',
        now: lowN.value.toFixed(1),
        limit: `≥ ${lowN.limit.toFixed(0)}`,
        // A floor, not a ceiling: the bar fills toward the threshold.
        ratio: lowN.limit > 0 ? lowN.value / lowN.limit : null,
        breached: true,
      });
    }

    const topSector = sectors[0];
    if (topSector) {
      rows.push({
        key: 'sector',
        name: `Sektör · ${topSector.label}`,
        now: formatPct(topSector.weightPct / 100),
        limit: null,
        ratio: null,
        breached: false,
      });
    }

    if (conc) {
      rows.push({
        key: 'gross',
        name: 'Brüt maruziyet',
        now: formatPct(conc.gross_exposure_pct / 100),
        limit: null,
        ratio: null,
        breached: false,
      });
      rows.push({
        key: 'cash',
        name: 'Nakit',
        now: formatPct(conc.cash_pct / 100),
        limit: null,
        ratio: null,
        breached: false,
      });
    }

    return rows;
  }, [conc, flags, sectors]);

  const hasServerLimit = limitRows.some((r) => r.limit != null);

  // ── Uyarılar ──────────────────────────────────────────────────────────────
  const alerts = useMemo<AlertRow[]>(() => {
    const rows: AlertRow[] = [];

    // A frozen book is the alert the rest of the app cannot see: every metric
    // still renders because a frozen book still has equity.
    const note = inertiaNote(flow.data);
    if (note) {
      rows.push({
        key: 'actionability',
        when: formatOrderDate(flow.data?.last_order_at_utc),
        text: note,
        open: true,
        ts: parseUtc(flow.data?.last_order_at_utc)?.getTime() ?? Date.now(),
      });
    }

    const snapshotTs = parseUtc(portfolio.data?.timestamp_utc)?.getTime() ?? Date.now();
    const snapshotWhen = formatOrderDate(portfolio.data?.timestamp_utc);
    for (const f of flags) {
      const text =
        f.code === 'single_name_cap'
          ? `${f.ticker ?? 'Bir isim'} ${formatPct(f.value / 100)} — tek isim tavanı ${formatPct(f.limit / 100)} aşıldı`
          : f.code === 'low_effective_n'
            ? `Etkin isim sayısı ${f.value.toFixed(1)} — ${f.limit.toFixed(0)} eşiğinin altında, kitap ${f.limit.toFixed(0)}'ten az bahis gibi davranıyor`
            : `${f.code} — ${f.value} (limit ${f.limit})`;
      rows.push({
        key: `flag-${f.code}-${f.ticker ?? ''}`,
        when: snapshotWhen,
        text,
        open: true,
        ts: snapshotTs,
      });
    }

    // Older deployments answer with the English `flags` prose and no
    // `flag_items`. Say that a breach exists rather than render English copy.
    if (flags.length === 0 && (conc?.flags.length ?? 0) > 0) {
      rows.push({
        key: 'flags-legacy',
        when: snapshotWhen,
        text: `Sunucu ${conc?.flags.length} yoğunlaşma uyarısı bildirdi — ayrıntısı bu sürümde okunamıyor.`,
        open: true,
        ts: snapshotTs,
      });
    }

    // Every refusal the risk layer wrote down. Closed by definition: the order
    // it blocked is already gone.
    for (const o of orders.data ?? []) {
      if (o.rejection_reasons.length === 0) continue;
      rows.push({
        key: `order-${o.order_id}`,
        when: formatOrderDate(o.submitted_at_utc),
        text: `${o.ticker} ${o.side} ${o.quantity} — ${o.rejection_reasons
          .map(rejectionReasonTr)
          .filter(Boolean)
          .join(' · ')}`,
        open: false,
        ts: parseUtc(o.submitted_at_utc)?.getTime() ?? 0,
      });
    }

    // Open first — the count in the header is what the operator is being asked
    // to look at — then newest first inside each group.
    return rows.sort((a, b) => Number(b.open) - Number(a.open) || b.ts - a.ts);
  }, [conc, flags, flow.data, orders.data, portfolio.data]);

  const openCount = alerts.filter((a) => a.open).length;

  const allDown =
    portfolio.isError && concentration.isError && flow.isError && orders.isError;

  /**
   * "Nothing tripped" and "we never asked" look identical once a list is empty,
   * and on this screen the first one is a claim about the operator's money. So
   * every section states which of the three it is, and an empty state is only
   * ever reached after the query it summarises has actually answered.
   *
   * `isLoading` is first-load-only in react-query, so a poll that fails later
   * keeps showing the last good rows rather than flickering to an error.
   */
  const alertsWaiting = flow.isLoading || concentration.isLoading || orders.isLoading;
  const alertsFailed =
    alerts.length === 0 && (flow.isError || concentration.isError || orders.isError);
  const limitsWaiting =
    limitRows.length === 0 && (concentration.isLoading || portfolio.isLoading);
  const limitsFailed =
    limitRows.length === 0 && (concentration.isError || portfolio.isError);

  const retryLimits = () => {
    void concentration.refetch();
    void portfolio.refetch();
  };
  const retryAlerts = () => {
    void flow.refetch();
    void concentration.refetch();
    void orders.refetch();
  };

  return (
    <SafeAreaView style={[styles.container, { backgroundColor: t.background }]} edges={['top']}>
      <ScrollView contentContainerStyle={styles.scroll}>
        <View style={styles.header}>
          <Text style={styles.h2}>Risk & uyarılar</Text>
          {/* No count until the sources it counts have answered — "0 açık" on a
              screen that has not loaded reads as an all-clear. */}
          {alertsWaiting ? null : <Tag label={`${openCount} açık`} variant="accent" />}
        </View>

        {/* ── Kill switch ─────────────────────────────────────────────────── */}
        <View style={styles.firstBlock}>
          <Text style={styles.sectionTitle}>Kill switch</Text>
          <Seg
            options={killOptions}
            value={ks?.state}
            onChange={applyKill}
            disabled={setKs.isPending}
            block
            style={styles.seg}
          />
          <Text style={styles.helper}>
            {ks?.state
              ? `${killEntry(ks.state)?.desc ?? ks.state} · timer'ı SSH olmadan durdurur.`
              : ksError
                ? 'Durum okunamadı — sunucuya ulaşılamıyor.'
                : 'Durum yükleniyor…'}
          </Text>
        </View>

        {allDown ? (
          <View style={styles.gutterReset}>
            <ErrorState
              title="Risk verileri okunamıyor"
              onRetry={() => {
                void portfolio.refetch();
                void concentration.refetch();
                void flow.refetch();
                void orders.refetch();
              }}
            />
          </View>
        ) : (
          <>
            {/* ── Koruyucu stoplar ──────────────────────────────────────── */}
            {/*
              First on the screen, above the circuit breakers, because it is the
              only section that answers "what happens if the book gaps down
              tonight". It sits here rather than on Portföy because the snapshot
              cannot answer it: Position.stop_loss is a placeholder zero — the
              protective leg is an ORDER, not a property of the position.
            */}
            <View style={styles.block}>
              <Text style={styles.sectionTitle}>Koruyucu stoplar</Text>
              {coverage.isLoading ? (
                <Text style={styles.muted}>Yükleniyor…</Text>
              ) : coverage.isError || !coverage.data ? (
                <View style={styles.gutterReset}>
                  {/* Never render "0% çıplak" on a failed read: that says the
                      book is safe because nothing was checked. */}
                  <ErrorState title="Stop kapsaması okunamadı" onRetry={coverage.refetch} />
                </View>
              ) : coverage.data.total_qty === 0 ? (
                <Text style={styles.helper}>Açık pozisyon yok — korunacak bir şey yok.</Text>
              ) : (
                <>
                  <View style={[styles.meterHead, styles.meter]}>
                    <Text style={styles.meterName}>Korumasız</Text>
                    <Text
                      style={[
                        styles.meterNow,
                        {
                          color:
                            coverage.data.naked_pct > 0
                              ? t.downText ?? t.down
                              : t.textPrimary,
                        },
                      ]}
                    >
                      {formatPct(coverage.data.naked_pct / 100)} ·{' '}
                      {coverage.data.naked_qty.toFixed(0)}/{coverage.data.total_qty.toFixed(0)} lot
                    </Text>
                  </View>
                  {coverage.data.indeterminate_qty > 0 ? (
                    <Text style={styles.helper}>
                      {coverage.data.indeterminate_qty.toFixed(0)} lot belirsiz durumda —
                      korumalı da sayılmıyor, korumasız da.
                    </Text>
                  ) : null}
                  {coverage.data.symbols
                    .filter((sym) => sym.naked_qty > 0)
                    .map((sym) => (
                      <View key={sym.symbol} style={styles.meterHead}>
                        <Text style={styles.meterName}>{sym.symbol}</Text>
                        <Text style={styles.meterNow}>
                          {sym.naked_qty.toFixed(0)} lot stopsuz
                          {sym.protected_qty > 0 ? ` · ${sym.protected_qty.toFixed(0)} korumalı` : ''}
                        </Text>
                      </View>
                    ))}
                  {coverage.data.orphan_stop_symbols.length > 0 ? (
                    <Text style={styles.helper}>
                      Pozisyonu olmayan stop emri: {coverage.data.orphan_stop_symbols.join(', ')} —
                      tetiklenirse açığa satış olur.
                    </Text>
                  ) : null}
                </>
              )}
            </View>

            {/* ── Devre kesiciler ───────────────────────────────────────── */}
            <View style={styles.block}>
              <Text style={styles.sectionTitle}>Devre kesiciler</Text>
              <Text style={styles.helper}>
                {flow.data
                  ? `Son ${flow.data.window_days} günde tetiklenen kesiciler. Eşik değerleri API'de yok — burada kaç kez durdurduğu var.`
                  : 'Eşik değerleri API\'de yok; tetiklenme sayısı emir akışından okunur.'}
              </Text>

              {dailyDd != null ? (
                <Meter
                  row={{
                    key: 'daily_dd',
                    name: 'Günlük drawdown',
                    now: formatPct(dailyDd),
                    limit: null,
                    ratio: null,
                    breached: false,
                  }}
                  styles={styles}
                  t={t}
                />
              ) : null}

              {flow.isLoading ? (
                <Text style={styles.muted}>Yükleniyor…</Text>
              ) : flow.isError ? (
                <View style={styles.gutterReset}>
                  <ErrorState
                    title="Emir akışı okunamadı"
                    onRetry={() => void flow.refetch()}
                  />
                </View>
              ) : breakerTrips.length === 0 ? (
                <View style={styles.gutterReset}>
                  <EmptyState
                    title="Devre kesici tetiklenmedi"
                    hint="Bu pencerede hiçbir emir drawdown, volatilite, API hatası ya da kill switch yüzünden durdurulmadı."
                  />
                </View>
              ) : (
                breakerTrips.map((r) => (
                  <View key={r.reason} style={styles.meter}>
                    <View style={styles.meterHead}>
                      <Text style={styles.meterName} numberOfLines={3}>
                        {r.label}
                      </Text>
                      <Text style={[styles.meterNow, { color: t.downText ?? t.down }]}>
                        {r.count} kez
                      </Text>
                    </View>
                    <View style={styles.barTrack}>
                      <View
                        style={[
                          styles.barFill,
                          {
                            width: `${Math.min(100, Math.max(0, r.share * 100))}%`,
                            backgroundColor: t.accent,
                          },
                        ]}
                      />
                    </View>
                  </View>
                ))
              )}
            </View>

            {/* ── Portföy limitleri ─────────────────────────────────────── */}
            <View style={styles.block}>
              <Text style={styles.sectionTitle}>Portföy limitleri</Text>
              {limitsWaiting ? (
                <Text style={styles.muted}>Yükleniyor…</Text>
              ) : limitsFailed ? (
                <View style={styles.gutterReset}>
                  <ErrorState title="Portföy verisi okunamadı" onRetry={retryLimits} />
                </View>
              ) : limitRows.length === 0 ? (
                <View style={styles.gutterReset}>
                  <EmptyState
                    title="Açık pozisyon yok"
                    hint="Pozisyon açıldığında ağırlıklar ve aşılan limitler burada görünür."
                  />
                </View>
              ) : (
                <>
                  <Text style={styles.helper}>
                    {hasServerLimit
                      ? 'Sunucu yalnızca aşılan limitin eşiğini bildiriyor; aşılmayanlar için yalnız güncel değer var.'
                      : 'Sunucu açık kitapta bir limit aşımı bildirmedi. Eşikler API\'de yok, bu yüzden yalnız güncel değerler gösteriliyor.'}
                  </Text>
                  {limitRows.map((row) => (
                    <Meter key={row.key} row={row} styles={styles} t={t} />
                  ))}
                </>
              )}
            </View>

            {/* ── Uyarılar ──────────────────────────────────────────────── */}
            <View style={styles.block}>
              <Text style={styles.sectionTitle}>Uyarılar</Text>
              {alerts.length === 0 && alertsWaiting ? (
                <Text style={styles.muted}>Yükleniyor…</Text>
              ) : alertsFailed ? (
                <View style={styles.gutterReset}>
                  <ErrorState title="Uyarılar okunamadı" onRetry={retryAlerts} />
                </View>
              ) : alerts.length === 0 ? (
                <View style={styles.gutterReset}>
                  <EmptyState
                    title="Açık uyarı yok"
                    hint="Bir limit aşıldığında ya da risk katmanı bir emri reddettiğinde buraya düşer."
                  />
                </View>
              ) : (
                alerts.map((a) => (
                  <View key={a.key} style={styles.alertRow}>
                    <View style={styles.alertHead}>
                      <Text style={styles.alertWhen}>{a.when}</Text>
                      <Tag
                        label={a.open ? 'açık' : 'kapandı'}
                        variant={a.open ? 'accent' : 'neutral'}
                      />
                    </View>
                    <Text style={styles.alertText}>{a.text}</Text>
                  </View>
                ))
              )}
            </View>
          </>
        )}
      </ScrollView>

      <Sheet
        visible={flattenOpen}
        title="Tüm pozisyonları kapat?"
        message="FLATTEN_ALL tüm açık pozisyonları piyasa fiyatından kapatır ve yeni girişi durdurur. Geri alınamaz."
        busy={setKs.isPending}
        busyLabel="Gönderiliyor…"
        onDismiss={() => setFlattenOpen(false)}
        actions={[
          { label: 'Vazgeç', onPress: () => setFlattenOpen(false) },
          {
            label: 'FLATTEN',
            onPress: confirmFlatten,
            destructive: true,
            accessibilityHint: 'Tüm açık pozisyonları piyasa fiyatından kapatır',
          },
        ]}
      />
    </SafeAreaView>
  );
}

type Styles = ReturnType<typeof makeStyles>;

const makeStyles = (t: Palette) =>
  StyleSheet.create({
    container: { flex: 1 },
    scroll: { paddingHorizontal: 16, paddingTop: 20, paddingBottom: 24 },

    header: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 12,
      marginBottom: 16,
    },
    // Every text style goes through the shared scale, which is what carries
    // `font()` — a bare fontSize renders the system face on Android, because
    // Archivo's three weights are three separate families.
    h2: { color: t.textPrimary, ...TYPE.h2, flexShrink: 1 },

    // The kill switch sits directly under the h2, so it takes no rule of its
    // own; every section after it opens with the 2px section rule.
    firstBlock: { gap: 8 },
    block: { borderTopWidth: 2, borderTopColor: t.divider, paddingTop: 12, marginTop: 16, gap: 8 },
    sectionTitle: { color: t.textPrimary, ...TYPE.section },
    helper: { color: t.textSecondary, ...TYPE.helper, lineHeight: 16 },
    muted: { color: t.textSecondary, ...TYPE.body },
    seg: { alignSelf: 'stretch', minHeight: MIN_TOUCH_TARGET },

    // EmptyState / ErrorState carry their own 16px gutter because every other
    // screen renders them at the ScrollView root. This screen pads its content
    // container instead, so the margin is cancelled rather than doubled.
    gutterReset: { marginHorizontal: -16 },

    meter: { paddingVertical: 8, gap: 4 },
    meterHead: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 },
    meterName: { color: t.textPrimary, ...TYPE.body, flexShrink: 1 },
    meterValue: { textAlign: 'right' },
    meterNow: { ...TYPE.body, ...font(800), ...TABULAR },
    meterLimit: { color: t.textSecondary, ...TYPE.body, ...TABULAR },

    barTrack: { height: 4, backgroundColor: t.neutral200 ?? t.surface, overflow: 'hidden' },
    barFill: { height: 4 },

    alertRow: { paddingVertical: 10, gap: 4, borderBottomWidth: 1, borderBottomColor: t.divider },
    alertHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
    alertWhen: { color: t.textSecondary, ...TYPE.helper },
    alertText: { color: t.textPrimary, ...TYPE.body, lineHeight: 19 },
  });
