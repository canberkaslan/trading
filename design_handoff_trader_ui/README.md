# Handoff: AI Investment Agent UI (Modernist)

Repo: `canberkaslan/trading` · hedef: `mobile/app/` (React Native + Expo) ve `agent/api/static/dashboard.html` (web paneli).

## Overview
Paper-trading yapan 7 ajanlı LLM trader için operatör arayüzü: portföy + eval karnesi, ajan kararları ve çalışma günlüğü, emir onayı (cihaz kilidi), kill switch, risk limitleri, izleme listesi, sohbet (analiz), dersler, bildirimler, giriş. Web ve mobilde aynı bilgi mimarisi.

## About the Design Files
Bu klasördeki `.dc.html` dosyaları **HTML ile yapılmış tasarım referanslarıdır** — görünüşü ve davranışı gösteren prototipler, doğrudan kopyalanacak üretim kodu değil. Görev: bu tasarımları hedef kod tabanının mevcut ortamında yeniden üretmek — mobil için `mobile/app/` (Expo Router, Zustand, TanStack Query, mevcut `src/utils/*` saf yardımcılar), web için mevcut FastAPI static dashboard (ya da tercih edilirse aynı RN kodunun `pnpm web` çıktısı). Mevcut hook'lar (`usePortfolio`, `useEval`, `usePendingOrders`, `useKillSwitch`, `useActionability`, `useTrades`…) ve `src/utils/{actionability,orders,decision,inbox,realized,concentration}.ts` etiket/ton kuralları **aynen** kullanılmalı; tasarım bu kuralları görselleştirir, değiştirmez.

## Fidelity
**High-fidelity.** Renkler, tipografi, aralıklar ve etkileşimler nihaidir. Aşağıdaki token'lar `mobile/app/src/theme/colors.ts` içine yeni bir tema olarak eklenmeli (mevcut koyu tema kaldırılmaz; light "modernist" tema olarak eklenir).

## Design Tokens (Modernist)
- Zemin `#f3f2f2` · yüzey `#eae9e9` · mürekkep `#201e1d` · aksan `#ec3013`
- Aksan rampası: 100 `#fff2ef` · 200 `#ffe0d9` · 300 `#ffc4b8` · 500 `#ff563c` · 600 `#dd2b0f` · 700 `#ae1800` (küçük kırmızı metin için) · 800 `#7c1405`
- Nötr rampa: 200 `#eae7e7` · 300 `#d7d3d3` · 500 `#9b9797` · 700 `#605d5d` (ikincil metin) · 800 `#444141` · 900 `#2d2b2b`
- Ayraç: mürekkep %40 alfa (`rgba(32,30,29,.4)`) — bölüm aralarında **2px**, satır aralarında 1px
- Köşe yarıçapı **0** her yerde · gölge yalnızca dialog/sheet: `0 12px 32px rgba(45,43,43,.22)`
- Font: **Archivo** 400/600/800 (Google Fonts). Başlık 800. `font-feature-settings: "tnum"` sayılarda.
- Tip ölçeği: hero rakam 64 (web) / 44 (mobil) · h2 28 / 24 · bölüm başlığı 16 / 15 · gövde 14 / 13 · yardımcı 12 / 11 · kicker 11 uppercase, harf aralığı .1em, renk aksan-700
- Aralık: 4 · 8 · 12 · 16 · 24 · 32. Web içerik dolgusu 28px 40px, maks. 1240px. Mobil ekran dolgusu 20px 16px.
- K/Z rengi (varsayılan "muhasebe"): kâr = mürekkep `#201e1d`, zarar = aksan `#ec3013`; her değer işaretli (+ / −, U+2212). Alternatif palet: kâr `oklch(0.52 0.14 152)`.
- Rating etiketi: Buy/Overweight → mürekkep dolgu (`#201e1d` üzerine `#f3f2f2`), Hold → nötr (`#f8f4f4` / `#444141`), Underweight/Sell → aksan-100 dolgu / aksan-800 metin. Uyarı → 1px aksan çerçeve, aksan metin.
- Dokunma hedefi min. 44px (mobil); web butonu 36px, birincil aksiyonlar 44px.

