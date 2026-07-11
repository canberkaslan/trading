# feat/cost-opt-adr006 — Merge Readiness

**Status:** READY (rebased on today's `main`, tests green). Merge + flag-flip is a
**go-live decision** — deliberately left for a human to execute alongside the funded
Alpaca cutover, not auto-merged. See [ADR-006](adr/006-llm-cost-optimization.md).

**Date assessed:** 2026-07-11 — the paper eval window closed the same day
(`eval_complete=true`, verdict **GO**, 12/10 trading days).

## What the branch does

Per-agent LLM routing (ADR-006): binds an `agent_model_map` so heuristic agents run
on a cheaper model and managers run on a stronger one, instead of one fixed
`deep_think`/`quick_think` pair for every role.

| File | Change |
|---|---|
| `tradingagents_us/graph/pipeline.py` | Opt-in wiring: build `cfg` from `DEFAULT_CONFIG`, inject `agent_model_map` only when flag set |
| `tradingagents_us/llm/routing.py` | `AGENT_MODEL_MAP` (already on `main`) |
| `third_party/.../graph/setup.py`, `trading_graph.py` | Accept + thread per-agent LLMs through graph construction |
| `tests/test_llm_routing.py`, `tests/test_graph_routing.py` | 120 lines of coverage for the routing wiring |

Single feature commit `1e26449`, +178/−14 across 5 files.

## Safety proof

- **Default OFF.** `pipeline.py` gates on `TRADINGAGENTS_PER_AGENT_ROUTING` (default `"0"`).
  Merging to `main` and deploying with the flag unset changes **zero** decisions — the
  system behaves exactly as it did through the eval. It is a no-op until someone flips it.
- **Rebased clean** onto current `main` (was 40 behind / 1 ahead) — no conflicts.
- **Full suite green:** `167 passed, 1 skipped, 1 deselected` (the deselected
  `test_synthetic_sma_crossover_on_aapl_2024` fails locally on S3, skipped in CI — normal).

## Go-live steps (human, post-funding)

1. `git checkout main && git merge --ff-only feat/cost-opt-adr006 && git push origin main`
2. Deploy (flag still OFF — no behavior change yet):
   `ssh agentmesh 'cd /opt/ai-trader && git pull --ff-only origin main && sudo systemctl restart ai-trader-api.service'`
3. **Flip separately, once, watched:** set `TRADINGAGENTS_PER_AGENT_ROUTING=1` in the
   service env and restart. This is the decision-quality change — do it on a day you can
   watch the next daily run, not blind.
4. **Rollback:** unset the flag + restart (instant, no redeploy). Routing state is env-only.

## Follow-ups before flipping the flag

- **Model IDs are stale.** `AGENT_MODEL_MAP` pins `claude-opus-4-7` / `claude-sonnet-4-6` /
  `claude-haiku-4-5`. Current-gen is Opus 4.8 / Sonnet 5 / Haiku 4.5. Refresh the map
  before enabling so the cost-opt run reflects real routing, not a downgrade. (Decision-path
  change — left for human review.)
- **Cost/quality A/B.** After flipping, compare a few daily runs' decisions + token cost
  against the fixed-model baseline before trusting it for funded trading.
