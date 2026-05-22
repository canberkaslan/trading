# ADR-007: Regulatory stance — personal use, defer commercial

**Status:** Accepted (Turkey/SPK section superseded by [ADR-008](008-scope-us-only.md) — US/SEC analysis remains live)
**Date:** 2026-05-22

## Context

Building an AI-powered autonomous trading system carries jurisdictional regulatory risk in both Turkey (SPK) and the US (SEC/FINRA). Crossing certain lines triggers licensing requirements with significant capital and compliance burdens (TR PYŞ min ₺3M; US SEC RIA registration + Form ADV + ongoing).

## Decision

**Phase 0–7 are personal-use only.** Single user (the author) trading their own capital through their own licensed broker accounts. No third-party signal distribution, no third-party trade execution, no AUM management.

Commercial paths (Phase 8+) are explicitly deferred until:
- Paper trade Sharpe > 1.0 sustained
- Legal review with SPK + SEC-savvy counsel completed
- License path chosen (or partnership with licensed broker-dealer)

## Regulatory lines we will not cross (in current phase)

### Turkey (SPK — Sermaye Piyasası Kurulu)

Personal algorithmic trading of one's own capital through a licensed `aracı kurum` is **legal**. We cross into regulated activity (SPKn m.37-39) the moment we:

| Activity | License required | Min capital |
|---|---|---|
| Manage third-party capital | Portföy yöneticiliği (PYŞ) per III-55.1 | ~₺3M |
| Give personalized investment advice | Yatırım danışmanlığı per III-55.1 | varies |
| Operate robo-advisor with discretionary execution | PYŞ + tech approval | ~₺3M |

**Grey zone we may eventually enter:** "Genel yatırım tavsiyesi" (general, non-personalized commentary) is permitted without license. The moment a recommendation is personalized to user risk profile, or auto-executed, license is required.

**Tax (current regime 2026):**
- BIST stocks held by TR residents: stopaj = 0% on equity gains
- Foreign stocks (Alpaca/US): taxable as değer artış kazancı, declared via gelir vergisi beyannamesi (15–40%)
- BSMV: applies to broker commissions, not gains

### United States (SEC / FINRA)

Personal algo trading of own capital is legal, no license required.

**PDT rule change (effective June 4, 2026):** SR-FINRA-2025-017 eliminates the 4-trades-in-5-days designation and $25K minimum. Replaced by intraday margin monitoring under amended Rule 4210. For our agent: PDT constraint disappears mid-2026.

**Wash sales (IRC §1091):** AI agents cycling in/out of the same ticker can trip 30-day wash sale rule constantly. Alpaca rejects intra-account wash patterns at the API (HTTP 403). Cross-broker tracking is our responsibility — we maintain wash sale ledger in Aurora for tax reporting.

**RIA registration triggers (Investment Advisers Act §202(a)(11)):**
- < $100M AUM → state RIA
- ≥ $110M AUM → SEC RIA
- **Internet Adviser Exemption** (amended March 2025) requires interactive website + advice generated *exclusively* by algorithm with no human personnel intervention + full Form ADV — potential future path if we go SaaS

## Data licensing (recap from ADR-004)

In personal-use phase, all chosen providers (Polygon Individuals, Finnhub Personal, SEC EDGAR, Reddit, X Basic, Matriks IQ) are properly licensed for our use case.

**Phase 8+ (multi-user) requires upgrades:**
- Polygon Individuals → Polygon Business
- Finnhub Personal → Finnhub Commercial
- Matriks: **cannot redistribute raw BIST data without BIST written permission** — must derive signals server-side
- Reddit: commercial ML training requires Enterprise tier (post-2023 ToS)

## KVKK / GDPR (relevant once mobile app has users)

KVKK Article 9 (Jun 2024 amendment + Jan 2025 KVKK Guide): cross-border transfer now follows GDPR-like model:
- Adequacy decision (TR has no US adequacy), OR
- Appropriate safeguards (Standard Contractual Clauses filed with KVKK), OR
- Explicit derogations (informed user consent for each transfer purpose)

**Our compliance posture (Phase 5+):**
- `aydınlatma metni` + explicit consent screen on signup
- Financial data is sensitive — explicit consent required
- Right to deletion: `DELETE /users/{id}` purges trade history + sentiment + fingerprints
- Retain tax-required records 5 years per VUK m.253 — disclose this retention
- EU users: GDPR applies in addition; designate EU representative if no EU establishment (we have eu-west-1 infra, no establishment — may need one)

