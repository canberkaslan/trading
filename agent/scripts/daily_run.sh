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
#
# A plain `source` overwrites what systemd set. For the alert channels that let a
# blank `HEALTHCHECK_URL=` in agent/.env (the example used to ship one) switch the
# dead-man's switch and the GitHub half off, silently, while preflight (which
# never reads agent/.env) reported both as configured. So for these keys a value
# the environment already has wins, as trade.py's own loader does for every key.
# Every other key keeps .env-over-environment: changing that would change the
# credentials, universe and submit flag the run trades with.
ALERT_KEYS=(HEALTHCHECK_URL OPS_ALERT_GITHUB_TOKEN OPS_ALERT_GITHUB_REPO)
_inherited_alerting=()
for _key in "${ALERT_KEYS[@]}"; do
  if [[ -n "${!_key:-}" ]]; then
    _inherited_alerting+=("${_key}=${!_key}")
  fi
done
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
# ${arr[@]+...}: an empty array is an unbound-variable error under set -u on
# bash < 4.4, which is what macOS ships.
for _kv in ${_inherited_alerting[@]+"${_inherited_alerting[@]}"}; do
  export "$_kv"
done

PYTHON="${PYTHON:-./.venv/bin/python}"
# Default matches the LIVE production universe — a box missing the env var
# must not silently trade a smaller book mid-eval.
UNIVERSE="${UNIVERSE:-SPY AAPL MSFT NVDA GOOGL AMZN META JPM V XOM UNH}"

# Hard ceiling on how many tickers one run may council, whatever the source.
#
# Nothing else in this system bounds the cost of a run: the only limit is the
# length of a shell variable. At roughly a dollar of model spend per ticker, a
# mistyped UNIVERSE — or a screener that one day feeds this loop its ranking —
# is an unbounded bill that nobody finds out about until the invoice, because
# there is no per-decision token accounting to catch it sooner.
#
# The cap is deliberately dumb and unconditional. It does not know where the
# list came from and does not try to choose well among the names; a run that
# silently trades a different book than intended is the failure this prevents,
# so it truncates in the order given and says so loudly in the log.
#
# Raise it consciously via the env var. Note that the book can only hold about
# ten positions anyway (scripts/trade.py's 10% single-name cap against a
# cash-only account), so a much larger number buys refusals at full price
# rather than more positions.
MAX_TICKERS="${MAX_TICKERS:-20}"
_UNIVERSE_COUNT=$(echo $UNIVERSE | wc -w | tr -d " ")
if [ "$_UNIVERSE_COUNT" -gt "$MAX_TICKERS" ]; then
  echo "WARNING: universe has $_UNIVERSE_COUNT tickers, MAX_TICKERS=$MAX_TICKERS — truncating, $(( _UNIVERSE_COUNT - MAX_TICKERS )) dropped"
  UNIVERSE=$(echo $UNIVERSE | tr " " "\n" | head -n "$MAX_TICKERS" | tr "\n" " ")
  echo "WARNING: run continues with: $UNIVERSE"
fi
SUBMIT="${SUBMIT:-1}"              # 1 = real paper submit, 0 = dry-run
LOG_DIR="${LOG_DIR:-./logs}"
# Hard wall-clock cap per ticker — one hung LangGraph/httpx call must not
# starve the rest of the post-close window (that would burn an eval day).
# Generous 30 min: a slow-but-viable ticker (Anthropic overload retries, long
# debate) must still complete — this only fires on a genuine hang, where the
# old behavior was worse (unit-level SIGKILL taking the REMAINING tickers too).
TICKER_TIMEOUT_S="${TICKER_TIMEOUT_S:-1800}"

