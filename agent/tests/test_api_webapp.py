"""/app — the Expo web build is served as an SPA, and cannot be walked out of.

The route exists because the Modernist screens are React Native compiled with
react-native-web: the same 17 screens the phone runs, reachable from a browser
so the operator is not blocked on an app build. Two behaviours carry real
weight and are pinned here.

First, the client-route fallback. expo-router resolves /app/portfolio in the
browser after the bundle boots, so it is not a file on disk. If a miss 404'd,
the first screen would work and a reload on any other would break — the sort of
failure that only shows up once someone is actually using it.

Second, the traversal guard. The handler joins a caller-controlled path onto a
directory and reads the result, which is the exact shape of an arbitrary-file
read. `_webapp_file` resolves the target and refuses anything that lands
outside the build root, so `..` segments fall through to the SPA fallback
rather than returning /etc/passwd. That is the difference between an SPA mount
and a file-disclosure bug, so it is asserted on the bytes, not just the status.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def build(tmp_path: Path) -> Path:
    """A minimal stand-in for `expo export` output: an index and a hashed asset."""
    root = tmp_path / "webapp"
    (root / "_expo" / "static" / "js" / "web").mkdir(parents=True)
    (root / "index.html").write_text(
        '<!DOCTYPE html><html><body><div id="root"></div>'
        '<script src="/app/_expo/static/js/web/entry-abc123.js" defer></script>'
        "</body></html>",
        encoding="utf-8",
    )
    (root / "_expo" / "static" / "js" / "web" / "entry-abc123.js").write_text(
        "console.log('bundle')", encoding="utf-8"
    )
    return root


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, build: Path) -> TestClient:
    monkeypatch.setenv("WEBAPP_DIR", str(build))
    import api.main

    # The module reads WEBAPP_DIR at import time, so a test that only set the
    # env var would silently exercise whatever path the first import captured.
    monkeypatch.setattr(api.main, "_WEBAPP", build)
    return TestClient(api.main.app)


def test_serves_the_index(client: TestClient) -> None:
    r = client.get("/app")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert 'id="root"' in r.text


def test_serves_hashed_assets_with_a_long_cache(client: TestClient) -> None:
    r = client.get("/app/_expo/static/js/web/entry-abc123.js")
    assert r.status_code == 200
    assert "bundle" in r.text
    # The filename carries a content hash, so it can never go stale.
    assert "immutable" in r.headers["cache-control"]


def test_index_is_never_cached(client: TestClient) -> None:
    """index.html names the current bundle hash — caching it strands clients
    on an old bundle after a deploy."""
    assert "no-store" in client.get("/app").headers["cache-control"]


@pytest.mark.parametrize("route", ["/app/portfolio", "/app/orders", "/app/trade/AAPL"])
def test_client_routes_fall_back_to_the_index(client: TestClient, route: str) -> None:
    r = client.get(route)
    assert r.status_code == 200
    assert 'id="root"' in r.text


@pytest.mark.parametrize(
    "attack",
    [
        "/app/../../../../etc/passwd",
        "/app/%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "/app/_expo/../../../../etc/hostname",
    ],
)
def test_cannot_read_outside_the_build(client: TestClient, attack: str) -> None:
    r = client.get(attack)
    # Falling back to the SPA index is the correct outcome; leaking a file is not.
    assert "root:" not in r.text
    assert "/etc/" not in r.text
    if r.status_code == 200:
        assert 'id="root"' in r.text


def test_a_symlink_planted_in_the_build_does_not_escape(
    client: TestClient, build: Path
) -> None:
    """resolve() runs before the containment check, so a link inside the build
    pointing out of it is refused too — not just `..` in the URL."""
    target = build.parent / "outside.txt"
    target.write_text("SECRET", encoding="utf-8")
    (build / "escape.txt").symlink_to(target)

    r = client.get("/app/escape.txt")
    assert "SECRET" not in r.text


def test_missing_build_reports_503_rather_than_500(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A box where the build was never deployed should say so, not crash."""
    import api.main

    monkeypatch.setattr(api.main, "_WEBAPP", tmp_path / "nope")
    r = TestClient(api.main.app).get("/app")
    assert r.status_code == 503
    assert "deploy" in r.json()["detail"]
