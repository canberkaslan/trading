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
# Cost: ~$0.50-1.50 LLM per ticker. With 5 tickers × 22 trading days that's
# ~$55-165/mo. Keep UNIVERSE small until prompt caching (Phase 3b) lands.

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
UNIVERSE="${UNIVERSE:-SPY AAPL MSFT NVDA GOOGL}"
SUBMIT="${SUBMIT:-1}"              # 1 = real paper submit, 0 = dry-run
LOG_DIR="${LOG_DIR:-./logs}"
mkdir -p "$LOG_DIR"

DATE="$(date -u +%F)"
RUN_LOG="${LOG_DIR}/daily_${DATE}.log"

echo "===============================================" | tee -a "$RUN_LOG"
echo "Daily run $(date -u +%FT%TZ)  universe=[$UNIVERSE]  submit=$SUBMIT" | tee -a "$RUN_LOG"
echo "===============================================" | tee -a "$RUN_LOG"

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
for TICKER in $UNIVERSE; do
  echo "" | tee -a "$RUN_LOG"
  echo "--- $TICKER @ $DATE ---" | tee -a "$RUN_LOG"
  # Fresh decision (no --use-cached). Guards + bracket are on by default.
  if PYTHONPATH=.:vendor/tradingagents "$PYTHON" -m scripts.trade \
        --ticker "$TICKER" --date "$DATE" $SUBMIT_FLAG 2>&1 | tee -a "$RUN_LOG"; then
    echo "  -> $TICKER done" | tee -a "$RUN_LOG"
  else
    rc=$?
    echo "  -> $TICKER FAILED (rc=$rc) — continuing" | tee -a "$RUN_LOG"
    rc_total=$((rc_total + 1))
  fi
done

echo "" | tee -a "$RUN_LOG"
echo "Daily run complete. $rc_total ticker(s) errored." | tee -a "$RUN_LOG"
exit 0