# Risk caps are scripts/trade.py's flag defaults (single name 0.10, sector 0.30,
# cash utilization 1.0), and this script passes none of those flags, on purpose.
# MAX_POSITION_PCT and MAX_SECTOR_PCT were listed in .env.example for months with
# nothing reading them, so a box's agent/.env or secrets.env may hold values no
# one has checked. Forwarding them would let such a value resize orders, or, if
# trade.py refuses it, fail every ticker before its council, on the first run
# after a deploy. Tying a cap to an env var is an order-flow change and ships as
# one, after the box has been checked.
mkdir -p "$LOG_DIR"

DATE="$(date -u +%F)"
RUN_LOG="${LOG_DIR}/daily_${DATE}.log"

# Dead-man's switch, healthchecks.io convention: HEALTHCHECK_URL on a healthy
# outcome (a full run, or a deliberate kill-switch skip), HEALTHCHECK_URL/fail on
# a failed one. The ping that matters most is the one that never arrives: a run
# that stops happening sends nothing, and the check pages on the silence. Every
# alert raised on this box used to reach only the phone app, which is how a
# refused broker key went unnoticed for nearly two weeks.
#
# A ping can never fail the run: a short timeout, two retries, and any error is
# a log line. The body is shown in the check's log and in its notification.
HC_PINGED=0
ping_healthcheck() {
  local outcome="${1:-}" body="${2:-}"
  HC_PINGED=1
  if [[ -z "${HEALTHCHECK_URL:-}" ]]; then
    return 0
  fi
  local url="${HEALTHCHECK_URL%/}"
  if [[ "$outcome" == "fail" ]]; then
    url="${url}/fail"
  fi
  if ! curl -fsS -m 10 --retry 2 --data-raw "$body" "$url" >/dev/null 2>&1; then
    echo "  -> healthcheck ping failed (non-fatal)" | tee -a "$RUN_LOG" || true
  fi
}

# The decision records of a parallel run, once decide_in_parallel has made
# them; on_exit removes this and nothing else. Cleared here, before the trap,
# so a PLAN_DIR from the caller's shell or agent/.env is never what it removes.
PLAN_DIR=""

# A run that dies between the explicit outcomes below (set -e on a failed write,
# a crash between steps) is a failure none of them saw. Report it now rather
# than leave the check to notice only when its grace period runs out.
on_exit() {
  local rc=$?
  # A parallel run's decision records die with it, whatever ended it: nothing
  # written by this run may be sent by another (see decide_in_parallel).
  if [[ -n "${PLAN_DIR:-}" ]]; then
    rm -rf "$PLAN_DIR" || true
  fi
  if [[ "$rc" -ne 0 && "$HC_PINGED" -eq 0 ]]; then
    ping_healthcheck fail "daily_run.sh exited rc=$rc before reporting an outcome @ ${DATE}"
  fi
}
trap on_exit EXIT

# Ops alert: push AND a GitHub issue (see tradingagents_us/notifications/
# ops_channel.py). Best-effort; notify_ops always exits 0.
notify_ops() {
  PYTHONPATH=.:vendor/tradingagents "$PYTHON" -m scripts.notify_ops "$@" 2>&1 \
    | tee -a "$RUN_LOG" || true
}

echo "===============================================" | tee -a "$RUN_LOG"
echo "Daily run $(date -u +%FT%TZ)  universe=[$UNIVERSE]  submit=$SUBMIT" | tee -a "$RUN_LOG"
echo "===============================================" | tee -a "$RUN_LOG"
if [[ -z "${HEALTHCHECK_URL:-}" ]]; then
  echo "WARNING: HEALTHCHECK_URL unset, no dead-man's switch (/readyz and the watchdog report it)" \
    | tee -a "$RUN_LOG"
fi

