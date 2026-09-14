"""FastAPI shared dependencies — auth, repo, broker client.

Auth modes (mutually exclusive, selected by env):

- **Firebase ID token** — set FIREBASE_PROJECT_ID. Validates RS256 against
  Google's JWKS, checks `aud` == project id, `iss` ==
  https://securetoken.google.com/<project>, a non-empty `sub`, and that
  `auth_time` is not in the future.
- **Cognito JWT (production, Phase 5h)** — set COGNITO_USER_POOL_ID +
  COGNITO_APP_CLIENT_ID + AWS_REGION. Validates RS256 signature against
  the JWKS endpoint, checks `aud` / `iss` / `token_use=access` / `exp`.
- **Dev bearer (Phase 5a)** — set DEV_API_TOKEN to a static value;
  callers must send that exact string.
- **Disabled** — both empty; every caller becomes 'anonymous'. Local dev only.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from functools import lru_cache
from typing import Any

import httpx
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import create_engine

from tradingagents_us.dataflows.alpaca_broker import AlpacaClient
from tradingagents_us.storage import TradeLogRepository

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_repo() -> TradeLogRepository:
    """Process-wide repository singleton, sqlite by default."""
    url = os.environ.get("TRADE_LOG_DB_URL", "sqlite:///./local.db")
    return TradeLogRepository(engine=create_engine(url, future=True))


def get_alpaca() -> AlpacaClient:
    """Per-request Alpaca client. Caller is responsible for closing it
    (FastAPI uses it within one request and discards)."""
    return AlpacaClient()


# -------------------------- Firebase ID token validation -------------------------
#
# Firebase is NOT Cognito with different URLs, and the differences are exactly
# the ones that matter for verification:
#
#   aud        the Firebase PROJECT ID — not an app/client id
#   iss        https://securetoken.google.com/<project id>
#   sub        the uid, and Google's own spec requires it be non-empty
#   token_use  does not exist; a Cognito-shaped check for it would reject
#              every valid Firebase token
#
# The signing keys are served as JWKS at a fixed Google URL and rotate, so they
# are fetched and cached with the same TTL as the Cognito path rather than
# pinned.

# Note the singular "jwk". The plural spelling — which is what every other
# provider uses and what I assumed — returns 404 from Google, so Firebase
# verification could never have succeeded. The dev-token migration window hid
# it: the shared bearer kept working, so nothing looked broken.
_FIREBASE_JWKS_URL = os.environ.get(
    "FIREBASE_JWKS_URL",
    "https://www.googleapis.com/service_accounts/v1/jwk/"
    "securetoken@system.gserviceaccount.com",
)


def _firebase_issuer(project_id: str) -> str:
    return f"https://securetoken.google.com/{project_id}"


def _validate_firebase_jwt(token: str) -> str:
    """Return the validated Firebase uid (`sub`). Raises HTTPException otherwise.

    Fails closed when python-jose is missing. The Cognito path above allows an
    unverified parse behind ALLOW_UNVERIFIED_JWT for local work; this one does
    not offer that door at all. An unverified Firebase token is a uid the
    caller chose for themselves, and this uid is what will separate one family
    member's actions from another's.
    """
    project_id = os.environ.get("FIREBASE_PROJECT_ID", "")
    if not project_id:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "firebase not configured"
        )

    try:
        from jose import jwt  # type: ignore[import-untyped]
    except ImportError as import_err:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "jwt verification unavailable (python-jose not installed)",
        ) from import_err

    # A key-server failure is OUR inability to verify, not the caller's bad
    # token, so it must not surface as a 401 that tells a legitimate user their
    # credentials are wrong. It sat outside the try below and arrived as a bare
    # 500 with no message at all.
    try:
        jwks = _load_jwks(_FIREBASE_JWKS_URL)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"cannot reach the Firebase key server: {e}",
        ) from e

    try:
        kid = jwt.get_unverified_header(token).get("kid")
        key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
        if key is None:
            # Keys rotate; a kid we have never seen is more often a stale cache
            # than an attack, so refresh once before refusing.
            _JWKS_CACHE.pop(_FIREBASE_JWKS_URL, None)
            jwks = _load_jwks(_FIREBASE_JWKS_URL)
            key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
        if key is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "kid not in JWKS")

        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=project_id,
            issuer=_firebase_issuer(project_id),
        )

        # Google's spec, and not redundant with signature verification: a token
        # can be correctly signed and still carry an empty subject.
        sub = str(claims.get("sub") or "")
        if not sub:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "empty sub claim")

        # auth_time in the future means the token describes a sign-in that has
        # not happened. Skew is allowed; travel is not.
        auth_time = claims.get("auth_time")
        if isinstance(auth_time, int | float) and auth_time > time.time() + 300:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "auth_time in the future")

        return sub
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"firebase token invalid: {e}") from e


# --------------------------- Cognito JWT validation ---------------------------

_JWKS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_JWKS_TTL_S = 3600.0


def _jwks_url() -> str | None:
    pool = os.environ.get("COGNITO_USER_POOL_ID")
    region = os.environ.get("AWS_REGION", "eu-west-1")
    if not pool:
        return None
    return f"https://cognito-idp.{region}.amazonaws.com/{pool}/.well-known/jwks.json"


def _load_jwks(url: str) -> dict[str, Any]:
    cached = _JWKS_CACHE.get(url)
    now = time.time()
    if cached and (now - cached[0]) < _JWKS_TTL_S:
        return cached[1]
    with httpx.Client(timeout=5.0) as cli:
        r = cli.get(url)
        r.raise_for_status()
        data = r.json()
    _JWKS_CACHE[url] = (now, data)
    return data


def _validate_cognito_jwt(token: str) -> str:
    """Returns the validated user `sub` claim. Raises HTTPException on failure.

    Uses `python-jose` if available (full RS256 verification). Falls back to
    `unverified` decoding ONLY if python-jose isn't installed — that path
    is for local development before `jose` lands in the runtime image."""
    try:
        from jose import jwt  # type: ignore[import-untyped]
        from jose.utils import base64url_decode  # noqa: F401  (used by jose)
    except ImportError as import_err:
        # python-jose absent. In production (Cognito configured) this is a
        # misconfiguration and we MUST fail closed rather than trust unsigned
        # claims — otherwise anyone can forge a `sub`. Local dev may opt into
        # the unverified parse with ALLOW_UNVERIFIED_JWT=1.
        if os.environ.get("ALLOW_UNVERIFIED_JWT") != "1":
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "jwt verification unavailable (python-jose not installed)",
            ) from import_err
        import base64
        import json
        try:
            payload_b64 = token.split(".")[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload_b64))
            return str(claims.get("sub", "unknown"))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {e}") from e

    url = _jwks_url()
    if url is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "cognito not configured")
    jwks = _load_jwks(url)

    try:
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        key = next((k for k in jwks["keys"] if k["kid"] == kid), None)
        if key is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "kid not in JWKS")

        client_id = os.environ.get("COGNITO_APP_CLIENT_ID", "")
        region = os.environ.get("AWS_REGION", "eu-west-1")
        pool = os.environ.get("COGNITO_USER_POOL_ID", "")
        issuer = f"https://cognito-idp.{region}.amazonaws.com/{pool}"

        claims = jwt.decode(
            token,
            key,
            algorithms=[key["alg"]],
            audience=client_id or None,
            issuer=issuer,
            options={"verify_at_hash": False},
        )

        # Cognito issues 'access' and 'id' tokens; we accept either.
        token_use = claims.get("token_use")
        if token_use not in ("access", "id"):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"unexpected token_use={token_use}")

        return str(claims.get("sub", "unknown"))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"jwt invalid: {e}") from e


async def require_token(authorization: str | None = Header(default=None)) -> str:
    """Authentication dispatcher: Firebase, else Cognito, else dev bearer,
    else fully open ('anonymous').

    Order is by specificity, not preference: each mode is selected by its own
    env var, and a box configures exactly one. Firebase is checked first only
    because it is the mode this deployment is moving to — a box with both set
    is a misconfiguration, and picking a deterministic winner beats picking
    one at random.
    """
    # Firebase path
    if os.environ.get("FIREBASE_PROJECT_ID"):
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
        presented = authorization.split(" ", 1)[1].strip()

        # Migration window. Setting FIREBASE_PROJECT_ID used to cut the shared
        # DEV_API_TOKEN dead in the same instant — every already-signed-in
        # browser and phone would have started returning 401 the moment the
        # box restarted, with no warning and nothing on screen explaining it.
        #
        # A Firebase ID token is a JWT and therefore has three dot-separated
        # parts; the dev bearer is an opaque string. The token's own shape
        # decides which path it takes, so no new flag is needed and neither
        # kind is ever checked against the wrong validator.
        #
        # This deliberately keeps the shared secret alive, so the cutover is a
        # separate, explicit act: delete DEV_API_TOKEN from secrets.env. Until
        # then every use logs, so "is anyone still on the old token?" is a
        # question the logs answer.
        if presented.count(".") != 2:
            expected = os.environ.get("DEV_API_TOKEN", "")
            if expected and secrets.compare_digest(presented, expected):
                log.warning(
                    "accepted the shared DEV_API_TOKEN while Firebase is configured — "
                    "remove DEV_API_TOKEN from secrets.env to complete the cutover"
                )
                return "dev-user"
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")

        return _validate_firebase_jwt(presented)

    # Cognito path
    if os.environ.get("COGNITO_USER_POOL_ID"):
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
        token = authorization.split(" ", 1)[1].strip()
        return _validate_cognito_jwt(token)

    # Dev bearer
    expected = os.environ.get("DEV_API_TOKEN", "")
    if expected:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
        presented = authorization.split(" ", 1)[1].strip()
        # Constant-time compare so an attacker can't recover the token byte by
        # byte from response-timing differences.
        if not secrets.compare_digest(presented, expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
        return "dev-user"

    # Open (local dev only)
    return "anonymous"


# ------------------------------- Authorisation --------------------------------
#
# Authentication answers "who is this"; this answers "may they". Until now the
# two were the same question: any valid Firebase user of the project could
# approve an order or throw FLATTEN_ALL, which closes the entire book at
# market. For a household of eight or nine that is not a theoretical concern —
# it is one mistaken tap from a family member who only wanted to look.
#
# The list is uids in an env var rather than Firebase custom claims. Claims
# would need an Admin SDK service account — another credential to create,
# store and rotate — to express a fact about eight people that changes once a
# year. A line in secrets.env is auditable by reading it.


def _admin_uids() -> frozenset[str]:
    raw = os.environ.get("ADMIN_UIDS", "")
    return frozenset(u.strip() for u in raw.split(",") if u.strip())


def is_admin(user: str) -> bool:
    """Whether `user` (a uid from require_token) may take privileged actions.

    Three cases, and the reasoning for each matters more than the code:

    `anonymous` — auth is switched off entirely, which only happens when
    neither Firebase nor a dev token is configured. That is a local machine
    with no identity to check, so denying would break development while
    protecting nothing.

    `dev-user` — the shared bearer during the Firebase migration. NOT admin.
    It is one secret held by everyone, so it cannot say who acted; and the
    operator's real session already runs on Firebase, so refusing it costs
    nothing and makes any remaining fallback path visible instead of silently
    privileged.

    Anything else is a Firebase uid, admitted only if it is on the list.
    """
    if user == "anonymous":
        return True
    if user == "dev-user":
        return False
    return user in _admin_uids()


async def require_admin(user: str = Depends(require_token)) -> str:
    """Authenticated AND authorised. Raises 403 for a known but unprivileged user.

    401 and 403 are kept distinct on purpose: 401 means "I do not know you",
    403 means "I know you and the answer is no". Collapsing them would send a
    signed-in family member to re-enter a password that was never the problem.

    When ADMIN_UIDS is unset this denies everyone (except the anonymous local
    case above). The asymmetry is deliberate: failing open would silently hand
    the kill switch to every user the moment an env var went missing, while
    failing closed is a loud lockout the operator fixes by editing one line.
    A missing list must not read as an empty restriction.
    """
    if not is_admin(user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "this action requires an administrator account",
        )
    return user
