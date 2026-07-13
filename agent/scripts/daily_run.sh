#!/usr/bin/env bash
# Phase 6 — daily paper-trading run.
#
# For each ticker in the universe:
#   1. Generate a FRESH LLM decision (real Anthropic call, ~$0.50-1.50)
#   2. Run risk sizer + stale/headroom/stop guards
#   3. Submit a bracket order to Alpaca PAPER (--submit)
#   4. Persist decision + order + update to the trade log DB
#
# Designed to run post-US-close (22:30 UTC) on weekdays via systemd timer.
# Idempotent per (ticker, date): the executor's client_order_id dedupes,
# and the stale/headroom guards stop accidental double-entries.
#
# Cost: ~$0.50-1.50 LLM per ticker. With 11 tickers × 22 trading days that's
# ~$120-360/mo on the current 2-tier Opus/Sonnet routing. ADR-006 per-agent
# Haiku routing + prompt caching cuts this ~2-3x — apply after the eval window.

set -euo pipefail

cd "$(dirname "$0")/.."   # -> agent/

# Load .env if present (systemd also injects via EnvironmentFile, this is the
# manual-run fallback).
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PYTHON="${PYTHON:-./.venv/bin/python}"
# Default matches the LIVE production universe — a box missing the env var
# must not silently trade a smaller book mid-eval.
UNIVERSE="${UNIVERSE:-SPY AAPL MSFT NVDA GOOGL AMZN META JPM V XOM UNH}"
SUBMIT="${SUBMIT:-1}"              # 1 = real paper submit, 0 = dry-run
LOG_DIR="${LOG_DIR:-./logs}"
# Hard wall-clock cap per ticker — one hung LangGraph/httpx call must not
# starve the rest of the post-close window (that would burn an eval day).
# Generous 30 min: a slow-but-viable ticker (Anthropic overload retries, long
# debate) must still complete — this only fires on a genuine hang, where the
# old behavior was worse (unit-level SIGKILL taking the REMAINING tickers too).
TICKER_TIMEOUT_S="${TICKER_TIMEOUT_S:-1800}"
mkdir -p "$LOG_DIR"

DATE="$(date -u +%F)"
RUN_LOG="${LOG_DIR}/daily_${DATE}.log"

# Dead-man's switch ping — call on every HEALTHY outcome (full run, or a
# deliberate kill-switch skip). A missed ping means the automation itself
# died, which is exactly what healthchecks.io should page on.
ping_healthcheck() {
  if [[ -n "${HEALTHCHECK_URL:-}" ]]; then
    curl -fsS -m 10 --retry 3 "$HEALTHCHECK_URL" >/dev/null 2>&1 \
      || echo "  -> healthcheck ping failed (non-fatal)" | tee -a "$RUN_LOG"
  fi
}

echo "===============================================" | tee -a "$RUN_LOG"
echo "Daily run $(date -u +%FT%TZ)  universe=[$UNIVERSE]  submit=$SUBMIT" | tee -a "$RUN_LOG"
echo "===============================================" | tee -a "$RUN_LOG"

# Honor the mobile kill switch BEFORE the weekend guard and BEFORE burning
# LLM tokens: an armed FLATTEN_ALL must execute even on a manual weekend
# run (close orders queue for Monday's open). The API already attempts the
# flatten at flip time; kill_check is the backstop. Exit 1 from kill_check
# means a FAILED/PARTIAL flatten — fail safe: skip the run, alert loudly.
run_snapshot_best_effort() {
  # Keep the eval snapshot chain unbroken on skip days (read-only, cheap).
  PYTHONPATH=.:vendor/tradingagents "$PYTHON" -m scripts.snapshot 2>&1 | tee -a "$RUN_LOG" \
    || echo "  -> snapshot failed (non-fatal)" | tee -a "$RUN_LOG"
}

