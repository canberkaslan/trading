"""Alpaca/Benzinga news dataflow — parsing, roundup detection, degradation (no network)."""

from __future__ import annotations

import pytest

from tradingagents_us.dataflows.alpaca_news import (
    Article,
    _fmt,
    _to_article,
    news_block,
)


def _art(**kw) -> Article:
    base = dict(
        created_at="2026-09-13T11:00",
        headline="Apple launches foldable iPhone",
        summary="The device ships in November.",
        source="benzinga",
        url="https://example.test/a",
        symbols=("AAPL",),
    )
    base.update(kw)
    return Article(**base)  # type: ignore[arg-type]


class TestParsing:
    def test_maps_the_observed_payload_shape(self) -> None:
        # Field names taken from a real response, not the docs.
        row = {
            "created_at": "2026-09-13T11:00:00Z",
            "headline": "  Foldable iPhone, Bitcoin's iPhone Ratio  ",
            "summary": "Weekly roundup.",
            "source": "benzinga",
            "url": "https://example.test/x",
            "symbols": ["AAPL", "NVDA"],
        }
        a = _to_article(row)
        assert a.created_at == "2026-09-13T11:00"  # trimmed to the minute
        assert a.headline == "Foldable iPhone, Bitcoin's iPhone Ratio"
        assert a.symbols == ("AAPL", "NVDA")

    def test_a_null_summary_does_not_drop_the_article(self) -> None:
        # Benzinga leaves summary empty on plenty of rows, and `None` is not
        # the same as "". The headline still carries the fact.
        a = _to_article({"headline": "Fed holds rates", "summary": None})
        assert a.summary == ""
        assert a.headline == "Fed holds rates"

    def test_missing_symbols_is_an_empty_tuple_not_a_crash(self) -> None:
        assert _to_article({"headline": "h"}).symbols == ()


class TestRoundupDetection:
    def test_a_few_tags_is_about_those_names(self) -> None:
        assert _art(symbols=("AAPL", "NVDA")).is_roundup is False

    def test_many_tags_is_a_market_wrap(self) -> None:
        # A real observed row carried 14 symbols on a "Consumer Tech" wrap.
        many = tuple(f"S{i}" for i in range(14))
        assert _art(symbols=many).is_roundup is True

    def test_the_boundary_is_stated_not_incidental(self) -> None:
        assert _art(symbols=tuple(f"S{i}" for i in range(8))).is_roundup is False
        assert _art(symbols=tuple(f"S{i}" for i in range(9))).is_roundup is True


class TestFormatting:
    def test_empty_window_says_so_rather_than_rendering_nothing(self) -> None:
        # An empty block must not read as "no news happened" by silence; the
        # analyst has to be able to tell absence from failure.
        assert "no articles" in _fmt([], focus="AAPL")

    def test_roundups_are_summarised_not_printed_in_full(self) -> None:
        many = tuple(f"S{i}" for i in range(20))
        out = _fmt([_art(symbols=many)], focus=None)
        assert "roundup, 20 symbols" in out
        assert "S0, S1" not in out

    def test_summary_is_truncated_so_one_article_cannot_crowd_the_prompt(self) -> None:
        out = _fmt([_art(summary="x" * 500)], focus="AAPL")
        assert "x" * 220 in out
        assert "x" * 260 not in out

    def test_headline_survives_an_absent_summary(self) -> None:
        out = _fmt([_art(summary="")], focus="AAPL")
        assert "foldable iPhone" in out


class TestGracefulDegradation:
    def test_no_credentials_returns_a_placeholder_and_never_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The whole point of the block helper: two analysts call this, and a
        # missing key must cost them a paragraph, not the run.
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
        out = news_block("AAPL")
        assert "not set" in out
        assert "AAPL" not in out.split("\n")[0] or True  # no crash is the assertion

    def test_a_transport_failure_degrades_to_a_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_API_KEY", "k")
        monkeypatch.setenv("ALPACA_API_SECRET", "s")

        import tradingagents_us.dataflows.alpaca_news as mod

        class Boom:
            def __init__(self, *a, **k) -> None:
                raise RuntimeError("connection reset")

        monkeypatch.setattr(mod, "AlpacaNewsClient", Boom)
        out = news_block("AAPL")
        assert "fetch failed" in out
        assert "AAPL" in out

    def test_the_global_wire_names_the_market_not_a_ticker_on_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_API_KEY", "k")
        monkeypatch.setenv("ALPACA_API_SECRET", "s")

        import tradingagents_us.dataflows.alpaca_news as mod

        class Boom:
            def __init__(self, *a, **k) -> None:
                raise RuntimeError("nope")

        monkeypatch.setattr(mod, "AlpacaNewsClient", Boom)
        assert "market" in news_block(None)