# Honor the mobile kill switch BEFORE the weekend guard and BEFORE burning
# LLM tokens: an armed FLATTEN_ALL must execute even on a manual weekend
# run (close orders queue for Monday's open). The API already attempts the
# flatten at flip time; kill_check is the backstop. Exit 1 from kill_check
# means a FAILED/PARTIAL flatten — fail safe: skip the run, alert loudly.
#
# PAUSE_NEW is "no new entries", not "stop looking after what is held": the
# decisions are skipped, and the position pass and the stop-coverage check
# still run and still page. Skipping them too left a lot whose queued time
# exit the open refused with neither stop nor exit, unnamed and unpaged, for
# as long as the switch stayed on.
run_snapshot_best_effort() {
  # Keep the eval snapshot chain unbroken on skip days (read-only, cheap).
  PYTHONPATH=.:vendor/tradingagents "$PYTHON" -m scripts.snapshot 2>&1 | tee -a "$RUN_LOG" \
    || echo "  -> snapshot failed (non-fatal)" | tee -a "$RUN_LOG"
}

PAUSED=0
set +e
PYTHONPATH=.:vendor/tradingagents "$PYTHON" -m scripts.kill_check 2>&1 | tee -a "$RUN_LOG"
kc_rc=${PIPESTATUS[0]}
set -e
if [[ "$kc_rc" -eq 75 ]]; then
  echo "kill switch PAUSE_NEW — no decisions today; positions are still managed and checked" \
    | tee -a "$RUN_LOG"
  PAUSED=1
elif [[ "$kc_rc" -ne 0 ]]; then
  case "$kc_rc" in
    76) echo "kill switch FLATTEN_ALL — close orders in, skipping daily run" | tee -a "$RUN_LOG"
        run_snapshot_best_effort
        ping_healthcheck ;;
    *)  echo "kill_check failed (rc=$kc_rc) — failing safe, skipping daily run" | tee -a "$RUN_LOG"
        notify_ops --kind kill_switch \
          --title "⚠️ kill_check FAILED — daily run skipped" \
          --body "rc=$kc_rc @ ${DATE}; flatten may be PARTIAL — check positions + logs"
        ping_healthcheck fail "kill_check failed (rc=$kc_rc) @ ${DATE}; daily run skipped" ;;
  esac
  exit 0
fi

# Skip weekends (US market closed). systemd timer also restricts to Mon-Fri,
# but a manual run shouldn't burn LLM tokens on a Saturday.
DOW="$(date -u +%u)"   # 1=Mon .. 7=Sun
if [[ "$DOW" -ge 6 ]]; then
  echo "weekend (dow=$DOW) — skipping, US market closed" | tee -a "$RUN_LOG"
  if [[ "$PAUSED" -eq 1 ]]; then
    run_snapshot_best_effort
    ping_healthcheck
  fi
  exit 0
fi

SUBMIT_FLAG=""
[[ "$SUBMIT" == "1" ]] && SUBMIT_FLAG="--submit"

# Manage what is already open BEFORE deciding what to buy.
#
# Order matters: capital this pass releases is available to the theses formed
# minutes later. The book holds ~10 names against an 11-name universe and a 10%
# per-name cap, so it is at the cap on everything it knows — 167 recorded
# refusals are "trimmed_to_zero_by_portfolio_caps". With no exit path that
# freeze is permanent, which is most of what the eval has been measuring.
#
# Best-effort: a failure here must not stop the decision loop. A stop that did
# not ratchet is yesterday's protection, which is what the book had anyway; a
# decision loop that did not run is a lost trading day.
echo "" | tee -a "$RUN_LOG"
echo "--- position management ---" | tee -a "$RUN_LOG"
# --backfill-stops: place protective stops on shares that have none. Without
# it the pass only REPORTS them, and the first live coverage check found 75.5%
# of the book naked — bracket legs go missing over time (a take-profit fills and
# cancels its OCO sibling, a flatten cancels orders, a position is added to) and
# nothing was putting them back. The flag is gated by --submit like everything
# else here, so SUBMIT=0 still only reports.
#
# --refresh-bars: fetch the held names' daily bars from Polygon first. Only the
# app's chart views write the bar cache, and a held name nobody charted kept
# weeks-old bars: its age stopped counting toward the time exit, and once it
# moved past the mark band its stop was refused every night. The bars are kept
# for the pass and not written to the cache, which the BUY checks below read.
# A name that cannot be refreshed is judged by its cache's age.
#
# Non-fatal, but never silent. rc 3 means a time exit may have left shares with
# no stop: a cancel still on its way strips the stop after this run, and the
# stop-coverage check at the end of the run still sees it standing, so this is
# the only point that can page about it. Any other failure pages too, more
# quietly: a pass that failed is protection nobody maintained today.
set +e
PYTHONPATH=.:vendor/tradingagents "$PYTHON" -m scripts.manage_positions --backfill-stops --refresh-bars $SUBMIT_FLAG 2>&1 | tee -a "$RUN_LOG"
mp_rc=${PIPESTATUS[0]}
set -e
if [[ "$mp_rc" -eq 3 ]]; then
  echo "  -> position management may have left shares with no stop (rc=3) — paging, continuing to decisions" | tee -a "$RUN_LOG"
  mp_detail="$(sed -n 's/^UNCOVERED: //p' "$RUN_LOG" | tail -n 1 || true)"
  notify_ops --kind position_pass \
    --title "⚠️ Time exit may have left shares with no stop" \
    --body "${mp_detail:-manage_positions rc=3} @ ${DATE}. See the FAILED time exit lines in ${RUN_LOG}. A cancel still on its way removes the stop after the run; check the broker's open orders for those names."
