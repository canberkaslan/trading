"""Credentials must never reach a log line."""

from __future__ import annotations

import io
import logging
import sys
from pathlib import Path

import pytest
import requests

from tradingagents_us import log_redaction
from tradingagents_us.log_redaction import RedactingFilter, install, redact

_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "tradingagents"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))


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


# --- Exceptions whose text is a keyed URL ------------------------------------
#
# The leak that brought these in: the vendor fallback logged a FRED 502 as
#   Vendor 'fred' failed for get_macro_indicators: 502 Server Error: Bad
#   Gateway for url: https://api.stlouisfed.org/...&api_key=<the key>&...
# The exception object was the argument, not a string, and the line came from
# a child logger propagating to root, so neither half of the old filter saw it.

# A planted value, not a credential: shaped so a secret scanner does not read it as one.
_PLANTED = "planted-value-for-this-test"
_URL = (
    "https://api.stlouisfed.org/fred/series/observations"
    f"?series_id=DGS10&observation_start=2025-07-21&api_key={_PLANTED}&file_type=json"
)


def _http_error() -> requests.HTTPError:
    """The error `raise_for_status()` raises for a 502 on a keyed URL."""
    response = requests.Response()
    response.status_code = 502
    response.reason = "Bad Gateway"
    response.url = _URL
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        return exc
    raise AssertionError("raise_for_status did not raise")


def _connection_error() -> requests.ConnectionError:
    """The shape urllib3 gives a connection failure: the path and query, key included."""
    path = _URL.split("api.stlouisfed.org", 1)[1]
    return requests.ConnectionError(
        f"HTTPSConnectionPool(host='api.stlouisfed.org', port=443): "
        f"Max retries exceeded with url: {path}"
    )


@pytest.fixture
def installed():
    """install() for one test, then put logging back as it was."""
    root = logging.getLogger()
    factory = logging.getLogRecordFactory()
    root_filters = list(root.filters)
    handler_filters = {h: list(h.filters) for h in root.handlers}
    install()
    yield
    logging.setLogRecordFactory(factory)
    root.filters[:] = root_filters
    for handler, filters in handler_filters.items():
        handler.filters[:] = filters


def _capture(logger: logging.Logger) -> io.StringIO:
    """A handler attached AFTER install(), the case a handler filter never covered."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s | %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    return stream


class TestExceptionsCarryingAKey:
    def test_the_exception_text_really_carries_the_key(self) -> None:
        # Guards the premise: if requests stopped putting the URL in the
        # message, the tests below would pass without testing anything.
        assert _PLANTED in str(_http_error())
        assert _PLANTED in str(_connection_error())

    def test_the_vendor_fallback_line_that_leaked(self, installed, monkeypatch) -> None:
        from tradingagents.dataflows import interface

        def fred(*_a: object, **_k: object) -> str:
            raise _http_error()

        monkeypatch.setitem(
            interface.VENDOR_METHODS, "get_macro_indicators",
            {"fred": fred, "backup": lambda *_a, **_k: "ok"},
        )
        monkeypatch.setattr(interface, "get_vendor", lambda *_a, **_k: "fred,backup")
        stream = _capture(logging.getLogger("tradingagents.dataflows.interface"))

        assert interface.route_to_vendor("get_macro_indicators") == "ok"

        line = stream.getvalue()
        assert "Vendor 'fred' failed for get_macro_indicators" in line
        assert "502 Server Error" in line
        assert "/fred/series/observations?series_id=DGS10" in line
        assert _PLANTED not in line
        assert "api_key=<redacted>" in line

    def test_an_exception_argument_on_a_child_logger(self, installed) -> None:
        stream = _capture(logging.getLogger("t.child.deep"))
        logging.getLogger("t.child.deep").warning("failed: %s", _connection_error())
        assert _PLANTED not in stream.getvalue()
        assert "Max retries exceeded" in stream.getvalue()

    def test_a_traceback_attached_with_exc_info(self, installed) -> None:
        log = logging.getLogger("t.exc_info")
        stream = _capture(log)
        try:
            raise _http_error()
        except requests.HTTPError:
            log.exception("fetch failed")
        out = stream.getvalue()
        assert "Traceback" in out and "HTTPError" in out
        assert _PLANTED not in out

    def test_an_exception_logged_as_the_message(self, installed) -> None:
        log = logging.getLogger("t.msg_obj")
        stream = _capture(log)
        log.error(_http_error())
        assert "502 Server Error" in stream.getvalue()
        assert _PLANTED not in stream.getvalue()

    def test_repr_formatting_keeps_its_shape(self, installed) -> None:
        log = logging.getLogger("t.repr")
        stream = _capture(log)
        log.warning("got %r", _connection_error())
        out = stream.getvalue()
        assert "ConnectionError(" in out
        assert _PLANTED not in out

    def test_arguments_without_a_key_are_left_as_they_are(self, installed) -> None:
        err = ValueError("no url here")
        record = logging.getLogger("t.plain").makeRecord(
            "t.plain", logging.INFO, __file__, 1, "n=%d x=%.1f e=%s", (5, 2.5, err), None,
        )
        assert record.args == (5, 2.5, err)
        assert record.args[2] is err
        assert record.getMessage() == "n=5 x=2.5 e=no url here"

    def test_the_record_factory_is_wrapped_once(self, installed) -> None:
        factory = logging.getLogRecordFactory()
        install()
        install()
        assert logging.getLogRecordFactory() is factory
        assert getattr(factory, "_redacting", False)

    def test_redact_value_leaves_numbers_for_numeric_formats(self) -> None:
        assert log_redaction._redact_value(7) == 7
        assert log_redaction._redact_value(None) is None
