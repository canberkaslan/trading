"""Backtest harness.

Two validity tiers (see docs/RESEARCH.md on LLM look-ahead bias):
- Deterministic strategies (momentum / mean-reversion / risk layer) — fully
  valid to backtest; no model has "seen the future".
- LLM agent — backtests suffer information leakage (the model was trained on
  the test period). Use `llm_backtest` only with strict point-in-time gating,
  and treat its numbers as indicative, never as proof. Forward paper is truth.
"""