elif [[ "$mp_rc" -ne 0 ]]; then
  echo "  -> position management failed (rc=$mp_rc, non-fatal) — paging, continuing to decisions" | tee -a "$RUN_LOG"
  notify_ops --kind position_pass \
    --title "⚠️ Position management failed (rc=$mp_rc)" \
    --body "manage_positions rc=$mp_rc @ ${DATE}: see the FAILED, REFUSED, UNREFRESHED and exit budget lines in ${RUN_LOG}. Stops were not maintained where it failed or refused; a deferred time exit means more names read as due than one day may close."
fi

# The names to decide on: none under PAUSE_NEW, which skips the commentator
# feed, every council and the order-flow check below with them.
DECIDE="$UNIVERSE"
if [[ "$PAUSED" -eq 1 ]]; then
  DECIDE=""
  echo "" | tee -a "$RUN_LOG"
  echo "kill switch PAUSE_NEW — decisions skipped" | tee -a "$RUN_LOG"
fi

# Commentator feed (ADR-009): fetch and extract ONCE, before the tickers, so
# every sentiment analyst reads the same cached items and none of them fetches.
# Off unless COMMENTATOR_FEED=1; with it off this block prints nothing and runs
# nothing. Best-effort: the script exits 0 on its own failures, and a hang is
# cut short rather than eating the post-close window.
if [[ "${COMMENTATOR_FEED:-0}" == "1" && -n "$DECIDE" ]]; then
  echo "" | tee -a "$RUN_LOG"
  echo "--- commentator feed ---" | tee -a "$RUN_LOG"
  # One live cutoff for the whole run, taken before the fetch: every ticker
  # admits items published up to this instant. A ticker that starts after
  # midnight UTC would otherwise read DATE as a past day and drop its items.
  export COMMENTATOR_LIVE_AS_OF
  COMMENTATOR_LIVE_AS_OF="$(date -u +%FT%TZ)"
  if timeout -k 30 "${COMMENTATOR_TIMEOUT_S:-600}" env PYTHONPATH=.:vendor/tradingagents "$PYTHON" -m scripts.commentator_fetch 2>&1 | tee -a "$RUN_LOG"; then
    :
  else
    echo "  -> commentator fetch failed (non-fatal) — tickers run without new items" | tee -a "$RUN_LOG"
  fi
fi

