# ADR-005: Risk management

**Status:** Accepted
**Date:** 2026-05-22

## Context

LLM agents are not risk managers. They produce ratings (Buy/Hold/Sell + entry/stop/size suggestion); a separate, deterministic risk layer must:
- Veto unsafe orders
- Size positions according to a tested formula (not LLM guess)
- Halt trading when limits breach
- Be remotely killable from the mobile app

Empirically: most quant strategies that pass backtest fail in production from risk-layer bugs, not signal bugs. **The risk layer is the primary defense against loss-of-principal events.**

## Decision

### Position sizing

| Method | Use case |
|---|---|
| **Fractional Kelly (0.25x)** | High-conviction signals when `p_win` and `b` are well-estimated |
| **ATR-based** | Default — `risk_per_trade=0.5%`, `stop=2*ATR` |
| **Volatility targeting** | Multi-strategy book — target 15% annualized portfolio vol |
| Fixed fractional | MVP only |
| Fixed dollar | Backtest baseline only |

Never full Kelly (empirically blows up; the math assumes perfect probability estimates we never have).

### Stop-loss strategy

| Type | Use case |
|---|---|
| ATR trailing (3×ATR) | Trend-following positions |
| Fixed % (typically 2%) | Mean-reversion, short hold |
| Time-based (N bars) | Catalyst-driven trades that didn't materialize |
| Vol-adjusted | Wider stops on high-vol names |

### Portfolio-level limits

```python
LIMITS = {
    "max_position_pct":   0.10,   # 10% of portfolio per name
    "max_sector_pct":     0.30,   # 30% per GICS sector / BIST sector
    "max_correlated":     3,      # max 3 names with rolling |rho| > 0.7
    "max_daily_dd":       0.03,   # halt at -3% intraday
    "max_consec_losses":  5,      # halt + alert after 5 consecutive losses
    "max_gross_exposure": 1.5,    # 1.5x leverage cap
    "min_liquidity_adv":  100_000,  # require >$100k avg daily volume
}
```

### Circuit breakers (in agent runtime)

```python
class CircuitBreaker:
    def check(self, equity_now, equity_open, price, mu, sigma) -> bool:
        # 1. Remote kill switch (DynamoDB flag, polled @5s, mobile-controlled)
        if read_kill_flag() in {"PAUSE_NEW", "FLATTEN_ALL"}: return False
        # 2. Drawdown halt
        if (equity_now / equity_open - 1) < -self.max_daily_dd: return False
        # 3. Price anomaly (>3σ from rolling mean)
        if abs((price - mu) / sigma) > 3.0: return False
        # 4. API error rate
        if self.requests > 20 and (self.errors / self.requests) > 0.20: return False
        return True
```

### Kill switch pattern

Three states stored in DynamoDB `kill_switches/{user_id}`:
- `RUN` — normal operation
- `PAUSE_NEW` — no new entries, manage existing positions (e.g., honor stops)
- `FLATTEN_ALL` — immediate market-exit of all positions

Agent polls every 5 seconds. Mobile app writes via authenticated REST endpoint.

### Risk metrics tracked

| Metric | Target (paper → live) |
|---|---|
| Sharpe (net of costs) | > 1.0 |
| Sortino | > 1.2 |
| Calmar (CAGR/MaxDD) | > 0.5 |
| Max drawdown | < 15% |
| Drawdown duration | < 60 days |
| VaR(95%) daily | < 2% of equity |
| CVaR(95%) daily | < 3.5% |
| Beta vs SPY (US) / XU100 (TR) | < 1.2 |
| Information Ratio vs benchmark | > 0.5 |

## Backtest pitfalls — defenses

| Pitfall | Defense |
|---|---|
| **Lookahead bias** | Always `t-1` close → `t` open. Runtime assertion: `assert signal_time < trade_time`. CI test verifies on synthetic data |
| **Survivorship bias** | Polygon historical ticker mappings (delisted included). Never use yfinance current-universe |
| **LLM training-set leakage** | Use a Claude snapshot dated **before** the test window's start. Frozen model snapshot per backtest |
| **Data snooping** | Walk-forward CV. Reserve final 20% as untouched holdout — touch only once at end |
| **Transaction costs** | Alpaca $0 commission but model 5–15 bps slippage. SEC TAF + SEC fees on sells. BIST: BSMV + işlem ücreti |
| **Selection bias** | Test on 2007–2009, 2020, 2022 bear regimes — not just bull markets |
| **Multiple testing** | Bonferroni correction or deflated Sharpe (Bailey & López de Prado) |

## Paper → Live gate

| Gate | Minimum |
|---|---|
| Paper duration | **90 days minimum across at least one regime change** |
| Live paper Sharpe | > 1.0 net of modeled costs |
| Max DD in paper | < 15% |
| Initial live capital | $2–5K |
| Position size in live | **Start at 25% of paper sizing** |
| Scale-up trigger | +30 days live with Sharpe > 0.8 and DD < paper max |

## Alternatives considered

| Option | Why rejected |
|---|---|
| Trust LLM for sizing | LLMs hallucinate position sizes; no audit trail |
| Equal-weight (1/N) | Ignores volatility — over-sizes in vol regimes |
| Black-Litterman | Requires confidence intervals we can't reliably get from LLMs |
| Hierarchical Risk Parity (HRP) | Strong choice for v2; deferred — Phase 8 |
| No remote kill switch | Unacceptable. The kill switch is the single most important safety control |

## Implementation locations

| Component | File |
|---|---|
| Position sizing | `agent/tradingagents_us/risk/position_sizing.py` |
| Stop-loss | `agent/tradingagents_us/risk/stop_loss.py` |
| Circuit breaker | `agent/tradingagents_us/risk/circuit_breaker.py` |
| Kill switch | `agent/tradingagents_us/risk/kill_switch.py` |
| Portfolio limits enforcement | `agent/tradingagents_us/risk/portfolio_limits.py` |
| Risk metrics | `agent/tradingagents_us/risk/metrics.py` |

## Sources

- Research output `docs/RESEARCH.md` §6
- Kelly Criterion: Thorp (1969), Vince (1990) "fractional Kelly"
- Deflated Sharpe Ratio: Bailey & López de Prado (2014)
- López de Prado "Advances in Financial Machine Learning" (2018) — backtest pitfalls chapter
- vectorbt: https://vectorbt.dev
