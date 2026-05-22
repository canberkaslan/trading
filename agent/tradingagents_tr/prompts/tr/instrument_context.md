# BIST Ticker Context Prompt Template (TR)

Used as `instrument_context` for BIST tickers. Cached via Anthropic prompt
caching (5-min TTL) — see ADR-006.

```
Hisse: {ticker}.IS (BIST 100 endeksinde {is_in_xu100})
Şirket: {company_name}
Sektör: {sector_tr}  ({sector_en})
Para birimi: TRY
Bağlı olduğu endeks: {indices}
Lot büyüklüğü: {lot_size}
Son işlem hacmi (ADV USD): {adv_usd}

Önemli regülasyon notları:
- BIST emir saatleri: 10:00-18:00 Europe/Istanbul
- Yarım gün uygulamaları dini bayram öncesi Cuma günlerinde geçerli
- Stopaj oranı: %0 (TR mukim gerçek kişi, mevcut rejim)
- SPK genel tebliğ III-55.1 kapsamında bu çıktı yatırım danışmanlığı değildir
```