# Councils side by side, orders one at a time: COUNCIL_PARALLELISM.
#
# At 1 (the default, and what any invalid value falls back to) each ticker is
# one scripts.trade process that councils and then sends its order, one ticker
# after another, as it always has. Above 1 the run has two passes:
#
#   1. Up to COUNCIL_PARALLELISM councils at once, each its own scripts.trade
#      process with its own timeout and log. `--plan-dir` makes each stop after
#      sizing: it records the decision for the second pass and touches nothing
#      at the broker, no submit and no cancel. Their logs go into the run log
#      in universe order once all are done.
#   2. One process, scripts.submit_plans, takes the recorded decisions in
#      universe order and does for each what scripts.trade does after its
#      council: reads the kill switch, account, positions and open orders
#      again, sizes under every cap and runs the executor with all its guards.
#      Each order is sized against the ones placed before it, as in the
#      sequential run, so nothing needs a lock between processes.
#
# The one difference: the pre-council gate in pass 1 sees the book before any
# of this run's orders, so a name the sequential run would skip for want of
# cash is councilled (one council's cost). Pass 2 asks the same gate again
# against the book as it stands then and sends nothing for it, so what reaches
# the broker is the same.
#
# A record is used at most once and only by this run (scripts/submit_plans.py),
# and the directory holding them is removed when the run ends, however it ends.
# A stop (systemctl stop, Ctrl-C) stops both passes: the councils in flight are
# killed, their partial logs kept, nothing more is sent, and the run exits 143
# so the dead-man's switch hears a failure.
#
# Each council in flight is a stream of model calls against one API key's rate
# limit, so raise this only after checking a run's 429s and retries.
COUNCIL_PARALLELISM="${COUNCIL_PARALLELISM:-1}"
MAX_COUNCIL_PARALLELISM=4
if ! [[ "$COUNCIL_PARALLELISM" =~ ^[1-9][0-9]*$ ]]; then
  echo "WARNING: COUNCIL_PARALLELISM='$COUNCIL_PARALLELISM' is not a positive integer — councils run one at a time" \
    | tee -a "$RUN_LOG"
  COUNCIL_PARALLELISM=1
elif [[ "${#COUNCIL_PARALLELISM}" -gt 1 || "$COUNCIL_PARALLELISM" -gt "$MAX_COUNCIL_PARALLELISM" ]]; then
  echo "WARNING: COUNCIL_PARALLELISM=$COUNCIL_PARALLELISM is above the cap of $MAX_COUNCIL_PARALLELISM — using $MAX_COUNCIL_PARALLELISM" \
    | tee -a "$RUN_LOG"
  COUNCIL_PARALLELISM=$MAX_COUNCIL_PARALLELISM
fi

COUNCILS_MERGED=0

# Pass 1, one ticker: council and record. Runs in the background.
plan_ticker() {
  set +e
  local ticker="$1" run_id="$2" rc
  timeout -k 30 "$TICKER_TIMEOUT_S" env PYTHONPATH=.:vendor/tradingagents "$PYTHON" -m scripts.trade \
      --ticker "$ticker" --date "$DATE" --plan-dir "$PLAN_DIR" --run-id "$run_id" \
      >"$PLAN_DIR/$ticker.log" 2>&1
  rc=$?
  echo "$rc" >"$PLAN_DIR/$ticker.rc"
  echo "  [council] $ticker finished rc=$rc" | tee -a "$RUN_LOG"
}

# Pass 2: every recorded decision, in the order given. Runs in the background
# so that a stop reaches the trap below at once.
submit_pass() {
  set +e
  local run_id="$1"
  shift
  timeout -k 30 "$TICKER_TIMEOUT_S" env PYTHONPATH=.:vendor/tradingagents "$PYTHON" -m scripts.submit_plans \
      --plan-dir "$PLAN_DIR" --run-id "$run_id" --date "$DATE" $SUBMIT_FLAG "$@" 2>&1 \
    | tee -a "$RUN_LOG" "$PLAN_DIR/submit.out"
  echo "${PIPESTATUS[0]}" >"$PLAN_DIR/submit.rc"
}