## Disclaimer language (samples)

Mobile app + any commentary product must display:

### English
> Past performance does not guarantee future results. This is not investment advice. The signals provided are for informational purposes only. You should consult a licensed financial advisor before making investment decisions. The author may hold positions in securities discussed. Trading involves risk of loss.

### Türkçe
> Burada yer alan yatırım bilgi, yorum ve tavsiyeleri yatırım danışmanlığı kapsamında değildir. Yatırım danışmanlığı hizmeti, yetkili kuruluşlar tarafından kişilerin risk ve getiri tercihleri dikkate alınarak verilmektedir. Burada yer alan yorum ve tavsiyeler ise genel niteliktedir. Bu tavsiyeler mali durumunuz ile risk ve getiri tercihlerinize uygun olmayabilir. Bu nedenle, sadece burada yer alan bilgilere dayanılarak yatırım kararı verilmesi beklentilerinize uygun sonuçlar doğurmayabilir.

## Monetization paths (decision tree for future)

```
Q: Will we monetize?
├─ No, personal use only → no further action (current phase)
├─ Yes, educational/general commentary
│   └─ TR: legal as `genel yatırım tavsiyesi` with disclaimers
│      US: likely publisher exemption (Lowe v. SEC 1985) if impersonal + regular + bona-fide
├─ Yes, signals service (specific tickers, same to all subscribers)
│   ├─ Non-personalized → still arguably publisher exemption
│   └─ Personalized to user profile → REGULATED
│       ├─ TR: yatırım danışmanlığı SPK lisansı
│       └─ US: RIA registration (state or SEC by AUM)
└─ Yes, auto-trade for users
    ├─ TR: PYŞ + ₺3M capital + ongoing oversight
    └─ US: RIA + broker-dealer partnership (Alpaca Broker API or similar)
```

## DO / DON'T

### DO (current phase)
- Trade only own capital through own licensed brokerage accounts
- Display risk disclaimers on all app screens that show trade decisions
- Implement remote kill switch before first live order
- Maintain wash-sale ledger across brokers for tax
- File KVKK aydınlatma metni once mobile app has any TR user (even author)
- Frame any incidental commentary as "general commentary / publisher" not "advice"

### DON'T (until properly licensed)
- Auto-execute trades on behalf of any user other than the author
- Personalize recommendations to user risk profile
- Redistribute raw Polygon/Finnhub/Matriks data to other users
- Charge for signals without legal review of publisher exemption applicability
- Transfer TR user PII to US servers without SCCs or explicit consent
- Cherry-pick performance results in any marketing material
- Claim "guaranteed returns" anywhere — instant App Store rejection + SEC/SPK risk

## Action items (Phase 0–4)

- [x] Document regulatory posture (this ADR)
- [ ] Add disclaimer to mobile app splash + every trade screen footer
- [ ] Implement wash-sale tracking in `agent/tradingagents_us/risk/wash_sale.py`
- [ ] Implement KVKK aydınlatma metni in mobile signup flow (Phase 5)

## Action items (future, before Phase 8)

- [ ] Legal review with SPK + SEC-experienced counsel
- [ ] Decide commercial path (educational / publisher / RIA / PYŞ)
- [ ] Upgrade Polygon to Business if going multi-user
- [ ] Upgrade Finnhub to Commercial if going multi-user
- [ ] BIST data redistribution agreement (almost certainly impossible — plan for signal-only)

## Sources

- Research `docs/RESEARCH.md` §7
- SR-FINRA-2025-017 https://www.finra.org/sites/default/files/2025-12/SR-FINRA-2025-017.pdf
- SPK Sermaye Piyasası Kanunu m.37-39
- SPK III-55.1 https://www.prmfinans.com/iii-55-1-portfoy-yonetimi
- KVKK Yurt Dışı Aktarım Rehberi (Jan 2025) https://www.kvkk.gov.tr/Icerik/8142/
- Lowe v. SEC, 472 U.S. 181 (1985)
- Polygon ToS https://polygon.io/legal/individuals-terms-of-service
- Alpaca PDT https://alpaca.markets/support/what-is-the-pattern-day-trading-pdt-rule
- Alpaca wash sale https://alpaca.markets/support/wash-sale
- IRC §1091
