# ADR-004: Data providers

**Status:** Accepted
**Date:** 2026-05-22

## Context

Need market data, news, sentiment, and fundamentals for both US and BIST markets. Constraints:
- Personal-use licensing (Phase 0)
- Total data spend < $300/mo
- BIST has no Bloomberg-grade public API
- Survivorship-bias safe historical data

## Decision

### US market data

| Tier | Provider | Cost | Coverage | Notes |
|---|---|---|---|---|
| OHLCV (1m–1d) | **Polygon.io Stocks Starter** | $29/mo | All US equities | Real-time WS streams |
| Options + open interest | Polygon Options Advanced | +$170/mo | Optional (Phase 2+) | Defer |
| News + earnings | **Finnhub Personal** | $50/mo | Earnings calendar + transcripts + news | |
| SEC filings | **SEC EDGAR (free)** | $0 | 10-K, 10-Q, 8-K, Form 4 | User-Agent required, 10 req/s |
| Macro indicators | **FRED (free)** | $0 | Fed rates, CPI, unemployment | |
| Social sentiment | Reddit (PRAW, free with auth) + X Basic | $0 + $100/mo | r/wallstreetbets, cashtags | X free tier insufficient |

**US monthly:** ~$179

### BIST market data

| Tier | Provider | Cost | Notes |
|---|---|---|---|
| OHLCV BIST 100 | **Matriks IQ** | ~₺500/mo (~$15) | Subscription required; cleanest source |
| Public disclosures | **KAP** (kap.org.tr free scrape) | $0 | Wrap `pykap` PyPI + Playwright Lambda |
| Mali tablolar | KAP + İş Yatırım analyst reports (free) | $0 | Quarterly statements |
| Macro | **TCMB EVDS** (free) | $0 | Interest rate, FX reserves, inflation |
| TR Twitter | X Basic (shared with US tier) | included | TR finance accounts + cashtags |
| Yabancı yatırımcı işlemleri | Borsa İstanbul weekly reports (free) | $0 | "Smart money" proxy |

**BIST monthly:** ~$15

### Total data spend: ~$194/mo

## Initial weight allocation (Phase 1 baseline)

Weights are **dynamically re-balanced** by the agent based on regime/ticker characteristics. These are starting priors:

### US

| Data type | Initial weight | Reasoning |
|---|---|---|
| OHLCV + technicals | 25% | Required, but no edge alone — everyone has it |
| Options flow + OI | 15% | Smart money signal, gamma squeeze detection |
| SEC filings (10-K/Q, 8-K, Form 4) | 15% | Fundamental ground truth |
| Earnings calendar + transcripts | 10% | Event-driven critical |
| News sentiment | 10% | LLM strength |
| Reddit + Twitter sentiment | 10% | Retail momentum |
| Macro (FRED) | 10% | Regime detection (rates, CPI, employment) |
| Insider trading (Form 4) | 5% | CFO/CEO selling = bear signal |

### BIST

| Data type | Initial weight | Reasoning |
|---|---|---|
| OHLCV (BIST 100) | 30% | Higher weight — less alt data available |
| KAP disclosures | 25% | TR's 10-K equivalent; bedelsiz, temettü, ortaklık değişiklikleri |
| Mali tablolar | 15% | Quarterly disclosures |
| TCMB / Hazine duyuruları | 15% | Faiz kararı, döviz rezervi |
| Twitter (TR finans hesapları) | 10% | Ekonomistler, analistler |
| Yabancı yatırımcı işlemleri | 5% | Smart money proxy |

### Dynamic re-weighting (pseudocode)

```python
def reweight(market_regime, ticker_type, week_context):
    w = base_weights.copy()
    if market_regime == "high_vol":
        w["sentiment"] *= 1.5
        w["fundamentals"] *= 0.7
    if ticker_type == "small_cap":
        w["insider_trading"] *= 2.0
    if week_context.is_earnings_week:
        w["earnings_transcript"] *= 3.0
    if week_context.is_fed_meeting_week:
        w["macro"] *= 2.0
    return normalize(w)
```

Tuned via Optuna in Phase 3 against historical Sharpe.

## Alternatives considered

| Option | Why rejected |
|---|---|
| Alpha Vantage (TradingAgents default) | Premium tier rate-limited (75 req/min) — fine for batch, not for real-time. Used as secondary fallback |
| IEX Cloud | Shut down 2024 |
| Bloomberg Terminal | $24K/yr — over budget |
| Refinitiv Eikon | Same — over budget |
| OpenBB Platform as primary | Excellent normalization layer (we use it as a wrapper), but doesn't own data — wraps the providers above |
| Yahoo Finance only | Survivorship-biased universe (yfinance current S&P 500 is the classic trap); fine for prototyping, NOT for production backtest |
| StockTwits | Used in TradingAgents v0.2.5 default; TR equivalents (eksisozluk) more useful for our markets |

## Survivorship-bias defenses

- Polygon provides **historical ticker mappings** including delisted (survivor-safe)
- SEC EDGAR includes all filings, even from delisted entities
- KAP has full TR disclosure history
- We explicitly include delisted tickers in backtest universe (CRSP-style point-in-time)

## Licensing pitfalls (critical for any future commercial product)

| Provider | Personal use | Redistribute to app users |
|---|---|---|
| Polygon.io Individuals | OK | **NO** ("may not use Market Data to build an application intended for use by end users other than you") |
| Polygon.io Business | OK | Authorized Users of *your* org only |
| Finnhub Personal | OK | Commercial tier required |
| SEC EDGAR | OK | OK (public domain) |
| Reddit API (post-2023 ToS) | OK with auth | Commercial ML training requires Enterprise tier |
| X API | Paid only | Per tier ToS |
| Matriks / BIST | OK with sub | **NO without BIST written permission** |

**Implication for Phase 7+ (multi-user / SaaS):** Must upgrade Polygon to Business and Finnhub to Commercial. Cannot redistribute Matriks/BIST raw data to app users at all — must derive signals server-side.

## Consequences

- Phase 0–6 (personal use, paper-trade) is fully licensed at <$200/mo
- Phase 7+ (live with own capital) — same licensing
- Phase 8+ (multi-user) — requires Polygon Business + Finnhub Commercial + signal-only distribution for BIST data
- KVKK cross-border concern: data flows from EU/US providers to our eu-west-1 infra are fine; user PII flowing TR → US needs SCCs or explicit consent (see ADR-007)

## Sources

- Research output `docs/RESEARCH.md` §1
- Polygon Terms https://polygon.io/legal/individuals-terms-of-service
- Polygon Business Terms https://polygon.io/legal/businesses-terms-of-service
- Finnhub Pricing https://finnhub.io/pricing-startups-and-enterprise
- KAP https://www.kap.org.tr/en
- Matriks https://www.matriksdata.com
- TCMB EVDS https://evds2.tcmb.gov.tr
