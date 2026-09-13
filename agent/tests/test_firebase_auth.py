"""Firebase ID-token verification — what it must accept, and what it must refuse."""

from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from api import deps

PROJECT = "fusapp-trader"
ISSUER = f"https://securetoken.google.com/{PROJECT}"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT)
    deps._JWKS_CACHE.clear()
    yield
    deps._JWKS_CACHE.clear()


def _install_fake_jose(monkeypatch: pytest.MonkeyPatch, claims: dict, *, kid="k1"):
    """Stand in for python-jose so signature checking is out of scope here.

    These tests pin the CLAIM rules — audience, issuer, sub, auth_time — which
    are the parts a Cognito-shaped implementation gets wrong. Signature
    verification is jose's job and is exercised by its own suite.
    """
    import sys
    import types

    seen: dict = {}

    def decode(token, key, algorithms=None, audience=None, issuer=None, **kw):
        seen["audience"] = audience
        seen["issuer"] = issuer
        seen["algorithms"] = algorithms
        if audience is not None and claims.get("aud") != audience:
            raise ValueError("Invalid audience")
        if issuer is not None and claims.get("iss") != issuer:
            raise ValueError("Invalid issuer")
        return claims

    fake = types.ModuleType("jose")
    fake.jwt = types.SimpleNamespace(  # type: ignore[attr-defined]
        get_unverified_header=lambda t: {"kid": kid},
        decode=decode,
    )
    monkeypatch.setitem(sys.modules, "jose", fake)
    monkeypatch.setattr(deps, "_load_jwks", lambda url: {"keys": [{"kid": "k1", "alg": "RS256"}]})
    return seen


def _valid_claims(**over) -> dict:
    base = {
        "aud": PROJECT,
        "iss": ISSUER,
        "sub": "uid-abc123",
        "auth_time": time.time() - 60,
        "exp": time.time() + 3600,
    }
    base.update(over)
    return base


class TestAccepts:
    def test_a_valid_token_returns_the_uid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_jose(monkeypatch, _valid_claims())
        assert deps._validate_firebase_jwt("tok") == "uid-abc123"

    def test_audience_is_the_project_id_not_a_client_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The single most likely porting mistake from the Cognito path.
        seen = _install_fake_jose(monkeypatch, _valid_claims())
        deps._validate_firebase_jwt("tok")
        assert seen["audience"] == PROJECT

    def test_issuer_is_securetoken_google(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = _install_fake_jose(monkeypatch, _valid_claims())
        deps._validate_firebase_jwt("tok")
        assert seen["issuer"] == "https://securetoken.google.com/fusapp-trader"

    def test_only_rs256_is_offered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = _install_fake_jose(monkeypatch, _valid_claims())
        deps._validate_firebase_jwt("tok")
        assert seen["algorithms"] == ["RS256"]

    def test_no_token_use_claim_is_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Firebase tokens carry no token_use; a Cognito-shaped check for it
        # would reject every valid token.
        _install_fake_jose(monkeypatch, _valid_claims())
        assert deps._validate_firebase_jwt("tok") == "uid-abc123"


class TestRefuses:
    def test_a_token_for_another_project(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_jose(monkeypatch, _valid_claims(aud="someone-elses-project"))
        with pytest.raises(HTTPException) as e:
            deps._validate_firebase_jwt("tok")
        assert e.value.status_code == 401

    def test_a_wrong_issuer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_jose(monkeypatch, _valid_claims(iss="https://evil.example/x"))
        with pytest.raises(HTTPException) as e:
            deps._validate_firebase_jwt("tok")
        assert e.value.status_code == 401

    def test_an_empty_sub(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A correctly signed token can still carry an empty subject, and the
        # uid is what separates one family member's actions from another's.
        _install_fake_jose(monkeypatch, _valid_claims(sub=""))
        with pytest.raises(HTTPException) as e:
            deps._validate_firebase_jwt("tok")
        assert "empty sub" in e.value.detail

    def test_auth_time_in_the_future(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_jose(monkeypatch, _valid_claims(auth_time=time.time() + 4000))
        with pytest.raises(HTTPException) as e:
            deps._validate_firebase_jwt("tok")
        assert "auth_time" in e.value.detail

    def test_small_clock_skew_is_tolerated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Travel is refused; skew is not. A minute ahead must still work.
        _install_fake_jose(monkeypatch, _valid_claims(auth_time=time.time() + 60))
        assert deps._validate_firebase_jwt("tok") == "uid-abc123"

    def test_an_unknown_kid_after_a_refresh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_jose(monkeypatch, _valid_claims(), kid="rotated-away")
        with pytest.raises(HTTPException) as e:
            deps._validate_firebase_jwt("tok")
        assert "kid not in JWKS" in e.value.detail

    def test_missing_project_id_is_a_server_error_not_an_auth_pass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
        with pytest.raises(HTTPException) as e:
            deps._validate_firebase_jwt("tok")
        assert e.value.status_code == 500

    def test_missing_jose_fails_closed_with_no_escape_hatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The Cognito path allows an unverified parse behind
        # ALLOW_UNVERIFIED_JWT. This one must not, even with it set: an
        # unverified Firebase token is a uid the caller picked themselves.
        import builtins

        monkeypatch.setenv("ALLOW_UNVERIFIED_JWT", "1")
        real_import = builtins.__import__

        def no_jose(name, *a, **k):
            if name == "jose":
                raise ImportError("no jose")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", no_jose)
        with pytest.raises(HTTPException) as e:
            deps._validate_firebase_jwt("tok")
        assert e.value.status_code == 500


class TestDispatcher:
    @pytest.mark.anyio
    async def test_firebase_wins_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COGNITO_USER_POOL_ID", "pool")
        monkeypatch.setattr(deps, "_validate_firebase_jwt", lambda t: "fb-uid")
        assert await deps.require_token("Bearer x") == "fb-uid"

    @pytest.mark.anyio
    async def test_a_missing_header_is_401_not_anonymous(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(HTTPException) as e:
            await deps.require_token(None)
        assert e.value.status_code == 401


@pytest.fixture
def anyio_backend():
    return "asyncio"
