#!/usr/bin/env bash
# One-shot installer for the AI Trader daily paper-trading job on a
# Hetzner (or any systemd Linux) box. Run AS the deploy user with sudo.
#
#   ssh deploy@<box>
#   curl -fsSL https://raw.githubusercontent.com/canberkaslan/trading/main/deploy/hetzner/install.sh | bash
#   # then: edit /opt/ai-trader/secrets.env, fill in the keys
#   sudo systemctl enable --now ai-trader.timer
#
# Idempotent: re-running pulls latest code and re-installs units.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/canberkaslan/trading.git}"
APP_DIR="/opt/ai-trader"
AGENT_DIR="${APP_DIR}/agent"

echo "==> AI Trader installer"
echo "    repo:    ${REPO_URL}"
echo "    target:  ${APP_DIR}"

# 1. System deps -----------------------------------------------------------
if ! command -v git >/dev/null;    then sudo apt-get update -y && sudo apt-get install -y git;    fi
if ! command -v python3 >/dev/null; then sudo apt-get install -y python3 python3-venv python3-pip; fi
if ! command -v uv >/dev/null; then
  echo "==> installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# 2. Code ------------------------------------------------------------------
sudo mkdir -p "${APP_DIR}"
sudo chown "$(whoami)":"$(whoami)" "${APP_DIR}"
if [[ -d "${APP_DIR}/.git" ]]; then
  echo "==> pulling latest"
  git -C "${APP_DIR}" fetch --depth 1 origin main
  git -C "${APP_DIR}" reset --hard origin/main
else
  echo "==> cloning"
  git clone --depth 1 "${REPO_URL}" "${APP_DIR}"
fi

# 3. Python env ------------------------------------------------------------
cd "${AGENT_DIR}"
echo "==> creating venv + installing deps"
uv venv
uv pip install -e . \
  httpx boto3 pandas pyarrow vectorbt \
  langchain-anthropic langchain-openai langgraph langgraph-checkpoint-sqlite \
  yfinance stockstats lxml beautifulsoup4 \
  fastapi "uvicorn[standard]" "python-jose[cryptography]" \
  sqlalchemy "psycopg[binary]" 2>&1 | tail -5 || true

# Vendored upstream comes with the repo (git subtree under agent/vendor).

# 4. Secrets template ------------------------------------------------------
if [[ ! -f "${APP_DIR}/secrets.env" ]]; then
  echo "==> writing secrets template (FILL THIS IN)"
  cat > "${APP_DIR}/secrets.env" <<'EOF'
# AI Trader secrets — chmod 600, never commit.
ANTHROPIC_API_KEY=
POLYGON_API_KEY=
FRED_API_KEY=
ALPACA_API_KEY=
ALPACA_API_SECRET=
ALPACA_BASE_URL=https://paper-api.alpaca.markets/v2
SEC_EDGAR_USER_AGENT="AI Trader you@example.com"

# TradingAgents upstream model routing
TRADINGAGENTS_LLM_PROVIDER=anthropic
TRADINGAGENTS_DEEP_THINK_LLM=claude-opus-4-7
TRADINGAGENTS_QUICK_THINK_LLM=claude-sonnet-4-6
TRADINGAGENTS_OUTPUT_LANGUAGE=English
TRADINGAGENTS_MAX_DEBATE_ROUNDS=1
TRADINGAGENTS_MAX_RISK_ROUNDS=1

# Daily run config — full production universe; a reinstall must not
# silently shrink the eval book. (Quoted: valid for BOTH systemd
# EnvironmentFile= and `source` in a shell.)
UNIVERSE="SPY AAPL MSFT NVDA GOOGL AMZN META JPM V XOM UNH"
SUBMIT=1
PYTHON=/opt/ai-trader/agent/.venv/bin/python

# Alerting that does not depend on the phone app. Both need an account, so a
# blank value never fails an install, a redeploy or preflight's exit code.
# Instead preflight records each gap, /readyz serves it, and the off-box
# watchdog keeps an incident open (as github-actions[bot]) until it is filled in.
#
# Dead-man's switch: a healthchecks.io check with schedule "30 22 * * 1-5" (UTC)
# and a grace period of about 7h (the run unit's 6h TimeoutStartSec plus margin).
# daily_run.sh pings the URL on success and <url>/fail on failure. A run that
# never happens sends nothing, and healthchecks.io pages on that silence.
HEALTHCHECK_URL=
# Box alerts (preflight, failed runs, naked book, kill switch, backup) are also
# filed as GitHub issues. The box dispatches .github/workflows/box-alert.yml and
# the workflow writes the issue as github-actions[bot]: GitHub does not notify
# you about issues filed with your own token, so the box must not write them.
# Fine-grained PAT on the alert repo only, permission Actions: Read and write
# (that also lets it dispatch the repo's other dispatchable workflows). Mind its
# expiry: preflight reads the workflow with it and reports a refused or
# soon-expiring token.
OPS_ALERT_GITHUB_TOKEN=
# Defaults to the repo the off-box watchdog files incidents on, which is public.
# A private repo keeps book details off a public page and narrows what the
# token can dispatch; it must carry box-alert.yml and scripts/box_alert_issue.py.
#OPS_ALERT_GITHUB_REPO=canberkaslan/trading