## Shell
**Web (sol ray, varsayılan):** 224px ray, sağında 2px ayraç; üstte marka (14px kırmızı kare + "TRADER" 800/17), altında PAPER/LIVE etiketi + eval kararı (NO-GO kırmızı, GO mürekkep) + "20 / 10 işlem günü"; nav listesi (aktif: 10px kırmızı kare işaret + 800 ağırlık), Emirler'de bekleyen sayısı etiketi; en altta kill switch durumu, son/sonraki koşu, "Çıkış yap". **Üst bar varyantı:** `.nav` — marka, linkler, PAPER etiketi, karar, zil. **LIVE modu:** en üstte tam genişlik kırmızı şerit "LIVE — GERÇEK PARA · Her emir cihaz kilidiyle onaya düşer".
**Mobil:** üstte durum şeridi (mod etiketi 11px 800 çerçeveli · "● bağlı · son koşu …" · zil + okunmamış sayısı; LIVE'da şerit kırmızı dolgu). Altta 5 sekme: Portföy · Sor · Emirler (rozet) · Ajanlar · Diğer (Grafik, İzleme, Risk, Öğren, Ayarlar, Bildirimler). Aktif sekme: 3px kırmızı üst çizgi + kırmızı etiket.

## Screens
1. **Portföy** — sol: kicker "PORTFÖY DEĞERİ · hesap", 64px `$109,061`, satır: başlangıçtan `+$9,061 (+9.1%)` · Bugün `−$815 (−0.75%)` · Nakit. Sağ (2px sol çizgili blok): "Eval kararı" + `NO-GO` 34px + `⚠ donmuş kitap · 4g` etiketi (yalnız `actionability.verdict==='inert'`), 4 gate satırı ✓/✗ (✗ kırmızı). Sermaye eğrisi: 220px SVG, portföy mürekkep 2.2px, SPY `#9b9797` 1.5px, 1A/3A/6A segment (`.seg`). Üç sütun (2px alt çizgili): **Emir akışı** (Durum/Broker'a giden/Son gönderim + sebep barları aksan-500 + inert notu), **Gerçekleşen** (net, kazanma/beklenti/kâr faktörü, gerçekleşen-açık bölünme barı, "Stratejinin çıkışları" n= bloğu, çıkış sınıfı listesi), **Risk & dağılım** (etkin isim, en yüksek ağırlık kırmızı, ilk 3, sektör barları, cap uyarısı). Pozisyon tablosu `.table`: Sembol+sektör, Adet, Ort., Son, Değer, Ağırlık (+`cap` etiketi >10%), K/Z, K/Z %, Stop, Grafik/Analiz. Mobilde aynı sıra dikey; pozisyon satırına dokunma → Sor (`/(tabs)/ask?ticker=`).
2. **Sor** — sohbet akışı: kullanıcı mesajı sağa yaslı yüzey kutusu (Archivo 800); ajan cevabı: "PORTFOLIO MANAGER · OPUS 4.8" kicker, karar şeridi (2px üst/alt çizgi: ticker + rating etiketi + Giriş/Stop/Hedef/Vade), PM metni, "Tam karar detayı →". Durum: yanıp sönen 10px kırmızı kare + "sıraya alındı… / ajanlar tartışıyor… (~5-10 dk)". Streaming imleci 8×14 kırmızı blok. Giriş satırı 2px üst çizgi: `.input` + birincil "Analiz et". Boş durum: açıklama + sembol chip'leri. Web sağ sütun: son 5 karar.
3. **Emirler** — segment "Onay bekleyen · N | Geçmiş · N". Bekleyen satırı: ticker 18/800, rating etiketi, sağda `BUY 18` (yön rengi), alt satır "MARKET · stop $312.40 · $6,158 · 17 sa önce"; seçili satır 4px kırmızı sol çizgi (web). Detay: 40px ticker, yön/lot, 2px çizgili istatistik ızgarası (Tutar, Portföy %, Stop, Kâr al, Giriş, Hedef, Vade, Süre), butonlar **Reddet** (ikincil) + **Doğrula ve onayla** (birincil, scan-face ikonu), PM gerekçesi, konsey oyları etiketleri, "Tüm ajan analizleri →". Geçmiş tablo: Sembol, Yön, Durum etiketi (`orderStatusMeta` — risk reddi aksan etiketi), Dolum, Ort., Tarih, Sebep (`rejectionReasonTr`) / broker id, İptal (yalnız `isCancellable`).
4. **Ajanlar** — karar satırı (toggle): ticker, rating, Giriş/Stop/Hedef, tarih·vade, "7 ajan +". Açılınca: konsey oyları (`tag-neutral`), her ajan 170px isim sütunu + model etiketi (`tag-outline`: Opus 4.8 / Sonnet 4.6) + özet + "6.1k↓ / 410↑ token · 5.2 sn". Sağ sütun **Çalışma günlüğü**: 11 aşama, saat + aşama + detay, uyarı aşamaları kırmızı kare (Risk katmanı, Executor), altında maliyet ve sonraki koşu. **Karar detayı** ekranı: 40px ticker + etiket, istatistik ızgarası, konsey satırı, Ajan analizleri (~token), Tartışma (bull/bear/risk_manager), PM çıktısı, uyarı metni.
5. **Grafik** — 40px ticker + son fiyat + değişim; sembol girişi; `.seg` Alan/Mum + 1A/3A/6A; 300px (web) / 220px (mobil) SVG mum grafiği: yükselen mum içi boş mürekkep çerçeve, düşen mum kırmızı dolu, fitil 1px; alan modu yüzey dolgu + çizgi. Altta son karar + pozisyon özeti + "analiz et".
6. **İzleme listesi** — sembol ekle satırı; tablo: Sembol, Son, Değişim, Son karar (etiket+tarih), Durum (Pozisyon · ağırlık / Onay bekliyor), Grafik / Analiz / × kaldır.
7. **Risk & uyarılar** — kill switch `.seg` RUN/PAUSE/FLATTEN (FLATTEN onay dialogu), Devre kesiciler (isim, şimdi/limit, bar — aşım kırmızı), Portföy limitleri (Tek isim 12.2%/10%, Sektör 33.2%/30% kırmızı), Uyarılar listesi (tarih, metin, açık/kapandı etiketi).
8. **Öğren** — ilerleme barı + n/8; 8 ders akordeon (10px kare işaret, çözülünce dolu), açılınca paragraflar, "AKILDA KALSIN" yüzey kutusu, quiz sorusu, 3 seçenek (doğru: mürekkep çerçeve + nötr-200 dolgu; yanlış: aksan çerçeve + aksan-100), açıklama. İçerik `src/content/lessons.json`.
9. **Ayarlar** — 220px etiket sütunu + içerik, bölümler 2px çizgili: Hesap & mod (etiket, hesap, endpoint, çıkış), Eval scorecard (karar + 6 metrik + gate'ler + inert notu), Emir gönderimi (3 toggle: 40×22 dikdörtgen, açık mürekkep / kapalı nötr-400, 16px kare düğme), Strateji (boyutlama `.seg` ATR/LLM, tek isim tavanı input, sabit değerler), Bildirimler (3 toggle + test + geçmiş), Sistem.
10. **Bildirimler** — "Tümünü okundu işaretle" / "Temizle"; satır: 10px kırmızı kare (okunmamış), tür etiketi (`typeLabelTr` — onay bekliyor çerçeveli, reddedildi/uyarı aksan, diğerleri nötr), başlık (okunmamışta 800), gövde, "17 sa · Aç →". Dokunma → `routeForType`.
11. **Giriş** — sol: marka + PAPER, h1 "Giriş yap", e-posta, şifre, onay kutusu (18px kare, işaretli dolu mürekkep) + yasal metin, **Giriş yap** (ack + geçerli e-posta olmadan disabled) + "Cihaz kilidi ile aç". Sağ (mobilde üst): kırmızı afiş alanı, Archivo 800 72px/34px "Gerçek para, / dört gate'in / ardında." + gate satırı. Tek kırmızı dolgu alanı budur.

## Interactions & Behavior
- **Onay akışı:** Doğrula ve onayla → sheet/dialog "Cihaz kilidi ile doğrula" (Vazgeç / Doğrula) → `expo-local-authentication` başarılıysa "Doğrulanıyor…" (spinner 18px, 3px çerçeve) → `POST /v1/orders/{id}/approve` → "Emir gönderildi · alp_… · accepted" → Tamam → listeden düşer, geçmişe "Broker'da" olarak girer. 422 → "Risk guard reddetti" uyarısı.
- **Reddet:** onay dialogu → `POST /v1/orders/{id}/reject` → toast "reddedildi — bugün yeniden önerilmez".
- **İptal (geçmiş):** yalnız `isCancellable`; onay dialogu → `POST /v1/orders/{id}/cancel`.
- **Kill switch:** RUN/PAUSE anında `POST /v1/orders/kill-switch`; FLATTEN_ALL onay dialogu ister.
- **Sor:** `POST /v1/analyze` → `queued`→`running`→`done` poll; `done` olunca karar şeridi + PM metni. Prototipte metin karakter akışıyla gösterilir (22ms / 3 karakter) — gerçekte `final_decision_text` tek seferde gelir; akış efekti isteğe bağlı.
- **Toggle'lar / segmentler:** yerel state; `refuse_outside_hours` ve bracket gerçek sistemde `ExecutionConfig` — uygulamadan salt okunur gösterilebilir.
- **Toast:** mürekkep dolgu, 12-13px, 2.6 sn, sol alt (web) / sekme çubuğu üstü (mobil).
- Geçişler yok (anlık); yalnız toggle düğmesi 150ms transform, imleç/durum karesi 0.8-1s step blink.

## State Management
`screen`, `authed`, `period`, `ordersTab`, `selectedOrderId`, `approveStep (null|auth|verifying|done)`, `expanded{decisionId}`, `detailDecisionId`, `chat{messages,input,busy,status}`, `chartTicker/mode/days`, `watch[]`, `killSwitch`, `sw{autoExecute,push,weekly,biometric,refuseOutside,bracket}`, `sizing`, `maxPos`, `notifs[]`, `lessonOpen`, `lessonAnswers{}`, `toast`. Veri kaynakları: `/v1/portfolio/snapshot`, `/v1/portfolio/history`, `/v1/portfolio/concentration`, `/v1/trades`, `/v1/diagnostics/actionability`, `/v1/eval`, `/v1/agents/decisions`, `/v1/orders`, `/v1/orders/pending`, `/v1/prices/{t}`, `/v1/orders/kill-switch`, `readyz`.

## Assets
İkonlar: Lucide (bell, check, x, chevron-right, plus, send, scan-face, menu). Marka: 14px kırmızı kare + "TRADER". Görsel yok.

## Files
- `WebConsole.dc.html` — web konsolu prototipi (Tweaks: screen, nav rail/top, mode, pnlPalette)
- `MobileApp.dc.html` — mobil prototip (Tweaks: screen, frame android/ios, mode, pnlPalette)
- `Canvas.dc.html` — tüm ekranlar tek tuvalde + tasarım gerekçesi
- `trader-core.js` — sahte veri + görünüm modeli (etiket/ton kuralları, formatlayıcılar; `types.ts` şekillerinde)
- `styles.css` — Modernist token'ları ve `.btn/.tag/.seg/.table/.dialog` sınıfları
- `android-frame.jsx`, `ios-frame.jsx` — yalnız önizleme çerçevesi; uygulamaya alınmaz
