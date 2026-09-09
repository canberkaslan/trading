// trader-core.js — shared mock state + view-model for WebConsole / MobileApp.
// Shapes mirror agent/tradingagents_us/schemas.py and mobile/app/src/api/types.ts.
// Snapshot values: design/canvas.json ($109,061 · NO-GO · 20 gün) + design/Main.dc.html positions.

const NOW = new Date('2026-09-08T15:24:00Z'); // 18:24 TRT
const TR_MONTHS = ['Oca','Şub','Mar','Nis','May','Haz','Tem','Ağu','Eyl','Eki','Kas','Ara'];
const MINUS = '−';

export function fmtUsd(v, { signed = false, dec = 0 } = {}) {
  if (v == null || Number.isNaN(v)) return '—';
  const s = v < 0 ? MINUS : signed ? '+' : '';
  return s + '$' + Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });
}
export function fmtPct(v, { signed = false, dec = 1 } = {}) {
  if (v == null || Number.isNaN(v)) return '—';
  const s = v < 0 ? MINUS : signed ? '+' : '';
  return s + Math.abs(v).toFixed(dec) + '%';
}
export function fmtDate(iso) {
  const d = new Date(new Date(iso).getTime() + 3 * 3600e3); // TRT
  return `${d.getUTCDate()} ${TR_MONTHS[d.getUTCMonth()]} ${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
}
export function fmtDay(iso) {
  const d = new Date(new Date(iso).getTime() + 3 * 3600e3);
  return `${d.getUTCDate()} ${TR_MONTHS[d.getUTCMonth()]}`;
}
export function relAge(iso) {
  if (!iso) return 'hiç';
  const m = Math.floor((NOW - new Date(iso)) / 60000);
  if (m < 1) return 'az önce';
  if (m < 60) return `${m} dk`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} sa`;
  const d = Math.floor(h / 24);
  return `${d} gün`;
}
const num = (v, dec = 2) => (v == null ? '—' : v.toFixed(dec));

// ── quotes & positions ────────────────────────────────────────────────────
export const QUOTES = {
  AAPL: { last: 316.32, chg: 0.6 }, AMZN: { last: 257.14, chg: -0.4 }, GOOGL: { last: 338.25, chg: -1.2 },
  JPM: { last: 356.42, chg: 0.3 }, META: { last: 617.30, chg: 0.2 }, MSFT: { last: 492.82, chg: -0.9 },
  NVDA: { last: 226.93, chg: 1.1 }, XOM: { last: 160.17, chg: -0.5 }, UNH: { last: 394.26, chg: -2.0 },
  V: { last: 368.64, chg: 0.4 }, SPY: { last: 652.40, chg: 0.3 }, TSLA: { last: 418.90, chg: -1.8 },
  AVGO: { last: 342.10, chg: 2.1 }, LLY: { last: 812.30, chg: 0.9 }, COST: { last: 984.15, chg: -0.2 },
  CRM: { last: 258.40, chg: -0.7 }, AMD: { last: 171.05, chg: 1.4 }, 'BRK.B': { last: 498.20, chg: 0.1 },
};
export const POSITIONS = [
  { ticker: 'MSFT', sector: 'Teknoloji', quantity: 27, avg: 361.49, stop: 448.00, opened: '2026-06-30T13:31:00Z' },
  { ticker: 'NVDA', sector: 'Teknoloji', quantity: 55, avg: 198.93, stop: 204.10, opened: '2026-07-14T13:30:00Z' },
  { ticker: 'META', sector: 'İletişim', quantity: 19, avg: 555.75, stop: 561.70, opened: '2026-08-21T13:30:00Z' },
  { ticker: 'V', sector: 'Finans', quantity: 30, avg: 332.90, stop: 335.50, opened: '2026-07-22T13:30:00Z' },
  { ticker: 'JPM', sector: 'Finans', quantity: 31, avg: 340.10, stop: 324.90, opened: '2026-08-22T13:31:00Z' },
  { ticker: 'AMZN', sector: 'Tüketici', quantity: 42, avg: 229.15, stop: 231.40, opened: '2026-07-09T13:30:00Z' },
  { ticker: 'GOOGL', sector: 'İletişim', quantity: 31, avg: 342.46, stop: 311.20, opened: '2026-08-05T13:30:00Z' },
  { ticker: 'AAPL', sector: 'Teknoloji', quantity: 33, avg: 286.77, stop: 287.80, opened: '2026-07-30T13:30:00Z' },
  { ticker: 'XOM', sector: 'Enerji', quantity: 56, avg: 151.20, stop: 145.80, opened: '2026-08-15T13:30:00Z' },
  { ticker: 'UNH', sector: 'Sağlık', quantity: 15, avg: 415.95, stop: 362.70, opened: '2026-08-12T13:30:00Z' },
];
export const CASH = 2829;
export const START_EQUITY = 100000;
export const DAILY_PNL = -815;

export const EVAL = {
  verdict: 'NO-GO', days: 20, days_required: 10, eval_complete: true,
  total_return_pct: 9.06, sharpe: -0.44, sortino: -0.61, max_dd_pct: -2.9, calmar: 3.1, spy_return_pct: 2.8,
  gate_sharpe: 1.0, gate_max_dd_pct: 15,
  gates: [
    { name: 'İşlem günü', passed: true, detail: '20 / 10' },
    { name: 'Sharpe (excess)', passed: false, detail: '−0.44 < 1.0' },
    { name: 'Max drawdown', passed: true, detail: '2.9% < 15%' },
    { name: 'SPY\'yi geçer', passed: true, detail: '+9.1% vs +2.8%' },
  ],
  reasons: ['Sharpe gate: −0.44 < 1.0'],
};
export const FLOW = {
  verdict: 'inert', window_days: 30, orders: 234, submitted: 6, refused: 228,
  by_reason: { position_pct: 118, trimmed_to_zero_by_cash_cap: 74, 'non-actionable': 61, gross_exposure: 22 },
  inert_run_days: 4, run_days: 21, inert_threshold_run_days: 3, last_submitted_at_utc: '2026-09-02T22:41:00Z',
};
export const REALIZED = {
  stats: { trades: 7, wins: 4, losses: 3, win_rate: 57.1, net_pnl: 1842, expectancy: 263, profit_factor: 1.9, avg_holding_days: 11.4 },
  reconciled_at_utc: '2026-09-08T14:12:00Z', window: 'eval', excluded_pre_eval: 3,
  by_exit: [
    { exit_class: 'take_profit', trades: 2, net_pnl: 1410 },
    { exit_class: 'flatten', trades: 2, net_pnl: 810 },
    { exit_class: 'decision_sell', trades: 1, net_pnl: 312 },
    { exit_class: 'stop', trades: 2, net_pnl: -690 },
  ],
  strategy: { trades: 5, net_pnl: 1032, win_rate: 60, avg_pnl: 206 },
};
export const CONC = { n_positions: 10, gross_exposure_pct: 97.4, cash_pct: 2.6, top_weight_pct: 12.2, top3_weight_pct: 34.4, hhi: 0.103, effective_n: 9.7,
  flags: ['MSFT 12.2%', 'NVDA 11.4%', 'META 10.8%', 'V 10.1%', 'JPM 10.1%'] };

