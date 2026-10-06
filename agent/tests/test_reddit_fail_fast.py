"""Reddit fails fast: 5 s timeouts and a run-wide breaker; the 429 back-off is the vendor's.

Drives the real vendored fetcher through the sentiment supplement, with
`urlopen` and the fetcher's clock mocked: nothing reaches Reddit, nothing
sleeps for real.
"""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

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


def test_a_retry_after_is_honoured_before_the_one_retry(
    monkeypatch: pytest.MonkeyPatch, slept: list[float]
) -> None:
    opener = _Urlopen(_429("60"))
    monkeypatch.setattr(vendor, "urlopen", opener)
    _fetch("NVDA")
    # One retry on the first subreddit, none on the rest (the vendor's rule).
    assert len(opener.timeouts) == SUBS + 1
    assert max(slept) == 60.0


def test_a_headerless_429_waits_the_vendors_minute(
    monkeypatch: pytest.MonkeyPatch, slept: list[float]
) -> None:
    # The vendor measured a retry 8, 10 or 30 s later still 429s.
    monkeypatch.setattr(vendor, "urlopen", _Urlopen(_429(None)))
    _fetch("NVDA")
    assert max(slept) >= vendor._RETRY_FALLBACK_SECONDS * 0.8


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


_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Earnings thread</title>
    <published>2026-10-05T14:00:00+00:00</published>
    <content type="html">&lt;p&gt;numbers look fine&lt;/p&gt;</content>
  </entry>
</feed>"""


class _Feed:
    """Reddit answering every search with a post, as its RSS feed serves it."""

    def __init__(self, refuse_first: int = 0) -> None:
        self.refuse_first = refuse_first
        self.calls = 0

    def __call__(self, req: Any, timeout: float) -> Any:
        self.calls += 1
        if self.calls <= self.refuse_first:
            raise _429("60")
        return io.BytesIO(_FEED)


def test_a_healthy_reddit_is_never_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, slept: list[float]
) -> None:
    # Every block the RSS path returns says "scores/comments unavailable" in
    # its header. That is a success, and it must not count toward the breaker.
    monkeypatch.setenv(fail_fast.RUN_STATE_DIR_ENV, str(tmp_path))
    opener = _Feed()
    monkeypatch.setattr(vendor, "urlopen", opener)
    tickers = ("SPY", "AAPL", "MSFT", "NVDA")
    for ticker in tickers:
        out = _fetch(ticker)
        assert "Reddit skipped" not in out
        assert f"recent posts mentioning {ticker}" in out
    assert opener.calls == len(tickers) * SUBS
    assert not fail_fast.RunBreaker("reddit", ss._FAILURE_LIMIT).is_open()


def test_one_refused_subreddit_is_not_a_refused_ticker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, slept: list[float]
) -> None:
    # The limiter let the other subreddits through: it does not have us, and
    # the next ticker is worth asking.
    monkeypatch.setenv(fail_fast.RUN_STATE_DIR_ENV, str(tmp_path))
    for ticker in ("AAPL", "MSFT", "NVDA"):
        monkeypatch.setattr(vendor, "urlopen", _Feed(refuse_first=2))  # the 429 and its retry
        assert "recent posts mentioning" in _fetch(ticker)
    assert not fail_fast.RunBreaker("reddit", ss._FAILURE_LIMIT).is_open()


class _Limiter:
    """Reddit's per-IP limiter as the vendor measured it, on a fake clock.

    It refuses the first search for each ticker, and anything within
    `WINDOW_S` of a refusal: a retry 8, 10 or 30 s later still gets a 429, one
    a minute later goes through (vendor reddit.py, `_RETRY_FALLBACK_SECONDS`).
    """

    WINDOW_S = 45.0

    def __init__(self) -> None:
        self.now = 0.0
        self.last_refusal: float | None = None
        self.seen: set[str] = set()

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self, req: Any, timeout: float) -> Any:
        ticker = parse_qs(urlparse(req.full_url).query)["q"][0]
        recent = self.last_refusal is not None and self.now - self.last_refusal < self.WINDOW_S
        if ticker not in self.seen or recent:
            self.seen.add(ticker)
            self.last_refusal = self.now
            raise _429(None)
        return io.BytesIO(_FEED)


def test_a_night_of_first_search_429s_still_reads_every_ticker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The box on 2026-10-05: a 429 and a back-off of about a minute per
    # ticker, and the retry got through. A retry the limiter is known to
    # refuse would lose the posts, then open the breaker for the whole run.
    monkeypatch.setenv(fail_fast.RUN_STATE_DIR_ENV, str(tmp_path))
    limiter = _Limiter()
    monkeypatch.setattr(vendor, "urlopen", limiter)
    monkeypatch.setattr(
        vendor, "time",
        SimpleNamespace(sleep=limiter.sleep, strftime=time.strftime, gmtime=time.gmtime),
    )
    tickers = ("SPY", "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "JPM",
               "XOM", "UNH")
    read = [t for t in tickers if f"recent posts mentioning {t}" in _fetch(t)]
    assert read == list(tickers)
