# Kullanım Rehberi (USAGE)

Bu sistemin **şu an** sana ne sağladığı, **ne komut çalıştıracağın**, ve **Phase 4-5 sonrası neyin gelmesini bekleyeceğin**.

> **Risk uyarısı:** Bu yazılım yatırım tavsiyesi değildir. Üretilen kararlar "paper" hesapta test etmek içindir. Gerçek parayla işleme geçmeden önce en az 90 gün paper'da Sharpe > 1.0 sustained gör (ADR-005, ROADMAP Phase 7 gate).

---

## 0. Bir kerelik kurulum (5 dk)

```bash
git clone git@github.com:canberkaslan/trading.git
cd trading/agent

# Python venv + bağımlılıklar
uv venv && uv pip install -e ".[dev]" httpx boto3 pandas pyarrow vectorbt \
  langchain-anthropic langchain-openai langgraph lxml beautifulsoup4

# Key'ler (zaten sende varsa kopyala, yoksa servislere kayıt ol)
cp .env.example .env
# .env içine doldur:
#   ANTHROPIC_API_KEY=sk-ant-...
#   POLYGON_API_KEY=...
#   ALPACA_API_KEY=PK...
#   ALPACA_API_SECRET=...
#   FRED_API_KEY=...
#   SEC_EDGAR_USER_AGENT="Adın Soyadın email@example.com"

# Vendored upstream (TradingAgents v0.2.5)
# Klon zaten subtree olarak çekti, ekstra iş yok.
```

---

## 1. Bugün **gerçekten** yapabileceğin şeyler

### A) Tek bir hisse için karar üret (~$0.50-1.50, 5-10 dk)

```bash
cd agent
set -a && source .env && set +a

PYTHONPATH=.:vendor/tradingagents \
  ./.venv/bin/python -m tradingagents_us.graph.pipeline \
    --ticker NVDA --date 2026-06-01
```

7 LLM ajanı debate eder, sonunda şöyle bir özet basar:

```
=== AGENT DECISION ===
  Rating:      Overweight
  Entry:       $...
  Stop:        $...
  Price Tgt:   $...
  Horizon:     12-18 months
```

Tam transkript `~/.tradingagents/logs/<TICKER>/TradingAgentsStrategy_logs/full_states_log_<DATE>.json` dosyasına kaydedilir.

### B) End-to-end: karar + risk sizing + Alpaca paper submit

**Bu tek komut:** karar üretir, sizer çalıştırır, Alpaca paper'a (opsiyonel) işlemi gönderir.

```bash
# Önce dry-run — gerçek emir gitmez, sadece "ne olur" göster
./.venv/bin/python -m scripts.trade --ticker AAPL --use-cached

# Cached olmayan, taze LLM kararı için
./.venv/bin/python -m scripts.trade --ticker NVDA

# Hazır mısın? --submit ile Alpaca paper'a gönder
./.venv/bin/python -m scripts.trade --ticker AAPL --use-cached --submit
```

`--use-cached`: daha önce üretilmiş aynı ticker için kararı diskten okur, **LLM cost yok**. AAPL için elimizde mevcut.

Çıktı örneği (dry run):

```
=== AGENT DECISION ===     Rating: Overweight  PT: $310
=== ALPACA ACCOUNT ===     Equity: $100,000  PDT: False
=== TRADE ORDER ===        BUY 29 shares ($7,859 = 7.86% of equity) APPROVED
=== EXECUTION RESULT ===   Dry run: True (re-run with --submit)
```

Parametreler:
- `--method atr` (default) — Kelly/ATR risk-based sizing, %0.5 of equity per trade
- `--method llm_pct` — LLM'in suggested_size_pct'sini olduğu gibi kullan
- `--max-position-pct 0.10` — bir ticker'ın equity'nin %10'undan fazlasını alamaz
- `--refuse-outside-hours` — market kapalıysa submit etme (paper'da gerek yok; live'da aç)

### C) Backtest çalıştır

```bash
PYTHONPATH=. ./.venv/bin/python -m pytest tests/test_backtest_engine.py -s
```