const EXIT_TR = { take_profit: 'Kâr al (TP)', stop: 'Koruyucu stop', decision_sell: 'Ajan satış kararı', flatten: 'Operatör flatten', unknown: 'Sınıflanmamış' };
const REJECT_TR = {
  'non-actionable': 'Aksiyon gerektirmeyen karar', risk_layer_rejected: 'Risk katmanı reddetti', trimmed_to_zero_by_portfolio_caps: 'Portföy limitleri emri sıfıra indirdi',
  trimmed_to_zero_by_cash_cap: 'Harcanabilir nakit kalmadı', kill_switch: 'Kill switch devrede', daily_drawdown: 'Günlük drawdown limiti', price_z_score: 'Aşırı volatilite',
  api_error_rate: 'API hata oranı yüksek', consecutive_losses: 'Üst üste zarar', position_pct: 'Tek isim limiti', sector_pct: 'Sektör limiti', gross_exposure: 'Brüt maruziyet limiti',
  adv_usd: 'Likidite yetersiz', market_closed_until: 'Piyasa kapalı', stale_decision: 'Karar bayatlamış', no_tp_headroom: 'Hedef fiyata yer kalmamış', too_close_to_stop: 'Stop seviyesine çok yakın',
  operator_reject: 'Operatör reddetti',
};
export function rejectionReasonTr(raw) {
  const text = (raw || '').trim(); if (!text) return '';
  const key = text.split(/[=:\s]/, 1)[0];
  const label = REJECT_TR[key]; if (!label) return text;
  const detail = text.slice(key.length).replace(/^[=:\s]+/, '').trim();
  return detail ? `${label} (${detail})` : label;
}
export function orderStatusMeta(s) {
  switch ((s || '').toLowerCase()) {
    case 'filled': return { label: 'Dolduruldu', cls: 'tag tag-neutral', tone: 'ink' };
    case 'partially_filled': return { label: 'Kısmi doldu', cls: 'tag tag-outline', tone: 'warn' };
    case 'new': case 'accepted': case 'pending_new': case 'held': return { label: "Broker'da", cls: 'tag tag-outline', tone: 'warn' };
    case 'canceled': case 'cancelled': return { label: 'İptal edildi', cls: 'tag tag-neutral', tone: 'muted' };
    case 'expired': return { label: 'Süresi doldu', cls: 'tag tag-neutral', tone: 'muted' };
    case 'rejected': return { label: 'Reddedildi', cls: 'tag tag-accent', tone: 'down' };
    case '': return { label: 'Broker durumu yok', cls: 'tag tag-neutral', tone: 'muted' };
    default: return { label: s, cls: 'tag tag-neutral', tone: 'muted' };
  }
}
const CANCELLABLE = new Set(['new', 'accepted', 'pending_new', 'held', 'partially_filled']);
export const RATING_CLASS = { Buy: 'ink', Overweight: 'ink', Hold: 'neutral', Underweight: 'accent', Sell: 'accent' };
export function ratingTagClass(r) { const k = RATING_CLASS[r]; return k === 'ink' ? 'tag' : `tag tag-${k}`; }
export function ratingTagStyle(r) { return RATING_CLASS[r] === 'ink' ? 'background:#201e1d;color:#f3f2f2' : ''; }
export function modelBadge(m) { m = (m || '').toLowerCase(); if (m.includes('opus')) return 'Opus 4.8'; if (m.includes('sonnet')) return 'Sonnet 4.6'; if (m.includes('haiku')) return 'Haiku 4.5'; if (m.includes('deepseek')) return 'DeepSeek'; if (m.includes('glm')) return 'GLM'; if (m.includes('qwen')) return 'qwen (local)'; return m; }
const fmtTok = (n) => (n < 1000 ? String(n) : `${(n / 1000).toFixed(1)}k`);
const fmtLat = (ms) => (ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} sn`);

// ── decisions ─────────────────────────────────────────────────────────────
const R = (agent, model, summary, ti, to, ms) => ({ agent, model, summary, tokens_in: ti, tokens_out: to, latency_ms: ms });
const RUN_TS = '2026-09-07T22:41:00Z';
export const DECISIONS = [
  { decision_id: 'dec_avgo_0908', ticker: 'AVGO', rating: 'Overweight', entry_price: 342.10, stop_loss: 312.40, take_profit: 398, price_target: 410, time_horizon: '6-12 ay', suggested_size_pct: 6, timestamp_utc: RUN_TS,
    council: { votes: [['DeepSeek V3', 'Overweight'], ['GLM-4.5', 'Overweight'], ['qwen2.5 (local)', 'Hold']], chair: 'Overweight', confidence: 0.74 },
    reasoning: [
      R('Market Analyst', 'claude-sonnet-4-6', 'Fiyat 50 ve 200 günlük ortalamaların üstünde; 30 günlük ATR $9.80. Hacim 20 günlük ortalamanın %18 üzerinde — birikim işareti.', 6120, 410, 5200),
      R('Fundamentals Analyst', 'claude-sonnet-4-6', 'FQ3: AI yarı iletken geliri y/y +63%. Serbest nakit akışı marjı %47. Net borç/EBITDA 1.8x ve düşüyor; VMware entegrasyonu marj seyreltmesini geride bıraktı.', 8340, 520, 6900),
      R('News Analyst', 'claude-sonnet-4-6', 'Hyperscaler özel ASIC siparişleri için 2027 kapasite anlaşması haberi. Olumsuz: ihracat lisansı belirsizliği Çin gelirini (%12) tehdit ediyor.', 5410, 380, 4800),
      R('Bull Researcher', 'claude-sonnet-4-6', 'Özel silikon, GPU pazarının iki katı hızla büyüyor; AVGO bu pazarın %70\'ini tutuyor. 2027 F/K 24x — büyümeye göre ucuz.', 9100, 640, 7300),
      R('Bear Researcher', 'claude-sonnet-4-6', 'Müşteri konsantrasyonu: iki hyperscaler gelirin %40\'ı. Kapasite anlaşmaları iptal edilebilir; 2025 sonu Nvidia NVLink Fusion ile özel silikon pazarına giriyor.', 8800, 610, 7100),
      R('Research Manager', 'claude-opus-4-8', 'Bull tezi daha güçlü: kârlılık ve görünürlük yüksek, bear riskleri fiyatlanmış. Hüküm: Overweight, ancak boyut sınırlı — sektör ağırlığı (Teknoloji %33) zaten tavanın üstünde.', 14200, 720, 11800),
      R('Portfolio Manager', 'claude-opus-4-8', 'Overweight, %6 ağırlık, giriş $342.10, stop $312.40 (2×ATR), TP $398. Teknoloji sektör limiti bu emri onayda kısabilir; PM bunu bilerek küçük boyut önerdi.', 17600, 860, 13900),
    ],
    debate: { bull: 'Özel silikon TAM 2027\'de $60B; AVGO\'nun payı korunursa gelir CAGR %28. Hyperscaler capex kılavuzları yukarı revize.', bear: 'Yoğunlaşma riski gerçek. Tek bir müşterinin ertelemesi çeyreklik gelirin %15\'ini kaydırır. Değerleme hata payı bırakmıyor.', risk_manager: 'Konsensüs: pozisyon aç ama bracket zorunlu; Teknoloji ağırlığı %30 tavanına karşı boyutu kıs.' },
    final_decision_text: 'Overweight. Özel ASIC talebi ve serbest nakit akışı marjı tezi taşıyor; bear tarafının müşteri yoğunlaşması itirazı boyutu sınırlıyor. %6 ağırlık, giriş $342.10, stop $312.40, kâr al $398. Teknoloji sektörü %33 ile limitin üstünde — risk katmanı boyutu kısabilir, emir onaya düşer.' },
  { decision_id: 'dec_googl_0908', ticker: 'GOOGL', rating: 'Underweight', entry_price: null, stop_loss: null, take_profit: null, price_target: 305, time_horizon: '3-6 ay', suggested_size_pct: 0, timestamp_utc: RUN_TS,
    council: { votes: [['DeepSeek V3', 'Underweight'], ['GLM-4.5', 'Hold'], ['qwen2.5 (local)', 'Underweight']], chair: 'Underweight', confidence: 0.66 },
    reasoning: [
      R('Market Analyst', 'claude-sonnet-4-6', 'Fiyat 50 günlük ortalamanın altına kırıldı; RSI 38. Göreli güç SPY\'ye karşı 3 haftadır negatif.', 6010, 390, 5100),
      R('News Analyst', 'claude-sonnet-4-6', 'Arama reklam gelirinde AI Overviews kaynaklı tıklama erozyonu ilk kez rakamlarda; DOJ çözüm duruşması Ekim\'de.', 5600, 400, 4900),
      R('Bear Researcher', 'claude-sonnet-4-6', 'Arama marjı yapısal baskı altında; Cloud büyümesi bunu telafi etmiyor. Pozisyon zaten stop\'a %8 uzaklıkta.', 8700, 580, 7000),
      R('Research Manager', 'claude-opus-4-8', 'Bear ağır basıyor. Tez bozuldu: giriş gerekçesi olan reklam marjı istikrarı artık geçerli değil. Hüküm: Underweight → pozisyonu kapat.', 13900, 690, 11200),
      R('Portfolio Manager', 'claude-opus-4-8', 'Underweight, çıkış emri. 31 lot piyasa fiyatından satış; −1.2% ile küçük zarar realize edilir, stop\'un vurmasını beklemek −9% demek.', 16900, 810, 13100),
    ],
    debate: { bull: 'Cloud %32 büyüyor, Waymo opsiyonelliği fiyatlanmamış. Düşüş alım fırsatı.', bear: 'Ana motor arama; erozyon başladıysa çarpan daralır. Elde tutmanın maliyeti fırsat maliyeti.' },
    final_decision_text: 'Underweight — pozisyonu kapat. Giriş tezi (arama reklam marjı istikrarı) bozuldu; AI Overviews erozyonu rakamlara girdi. 31 lot piyasa satışı, küçük zararla çık; stop\'u beklemek gereksiz risk.' },
  { decision_id: 'dec_nvda_0908', ticker: 'NVDA', rating: 'Overweight', entry_price: 226.90, stop_loss: 204.10, take_profit: 268, price_target: 275, time_horizon: '6-12 ay', suggested_size_pct: 8, timestamp_utc: RUN_TS,
    council: { votes: [['DeepSeek V3', 'Overweight'], ['GLM-4.5', 'Overweight'], ['qwen2.5 (local)', 'Overweight']], chair: 'Overweight', confidence: 0.81 },
    reasoning: [
      R('Fundamentals Analyst', 'claude-sonnet-4-6', 'Veri merkezi geliri y/y +56%; Blackwell rampası brüt marjı %75\'e taşıdı.', 8200, 500, 6700),
      R('Bear Researcher', 'claude-sonnet-4-6', 'Değerleme ve ihracat kısıtları; hyperscaler capex döngüsünün 2027\'de yavaşlama riski.', 8600, 590, 6900),
      R('Portfolio Manager', 'claude-opus-4-8', 'Overweight ama mevcut pozisyon %11.4 ile tek isim tavanının üstünde. Karar aksiyona dönüşmez; risk katmanı ekleme emrini reddeder.', 16800, 790, 12900),
    ],
    debate: {}, final_decision_text: 'Overweight. Ancak pozisyon %11.4 ile %10 tavanının üstünde — ekleme yok. Mevcut pozisyon bracket stop $204.10 ile korunuyor.' },
  { decision_id: 'dec_unh_0822', ticker: 'UNH', rating: 'Hold', entry_price: null, stop_loss: 362.70, take_profit: null, price_target: 413, time_horizon: '6-12 ay', suggested_size_pct: 0, timestamp_utc: '2026-08-22T22:41:00Z',
    council: { votes: [['DeepSeek V3', 'Hold'], ['GLM-4.5', 'Hold'], ['qwen2.5 (local)', 'Underweight']], chair: 'Hold', confidence: 0.58 },
    reasoning: [
      R('News Analyst', 'claude-sonnet-4-6', 'Medicare Advantage maliyet oranı kılavuzu yukarı; DOJ soruşturması sürüyor.', 5300, 370, 4700),
      R('Portfolio Manager', 'claude-opus-4-8', 'Hold. Tez zayıfladı ama değerleme 12x F/K ile taban oluşturuyor. Stop $362.70 korunur.', 15900, 700, 12000),
    ], debate: {}, final_decision_text: 'Hold. Değerleme tabanı var, ancak maliyet oranı görünürlüğü düşük. Yeni para yok; stop korunur.' },
  { decision_id: 'dec_xom_0822', ticker: 'XOM', rating: 'Hold', entry_price: null, stop_loss: 145.80, take_profit: null, price_target: 166, time_horizon: '12-18 ay', suggested_size_pct: 0, timestamp_utc: '2026-08-22T22:41:00Z',
    council: { votes: [['DeepSeek V3', 'Hold'], ['GLM-4.5', 'Hold'], ['qwen2.5 (local)', 'Hold']], chair: 'Hold', confidence: 0.7 },
    reasoning: [R('Portfolio Manager', 'claude-opus-4-8', 'Hold. Brent $78 bandında; Permian birim maliyeti düşüyor. Temettü + geri alım getirisi %7.', 15200, 640, 11500)], debate: {},
    final_decision_text: 'Hold. Nakit getirisi pozisyonu taşıyor; katalizör yok, ekleme yok.' },
  { decision_id: 'dec_jpm_0822', ticker: 'JPM', rating: 'Overweight', entry_price: 340.10, stop_loss: 324.90, take_profit: 372, price_target: 366, time_horizon: '6-12 ay', suggested_size_pct: 10, timestamp_utc: '2026-08-22T22:41:00Z',
    council: { votes: [['DeepSeek V3', 'Overweight'], ['GLM-4.5', 'Overweight'], ['qwen2.5 (local)', 'Hold']], chair: 'Overweight', confidence: 0.69 },
    reasoning: [R('Portfolio Manager', 'claude-opus-4-8', 'Overweight, %10. Net faiz geliri kılavuzu yukarı, yatırım bankacılığı ücretleri toparlanıyor.', 16100, 720, 12200)], debate: {},
    final_decision_text: 'Overweight, %10 ağırlık. Giriş $340.10, stop $324.90, TP $372.' },
  { decision_id: 'dec_meta_0821', ticker: 'META', rating: 'Overweight', entry_price: 555.75, stop_loss: 561.70, take_profit: 640, price_target: 625, time_horizon: '6-12 ay', suggested_size_pct: 10, timestamp_utc: '2026-08-21T22:41:00Z',
    council: { votes: [['DeepSeek V3', 'Overweight'], ['GLM-4.5', 'Overweight'], ['qwen2.5 (local)', 'Overweight']], chair: 'Overweight', confidence: 0.77 },
    reasoning: [R('Portfolio Manager', 'claude-opus-4-8', 'Overweight. Reklam yükü artışı ve Reels monetizasyonu; capex endişesi fiyatlanmış.', 16400, 740, 12600)], debate: {},
    final_decision_text: 'Overweight, %10 ağırlık. Giriş $555.75, stop trailing $561.70, TP $640.' },
];

export const PENDING = [
  { order_id: 'ord_8f2a', decision_id: 'dec_avgo_0908', ticker: 'AVGO', side: 'BUY', quantity: 18, order_type: 'MARKET', stop_loss: 312.40, take_profit: 398, notional: 6158, pct: 5.6, risk_approved: true, rejection_reasons: [], broker_order_id: null, broker_status: null, filled_qty: 0, avg_fill_price: null, submitted_at_utc: '2026-09-07T22:44:00Z', expires_utc: '2026-09-08T20:00:00Z' },
  { order_id: 'ord_9c11', decision_id: 'dec_googl_0908', ticker: 'GOOGL', side: 'SELL', quantity: 31, order_type: 'MARKET', stop_loss: 311.20, take_profit: null, notional: 10486, pct: 9.6, risk_approved: true, rejection_reasons: [], broker_order_id: null, broker_status: null, filled_qty: 0, avg_fill_price: null, submitted_at_utc: '2026-09-07T22:44:00Z', expires_utc: '2026-09-08T20:00:00Z' },
];
export const HISTORY = [
  { order_id: 'ord_7a01', ticker: 'NVDA', side: 'BUY', quantity: 22, order_type: 'MARKET', stop_loss: 204.10, risk_approved: false, rejection_reasons: ['position_pct=11.40% exceeds 10%'], broker_order_id: null, broker_status: null, filled_qty: 0, avg_fill_price: null, submitted_at_utc: '2026-09-07T22:44:00Z' },
  { order_id: 'ord_6b22', ticker: 'JPM', side: 'BUY', quantity: 31, order_type: 'MARKET', stop_loss: 324.90, risk_approved: true, rejection_reasons: [], broker_order_id: 'alp_5e1c…', broker_status: 'filled', filled_qty: 31, avg_fill_price: 340.10, submitted_at_utc: '2026-08-22T22:44:00Z' },
  { order_id: 'ord_6b23', ticker: 'MSFT', side: 'BUY', quantity: 4, order_type: 'MARKET', stop_loss: 448.00, risk_approved: false, rejection_reasons: ['position_pct=12.40% exceeds 10%'], broker_order_id: null, broker_status: null, filled_qty: 0, avg_fill_price: null, submitted_at_utc: '2026-08-22T22:44:00Z' },
  { order_id: 'ord_5c10', ticker: 'XOM', side: 'BUY', quantity: 56, order_type: 'MARKET', stop_loss: 145.80, risk_approved: true, rejection_reasons: [], broker_order_id: 'alp_41ab…', broker_status: 'filled', filled_qty: 56, avg_fill_price: 151.20, submitted_at_utc: '2026-08-14T22:44:00Z' },
  { order_id: 'ord_4d09', ticker: 'CRM', side: 'SELL', quantity: 20, order_type: 'MARKET', stop_loss: 241.00, risk_approved: true, rejection_reasons: [], broker_order_id: 'alp_3c90…', broker_status: 'filled', filled_qty: 20, avg_fill_price: 262.30, submitted_at_utc: '2026-08-11T22:44:00Z' },
  { order_id: 'ord_3e08', ticker: 'TSLA', side: 'BUY', quantity: 12, order_type: 'LIMIT', stop_loss: 372.00, risk_approved: true, rejection_reasons: [], broker_order_id: 'alp_2b7f…', broker_status: 'canceled', filled_qty: 0, avg_fill_price: null, submitted_at_utc: '2026-08-07T22:44:00Z' },
  { order_id: 'ord_2f07', ticker: 'AAPL', side: 'BUY', quantity: 33, order_type: 'MARKET', stop_loss: 263.80, risk_approved: true, rejection_reasons: [], broker_order_id: 'alp_1a6e…', broker_status: 'filled', filled_qty: 33, avg_fill_price: 286.77, submitted_at_utc: '2026-07-29T22:44:00Z' },
  { order_id: 'ord_1g06', ticker: 'LLY', side: 'BUY', quantity: 9, order_type: 'LIMIT', stop_loss: 742.00, risk_approved: true, rejection_reasons: [], broker_order_id: 'alp_0f5d…', broker_status: 'expired', filled_qty: 0, avg_fill_price: null, submitted_at_utc: '2026-07-24T22:44:00Z' },
];
export const NOTIFS = [
  { id: 'n1', type: 'decision_pending', title: 'AVGO BUY 18 onay bekliyor', body: 'Overweight · stop $312.40 · portföyün %5.6\'sı', receivedAt: '2026-09-07T22:44:00Z', route: 'orders', read: false },
  { id: 'n2', type: 'decision_pending', title: 'GOOGL SELL 31 onay bekliyor', body: 'Underweight · pozisyon kapanışı · −1.2%', receivedAt: '2026-09-07T22:44:00Z', route: 'orders', read: false },
  { id: 'n3', type: 'inert_alert', title: 'Emir akışı donmuş — 4 çalışma günü', body: 'Broker\'a hiçbir emir ulaşmadı (eşik 3). Baskın sebep: tek isim limiti.', receivedAt: '2026-09-06T06:00:00Z', route: 'risk', read: false },
  { id: 'n4', type: 'eval_report', title: 'Haftalık eval: NO-GO', body: 'Sharpe −0.44 < 1.0 · MaxDD 2.9% · +9.1% vs SPY +2.8%', receivedAt: '2026-09-07T06:00:00Z', route: 'settings', read: true },
  { id: 'n5', type: 'order_rejected', title: 'MSFT BUY 4 reddedildi', body: 'Tek isim limiti (12.40% > 10%)', receivedAt: '2026-08-22T22:44:00Z', route: 'orders', read: true },
  { id: 'n6', type: 'order_filled', title: 'JPM BUY 31 gerçekleşti', body: 'Ort. $340.10 · bracket stop $324.90 · TP $372', receivedAt: '2026-08-23T13:31:00Z', route: 'portfolio', read: true },
  { id: 'n7', type: 'order_submitted', title: 'JPM BUY 31 broker\'a gönderildi', body: 'Alpaca paper · alp_5e1c…', receivedAt: '2026-08-22T22:45:00Z', route: 'orders', read: true },
];
const NOTIF_TYPE = { decision_pending: ['Onay bekliyor', 'tag tag-outline'], order_submitted: ['Emir gönderildi', 'tag tag-neutral'], order_filled: ['Emir gerçekleşti', 'tag tag-neutral'], order_rejected: ['Emir reddedildi', 'tag tag-accent'], eval_report: ['Eval raporu', 'tag tag-neutral'], inert_alert: ['Uyarı', 'tag tag-accent'] };

export const WATCH_DEFAULT = ['SPY', 'AVGO', 'TSLA', 'LLY', 'COST', 'CRM'];
const LAST_RATING = { AVGO: ['Overweight', RUN_TS], TSLA: ['Hold', '2026-09-03T22:41:00Z'], LLY: ['Hold', '2026-07-24T22:41:00Z'], CRM: ['Sell', '2026-08-11T22:41:00Z'] };

export const LESSONS = [
  { id: 'paper_trading', title: 'Paper trading ne kanıtlar', summary: 'Simüle parayla gerçek fiyatlarda test etmenin neyi kanıtlayıp neyi kanıtlamadığı.',
    body: ['Paper hesap gerçek fiyatlarla ama sanal parayla çalışır: karar üretimi, risk katmanı, emir tesisatı ve stop kapsaması uçtan uca test edilir.', 'Kanıtlamadığı şey: gerçek dolum kayması (slippage), kısmi dolumlar ve panik anında insanın müdahalesi. Bu yüzden go-live küçük sermayeyle başlar.'],
    takeaway: 'Paper, tesisatın çalıştığını kanıtlar; getiriyi değil.', quizQ: 'Paper eval GO verdi. Bu ne anlama gelir?', quizOptions: ['Strateji gerçek parada da kazanır', 'Tesisat çalıştı ve strateji yıkıcı olmadı', 'Sharpe kalıcı olarak 1\'in üstünde kalır'], quizAnswer: 1, quizExplain: 'GO, "plumbing works and the strategy wasn\'t destructive" demektir — bir getiri tahmini değil.' },
  { id: 'agents', title: 'Tartışmadan emre giden yol', summary: 'Bir rating nasıl doğuyor, emre dönüşmeden önce onu gerçekten ne durdurabiliyor.',
    body: ['Dört analist veri toplar; bull ve bear araştırmacılar tartışır; Research Manager hüküm verir; trader ve üç risk tartışmacısı boyutu konuşur; Portfolio Manager son kararı yazar; LLM konseyi çapraz kontrol eder.', 'Ancak emri durduran LLM değil, deterministik risk katmanıdır: tek isim %10, sektör %30, nakit bütçesi, devre kesiciler ve kill switch.'],
    takeaway: 'Rating bir görüştür; emir, risk katmanının izniyle var olur.', quizQ: 'Overweight kararı verildi ama emir gitmedi. En olası sebep?', quizOptions: ['Konsey kararı veto etti', 'Risk katmanı bir limite takıldı', 'Piyasa açık değildi'], quizAnswer: 1, quizExplain: 'Bu kitapta emirlerin %97\'si risk katmanında (position_pct, nakit) durdu; konsey rating\'i etkiler, emri değil.' },
  { id: 'sharpe', title: 'Risk başına getiri', summary: 'Bir getiri sayısı, hangi oynaklıkla alındığını bilmeden neden hiçbir şey anlatmaz.',
    body: ['Sharpe = (getiri − risksiz faiz) / oynaklık. +9% getiri, günlük %2 salınımla alındıysa şanslı bir yazı-tura olabilir.', 'Bu kitapta Sharpe −0.44: getiri pozitif ama risksiz faizin üstündeki fazla, oynaklığa göre negatif. Gate 1.0 istiyor.'],
    takeaway: 'Getiriyi oynaklığa böl; bölüm 1\'in altındaysa şansı yeteneğe karıştırma.', quizQ: 'İki strateji de +9% yaptı. A\'nın günlük oynaklığı %0.5, B\'nin %2. Hangisi daha iyi?', quizOptions: ['A', 'B', 'Aynı'], quizAnswer: 0, quizExplain: 'Aynı getiri, dörtte bir oynaklık: A\'nın Sharpe\'ı yaklaşık dört kat yüksek.' },
  { id: 'max_drawdown', title: 'Max drawdown gate\'i', summary: 'Bir stratejinin taşınabilir olup olmadığını getirisi değil, en kötü tepe-dip düşüşü belirler.',
    body: ['Max drawdown, sermaye eğrisinin en yüksek noktasından en derin dibe düşüşüdür. %50 düşüş, geri dönmek için %100 kazanç ister.', 'Gate %15: bunun üstünde bir düşüş, operatörün stratejiyi panikle kapatma olasılığını dayanılmaz yapar.'],
    takeaway: 'Taşıyamayacağın drawdown\'ı olan strateji, kazanmadan önce kapatılır.', quizQ: '%20 drawdown sonrası başa dönmek için gereken getiri?', quizOptions: ['%20', '%25', '%40'], quizAnswer: 1, quizExplain: '0.8 × 1.25 = 1.0. Düşüşler asimetriktir.' },
  { id: 'alpha_benchmark', title: 'Kâr değil, alpha', summary: 'SPY aynı pencerede +12% yapmışken +9% getirinin neden kötü bir sonuç olduğu.',
    body: ['Bir endeks fonu almak sıfır çaba ve ~%0.03 masraf demektir. Strateji SPY\'yi geçmiyorsa LLM maliyeti ve emek boşa gitmiştir.', 'Bu penceredeki +9.1% vs SPY +2.8% olumlu görünür; ancak 20 günlük bir örneklem istatistiksel olarak hiçbir şeydir.'],
    takeaway: 'Karşılaştırma ölçütü sıfır değil, SPY\'dir.', quizQ: 'Strateji +9%, SPY +12%. Alpha?', quizOptions: ['+9%', '−3%', '+21%'], quizAnswer: 1, quizExplain: 'Alpha = strateji − benchmark = −3%.' },
  { id: 'concentration', title: 'On isim, üç bahis', summary: 'Pozisyon sayısı çeşitlendirme konusunda neden yanıltıcı; HHI ve effective N ne ölçüyor.',
    body: ['On pozisyon, üçü portföyün %35\'iyse ve hepsi Teknoloji\'yse, aslında birkaç bahis vardır. HHI ağırlıkların karelerinin toplamıdır; effective N = 1/HHI.', 'Bu kitapta effective N 9.7 — iyi; ama beş isim %10 tavanının üstünde ve Teknoloji %33 ile sektör limitini aşıyor.'],
    takeaway: 'Sayıya değil, ağırlıkların dağılımına bak.', quizQ: 'Effective N 9.7 ne anlatır?', quizOptions: ['9.7 pozisyon var', 'Eşit ağırlıklı ~10 pozisyon kadar çeşitli', 'Sektör riski yok'], quizAnswer: 1, quizExplain: 'Effective N, ağırlık dağılımını eşit-ağırlıklı eşdeğerine çevirir; sektör riskini söylemez.' },
  { id: 'stop_loss', title: 'Stop-loss ve bracket order', summary: 'Risk katmanı stop\'u girişte neden ekler, dar stop neden daha güvenli değil.',
    body: ['Her giriş bracket olarak gider: stop + kâr al broker tarafında durur. Box çökse bile stop çalışır.', 'Stop 2×ATR uzaklığa konur. Daha dar stop daha çok "gürültüyle" vurulur; pozisyon boyutu stop mesafesine göre ölçeklenir (%0.5 risk/işlem).'],
    takeaway: 'Stop mesafesini oynaklık belirler; boyutu stop belirler.', quizQ: 'Stop\'u iki kat yaklaştırırsan risk katmanı ne yapar?', quizOptions: ['Boyutu iki katına çıkarır', 'Boyutu değiştirmez', 'Emri reddeder'], quizAnswer: 0, quizExplain: 'Risk/işlem sabit (%0.5): stop yarıya inerse adet iki katına çıkar.' },
  { id: 'go_live_gates', title: 'Gate geçilir, tartışılmaz', summary: 'Gerçek paraya geçişin neden sabit gate\'lere bağlı olduğu.',
    body: ['Dört gate: ≥10 işlem günü, Sharpe >1.0, MaxDD <15%, SPY\'yi geçmek. Hepsi geçilmeden ALPACA_BASE_URL live\'a çevrilmez.', 'Gate\'ler günün ruh haline karşı koruma sağlar: iyi bir hafta sonrası "hadi geçelim" refleksini engeller.'],
    takeaway: 'Kural önceden yazılır; sonuç geldiğinde tartışılmaz.', quizQ: 'Üç gate geçti, Sharpe 0.9. Karar?', quizOptions: ['GO — yeterince yakın', 'NO-GO', 'Gate\'i 0.9\'a çek'], quizAnswer: 1, quizExplain: 'Gate eşiği sonuç görüldükten sonra değiştirilmez.' },
];

export const RUNLOG = [
  { t: '01:30:04', stage: 'Preflight', detail: 'Alpaca ✓ · Polygon ✓ · FRED ✓ · SEC EDGAR ✓', status: 'ok' },
  { t: '01:30:11', stage: 'Evren', detail: 'SPY + 20 isim: 10 pozisyon · 6 izleme · 4 tarama', status: 'ok' },
  { t: '01:30–01:47', stage: '4 analist × 21', detail: 'Market · Fundamentals · News · Sentiment — Sonnet 4.6 · 84 çağrı', status: 'ok' },
  { t: '01:47–01:53', stage: 'Bull / Bear tartışması', detail: '2 tur · Sonnet 4.6', status: 'ok' },
  { t: '01:53–01:56', stage: 'Research Manager', detail: '21 hüküm · Opus 4.8', status: 'ok' },
  { t: '01:56–02:01', stage: 'Trader + 3 risk tartışmacısı', detail: 'Boyut ve stop önerileri', status: 'ok' },
  { t: '02:01–02:05', stage: 'Portfolio Manager', detail: '3 Overweight · 1 Underweight · 17 Hold — Opus 4.8', status: 'ok' },
  { t: '02:05–02:08', stage: 'LLM konseyi', detail: 'DeepSeek · GLM · qwen (local) → Chair Opus 4.8 · 2 uyumsuzluk çözüldü', status: 'ok' },
  { t: '02:08:12', stage: 'Risk katmanı', detail: '4 aksiyon → 2 emir onaya (hold) · 1 red: position_pct NVDA · 1 sıfıra kısıldı: nakit', status: 'warn' },
  { t: '02:08:20', stage: 'Executor', detail: '0 gönderim · 2 emir onay bekliyor', status: 'warn' },
  { t: '02:12:40', stage: 'Reconcile', detail: '10 pozisyon ↔ Alpaca eşleşti · stop kapsaması 10/10', status: 'ok' },
];
export const RUN_COST = '$14.20 · 1.9M token · 22 dk';
export const ALERTS = [
  { when: '6 Eyl', kind: 'inert_alert', text: 'Donmuş kitap: 4 çalışma günüdür broker\'a emir ulaşmadı (eşik 3)', open: true },
  { when: '8 Eyl', kind: 'concentration', text: '5 isim tek-isim tavanının (%10) üstünde: MSFT · NVDA · META · V · JPM', open: true },
  { when: '8 Eyl', kind: 'sector_pct', text: 'Teknoloji %33.2 — sektör limiti %30 aşıldı', open: true },
  { when: '8 Eyl', kind: 'cash_budget', text: 'Harcanabilir nakit $0 — yeni giriş için satış gerekir', open: true },
  { when: '2 Eyl', kind: 'stop_coverage', text: 'Stop kapsaması 10/10 — tüm pozisyonlarda broker-side stop var', open: false },
  { when: '23 Ağu', kind: 'order_rejected', text: 'MSFT BUY 4 — position_pct 12.40% > 10%', open: false },
];
export const BREAKERS = [
  { name: 'Günlük drawdown', limit: '−3.0%', now: '−0.74%', pct: 25, ok: true },
  { name: 'Üst üste zarar', limit: '4', now: '1', pct: 25, ok: true },
  { name: 'API hata oranı', limit: '20%', now: '1.2%', pct: 6, ok: true },
  { name: 'Fiyat z-skoru', limit: '|z| > 4', now: '2.1', pct: 52, ok: true },
];
export const LIMITS = [
  { name: 'Tek isim', limit: '10%', now: '12.2% (MSFT)', pct: 122, ok: false },
  { name: 'Sektör', limit: '30%', now: '33.2% (Teknoloji)', pct: 111, ok: false },
  { name: 'Brüt maruziyet', limit: '100%', now: '97.4%', pct: 97, ok: true },
  { name: 'Nakit rezervi', limit: '≥ 2.5%', now: '2.6%', pct: 96, ok: true },
];

// ── series ────────────────────────────────────────────────────────────────
function seed(str) { let h = 2166136261; for (const c of str) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return () => { h ^= h << 13; h ^= h >>> 17; h ^= h << 5; return ((h >>> 0) % 10000) / 10000; }; }
export function bars(ticker, days) {
  const q = QUOTES[ticker] || { last: 100 + (ticker.length * 17) % 300 };
  const rnd = seed(ticker + days);
  const out = []; let c = q.last;
  for (let i = 0; i < days; i++) { // walk backwards from the last close
    const o = c * (1 + (rnd() - 0.5) * 0.03);
    const h = Math.max(o, c) * (1 + rnd() * 0.012);
    const l = Math.min(o, c) * (1 - rnd() * 0.012);
    out.unshift({ o, h, l, c, v: 1e6 + rnd() * 3e6 });
    c = o * (1 + (rnd() - 0.48) * 0.004);
  }
  return out;
}
export function equitySeries(period) {
  const evalDays = 20; const total = period === '1A' ? 22 : period === '3A' ? 64 : 128;
  const rnd = seed('equity' + period); const pts = [];
  const target = [232, 244, 208, 168, 150, 142, 120, 138, 96, 60, 66, 58, 74, 70, 88, 80, 96, 72, 84, 52]; // shape from design/Main.dc.html
  const spyShape = [232, 238, 226, 214, 220, 208, 214, 244, 196, 188, 196, 182, 190, 178, 186, 180, 190, 184, 178, 182];
  for (let i = 0; i < total; i++) {
    const k = i - (total - evalDays);
    let eq, spy;
    if (k < 0) { // pre-eval: idle paper account after the 24 Haz flatten
      eq = START_EQUITY + (i < total - 55 ? (rnd() - 0.5) * 1800 : 0);
      spy = START_EQUITY * (1 + (i / total) * 0.06 + (rnd() - 0.5) * 0.01);
    } else {
      eq = START_EQUITY + (232 - target[k]) / 180 * 9061;
      spy = START_EQUITY * (1 + 0.06 * (total - evalDays) / total) + (232 - spyShape[k]) / 50 * 2800;
    }
    pts.push({ eq, spy });
  }
  return pts;
}
export function toPath(pts, key, W, H, pad = 6) {
  const vals = pts.flatMap(p => [p.eq, p.spy]); const min = Math.min(...vals), max = Math.max(...vals);
  const x = (i) => (i / (pts.length - 1)) * W; const y = (v) => H - pad - ((v - min) / (max - min || 1)) * (H - pad * 2);
  return { d: pts.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join(' '), lastY: y(pts[pts.length - 1][key]), min, max };
}

// ── state ─────────────────────────────────────────────────────────────────
export function initialState(props) {
  const screen = props.screen || 'portfolio';
  return {
    screen, authed: screen !== 'login', period: '1A', ordersTab: 'pending', selectedOrderId: null, approveStep: null, rejectDialog: false, cancelDialogId: null,
    pending: PENDING.map(o => ({ ...o })), history: HISTORY.map(o => ({ ...o })),
    expanded: {}, detailDecisionId: null,
    chat: { messages: [], input: '', busy: false, status: '' }, stream: null,
    chartTicker: props.ticker || 'AAPL', chartInput: props.ticker || 'AAPL', chartMode: 'candle', chartDays: 90,
    watch: [...WATCH_DEFAULT], watchInput: '',
    killSwitch: 'RUN', flattenDialog: false,
    sw: { autoExecute: true, push: true, weekly: true, biometric: true, refuseOutside: false, bracket: true }, sizing: 'atr', maxPos: '10',
    notifs: NOTIFS.map(n => ({ ...n })), lessonOpen: null, lessonAnswers: {}, toast: null,
    email: '', password: '', ack: false, moreOpen: false,
  };
}

export function vals(comp) {
  const s = comp.state; const p = comp.props;
  const set = (patch) => comp.setState(patch);
  const isLive = (p.mode || 'paper') === 'live';
  const palette = p.pnlPalette || 'accounting';
  const UP = palette === 'greenred' ? 'oklch(0.52 0.14 152)' : '#201e1d';
  const DOWN = '#ec3013';
  const tone = (v) => (v < 0 ? DOWN : UP);
  const toast = (t) => { set({ toast: t }); clearTimeout(comp._toastT); comp._toastT = setTimeout(() => set({ toast: null }), 2600); };
  const go = (screen) => set({ screen, selectedOrderId: null, approveStep: null, detailDecisionId: null, rejectDialog: false, moreOpen: false });

  // navigation
  const NAV = [['portfolio', 'Portföy'], ['ask', 'Sor'], ['orders', 'Emirler'], ['agents', 'Ajanlar'], ['charts', 'Grafik'], ['watch', 'İzleme'], ['risk', 'Risk'], ['learn', 'Öğren'], ['settings', 'Ayarlar']];
  const pendingCount = s.pending.length;
  const nav = NAV.map(([id, label]) => ({ id, label, active: s.screen === id, go: () => go(id), badge: id === 'orders' && pendingCount ? String(pendingCount) : null }));
  const MOBILE_TABS = ['portfolio', 'ask', 'orders', 'agents'];
  const tabs = nav.filter(n => MOBILE_TABS.includes(n.id)).map(n => ({ ...n, active: n.active && !s.moreOpen }));
  const moreItems = nav.filter(n => !MOBILE_TABS.includes(n.id)).concat([{ id: 'notifs', label: 'Bildirimler', active: s.screen === 'notifs', go: () => go('notifs'), badge: null }]);
  const moreActive = s.moreOpen || (!MOBILE_TABS.includes(s.screen));
  const unread = s.notifs.filter(n => !n.read).length;

  // portfolio
  const equity = POSITIONS.reduce((a, q) => a + q.quantity * QUOTES[q.ticker].last, 0) + CASH;
  const positions = POSITIONS.map(q => { const last = QUOTES[q.ticker].last; const value = q.quantity * last; const pnl = (last - q.avg) * q.quantity; const pnlPct = (last / q.avg - 1) * 100; const weight = value / equity * 100;
    return { ...q, last, avgF: fmtUsd(q.avg, { dec: 2 }), lastF: fmtUsd(last, { dec: 2 }), valueF: fmtUsd(value), weightF: fmtPct(weight), pnlF: fmtUsd(pnl, { signed: true }), pnlPctF: fmtPct(pnlPct, { signed: true }), stopF: fmtUsd(q.stop, { dec: 2 }), color: tone(pnl), overCap: weight > 10, qty: String(q.quantity), meta: `${q.quantity} @ ${fmtUsd(q.avg, { dec: 2 })} · şimdi ${fmtUsd(last, { dec: 2 })}`,
      analyze: () => { go('ask'); comp.runAnalysis(q.ticker); }, chart: () => { set({ screen: 'charts', chartTicker: q.ticker, chartInput: q.ticker }); } }; }).sort((a, b) => b.last * b.quantity - a.last * a.quantity);
  const sinceStart = equity - START_EQUITY;
  const sectors = Object.values(positions.reduce((m, q) => { (m[q.sector] ||= { label: q.sector, w: 0 }).w += q.last * q.quantity / equity * 100; return m; }, {})).sort((a, b) => b.w - a.w);
  const maxSector = sectors[0]?.w || 1;
  const sectorRows = sectors.map(x => ({ label: x.label, wF: fmtPct(x.w), bar: `${(x.w / maxSector * 100).toFixed(0)}%`, over: x.w > 30 }));
  const series = equitySeries(s.period); const W = 1000, H = 240;
  const eqPath = toPath(series, 'eq', W, H); const spyPath = toPath(series, 'spy', W, H);
  const periods = ['1A', '3A', '6A'].map(id => ({ id, active: s.period === id, pick: () => set({ period: id }) }));
  const periodRet = (series[series.length - 1].eq / series[0].eq - 1) * 100; const spyRet = (series[series.length - 1].spy / series[0].spy - 1) * 100;
  const reasons = Object.entries(FLOW.by_reason).sort((a, b) => b[1] - a[1]); const topReason = reasons[0][1];
  const flowReasons = reasons.map(([k, n]) => ({ label: rejectionReasonTr(k), n: String(n), bar: `${(n / topReason * 100).toFixed(0)}%` }));
  const openPnl = positions.reduce((a, q) => a + (q.last - q.avg) * q.quantity, 0);
  const realizedShare = Math.max(2, Math.min(98, REALIZED.stats.net_pnl / (REALIZED.stats.net_pnl + openPnl) * 100));
  const exits = REALIZED.by_exit.map(b => ({ label: EXIT_TR[b.exit_class], n: String(b.trades), pnlF: fmtUsd(b.net_pnl, { signed: true }), color: tone(b.net_pnl) }));
  const gates = EVAL.gates.map(g => ({ ...g, mark: g.passed ? '✓' : '✗', color: g.passed ? '#201e1d' : DOWN }));
  const inertNote = FLOW.verdict === 'inert' ? `${FLOW.inert_run_days} çalışma günüdür broker'a hiçbir emir ulaşmadı (eşik ${FLOW.inert_threshold_run_days}). Karne açık pozisyonların değerlemesini ölçüyor, yeni karar akışını değil.` : null;

  // decisions
  const decRows = DECISIONS.map(d => { const open = !!s.expanded[d.decision_id]; return { ...d, open, tagClass: ratingTagClass(d.rating), tagStyle: ratingTagStyle(d.rating),
    entryF: fmtUsd(d.entry_price, { dec: 2 }), stopF: fmtUsd(d.stop_loss, { dec: 2 }), ptF: fmtUsd(d.price_target), tpF: fmtUsd(d.take_profit), dateF: fmtDate(d.timestamp_utc), horizon: d.time_horizon || '—', sizeF: d.suggested_size_pct ? `%${d.suggested_size_pct}` : '—',
    agentCount: `${d.reasoning.length} ajan`, chevron: open ? '−' : '+', confF: `${Math.round(d.council.confidence * 100)}%`,
    votes: d.council.votes.map(([m, v]) => ({ model: m, vote: v, agrees: v === d.rating })),
    reasoning: d.reasoning.map(r => ({ ...r, badge: modelBadge(r.model), meta: `${fmtTok(r.tokens_in)}↓ / ${fmtTok(r.tokens_out)}↑ token · ${fmtLat(r.latency_ms)}` })),
    debate: Object.entries(d.debate).map(([role, text]) => ({ role: role.split('_').map(w => w[0].toUpperCase() + w.slice(1)).join(' '), text })),
    tokensF: fmtTok(d.reasoning.reduce((a, r) => a + r.tokens_in + r.tokens_out, 0)),
    toggle: () => set({ expanded: { ...s.expanded, [d.decision_id]: !open } }), openDetail: () => set({ screen: 'agents', detailDecisionId: d.decision_id, moreOpen: false }) }; });
  const detailDecision = decRows.find(d => d.decision_id === s.detailDecisionId) || null;
  const runlog = RUNLOG.map(r => ({ ...r, warn: r.status === 'warn' }));

  // orders
  const decOf = (id) => decRows.find(d => d.decision_id === id);
  const pending = s.pending.map(o => { const d = decOf(o.decision_id); return { ...o, isBuy: o.side === 'BUY', sideColor: o.side === 'BUY' ? UP : DOWN, stopF: fmtUsd(o.stop_loss, { dec: 2 }), tpF: fmtUsd(o.take_profit), notionalF: fmtUsd(o.notional), pctF: `%${o.pct}`, age: relAge(o.submitted_at_utc), expires: relAge(o.expires_utc).replace(' ', ' ') , rating: d?.rating, tagClass: d ? ratingTagClass(d.rating) : 'tag', tagStyle: d ? ratingTagStyle(d.rating) : '', decision: d, selected: s.selectedOrderId === o.order_id, select: () => set({ selectedOrderId: o.order_id, approveStep: null, rejectDialog: false }) }; });
  const selectedOrder = pending.find(o => o.selected) || null;
  const history = s.history.map(o => { const m = orderStatusMeta(o.broker_status); const riskRejected = !o.risk_approved || (o.rejection_reasons.length && !o.broker_order_id);
    return { ...o, sideColor: o.side === 'BUY' ? UP : DOWN, sideQty: `${o.side} ${o.quantity}`, statusLabel: riskRejected ? 'Risk reddi' : m.label, statusClass: riskRejected ? 'tag tag-accent' : m.cls, fill: `${o.filled_qty}/${o.quantity} lot`, avgF: o.avg_fill_price != null ? fmtUsd(o.avg_fill_price, { dec: 2 }) : '—', dateF: fmtDate(o.submitted_at_utc), reasons: o.rejection_reasons.map(rejectionReasonTr), hasReasons: o.rejection_reasons.length > 0, cancellable: !!o.broker_order_id && CANCELLABLE.has((o.broker_status || '').toLowerCase()), askCancel: () => set({ cancelDialogId: o.order_id }), brokerId: o.broker_order_id || '—' }; });
  const cancelTarget = history.find(o => o.order_id === s.cancelDialogId) || null;
  const approve = () => set({ approveStep: 'auth' });
  const confirmAuth = () => { set({ approveStep: 'verifying' }); comp._t = setTimeout(() => { const o = s.pending.find(x => x.order_id === s.selectedOrderId); if (!o) return;
    set({ approveStep: 'done', pending: comp.state.pending.filter(x => x.order_id !== o.order_id), history: [{ ...o, broker_order_id: 'alp_9d4b…', broker_status: 'accepted', submitted_at_utc: NOW.toISOString() }, ...comp.state.history] }); }, 1400); };
  const finishApprove = () => set({ selectedOrderId: null, approveStep: null });
  const reject = () => set({ rejectDialog: true });
  const confirmReject = () => { const o = s.pending.find(x => x.order_id === s.selectedOrderId); if (!o) return; set({ rejectDialog: false, selectedOrderId: null, pending: s.pending.filter(x => x.order_id !== o.order_id), history: [{ ...o, risk_approved: true, rejection_reasons: ['operator_reject'], submitted_at_utc: NOW.toISOString() }, ...s.history] }); toast(`${o.ticker} ${o.side} ${o.quantity} reddedildi — bugün yeniden önerilmez`); };
  const confirmCancel = () => { set({ cancelDialogId: null, history: s.history.map(o => o.order_id === s.cancelDialogId ? { ...o, broker_status: 'canceled' } : o) }); toast('İptal isteği broker\'a gönderildi'); };
  const closeDialogs = () => set({ rejectDialog: false, cancelDialogId: null, flattenDialog: false });

  // chat
  const chatMessages = s.chat.messages.map((m, i) => ({ ...m, isUser: m.role === 'user', decision: m.decisionId ? decOf(m.decisionId) : null, key: i }));
  const chips = ['AAPL', 'NVDA', 'MSFT', 'GOOGL', 'AMZN', 'AVGO'].map(t => ({ t, ask: () => comp.runAnalysis(t) }));
  const sendChat = () => { const t = s.chat.input.trim(); if (t && !s.chat.busy) comp.runAnalysis(t); };

  // charts
  const cb = bars(s.chartTicker, s.chartDays); const CH = 260, CW = 1000; const lo = Math.min(...cb.map(b => b.l)), hi = Math.max(...cb.map(b => b.h)); const span = hi - lo || 1; const slot = CW / cb.length;
  const yy = (v) => CH - ((v - lo) / span) * CH;
  const candles = cb.map((b, i) => { const up = b.c >= b.o; const top = yy(Math.max(b.o, b.c)); return { key: i, x: (i * slot + slot * 0.2).toFixed(1), w: (slot * 0.6).toFixed(1), y: top.toFixed(1), h: Math.max(1, yy(Math.min(b.o, b.c)) - top).toFixed(1), wx: (i * slot + slot / 2).toFixed(1), y1: yy(b.h).toFixed(1), y2: yy(b.l).toFixed(1), color: up ? UP : DOWN, fill: up ? 'transparent' : DOWN }; });
  const areaPath = cb.map((b, i) => `${i ? 'L' : 'M'}${(i * slot + slot / 2).toFixed(1)},${yy(b.c).toFixed(1)}`).join(' ') + ` L${CW},${CH} L0,${CH} Z`;
  const linePathD = cb.map((b, i) => `${i ? 'L' : 'M'}${(i * slot + slot / 2).toFixed(1)},${yy(b.c).toFixed(1)}`).join(' ');
  const chg = (cb[cb.length - 1].c / cb[0].c - 1) * 100;
  const chartDecision = decRows.find(d => d.ticker === s.chartTicker) || null;
  const chartPos = positions.find(q => q.ticker === s.chartTicker) || null;

  // watchlist
  const watchRows = s.watch.map(t => { const q = QUOTES[t] || { last: 100, chg: 0 }; const lr = LAST_RATING[t]; const pos = positions.find(x => x.ticker === t); const pend = s.pending.find(x => x.ticker === t);
    return { t, lastF: fmtUsd(q.last, { dec: 2 }), chgF: fmtPct(q.chg, { signed: true }), color: tone(q.chg), rating: lr ? lr[0] : '—', ratingDate: lr ? fmtDay(lr[1]) : 'karar yok', tagClass: lr ? ratingTagClass(lr[0]) : 'tag tag-neutral', tagStyle: lr ? ratingTagStyle(lr[0]) : '', status: pos ? `Pozisyon · ${pos.weightF}` : pend ? 'Onay bekliyor' : '—',
      remove: () => set({ watch: s.watch.filter(x => x !== t) }), chart: () => set({ screen: 'charts', chartTicker: t, chartInput: t, moreOpen: false }), analyze: () => { go('ask'); comp.runAnalysis(t); } }; });
  const addWatch = () => { const t = s.watchInput.trim().toUpperCase(); if (!t) return; if (s.watch.includes(t)) { toast(`${t} zaten listede`); return; } set({ watch: [...s.watch, t], watchInput: '' }); };

  // kill switch & settings
  const KILL = [['RUN', 'RUN', 'Normal işlem'], ['PAUSE_NEW', 'PAUSE', 'Yeni giriş yok, mevcut yönetilir'], ['FLATTEN_ALL', 'FLATTEN', 'Tüm pozisyonları piyasa fiyatından kapat']];
  const kill = KILL.map(([id, label, desc]) => ({ id, label, desc, active: s.killSwitch === id, danger: id === 'FLATTEN_ALL', pick: () => { if (id === s.killSwitch) return; if (id === 'FLATTEN_ALL') set({ flattenDialog: true }); else { set({ killSwitch: id }); toast(`Kill switch → ${label}`); } } }));
  const killDesc = KILL.find(k => k[0] === s.killSwitch)[2];
  const confirmFlatten = () => { set({ killSwitch: 'FLATTEN_ALL', flattenDialog: false }); toast('FLATTEN_ALL gönderildi — pozisyonlar piyasa fiyatından kapatılıyor'); };
  const SW = { autoExecute: ['Risk kontrolünden geçen emirleri otomatik gönder', 'Paper hesapta bracket (stop + TP) ile gönderilir. LIVE modda emirler onaya düşer.'], push: ['Push bildirimleri', 'Onay bekleyen ve gerçekleşen emirler için.'], weekly: ['Haftalık eval raporu', 'Pazartesi 09:00 — Sharpe, MaxDD, GO/NO-GO.'], biometric: ['Onayda cihaz kilidi zorunlu', 'Face ID / parmak izi / şifre olmadan emir onaylanamaz.'], refuseOutside: ['Piyasa kapalıyken gönderme', 'refuse_outside_hours — LIVE\'da açık olmalı.'], bracket: ['Bracket emirler', 'Her giriş broker tarafında stop + kâr al ile gider.'] };
  const sw = {}; for (const k of Object.keys(SW)) { const on = s.sw[k]; sw[k] = { on, label: SW[k][0], desc: SW[k][1], bg: on ? '#201e1d' : '#bab6b6', tx: on ? 18 : 2, toggle: () => set({ sw: { ...s.sw, [k]: !on } }) }; }
  const sizing = [['atr', 'ATR / Kelly'], ['llm_pct', 'LLM önerisi']].map(([id, label]) => ({ id, label, active: s.sizing === id, pick: () => set({ sizing: id }) }));

  // notifications
  const notifs = s.notifs.map(n => { const [typeLabel, typeClass] = NOTIF_TYPE[n.type] || ['Bildirim', 'tag tag-neutral']; return { ...n, typeLabel, typeClass, time: relAge(n.receivedAt), isUnread: !n.read, open: () => { set({ notifs: s.notifs.map(x => x.id === n.id ? { ...x, read: true } : x) }); go(n.route); } }; });
  const markAllRead = () => set({ notifs: s.notifs.map(n => ({ ...n, read: true })) });
  const clearNotifs = () => { set({ notifs: [] }); toast('Yerel bildirim geçmişi temizlendi'); };

  // lessons
  const lessons = LESSONS.map(l => { const open = s.lessonOpen === l.id; const picked = s.lessonAnswers[l.id]; const reveal = picked !== undefined; const solved = picked === l.quizAnswer;
    return { ...l, open, chevron: open ? '−' : '+', solved, reveal, verdict: solved ? 'Doğru' : 'Yanlış', explain: l.quizExplain, toggle: () => set({ lessonOpen: open ? null : l.id }),
      options: l.quizOptions.map((text, i) => ({ text, key: i, right: reveal && i === l.quizAnswer, wrong: reveal && picked === i && i !== l.quizAnswer, pick: () => { if (!reveal) set({ lessonAnswers: { ...s.lessonAnswers, [l.id]: i } }); } })) }; });
  const solvedCount = lessons.filter(l => l.solved).length;

  // login
  const canSignIn = s.email.includes('@') && s.password.length >= 4 && s.ack;
  const signIn = () => { if (canSignIn) set({ authed: true, screen: 'portfolio' }); };

  const bind = (key) => (e) => set({ [key]: e.target.value });
  const onEnter = (fn) => (e) => { if (e.key === 'Enter') fn(); };

  return {
    // shell
    isLive, isPaper: !isLive, modeLabel: isLive ? 'LIVE — GERÇEK PARA' : 'PAPER', nav, tabs, moreItems, moreActive, toggleMore: () => set({ moreOpen: !s.moreOpen }), pendingCount: String(pendingCount), hasPending: pendingCount > 0,
    unread: String(unread), hasUnread: unread > 0, goNotifs: () => go('notifs'), goPortfolio: () => go('portfolio'), goOrders: () => go('orders'), goAgents: () => go('agents'), goSettings: () => go('settings'), goRisk: () => go('risk'), goAsk: () => go('ask'), goCharts: () => go('charts'), goWatch: () => go('watch'), goLearn: () => go('learn'),
    signOut: () => set({ authed: false, screen: 'login' }), authed: s.authed, isLogin: !s.authed,
    isPortfolio: s.authed && s.screen === 'portfolio', isAsk: s.authed && s.screen === 'ask', isOrders: s.authed && s.screen === 'orders', isAgents: s.authed && s.screen === 'agents' && !s.detailDecisionId, isDecisionDetail: s.authed && s.screen === 'agents' && !!s.detailDecisionId,
    isCharts: s.authed && s.screen === 'charts', isWatch: s.authed && s.screen === 'watch', isRisk: s.authed && s.screen === 'risk', isLearn: s.authed && s.screen === 'learn', isSettings: s.authed && s.screen === 'settings', isNotifs: s.authed && s.screen === 'notifs', isMore: s.moreOpen,
    isRail: (p.nav || 'rail') === 'rail', isTop: (p.nav || 'rail') === 'top', toast: s.toast, hasToast: !!s.toast, killState: s.killSwitch, killLabel: KILL.find(k => k[0] === s.killSwitch)[1], killNotRun: s.killSwitch !== 'RUN', lastRun: '8 Eyl 01:30', nextRun: 'Bugün 01:30',
    // portfolio
    equityF: fmtUsd(equity), sinceF: `${fmtUsd(sinceStart, { signed: true })} (${fmtPct(sinceStart / START_EQUITY * 100, { signed: true })})`, sinceColor: tone(sinceStart), dailyF: `${fmtUsd(DAILY_PNL, { signed: true })} (${fmtPct(DAILY_PNL / equity * 100, { signed: true, dec: 2 })})`, dailyColor: tone(DAILY_PNL), cashF: fmtUsd(CASH), cashPct: fmtPct(CASH / equity * 100),
    verdict: EVAL.verdict, verdictColor: EVAL.verdict === 'GO' ? '#201e1d' : DOWN, verdictNoGo: EVAL.verdict !== 'GO', evalDays: `${EVAL.days} / ${EVAL.days_required} işlem günü`, gates, inertQualifier: FLOW.verdict === 'inert' ? `donmuş kitap · ${FLOW.inert_run_days}g` : null, inertNote, isInert: FLOW.verdict === 'inert',
    sharpeF: num(EVAL.sharpe), sortinoF: num(EVAL.sortino), maxDdF: fmtPct(EVAL.max_dd_pct), calmarF: num(EVAL.calmar), retF: fmtPct(EVAL.total_return_pct, { signed: true }), spyF: fmtPct(EVAL.spy_return_pct, { signed: true }), alphaF: fmtPct(EVAL.total_return_pct - EVAL.spy_return_pct, { signed: true }), evalReason: EVAL.reasons[0],
    periods, eqPath: eqPath.d, spyPath: spyPath.d, eqLastY: eqPath.lastY, spyLastY: spyPath.lastY, periodRetF: fmtPct(periodRet, { signed: true }), spyRetF: `SPY ${fmtPct(spyRet, { signed: true })}`, periodRetColor: tone(periodRet), chartW: W, chartH: H, periodLabel: s.period === '1A' ? 'son 1 ay' : s.period === '3A' ? 'son 3 ay' : 'son 6 ay',
    flowVerdict: FLOW.verdict === 'inert' ? 'Donmuş' : 'Aktif', flowColor: FLOW.verdict === 'inert' ? DOWN : UP, flowRunDays: `${FLOW.run_days} çalışma günü`, flowRatio: `${FLOW.submitted} / ${FLOW.orders}`, flowRate: fmtPct(FLOW.submitted / FLOW.orders * 100), flowLast: relAge(FLOW.last_submitted_at_utc), flowWindow: `son ${FLOW.window_days} gün`, flowReasons,
    realizedF: fmtUsd(REALIZED.stats.net_pnl, { signed: true }), realizedColor: tone(REALIZED.stats.net_pnl), realizedMeta: `${REALIZED.stats.trades} kapanan işlem · ${REALIZED.stats.wins}K / ${REALIZED.stats.losses}Z · eval penceresi (${REALIZED.excluded_pre_eval} eski işlem hariç)`, winRateF: fmtPct(REALIZED.stats.win_rate), expectancyF: fmtUsd(REALIZED.stats.expectancy, { signed: true }), pfF: REALIZED.stats.profit_factor.toFixed(2), reconciled: `reconcile ${relAge(REALIZED.reconciled_at_utc)} önce`, realizedShare: `${realizedShare.toFixed(0)}%`, openShare: `${(100 - realizedShare).toFixed(0)}%`, openPnlF: fmtUsd(openPnl, { signed: true }), exits,
    stratN: `n=${REALIZED.strategy.trades}`, stratNetF: fmtUsd(REALIZED.strategy.net_pnl, { signed: true }), stratWinF: fmtPct(REALIZED.strategy.win_rate), stratAvgF: fmtUsd(REALIZED.strategy.avg_pnl, { signed: true }), stratColor: tone(REALIZED.strategy.net_pnl),
    effN: CONC.effective_n.toFixed(1), topW: fmtPct(CONC.top_weight_pct), top3W: fmtPct(CONC.top3_weight_pct), hhi: CONC.hhi.toFixed(3), gross: fmtPct(CONC.gross_exposure_pct), concFlags: CONC.flags.join(' · '), concFlagCount: String(CONC.flags.length), sectorRows, positions, positionCount: `${positions.length} açık`, overCapCount: String(positions.filter(q => q.overCap).length),
    // agents
    decRows, detailDecision, closeDetail: () => set({ detailDecisionId: null }), runlog, runCost: RUN_COST, decisionCount: String(DECISIONS.length),
    // orders
    pending, selectedOrder, hasSelected: !!selectedOrder, history, ordersTab: s.ordersTab, isPendingTab: s.ordersTab === 'pending', isHistoryTab: s.ordersTab === 'history', showPending: () => set({ ordersTab: 'pending' }), showHistory: () => set({ ordersTab: 'history' }), pendingEmpty: pending.length === 0, historyCount: String(history.length),
    approve, confirmAuth, finishApprove, reject, confirmReject, closeDialogs, isAuth: s.approveStep === 'auth', isVerifying: s.approveStep === 'verifying', isDone: s.approveStep === 'done', inApprove: !!s.approveStep, showRejectDialog: s.rejectDialog, cancelTarget, showCancelDialog: !!cancelTarget, confirmCancel, backToOrders: () => set({ selectedOrderId: null, approveStep: null, rejectDialog: false }),
    approveLabel: selectedOrder ? `${selectedOrder.side} ${selectedOrder.quantity} ${selectedOrder.ticker} onayla` : '',
    // chat
    chatMessages, chatEmpty: chatMessages.length === 0 && !s.chat.busy, chatInput: s.chat.input, setChatInput: (e) => set({ chat: { ...s.chat, input: e.target.value } }), chatKey: onEnter(sendChat), sendChat, chatBusy: s.chat.busy, chatStatus: s.chat.status, chips, streamText: s.stream ? s.stream.text : '', isStreaming: !!s.stream, streamTicker: s.stream ? s.stream.ticker : '',
    // charts
    chartTicker: s.chartTicker, chartInput: s.chartInput, setChartInput: bind('chartInput'), showChart: () => { const t = s.chartInput.trim().toUpperCase(); if (t) set({ chartTicker: t, chartInput: t }); }, chartKey: onEnter(() => { const t = s.chartInput.trim().toUpperCase(); if (t) set({ chartTicker: t, chartInput: t }); }),
    chartModes: ['area', 'candle'].map(m => ({ id: m, label: m === 'area' ? 'Alan' : 'Mum', active: s.chartMode === m, pick: () => set({ chartMode: m }) })), isCandle: s.chartMode === 'candle', isArea: s.chartMode === 'area',
    chartRanges: [[30, '1A'], [90, '3A'], [180, '6A']].map(([d, l]) => ({ id: d, label: l, active: s.chartDays === d, pick: () => set({ chartDays: d }) })), candles, areaPath, linePathD, chartLastF: fmtUsd(cb[cb.length - 1].c, { dec: 2 }), chartChgF: fmtPct(chg, { signed: true, dec: 2 }), chartChgColor: tone(chg), chartLoF: fmtUsd(lo, { dec: 2 }), chartHiF: fmtUsd(hi, { dec: 2 }), chartBarsF: `${cb.length} gün`, chartDecision, chartPos, hasChartDecision: !!chartDecision, hasChartPos: !!chartPos, analyzeChartTicker: () => { go('ask'); comp.runAnalysis(s.chartTicker); }, candleW: CW, candleH: CH, lineColor: tone(chg),
    // watch
    watchRows, watchInput: s.watchInput, setWatchInput: bind('watchInput'), addWatch, watchKey: onEnter(addWatch), watchCount: `${s.watch.length} sembol`, watchEmpty: s.watch.length === 0,
    // risk
    kill, killDesc, showFlattenDialog: s.flattenDialog, confirmFlatten, alerts: ALERTS, openAlerts: String(ALERTS.filter(a => a.open).length), breakers: BREAKERS.map(b => ({ ...b, bar: `${Math.min(100, b.pct)}%`, color: b.ok ? '#201e1d' : DOWN })), limits: LIMITS.map(l => ({ ...l, bar: `${Math.min(100, l.pct)}%`, color: l.ok ? '#201e1d' : DOWN })),
    // settings
    sw, sizing, maxPos: s.maxPos, setMaxPos: bind('maxPos'), account: isLive ? 'Alpaca LIVE · api.alpaca.markets' : 'Alpaca paper · PA348DFG9628', endpoint: isLive ? 'https://api.alpaca.markets/v2' : 'https://paper-api.alpaca.markets/v2', testPush: () => toast('Test bildirimi 1 cihaza gönderildi'),
    // notifs
    notifs, notifsEmpty: notifs.length === 0, markAllRead, clearNotifs, notifCount: String(notifs.length),
    // learn
    lessons, solvedCount: `${solvedCount}/${LESSONS.length}`, progress: `${(solvedCount / LESSONS.length * 100).toFixed(0)}%`,
    // login
    email: s.email, password: s.password, setEmail: bind('email'), setPassword: bind('password'), ack: s.ack, toggleAck: () => set({ ack: !s.ack }), ackBg: s.ack ? '#201e1d' : 'transparent', canSignIn, signIn, loginKey: onEnter(signIn), biometricSignIn: () => set({ authed: true, screen: 'portfolio' }),
  };
}

