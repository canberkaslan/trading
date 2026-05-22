# ADR-008: Scope reduced to US equities only

**Status:** Accepted
**Date:** 2026-05-22
**Supersedes (partially):** ADR-001 (BIST adaptation plan), ADR-004 (BIST data sources), ADR-007 (Turkey regulatory analysis)

## Context

Original Phase 0 scope (ADR-001/004/007) included parallel BIST (Borsa Istanbul) support — KAP scraper, TCMB EVDS, Matriks IQ execution, Turkish prompts, BIST 30 universe. Reasons given: cover Turkish market for local investor (the author), maximize personal applicability.

After Phase 1+2 implementation we have:
- A working US data pipeline (Polygon + SEC EDGAR + FRED)
- A working US LLM agent (TradingAgents v0.2.5 fork, first AAPL decision generated)
- BIST stubs that were never live (KAP/TCMB/Matriks placeholders)

## Decision

**Remove BIST entirely. US equities only.**

Concrete removals:
- `tradingagents_us/dataflows/kap.py` — deleted
- `tradingagents_us/dataflows/tcmb.py` — deleted
- `tradingagents_us/dataflows/matriks.py` — deleted
- `tradingagents_us/prompts/tr/` — deleted
- `BIST30_UNIVERSE` in `bulk_loader.py` — deleted
- `Market = Literal["US", "BIST"]` → `Market = Literal["US"]`
- `Currency = Literal["USD", "TRY"]` → `Currency = Literal["USD"]`
- `cash_try` field in `PortfolioSnapshot` — deleted
- `MATRIKS_*` + `KAP_USER_AGENT` env vars — deleted
- `matriks` secret in Terraform `modules/secrets` — deleted
- Namespace rename: `tradingagents_tr` → `tradingagents_us`
- Graph entry: `graph/tr_setup.py` → `graph/pipeline.py`, market argument removed

Mobile app i18n (Turkish + English UI language) **retained** — UI language is independent of market support; the user prefers Turkish UI.

## Rationale

1. **Focus.** Single market end-to-end beats two half-baked markets.
2. **No data path for BIST retail.** Matriks IQ is the only viable BIST data source and requires paid subscription + per-seat licensing. Algolab approval is opaque. Midas (Turkey's Robinhood) has no public API. Building a Matriks integration before US system is even paper-profitable is poor capital allocation.
3. **Regulatory complexity.** SPK rules for any future commercial product (yatırım danışmanlığı license, PYŞ for portfolio management, ₺3M+ capital) substantially exceed US RIA registration. Pushing the BIST commercial decision to Phase 8+ is honest.
4. **Survivorship-safe BIST data is hard.** No equivalent of CRSP or Polygon's delisted ticker history for BIST. Backtesting BIST without survivor-safe universe gives inflated Sharpe.
5. **LLM training data bias.** Frontier LLMs (Claude, GPT) have substantially more US-equity financial reasoning in their training data than BIST. Agent quality on BIST is unknown and likely worse.
6. **Currency complication.** TRY's structural devaluation vs USD adds a macro overlay (carry trade, hedging) the agent has no awareness of without explicit augmentation. US-only sidesteps this entirely.

## Alternatives considered

| Option | Why rejected |
|---|---|
| Keep BIST as Phase 3 work | Adds 4-6 weeks before any market is profitable; stretches focus |
| BIST-only (drop US) | US has the best data ecosystem and LLM training coverage — strictly worse |
| Hybrid via shared agent + market-specific dataflows | Architecturally we did this in Phase 0, but BIST dataflows were never built; deletion is cleaner than maintaining stubs |
| Defer to Phase 8 "parking lot" without code removal | Stubs decay; dead code obscures the live codebase |

## Consequences

### Gained
- Cleaner namespace, smaller surface area
- Single broker integration to think about (Alpaca)
- Single regulatory regime (SEC/FINRA)
- Single currency, single trading calendar, single tax framework
- Single survivor-safe data path (Polygon historical)
- Faster path to Phase 4 paper trade

### Lost
- Personal applicability for BIST trades (author's home market)
- Coverage for TRY-denominated portfolio diversification
- The "novel TR-specific" angle that might have differentiated a future commercial product

### Cost of reversal
If we ever want BIST back (Phase 8+), the work to add is:
- New dataflow modules (KAP scraper, Matriks adapter) — ~2 weeks
- Loosen `Market` / `Currency` literals back to union — 1 hour
- BIST trading calendar in graph — 1 day
- Turkish prompts (if pursued) — 2-3 days

Net: ~3 weeks. Reversible.

## Triggers to reconsider BIST

- US system in live trading with sustained Sharpe > 1.0 for 6 months
- Author personally wants BIST exposure that can't be matched via ADRs (e.g., THYAO without ADR equivalent)
- Material change in Matriks/Algolab API accessibility (lower cost, easier approval)
- SPK launches a clearer retail-algo regulatory framework

## Action items

- [x] Code removal (namespace rename, dataflow deletions, schema tightening)
- [x] Doc updates (README, ARCHITECTURE, ROADMAP, ADR-001/004/007 cross-references)
- [x] Infra: drop `matriks` secret entry
- [x] `.env` / `.env.example` cleanup
- [x] GitHub repo description + topics updated
- [x] CI workflow path filters (no BIST-specific paths existed)

## Sources

- ADR-001 (multi-agent fork) — BIST adaptation table preserved as historical context but no longer actionable
- ADR-004 (data providers) — BIST data section preserved as historical context
- ADR-007 (regulatory) — Turkey/SPK section preserved as historical context for potential future re-entry
- Author decision 2026-05-22
