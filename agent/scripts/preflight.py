#!/usr/bin/env python3
"""Pre-run preflight canary — validate dependencies BEFORE the daily run.

Runs ~45 min before the 22:30 UTC daily run (systemd timer). A broken API
key discovered mid-run costs a full eval trading day out of 10, plus the
LLM spend — this catches it while there's still time to fix.

Alert-only: it never blocks or gates the daily run. On any hard failure it
sends an ops alert (push AND a GitHub issue, see notifications.ops_channel)
and exits 1 (visible in systemctl status); on success it exits 0 quietly.

Hard checks: Alpaca (account + paper URL), Anthropic key, Polygon, Finnhub,
OpenRouter (only when LLM_COUNCIL=1), DB writable, disk. FRED is warn-only.

Alerting gaps (no HEALTHCHECK_URL; no GitHub alert token, or one GitHub
refuses or that expires within days) are reported separately and do NOT set
the exit code. Exit 1 keeps meaning "a key or dependency the run needs is
broken", so OnFailure and systemctl status stay worth reading on a box that
has not set the alert accounts up yet. The gaps still reach a human: every run
records its result (monitoring.alerting_state), /readyz serves it, and the
off-box watchdog opens an incident, on a path that needs no secret on the box.
Reporting a dead alert channel through that same channel is how the
2026-09-14 outage stayed quiet.
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import UTC

import httpx

from tradingagents_us.log_redaction import install as install_log_redaction

# (name, hard) — hard failures alert; soft ones only log.
Failure = tuple[str, str]


def _check_alpaca(failures: list[Failure]) -> None:
    try:
        from tradingagents_us.dataflows.alpaca_broker import AlpacaClient

        with AlpacaClient() as ac:
            acct = ac.account()
            # Check the RESOLVED base the client actually routes to (the env
            # var may be unset — the client defaults to paper).
            base = str(getattr(ac, "base_url", os.environ.get("ALPACA_BASE_URL", "")))
        # Assert routing matches the DECLARED mode, not hardcoded paper —
        # otherwise go-live day turns this canary into a daily false alarm.
        expected = os.environ.get("EXPECTED_TRADING_MODE", "paper").strip().lower()
        is_paper = "paper" in base
        if expected == "live" and is_paper:
            failures.append(
                ("alpaca", f"EXPECTED_TRADING_MODE=live but routing to paper: {base!r}")
            )
        elif expected != "live" and not is_paper:
            failures.append(
                ("alpaca", f"base URL is NOT paper: {base!r} (EXPECTED_TRADING_MODE={expected})")
            )
        status = getattr(acct, "status", "")
        if status and str(status).upper() != "ACTIVE":
            failures.append(("alpaca", f"account status={status}"))
    except Exception as exc:
        failures.append(("alpaca", f"account check failed: {exc}"))


def _check_anthropic(failures: list[Failure]) -> None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        failures.append(("anthropic", "ANTHROPIC_API_KEY unset"))
        return
    try:
        r = httpx.get(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            timeout=15.0,
        )
        if r.status_code in (401, 403):
            failures.append(("anthropic", f"key rejected (HTTP {r.status_code})"))
        # 5xx / rate limits are Anthropic's problem, not a broken key — don't alert.
    except Exception as exc:
        print(f"preflight: anthropic unreachable (soft): {exc}", file=sys.stderr)


def _check_polygon(failures: list[Failure]) -> None:
    try:
        from tradingagents_us.dataflows.polygon import PolygonClient

        with PolygonClient() as pc:
            resp = pc.previous_close("SPY")
        if not (resp.get("results") or []):
            failures.append(("polygon", f"prev-close empty: {str(resp)[:120]}"))
    except Exception as exc:
        failures.append(("polygon", f"check failed: {exc}"))


def _check_finnhub(failures: list[Failure]) -> None:
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        # Optional in the pipeline (finnhub.py degrades to placeholders) —
        # unset must not page anyone; only a REJECTED configured key alerts.
        print(
            "preflight: FINNHUB_API_KEY unset (soft — pipeline uses placeholders)",
            file=sys.stderr,
        )
        return
    try:
        r = httpx.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": "AAPL"},
            headers={"X-Finnhub-Token": key},
            timeout=15.0,
        )
        if r.status_code in (401, 403):
            failures.append(("finnhub", f"key rejected (HTTP {r.status_code})"))
    except Exception as exc:
        print(f"preflight: finnhub unreachable (soft): {exc}", file=sys.stderr)


def _check_openrouter(failures: list[Failure]) -> None:
    if os.environ.get("LLM_COUNCIL") != "1":
        return
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        failures.append(("openrouter", "LLM_COUNCIL=1 but OPENROUTER_API_KEY unset"))
        return
    try:
        r = httpx.get(
            "https://openrouter.ai/api/v1/key",
            headers={"Authorization": f"Bearer {key}"},
            timeout=15.0,
        )
        if r.status_code in (401, 403):
            failures.append(("openrouter", f"key rejected (HTTP {r.status_code})"))
    except Exception as exc:
        print(f"preflight: openrouter unreachable (soft): {exc}", file=sys.stderr)


def _check_fred() -> None:
    """Warn-only — the risk-free rate falls back gracefully without FRED."""
    try:
        from datetime import datetime, timedelta

        from tradingagents_us.dataflows.fred import FREDClient

        today = datetime.now(UTC).date()
        with FREDClient() as fc:
            fc.series("DGS3MO", start=today - timedelta(days=14), end=today)
    except Exception as exc:
        print(f"preflight: FRED check failed (soft): {exc}", file=sys.stderr)


def _check_db(failures: list[Failure]) -> None:
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import make_url

        url = os.environ.get("TRADE_LOG_DB_URL", "sqlite:///./local.db")
        u = make_url(url)
        # sqlite connect() auto-creates a missing file — check existence
        # FIRST or a mistyped URL passes while pointing at a fresh empty DB
        # (losing client_order_id dedupe history and device tokens).
        if u.get_backend_name() == "sqlite" and u.database:
            db_path = u.database
            if not os.path.exists(db_path):
                failures.append(("db", f"{db_path} does not exist (mistyped TRADE_LOG_DB_URL?)"))
                return
            if not os.access(db_path, os.W_OK):
                failures.append(("db", f"{db_path} not writable"))
        engine = create_engine(url, future=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        failures.append(("db", f"connect failed: {exc}"))


def _check_disk(failures: list[Failure], min_free_gb: float = 5.0) -> None:
    try:
        usage = shutil.disk_usage(os.getcwd())
        free_gb = usage.free / 1e9
        if free_gb < min_free_gb:
            failures.append(("disk", f"only {free_gb:.1f}GB free (< {min_free_gb}GB)"))
    except Exception as exc:
        failures.append(("disk", f"check failed: {exc}"))


def _check_alerting(gaps: list[Failure]) -> None:
    """The two ways a problem on this box reaches a human without the phone.

    Both depend on an account created outside this repo, so a deploy cannot
    require them. Gaps go into their own list: they are recorded for /readyz
    and the watchdog and ride along in a hard-failure alert, but they never
    make preflight fail on their own.

    Neither check sends anything. A preflight ping to HEALTHCHECK_URL would
    record a daily run that has not happened yet, which defeats the point of
    it; the GitHub token is exercised with a read of the alert workflow.
    """
    from tradingagents_us.notifications.ops_channel import check_github_channel

    hc = os.environ.get("HEALTHCHECK_URL", "").strip()
    if not hc:
        gaps.append(
            (
                "healthcheck",
                "HEALTHCHECK_URL unset: no dead-man's switch, so a daily run that "
                "stops happening pages nobody",
            )
        )
    elif not hc.startswith(("https://", "http://")):
        # Never echo the value: the URL is a credential for reporting success.
        gaps.append(("healthcheck", "HEALTHCHECK_URL is not an http(s) URL"))

    check = check_github_channel()
    if check.ok is False:
        gaps.append(("ops_alert_channel", check.detail))
    elif check.ok is None:
        print(f"preflight: alert channel not checked (soft): {check.detail}", file=sys.stderr)


def _alert(failures: list[Failure], gaps: list[Failure]) -> None:
    body = "; ".join(f"{n}: {msg}" for n, msg in failures)
    if gaps:
        body += " | alerting gaps: " + "; ".join(f"{n}: {msg}" for n, msg in gaps)
    print(f"preflight FAILED: {body}", file=sys.stderr)
    try:
        from tradingagents_us.notifications.ops_channel import send_ops_alert

        delivery = send_ops_alert(f"⚠️ Preflight FAILED ({len(failures)})", body, kind="preflight")
        print(f"preflight: alert {delivery.describe()}", file=sys.stderr)
    except Exception as exc:
        print(f"preflight: alert failed: {exc}", file=sys.stderr)


def _record(failures: list[Failure], gaps: list[Failure]) -> None:
    """Leave this run's result where /readyz serves it. Never fails preflight."""
    from datetime import datetime

    from tradingagents_us.monitoring.alerting_state import PreflightRecord, write_preflight

    record = PreflightRecord.from_names(
        datetime.now(UTC).isoformat(timespec="seconds"),
        [n for n, _ in failures],
        [n for n, _ in gaps],
    )
    try:
        write_preflight(record)
    except Exception as exc:
        print(f"preflight: could not record the result for /readyz: {exc}", file=sys.stderr)


def main() -> int:
    install_log_redaction()
    failures: list[Failure] = []
    gaps: list[Failure] = []
    _check_alerting(gaps)
    _check_alpaca(failures)
    _check_anthropic(failures)
    _check_polygon(failures)
    _check_finnhub(failures)
    _check_openrouter(failures)
    _check_fred()
    _check_db(failures)
    _check_disk(failures)

    _record(failures, gaps)
    if gaps:
        print(
            "preflight: alerting gaps (served on /readyz for the watchdog; not a failure): "
            + "; ".join(f"{n}: {msg}" for n, msg in gaps),
            file=sys.stderr,
        )
    if failures:
        _alert(failures, gaps)
        return 1
    print("preflight OK — all dependency checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