# Local trade-log DB (sqlite on the box)
TRADE_LOG_DB_URL=sqlite:////opt/ai-trader/agent/local.db

# Kill-switch flag file — MUST be the same absolute path for the API
# (writer) and the trading scripts (readers). Code defaults to the agent
# root, but pin it explicitly so a relocated process can never split them.
KILL_SWITCH_PATH=/opt/ai-trader/agent/kill_switch.state

# Trading mode the preflight canary asserts against ('paper' | 'live').
# Flip to live TOGETHER with ALPACA_BASE_URL on go-live day.
EXPECTED_TRADING_MODE=paper

# Commentator feed (docs/adr/009-commentator-feed.md). Off unless exactly 1;
# each source is skipped, with a log line, while its key is blank. X also needs
# a numeric user id pinned once (never a handle), and stores no post until
# ai-trader-commentator-retention.timer (the daily deletion check) has run.
#COMMENTATOR_FEED=1
#YOUTUBE_API_KEY=
#X_BEARER_TOKEN=
#COMMENTATOR_X_USER_ID=
EOF
  chmod 600 "${APP_DIR}/secrets.env"
else
  echo "==> secrets.env already exists, leaving it alone"
fi

# Backup destination template (optional — backup.py no-ops without it, so
# the timer would run green while backing up NOTHING; fill it in).
if [[ ! -f "${APP_DIR}/backup.env" ]]; then
  cat > "${APP_DIR}/backup.env" <<'EOF'
# Off-box backup destinations — configure at least one.
# Git: local clone of a PRIVATE repo with a write deploy key.
#BACKUP_GIT_DIR=/opt/ai-trader/backups
# S3: bucket + dedicated put-only credentials.
#BACKUP_S3_BUCKET=
#BACKUP_S3_PREFIX=ai-trader
#BACKUP_AWS_ACCESS_KEY_ID=
#BACKUP_AWS_SECRET_ACCESS_KEY=
#BACKUP_AWS_REGION=eu-west-1
EOF
  chmod 600 "${APP_DIR}/backup.env"
fi

# An existing secrets.env is never rewritten, so a box installed before these
# settings existed will not have them. Say so here; never fail the install.
for var in HEALTHCHECK_URL OPS_ALERT_GITHUB_TOKEN; do
  if ! grep -Eq "^${var}=[\"']?[^\"'[:space:]]" "${APP_DIR}/secrets.env"; then
    echo "==> WARNING: ${var} is not set in ${APP_DIR}/secrets.env;" \
      "box alerts reach only the phone, and the watchdog will open an incident"
  fi
done

# 5. systemd units ---------------------------------------------------------
echo "==> installing systemd units"
for unit in ai-trader.service ai-trader.timer ai-trader-alert.service \
            ai-trader-preflight.service ai-trader-preflight.timer \
            ai-trader-backup.service ai-trader-backup.timer \
            ai-trader-reconcile.service ai-trader-reconcile.timer \
            ai-trader-commentator-retention.service \
            ai-trader-commentator-retention.timer \
            ai-trader-api.service eval-report.service eval-report.timer; do
  sudo cp "${APP_DIR}/deploy/hetzner/${unit}" "/etc/systemd/system/${unit}"
done
sudo systemctl daemon-reload

echo ""
echo "============================================================"
echo " Install complete."
echo ""
echo " NEXT:"
echo "   1. Fill in secrets:   nano ${APP_DIR}/secrets.env"
echo "        (incl. HEALTHCHECK_URL + OPS_ALERT_GITHUB_TOKEN: alerts off the phone)"
echo "   2. Test one ticker:   cd ${AGENT_DIR} && \\"
echo "        set -a && source ${APP_DIR}/secrets.env && set +a && \\"
echo "        UNIVERSE='AAPL' SUBMIT=0 bash scripts/daily_run.sh"
echo "   3. Enable the timers: sudo systemctl enable --now ai-trader.timer \\"
echo "        ai-trader-preflight.timer ai-trader-backup.timer \\"
echo "        ai-trader-reconcile.timer ai-trader-commentator-retention.timer"
echo "   4. Check schedule:    systemctl list-timers 'ai-trader*'"
echo "   5. Watch a run:       journalctl -u ai-trader.service -f"
echo "============================================================"
