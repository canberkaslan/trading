"""Ticker search — finding a stock instead of remembering its symbol.

Analysing any US stock always worked: /v1/analyze never checked a universe,
only the shape of the symbol. What did not work was FINDING one, because the
Ask screen is a text box. For the eleven names in the daily run that is fine;
for the other twelve thousand it is the whole obstacle.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.routes.tickers import search

ROWS = [
    {"ticker": "AAPL", "name": "Apple Inc."},
    {"ticker": "AAPLW", "name": "Apple Inc. Warrants"},
    {"ticker": "MSFT", "name": "Microsoft Corporation"},
    {"ticker": "GOOGL", "name": "Alphabet Inc. Class A"},
    {"ticker": "SNAP", "name": "Snap Inc."},
    {"ticker": "PLTR", "name": "Palantir Technologies"},
]


class TestRanking:
    def test_a_symbol_prefix_beats_a_name_match(self) -> None:
        # Someone typing "SNAP" means the ticker, not a company with "snap"
        # somewhere in its name. Burying the obvious answer teaches the
        # operator to stop using the search.
        assert search(ROWS, "SNAP")[0]["ticker"] == "SNAP"

    def test_the_shorter_symbol_wins(self) -> None:
        # "AAPL" must beat "AAPLW" for the query "AAPL".
        assert [r["ticker"] for r in search(ROWS, "AAPL")] == ["AAPL", "AAPLW"]

    def test_a_company_name_is_findable(self) -> None:
        # The point of the feature: you know the company, not the symbol.
        assert [r["ticker"] for r in search(ROWS, "Microsoft")] == ["MSFT"]

    def test_partial_names_work(self) -> None:
        assert "PLTR" in [r["ticker"] for r in search(ROWS, "palantir")]

    def test_case_does_not_matter(self) -> None:
        assert search(ROWS, "aapl")[0]["ticker"] == "AAPL"


class TestEdges:
    def test_an_empty_query_returns_nothing(self) -> None:
        assert search(ROWS, "") == []
        assert search(ROWS, "   ") == []

    def test_no_match_is_an_empty_list(self) -> None:
        assert search(ROWS, "ZZZZZZ") == []

    def test_results_are_capped(self) -> None:
        many = [{"ticker": f"A{i:04d}", "name": f"Co {i}"} for i in range(200)]
        assert len(search(many, "A", limit=20)) == 20

    def test_a_symbol_hit_is_never_duplicated_by_a_name_hit(self) -> None:
        rows = [{"ticker": "SNAP", "name": "Snap Inc."}]
        out = search(rows, "SNAP")
        assert [r["ticker"] for r in out] == ["SNAP"]


class TestEndpoint:
    @pytest.fixture()
    def client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        monkeypatch.setenv("DEV_API_TOKEN", "t" * 64)
        monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
        import api.routes.tickers as mod

        monkeypatch.setattr(mod, "_load", lambda: ROWS)
        from api.main import app

        return TestClient(app)

    def _auth(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + "t" * 64}

    def test_requires_authentication(self, client: TestClient) -> None:
        assert client.get("/v1/tickers?q=AAPL").status_code == 401

    def test_returns_hits(self, client: TestClient) -> None:
        body = client.get("/v1/tickers?q=AAPL", headers=self._auth()).json()
        assert body[0] == {"ticker": "AAPL", "name": "Apple Inc."}

    def test_an_empty_query_is_rejected_not_silently_empty(
        self, client: TestClient
    ) -> None:
        assert client.get("/v1/tickers?q=", headers=self._auth()).status_code == 422

    def test_an_unreachable_catalogue_is_503_not_an_empty_list(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An empty list would read as "no such stock" — a false statement about
        # the market rather than a true one about us.
        import api.routes.tickers as mod

        monkeypatch.setattr(
            mod, "_load", lambda: (_ for _ in ()).throw(RuntimeError("polygon down"))
        )
        r = client.get("/v1/tickers?q=AAPL", headers=self._auth())
        assert r.status_code == 503
        assert "unavailable" in r.json()["detail"]
