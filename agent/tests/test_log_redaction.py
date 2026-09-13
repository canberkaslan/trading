"""Credentials must never reach a log line."""

from __future__ import annotations

import logging

from tradingagents_us.log_redaction import RedactingFilter, install, redact


class TestRedact:
    def test_the_real_leak_that_prompted_this(self) -> None:
        # Verbatim shape of the httpx INFO line seen in production.
        line = (
            "HTTP Request: GET https://api.polygon.io/v2/aggs/ticker/NVDA/prev"
            "?apiKey=SECRETVALUE123 \"HTTP/1.1 200 OK\""
        )
        out = redact(line)
        assert "SECRETVALUE123" not in out
        assert "apiKey=<redacted>" in out
        # The rest of the line must survive — a redacted URL is still useful.
        assert "api.polygon.io/v2/aggs/ticker/NVDA/prev" in out
        assert "200 OK" in out

    def test_is_case_insensitive_across_spellings(self) -> None:
        for param in ("apiKey", "APIKEY", "api_key", "token", "secret", "password"):
            assert "xyzzy" not in redact(f"https://h/p?{param}=xyzzy")

    def test_a_following_parameter_survives(self) -> None:
        out = redact("https://h/p?apiKey=SECRET&symbol=NVDA&limit=5")
        assert "SECRET" not in out
        assert "symbol=NVDA" in out
        assert "limit=5" in out

    def test_bearer_tokens_are_redacted(self) -> None:
        out = redact("Authorization: Bearer abcdef1234567890TOKEN")
        assert "abcdef1234567890TOKEN" not in out
        assert "<redacted>" in out

    def test_it_shows_that_a_parameter_was_present(self) -> None:
        # A redacted URL must not read like a request that carried no key —
        # otherwise a genuinely keyless call looks identical to a censored one.
        assert "apiKey=" in redact("https://h/p?apiKey=S")

    def test_ordinary_text_is_untouched(self) -> None:
        line = "councilling NVDA: holding 55 shares — exit path must stay open"
        assert redact(line) == line


class TestFilter:
    def test_redacts_the_message(self, caplog) -> None:
        log = logging.getLogger("t.msg")
        log.addFilter(RedactingFilter())
        with caplog.at_level(logging.INFO):
            log.info("GET https://h/p?apiKey=LEAKME")
        assert "LEAKME" not in caplog.text

    def test_redacts_lazy_format_arguments(self, caplog) -> None:
        # The leak arrives as `log.info("HTTP Request: %s", url)`, so rewriting
        # only record.msg would miss it entirely.
        log = logging.getLogger("t.args")
        log.addFilter(RedactingFilter())
        with caplog.at_level(logging.INFO):
            log.info("HTTP Request: %s", "https://h/p?apiKey=LEAKME")
        assert "LEAKME" not in caplog.text

    def test_never_suppresses_a_line(self, caplog) -> None:
        # This filter censors; it must not drop records on an edge case.
        log = logging.getLogger("t.keep")
        log.addFilter(RedactingFilter())
        with caplog.at_level(logging.INFO):
            log.info("plain line")
        assert "plain line" in caplog.text

    def test_a_non_string_argument_does_not_raise(self, caplog) -> None:
        log = logging.getLogger("t.types")
        log.addFilter(RedactingFilter())
        with caplog.at_level(logging.INFO):
            log.info("n=%d obj=%s", 5, {"apiKey": "x"})
        assert "n=5" in caplog.text


class TestInstall:
    def test_is_idempotent(self) -> None:
        root = logging.getLogger()
        before = len([f for f in root.filters if isinstance(f, RedactingFilter)])
        install()
        install()
        after = len([f for f in root.filters if isinstance(f, RedactingFilter)])
        assert after == max(1, before)
