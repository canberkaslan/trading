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
