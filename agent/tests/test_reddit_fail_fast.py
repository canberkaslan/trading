"""Reddit fails fast: 5 s timeouts, a capped 429 back-off, a run-wide breaker.

Drives the real vendored fetcher through the sentiment supplement, with
`urlopen` and the fetcher's clock mocked: nothing reaches Reddit, nothing
sleeps for real.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.error import HTTPError

import pytest

_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "tradingagents"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from tradingagents.agents.analysts import sentiment_analyst as analyst  # noqa: E402
from tradingagents.dataflows import reddit as vendor  # noqa: E402

from tradingagents_us.dataflows import fail_fast  # noqa: E402
from tradingagents_us.dataflows import sentiment_supplement as ss  # noqa: E402

SUBS = len(vendor.DEFAULT_SUBREDDITS)


class _Urlopen:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.timeouts: list[float] = []

    def __call__(self, req: Any, timeout: float) -> Any:
        self.timeouts.append(timeout)
        raise self.error


def _429(retry_after: str | None) -> HTTPError:
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    url = "https://reddit.invalid/r/x/search.rss"
    return HTTPError(url, 429, "Too Many Requests", headers, None)  # type: ignore[arg-type]


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waits: list[float] = []
    clock = SimpleNamespace(sleep=waits.append, strftime=time.strftime, gmtime=time.gmtime)
    monkeypatch.setattr(vendor, "time", clock)
    return waits


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ss, "apewisdom_block", lambda t: f"[ApeWisdom — {t}] 3 mentions")
    monkeypatch.setattr(analyst, "fetch_reddit_posts", vendor.fetch_reddit_posts)
    monkeypatch.setattr(vendor, "_RETRY_FALLBACK_SECONDS", vendor._RETRY_FALLBACK_SECONDS)
    monkeypatch.setattr(vendor, "_retry_after_seconds", vendor._retry_after_seconds)
    ss._BREAKER.reset()
    assert ss.install()
    yield
    ss._BREAKER.reset()


def _fetch(ticker: str) -> str:
    return analyst.fetch_reddit_posts(ticker)


def test_requests_use_the_short_timeout(
    monkeypatch: pytest.MonkeyPatch, slept: list[float]
) -> None:
    opener = _Urlopen(TimeoutError("timed out"))
    monkeypatch.setattr(vendor, "urlopen", opener)
    out = _fetch("NVDA")
    assert opener.timeouts and set(opener.timeouts) == {fail_fast.HTTP_READ_TIMEOUT_S}
    assert "unavailable" in out.lower()
    assert "not an absence of discussion" in out


def test_a_long_retry_after_is_capped(
    monkeypatch: pytest.MonkeyPatch, slept: list[float]
) -> None:
    opener = _Urlopen(_429("60"))
    monkeypatch.setattr(vendor, "urlopen", opener)
    _fetch("NVDA")
    # One retry on the first subreddit, none on the rest (the vendor's rule).
    assert len(opener.timeouts) == SUBS + 1
    assert max(slept) <= fail_fast.RETRY_AFTER_CAP_S
    # Before: a 60 s sleep here, per ticker.
    assert sum(slept) <= fail_fast.RETRY_AFTER_CAP_S + (SUBS - 1) * 1.2


def test_a_headerless_429_waits_the_short_jittered_base(
    monkeypatch: pytest.MonkeyPatch, slept: list[float]
) -> None:
    monkeypatch.setattr(vendor, "urlopen", _Urlopen(_429(None)))
    _fetch("NVDA")
    assert max(slept) <= fail_fast.RETRY_BASE_S * (1 + fail_fast.JITTER_FRACTION)


def test_a_429_storm_opens_the_breaker_for_the_rest_of_the_run(
    monkeypatch: pytest.MonkeyPatch, slept: list[float]
) -> None:
    opener = _Urlopen(_429("60"))
    monkeypatch.setattr(vendor, "urlopen", opener)
    outputs = [_fetch(t) for t in ("AAPL", "MSFT", "NVDA", "GOOGL", "AMZN")]
    # Two tickers pay for the discovery; the other three make no request.
    assert len(opener.timeouts) == ss._FAILURE_LIMIT * (SUBS + 1)
    for out in outputs[ss._FAILURE_LIMIT:]:
        assert "Reddit skipped" in out
        assert "not an absence of discussion" in out
        assert "ApeWisdom" in out  # the analyst still gets the aggregate


def test_the_breaker_spans_ticker_processes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, slept: list[float]
) -> None:
    monkeypatch.setenv(fail_fast.RUN_STATE_DIR_ENV, str(tmp_path))
    earlier_tickers = fail_fast.RunBreaker("reddit", ss._FAILURE_LIMIT)
    for _ in range(ss._FAILURE_LIMIT):
        earlier_tickers.record(success=False)
    opener = _Urlopen(_429("60"))
    monkeypatch.setattr(vendor, "urlopen", opener)
    assert "Reddit skipped" in _fetch("XOM")
    assert opener.timeouts == []
