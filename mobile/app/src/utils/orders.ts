/**
 * Order-history presentation helpers — pure, unit-tested.
 *
 * The "Geçmiş" (history) tab renders broker-submitted orders enriched with
 * live Alpaca status. These helpers turn the raw broker status string + fill
 * counts into TR-facing labels + a semantic tone, so the screen stays a thin
 * View over already-fetched data (item 5 slice-1, read-only, OTA-safe).
 */

export type OrderTone = 'up' | 'down' | 'warning' | 'muted';

export interface OrderStatusMeta {
  label: string;
  tone: OrderTone;
}

/**
 * Map an Alpaca broker order status (or null when the broker is unreachable /
 * the row is DB-only) to a Turkish label + tone. Unknown statuses fall back to
 * the raw string with a neutral tone rather than hiding information.
 */
export function orderStatusMeta(brokerStatus: string | null | undefined): OrderStatusMeta {
  if (brokerStatus == null || brokerStatus === '') {
    return { label: 'Broker durumu yok', tone: 'muted' };
  }
  switch (brokerStatus.toLowerCase()) {
    case 'filled':
      return { label: 'Dolduruldu', tone: 'up' };
    case 'partially_filled':
      return { label: 'Kısmi doldu', tone: 'warning' };
    case 'new':
    case 'accepted':
    case 'pending_new':
    case 'accepted_for_bidding':
    case 'held':
    case 'calculated':
      return { label: "Broker'da", tone: 'warning' };
    case 'pending_cancel':
    case 'pending_replace':
    case 'replaced':
      return { label: 'İşleniyor', tone: 'warning' };
    case 'canceled':
    case 'cancelled':
      return { label: 'İptal edildi', tone: 'muted' };
    case 'expired':
    case 'done_for_day':
      return { label: 'Süresi doldu', tone: 'muted' };
    case 'rejected':
    case 'suspended':
    case 'stopped':
      return { label: 'Reddedildi', tone: 'down' };
    default:
      return { label: brokerStatus, tone: 'muted' };
  }
}

/**
 * Human fill summary, e.g. "12/33 lot". Clamps a NaN/negative filled_qty to 0
 * so a bad broker payload never renders "NaN/33".
 */
export function fillSummary(filledQty: number | null | undefined, quantity: number): string {
  const filled =
    filledQty == null || Number.isNaN(filledQty) || filledQty < 0 ? 0 : Math.floor(filledQty);
  return `${filled}/${quantity} lot`;
}

/**
 * Short TR-ish timestamp for a history row, e.g. "23 Tem 09:08". Avoids Intl
 * (unreliable on RN Android) — parses the ISO string and formats manually in
 * UTC. Returns an em dash for an unparseable input.
 */
const TR_MONTHS = [
  'Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz',
  'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara',
];

/**
 * Can this order still be cancelled at the broker?
 *
 * Only orders that actually reached the broker are cancellable — a held order
 * with no `broker_order_id` is /reject's business, and the backend answers 409
 * for it. Terminal broker statuses (filled, canceled, expired, rejected) and
 * in-flight cancels are excluded so the button never offers an action the
 * broker will refuse.
 *
 * Unknown statuses fall through to *not* cancellable: an unrecognised status
 * means we don't know what the order is doing, and a 502 on a money screen is
 * worse than a missing button.
 */
const CANCELLABLE_STATUSES = new Set([
  'new',
  'accepted',
  'pending_new',
  'accepted_for_bidding',
  'held',
  'calculated',
  'partially_filled',
  'replaced',
]);

export function isCancellable(
  brokerStatus: string | null | undefined,
  brokerOrderId: string | null | undefined,
): boolean {
  if (!brokerOrderId) return false;
  if (brokerStatus == null || brokerStatus === '') return false;
  return CANCELLABLE_STATUSES.has(brokerStatus.toLowerCase());
}

/**
 * Turn a raw risk/execution rejection reason into a Turkish sentence.
 *
 * The backend emits machine-keyed strings with the offending numbers attached
 * (`position_pct=12.40% exceeds 10%`, `kill_switch=PAUSE_NEW`). We translate
 * the key and keep the detail verbatim — the number is the useful half, and
 * inventing a friendlier one would be a lie.
 */
const REJECTION_LABELS_TR: Record<string, string> = {
  'non-actionable': 'Aksiyon gerektirmeyen karar',
  risk_layer_rejected: 'Risk katmanı reddetti',
  trimmed_to_zero_by_portfolio_caps: 'Portföy limitleri emri sıfıra indirdi',
  zero_equity: 'Hesap özkaynağı sıfır',
  kill_switch: 'Kill switch devrede',
  daily_drawdown: 'Günlük drawdown limiti',
  price_z_score: 'Aşırı volatilite',
  api_error_rate: 'API hata oranı yüksek',
  consecutive_losses: 'Üst üste zarar',
  position_pct: 'Tek isim limiti',
  sector_pct: 'Sektör limiti',
  correlated_positions: 'Korelasyon limiti',
  gross_exposure: 'Brüt maruziyet limiti',
  adv_usd: 'Likidite yetersiz',
  account_trading_blocked: 'Hesapta işlem engelli',
  pattern_day_trader_active: 'PDT kısıtı aktif',
  market_closed_until: 'Piyasa kapalı',
  stale_decision: 'Karar bayatlamış',
  no_tp_headroom: 'Hedef fiyata yer kalmamış',
  too_close_to_stop: 'Stop seviyesine çok yakın',
};

export function rejectionReasonTr(raw: string | null | undefined): string {
  const text = (raw ?? '').trim();
  if (!text) return '';
  // Key is everything before the first '=' or ':' or space, e.g.
  // "position_pct=12.40% exceeds 10%" -> "position_pct".
  const key = text.split(/[=:\s]/, 1)[0] ?? '';
  const label = REJECTION_LABELS_TR[key];
  if (!label) return text;
  const detail = text.slice(key.length).replace(/^[=:\s]+/, '').trim();
  return detail ? `${label} (${detail})` : label;
}

export function formatOrderDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  const ms = d.getTime();
  if (Number.isNaN(ms)) return '—';
  const day = d.getUTCDate();
  const mon = TR_MONTHS[d.getUTCMonth()];
  const hh = String(d.getUTCHours()).padStart(2, '0');
  const mm = String(d.getUTCMinutes()).padStart(2, '0');
  return `${day} ${mon} ${hh}:${mm}`;
}