stop_parallel_run() {
  trap '' TERM INT
  echo "" | tee -a "$RUN_LOG"
  echo "SIGNAL received — stopping the councils and the submit pass, nothing more is sent" \
    | tee -a "$RUN_LOG"
  local pid ticker
  for pid in $(jobs -p); do
    pkill -TERM -P "$pid" 2>/dev/null || true
    kill -TERM "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  if [[ "$COUNCILS_MERGED" -eq 0 ]]; then
    for ticker in $DECIDE; do
      if [[ -s "$PLAN_DIR/$ticker.log" ]]; then
        echo "--- $ticker @ $DATE (stopped) ---" | tee -a "$RUN_LOG"
        tee -a "$RUN_LOG" <"$PLAN_DIR/$ticker.log"
      fi
    done
  fi
  exit 143
}

decide_in_parallel() {
  local run_id ticker rc running planned="" failed_submits
  PLAN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/daily-plans.XXXXXX")"
  run_id="$(basename "$PLAN_DIR")"
  trap stop_parallel_run TERM INT

  echo "" | tee -a "$RUN_LOG"
  echo "--- councils: parallelism=$COUNCIL_PARALLELISM, nothing is sent until the submit pass ---" \
    | tee -a "$RUN_LOG"
  for ticker in $DECIDE; do
    while running="$(jobs -rp | wc -l | tr -d ' ')" && [[ "$running" -ge "$COUNCIL_PARALLELISM" ]]; do
      sleep 1
    done
    echo "  [council] $ticker started" | tee -a "$RUN_LOG"
    plan_ticker "$ticker" "$run_id" &
  done
  wait

  for ticker in $DECIDE; do
    echo "" | tee -a "$RUN_LOG"
    echo "--- $ticker @ $DATE ---" | tee -a "$RUN_LOG"
    if [[ -f "$PLAN_DIR/$ticker.log" ]]; then
      tee -a "$RUN_LOG" <"$PLAN_DIR/$ticker.log"
    fi
    # No rc file: the council's job died before it could write one.
    rc="$(cat "$PLAN_DIR/$ticker.rc" 2>/dev/null || echo 1)"
    if [[ "$rc" == "0" && -f "$PLAN_DIR/$ticker.plan.json" ]]; then
      echo "  -> $ticker decided — sized and sent in the submit pass" | tee -a "$RUN_LOG"
      planned="$planned $ticker"
    elif [[ "$rc" == "0" ]]; then
      echo "  -> $ticker done" | tee -a "$RUN_LOG"
    else
      # A record a failed or timed-out council left behind is never sent.
      if [[ "$rc" == "124" ]]; then
        echo "  -> $ticker TIMED OUT after ${TICKER_TIMEOUT_S}s — continuing" | tee -a "$RUN_LOG"
      else
        echo "  -> $ticker FAILED (rc=$rc) — continuing" | tee -a "$RUN_LOG"
      fi
      rc_total=$((rc_total + 1))
      failed_tickers="${failed_tickers} ${ticker}"
    fi
  done
  COUNCILS_MERGED=1

  if [[ -n "$planned" ]]; then
    echo "" | tee -a "$RUN_LOG"
    echo "--- submit pass:${planned} ---" | tee -a "$RUN_LOG"
    # shellcheck disable=SC2086 # one argument per ticker
    submit_pass "$run_id" $planned &
    wait "$!" || true
    rc="$(cat "$PLAN_DIR/submit.rc" 2>/dev/null || echo 1)"
    failed_submits="$(sed -n 's/^  -> \([^ ]*\) FAILED .*/\1/p' "$PLAN_DIR/submit.out" 2>/dev/null || true)"
    for ticker in $failed_submits; do
      rc_total=$((rc_total + 1))
      failed_tickers="${failed_tickers} ${ticker}"
    done
    # Ended some other way than reporting its tickers (a crash, its timeout):
    # whatever it had not reached yet was not sent.
    if [[ "$rc" != "0" && ( -z "$failed_submits" || "$rc" != "1" ) ]]; then
      echo "  -> submit pass FAILED (rc=$rc) — tickers after the last one reported were not sent" \
        | tee -a "$RUN_LOG"
      rc_total=$((rc_total + 1))
      failed_tickers="${failed_tickers} submit-pass"
    fi
  fi
  trap - TERM INT
}