Şu an demo strategy: AAPL 2024 SMA(10/30) crossover → Sharpe 1.33, Sortino 2.08, MaxDD %14, +%17.7 return (buy&hold %34.9'u yenmiyor ama harness validate edildi).

Gerçek **LLM-driven backtest** Phase 4 sonrasında geliyor (50 ticker × 5 yıl × decision üretmek $2-3k LLM cost; cached state biriktikçe ucuza geleceğiz).

### D) Survivorship-safe historical universe

```bash
./.venv/bin/python -c "
from tradingagents_us.dataflows import members_as_of
from datetime import date
m = members_as_of(date(2007,12,31))
print(f'2007-12-31 S&P 500: {len(m)} ticker  LEH={\"LEH\" in m}  AAPL={\"AAPL\" in m}')
"
# -> 2007-12-31 S&P 500: 513 ticker  LEH=True  AAPL=True
```

Bu Lehman, Bear Stearns, WaMu gibi batıkları geri getirir — yfinance'in "bugün S&P 500" listesinden gelen survivorship bias'ı çözer.

---

## 2. Phase 4 sonrası (yakında, blocker yok)

| Şu an manuel | Phase 4 sonrası otomatik |
|---|---|
| Her gün `scripts/trade.py` el ile | **EventBridge cron 22:30 UTC** her gün US post-close otomatik |
| Tek bir ticker | **Tüm S&P 500 alt setinde** (SPY + top 20 başlangıç) |
| Dry-run görmek için terminal | **Aurora trade log** + Grafana dashboard'da equity curve |
| Manuel review | Push notification: "AAPL Overweight signal, click to approve" |

## 3. Phase 5 sonrası (mobile)

- iOS / Android app
- Push notification: trade kararı geldiğinde
- Biyometrik onay (FaceID) ile auto-execute toggle
- Portfolio dashboard, agent reasoning streaming

---

## 4. Sıkça sorulanlar

### Şu an gerçek para kaybetme riskim var mı?
**Hayır.** Tüm akış Alpaca **paper** account üstünde çalışıyor — $100k sanal para. Live'a geçmeden önce 90 gün paper'da Sharpe > 1.0 görmek gerekir (ROADMAP Phase 6 gate).

### Bir karar üretmek ne kadar?
- Tek ticker, full pipeline (Opus + Sonnet, 17 LLM call): **~$0.50-1.50**
- Phase 3b prompt caching aktif olunca: ~%70-85 input cost cut → **~$0.10-0.30**
- Phase 3c 3-tier routing (Haiku risk debators): ek %20-30 cut

### "Use cached" nedir?
Phase 2'de bir kere AAPL kararı ürettik (~$1). State diske kaydedildi. `--use-cached` ile diskteki state'ten okuyup risk + execution layer'ları tekrar tekrar test edebiliyoruz, sıfır LLM cost.

### Backtest'te survivorship bias nasıl çözülüyor?
`dataflows/sp500_history.py` Wikipedia'dan tüm S&P 500 historical değişimleri çeker (395 değişim, 1976-2026). `members_as_of(date)` o tarihteki tam liste verir — Lehman dahil.

### Reddit / Finnhub neden eksik?
- **Reddit**: 2024'te "Responsible Builder Policy" geldi, geliştirici hesabı onayı 1-7 gün sürer. Sentiment için alternatif: StockTwits (Phase 3+ planlı), Finnhub free tier news.
- **Finnhub**: Opsiyonel; news headlines + earnings calendar için. Free tier 60 req/dk yeterli ama key gerek.

### BIST (Türk borsası) niye yok?
ADR-008 ile kaldırıldı. Tek market'e odaklanmak için. Reversible (~3 hafta) eğer US sistem live'da kazançlı olursa.

---

## 5. Komut özet kartı

```bash
# Tek karar (taze LLM)
./.venv/bin/python -m tradingagents_us.graph.pipeline --ticker AAPL --date 2026-06-01

# Tek karar + risk sizing (cached, sıfır cost)
./.venv/bin/python -m scripts.trade --ticker AAPL --use-cached

# Aynı + Alpaca paper'a submit
./.venv/bin/python -m scripts.trade --ticker AAPL --use-cached --submit

# Tüm testler
PYTHONPATH=. ./.venv/bin/python -m pytest -q

# Backtest (mevcut harness)
PYTHONPATH=. ./.venv/bin/python -m pytest tests/test_backtest_engine.py -s

# AWS Secrets Manager'da key'ler (rootingo profile)
aws --profile rootingo --region eu-west-1 secretsmanager list-secrets \
  --filters Key=name,Values=ai-trader/dev

# Repo
gh repo view canberkaslan/trading
```

---

## 6. İlgili dokümanlar

- [ARCHITECTURE.md](ARCHITECTURE.md) — Sistem tasarımı
- [ROADMAP.md](ROADMAP.md) — Faz planı + exit gate'ler
- [adr/](adr/) — 8 mimari karar
- [RESEARCH.md](RESEARCH.md) — Landscape analizi (Bist tarafı archived)
