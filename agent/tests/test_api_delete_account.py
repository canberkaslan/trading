"""DELETE /v1/me — App Store 5.1.1(v) account deletion.

The mobile app calls this BEFORE Firebase deleteUser(), so a 200 that deletes
nothing leaves the backend holding data for an identity that no longer exists
anywhere the user can reach it. These tests guard that the endpoint actually
removes the caller's rows and reports how many, rather than a fixed
`{"status": "deleted"}` regardless of what happened.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tradingagents_us.storage import TradeLogRepository
from tradingagents_us.storage.device_tokens import list_tokens_for, upsert_token


@pytest.fixture()
def repo(tmp_path: Path) -> TradeLogRepository:
    from sqlalchemy import create_engine

    return TradeLogRepository(engine=create_engine(f"sqlite:///{tmp_path/'t.db'}", future=True))


@pytest.fixture()
def client(repo: TradeLogRepository, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DEV_API_TOKEN", "t" * 64)
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)

    from api.deps import get_repo
    from api.main import app

    app.dependency_overrides[get_repo] = lambda: repo
    yield TestClient(app)
    app.dependency_overrides.pop(get_repo, None)


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer " + "t" * 64}


def test_requires_authentication(client: TestClient) -> None:
    assert client.delete("/v1/me").status_code == 401


def test_deletes_the_callers_device_tokens(
    client: TestClient, repo: TradeLogRepository
) -> None:
    with repo.session() as s:
        upsert_token(s, token="tok-mine", user_id="dev-user", platform="ios",
                     ts=datetime.now(UTC))
        upsert_token(s, token="tok-other", user_id="someone-else", platform="android",
                     ts=datetime.now(UTC))

    r = client.delete("/v1/me", headers=_auth())

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "deleted"
    assert body["uid"] == "dev-user"
    assert body["deleted_rows"] == {"device_tokens": 1}

    with repo.session() as s:
        assert list_tokens_for(s, "dev-user") == []
        assert list_tokens_for(s, "someone-else") == ["tok-other"]


def test_defined_behaviour_when_the_caller_has_no_data(client: TestClient) -> None:
    r = client.delete("/v1/me", headers=_auth())

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "deleted"
    assert body["deleted_rows"] == {"device_tokens": 0}
