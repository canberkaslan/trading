"""A keyed vendor's error must not carry its key out of the client.

FRED, Polygon, Finnhub and Alpha Vantage take the key as a query parameter,
and `httpx`/`requests` put the request URL in the exception message. Logging
redaction only covers logging; the same text also reaches print(), the API's
503 body, the DATA_UNAVAILABLE line the agent reads (and the saved state and
reports after it) and a crash traceback. So each client scrubs what it raises.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import httpx
import pytest
import requests

from tradingagents_us.dataflows import finnhub, polygon
from tradingagents_us.dataflows.fred import FREDClient
from tradingagents_us.log_redaction import scrub_exception

_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "tradingagents"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

# A planted value, not a credential: shaped so a secret scanner does not read it as one.
PLANTED = "planted-value-for-this-test"


def _status(code: int) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(code, request=request))


class TestScrubException:
    def test_an_httpx_status_error_keeps_its_type_and_loses_the_key(self) -> None:
        request = httpx.Request("GET", f"https://api.example/x?apiKey={PLANTED}&q=1")
        response = httpx.Response(403, request=request)
        err = httpx.HTTPStatusError(f"Client error '403' for url '{request.url}'", request=request,
                                    response=response)
        assert PLANTED in str(err)  # the premise
        assert scrub_exception(err) is err
        assert PLANTED not in str(err)
        assert "apiKey=<redacted>" in str(err) and "q=1" in str(err)
        assert err.response.status_code == 403

    def test_a_non_string_argument_is_replaced_by_its_redacted_text(self) -> None:
        class PoolError(Exception):  # urllib3's MaxRetryError travels as an object
            pass

        pool = PoolError(f"Max retries exceeded with url: /s?api_key={PLANTED}")
        err = requests.ConnectionError(pool)
        scrub_exception(err)
        assert PLANTED not in str(err) and "Max retries exceeded" in str(err)

    def test_the_chained_cause_is_scrubbed_too(self) -> None:
        try:
            try:
                raise ValueError(f"inner https://h/p?token={PLANTED}")
            except ValueError as inner:
                raise RuntimeError("outer") from inner
        except RuntimeError as outer:
            scrub_exception(outer)
            assert PLANTED not in str(outer.__cause__)

    def test_text_without_a_key_is_untouched(self) -> None:
        err = ValueError("ordinary", 3)
        scrub_exception(err)
        assert err.args == ("ordinary", 3)


class TestPolygon:
    def _client(self, code: int) -> polygon.PolygonClient:
        client = polygon.PolygonClient(api_key=PLANTED)
        client._http = httpx.Client(base_url=polygon.BASE, transport=_status(code))
        return client

    def test_a_4xx_raises_without_the_key(self) -> None:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            self._client(403).previous_close("NVDA")
        assert "403" in str(caught.value)
        assert PLANTED not in str(caught.value)

    def test_retries_run_out_without_the_key(self, monkeypatch) -> None:
        monkeypatch.setattr(polygon.time, "sleep", lambda _s: None)
        with pytest.raises(RuntimeError) as caught:
            self._client(502).previous_close("NVDA")
        assert "polygon request failed" in str(caught.value)
        assert PLANTED not in str(caught.value)
        assert PLANTED not in str(caught.value.__context__ or "")


class TestFinnhub:
    def test_the_prompt_block_says_it_failed_without_the_key(self, monkeypatch) -> None:
        monkeypatch.setenv("FINNHUB_API_KEY", PLANTED)
        real = httpx.Client
        monkeypatch.setattr(
            finnhub.httpx, "Client", lambda **kw: real(transport=_status(403), **kw)
        )
        block = finnhub.finnhub_block("NVDA")
        assert block.startswith("[Finnhub] fetch failed for NVDA")
        assert PLANTED not in block


class TestFRED:
    def test_a_502_raises_without_the_key(self) -> None:
        client = FREDClient(api_key=PLANTED)
        client._http = httpx.Client(transport=_status(502))
        with pytest.raises(httpx.HTTPStatusError) as caught:
            client.series("DGS10", start=date(2026, 7, 1))
        assert "502" in str(caught.value)
        assert PLANTED not in str(caught.value)


class TestVendorFallback:
    def test_the_text_handed_to_the_agent_carries_no_key(self, monkeypatch) -> None:
        # get_macro_indicators is optional: when its only vendor fails, the
        # agent gets DATA_UNAVAILABLE with the error text, which then lands
        # in the saved state and reports.
        from tradingagents.dataflows import interface

        response = requests.Response()
        response.status_code = 502
        response.reason = "Bad Gateway"
        response.url = f"https://api.stlouisfed.org/fred/series/observations?api_key={PLANTED}"

        def fred(*_a: object, **_k: object) -> str:
            response.raise_for_status()
            return "unreachable"

        monkeypatch.setitem(interface.VENDOR_METHODS, "get_macro_indicators", {"fred": fred})
        monkeypatch.setattr(interface, "get_vendor", lambda *_a, **_k: "fred")
        text = interface.route_to_vendor("get_macro_indicators")
        assert text.startswith("DATA_UNAVAILABLE")
        assert "502 Server Error" in text
        assert PLANTED not in text


class TestSurvivorPrices:
    def test_a_5xx_raises_without_the_key(self, monkeypatch) -> None:
        from backtest import survivor_prices

        def get(url: str, params: dict, timeout: float) -> httpx.Response:
            request = httpx.Request("GET", url, params=params)
            return httpx.Response(503, request=request)

        monkeypatch.setattr(httpx, "get", get)
        with pytest.raises(httpx.HTTPStatusError) as caught:
            survivor_prices.read_issuer("NVDA", date(2026, 7, 1), api_key=PLANTED)
        assert PLANTED not in str(caught.value)