// Fake analysis job: queued → running → streamed PM text (the real pipeline takes 5-10 min).
export function runAnalysis(comp, raw) {
  const t = raw.trim().toUpperCase(); if (!t || comp.state.chat.busy) return;
  const msgs = [...comp.state.chat.messages, { role: 'user', text: t }];
  comp.setState({ screen: 'ask', moreOpen: false, chat: { messages: msgs, input: '', busy: true, status: `${t}: sıraya alındı…` } });
  const d = DECISIONS.find(x => x.ticker === t);
  const full = d ? d.final_decision_text : `${t} için önceki karar yok. 7 ajanlı pipeline gerçek koşuda ~5-10 dk sürer; bu önizlemede sonuç üretilmedi. Analiz kuyruğa alındı — bittiğinde bildirim gelir.`;
  comp._t1 = setTimeout(() => comp.setState({ chat: { ...comp.state.chat, status: `${t}: ajanlar tartışıyor… (~5-10 dk)` } }), 800);
  comp._t2 = setTimeout(() => { comp.setState({ stream: { ticker: t, text: '' } });
    let i = 0; comp._iv = setInterval(() => { i += 3; if (i >= full.length) { clearInterval(comp._iv);
      comp.setState({ stream: null, chat: { ...comp.state.chat, busy: false, status: '', messages: [...comp.state.chat.messages, { role: 'assistant', text: full, decisionId: d ? d.decision_id : null }] } }); }
      else comp.setState({ stream: { ticker: t, text: full.slice(0, i) } }); }, 22); }, 2000);
}
export function clearTimers(comp) { clearTimeout(comp._t); clearTimeout(comp._t1); clearTimeout(comp._t2); clearTimeout(comp._toastT); clearInterval(comp._iv); }