set +e
PYTHONPATH=.:vendor/tradingagents "$PYTHON" -m scripts.kill_check 2>&1 | tee -a "$RUN_LOG"
kc_rc=${PIPESTATUS[0]}
set -e
if [[ "$kc_rc" -ne 0 ]]; then
  case "$kc_rc" in
    75) echo "kill switch PAUSE_NEW — skipping daily run" | tee -a "$RUN_LOG"
        run_snapshot_best_effort
        ping_healthcheck ;;
    76) echo "kill switch FLATTEN_ALL — close orders in, skipping daily run" | tee -a "$RUN_LOG"
        run_snapshot_best_effort
        ping_healthcheck ;;
    *)  echo "kill_check failed (rc=$kc_rc) — failing safe, skipping daily run" | tee -a "$RUN_LOG"
        PYTHONPATH=.:vendor/tradingagents "$PYTHON" -m scripts.notify_ops \
          --title "⚠️ kill_check FAILED — daily run skipped" \
          --body "rc=$kc_rc @ ${DATE}; flatten may be PARTIAL — check positions + logs" 2>&1 | tee -a "$RUN_LOG" || true ;;
  esac
  exit 0
fi

# Skip weekends (US market closed). systemd timer also restricts to Mon-Fri,
# but a manual run shouldn't burn LLM tokens on a Saturday.
DOW="$(date -u +%u)"   # 1=Mon .. 7=Sun
if [[ "$DOW" -ge 6 ]]; then
  echo "weekend (dow=$DOW) — skipping, US market closed" | tee -a "$RUN_LOG"
  exit 0
fi

SUBMIT_FLAG=""
[[ "$SUBMIT" == "1" ]] && SUBMIT_FLAG="--submit"

rc_total=0
failed_tickers=""
for TICKER in $UNIVERSE; do
  echo "" | tee -a "$RUN_LOG"
  echo "--- $TICKER @ $DATE ---" | tee -a "$RUN_LOG"
  # Fresh decision (no --use-cached). Guards + bracket are on by default.
  if timeout -k 30 "$TICKER_TIMEOUT_S" env PYTHONPATH=.:vendor/tradingagents "$PYTHON" -m scripts.trade \
        --ticker "$TICKER" --date "$DATE" $SUBMIT_FLAG 2>&1 | tee -a "$RUN_LOG"; then
    echo "  -> $TICKER done" | tee -a "$RUN_LOG"
  else
    rc=$?
    if [[ "$rc" -eq 124 ]]; then
      echo "  -> $TICKER TIMED OUT after ${TICKER_TIMEOUT_S}s — continuing" | tee -a "$RUN_LOG"
    else
      echo "  -> $TICKER FAILED (rc=$rc) — continuing" | tee -a "$RUN_LOG"
    fi
    rc_total=$((rc_total + 1))
    failed_tickers="${failed_tickers} ${TICKER}"
  fi
done

echo "" | tee -a "$RUN_LOG"
# Append an end-of-run portfolio snapshot for eval enrichment (best-effort —
# a snapshot failure must not fail the run).
if PYTHONPATH=.:vendor/tradingagents "$PYTHON" -m scripts.snapshot 2>&1 | tee -a "$RUN_LOG"; then
  :
else
  echo "  -> snapshot failed (non-fatal)" | tee -a "$RUN_LOG"
fi

echo "" | tee -a "$RUN_LOG"
echo "Daily run complete. $rc_total ticker(s) errored." | tee -a "$RUN_LOG"

if [[ "$rc_total" -gt 0 ]]; then
  # Alert the human (best-effort — notify_ops always exits 0) and exit
  # non-zero so systemd marks the unit failed and OnFailure= fires too.
  PYTHONPATH=.:vendor/tradingagents "$PYTHON" -m scripts.notify_ops \
    --title "⚠️ Daily run: ${rc_total} ticker(s) failed" \
    --body "Failed:${failed_tickers} @ ${DATE}" 2>&1 | tee -a "$RUN_LOG" || true
  exit 1
fi

# Dead-man's switch: ping only on a fully successful run.
ping_healthcheck

exit 0