rc_total=0
failed_tickers=""
# The sequential run, as it always was: every ticker at COUNCIL_PARALLELISM=1,
# none once the two passes have run.
SEQUENTIAL="$DECIDE"
if [[ "$COUNCIL_PARALLELISM" -gt 1 && -n "$DECIDE" ]]; then
  decide_in_parallel
  SEQUENTIAL=""
fi
for TICKER in $SEQUENTIAL; do
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
# Order-flow health check. A run where every decision is refused exits 0 and
# logs clean — a policy refusal is a success by design — so nothing else in
# this script would ever mention that the book stopped reaching the broker.
# Not under PAUSE_NEW, and below the FLATTEN_ALL exit: such a book is inert on
# purpose and must not page. Always exits 0 (see the script).
if [[ "$PAUSED" -eq 0 ]]; then
  PYTHONPATH=.:vendor/tradingagents "$PYTHON" -m scripts.inert_alert 2>&1 | tee -a "$RUN_LOG" || true
fi

echo "" | tee -a "$RUN_LOG"
# Stop-coverage check. `risk.stop_coverage` could always compute how much of the
# book is unprotected and nothing ever asked it — the first run of the position
# pass found 75.5% naked. It then ran here behind `|| true` and exited 0 either
# way, so a naked book was a log line. Exit 3 now means shares are held with no
# protective stop, any other non-zero that coverage is unknown; both page on
# every run they persist, and neither stops the rest of this script. A lot the
# position pass time-exited tonight is not naked: its stops are gone, but our
# own exit reserves every share until the open sells them. It is counted as
# `exiting` on the coverage line, and pages only if it is not that exit (a sell
# that is not ours, one for fewer shares than held, one an open has met).
naked_rc=0
naked_out="$(PYTHONPATH=.:vendor/tradingagents "$PYTHON" -m scripts.naked_alert 2>&1)" || naked_rc=$?
printf '%s\n' "$naked_out" | tee -a "$RUN_LOG"
if [[ "$naked_rc" -eq 3 ]]; then
  naked_detail="$(printf '%s\n' "$naked_out" | sed -n '/^NAKED: /{s/^NAKED: //;p;q;}' || true)"
  notify_ops --kind naked_book \
    --title "⚠️ Book has shares with no protective stop" \
    --body "${naked_detail:-see the stop coverage line in ${RUN_LOG}} @ ${DATE}"
elif [[ "$naked_rc" -ne 0 ]]; then
  notify_ops --kind naked_book \
    --title "⚠️ Stop-coverage check failed (rc=$naked_rc)" \
    --body "Coverage of the book is unknown @ ${DATE}; see ${RUN_LOG}"
fi

PAUSED_NOTE=""
if [[ "$PAUSED" -eq 1 ]]; then
  PAUSED_NOTE=" PAUSE_NEW: no decisions, positions managed."
fi
echo "" | tee -a "$RUN_LOG"
echo "Daily run complete. $rc_total ticker(s) errored.${PAUSED_NOTE}" | tee -a "$RUN_LOG"

if [[ "$rc_total" -gt 0 ]]; then
  # Alert the human (best-effort — notify_ops always exits 0) and exit
  # non-zero so systemd marks the unit failed and OnFailure= fires too.
  notify_ops --kind daily_run \
    --title "⚠️ Daily run: ${rc_total} ticker(s) failed" \
    --body "Failed:${failed_tickers} @ ${DATE}"
  ping_healthcheck fail "${rc_total} ticker(s) failed:${failed_tickers} @ ${DATE}"
  exit 1
fi

ping_healthcheck "" "Daily run complete @ ${DATE}. 0 ticker(s) errored.${PAUSED_NOTE}"

exit 0
