"""Authentication answers "who"; authorisation answers "may they".

Until this, any valid Firebase user of the project could throw FLATTEN_ALL —
which closes the entire book at market. For a household that is one mistaken
tap from someone who only wanted to look.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from api import deps

OPERATOR = "JRpzu78JeXWfOhtuazDYBZ85gq33"
OTHER = "someoneelse456"


class TestWhoIsAdmin:
    def test_a_listed_uid_is(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ADMIN_UIDS", OPERATOR)
        assert deps.is_admin(OPERATOR) is True

    def test_another_signed_in_user_is_not(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ADMIN_UIDS", OPERATOR)
        assert deps.is_admin(OTHER) is False

    def test_the_list_accepts_several_with_whitespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ADMIN_UIDS", f" {OPERATOR} , {OTHER} ")
        assert deps.is_admin(OPERATOR) and deps.is_admin(OTHER)

    def test_an_empty_entry_does_not_admit_an_empty_uid(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # "a,,b" must not make "" an administrator.
        monkeypatch.setenv("ADMIN_UIDS", f"{OPERATOR},,")
        assert deps.is_admin("") is False


class TestFailsClosed:
    def test_an_unset_list_admits_nobody(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Failing open would silently hand the kill switch to every user the
        # moment an env var went missing. Failing closed is a loud lockout the
        # operator fixes by editing one line.
        monkeypatch.delenv("ADMIN_UIDS", raising=False)
        assert deps.is_admin(OPERATOR) is False

    def test_an_empty_list_admits_nobody(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ADMIN_UIDS", "   ")
        assert deps.is_admin(OPERATOR) is False


class TestTheSharedBearerIsNotPrivileged:
    def test_dev_user_is_never_admin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # One secret held by everyone cannot say who acted. The operator's real
        # session already runs on Firebase, so refusing it costs nothing and
        # makes any remaining fallback path visible instead of silently
        # privileged.
        monkeypatch.setenv("ADMIN_UIDS", OPERATOR)
        assert deps.is_admin("dev-user") is False

    def test_not_even_if_someone_lists_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ADMIN_UIDS", "dev-user")
        assert deps.is_admin("dev-user") is False


class TestLocalDevelopment:
    def test_anonymous_needs_the_explicit_opt_in(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # This assertion used to be `is True` on the reasoning that no auth
        # configured means a local machine. That inferred intent from absence,
        # and a box with an unmounted secrets.env looks identical.
        monkeypatch.delenv("ADMIN_UIDS", raising=False)
        monkeypatch.delenv("ALLOW_ANONYMOUS_ADMIN", raising=False)
        assert deps.is_admin("anonymous") is False
        monkeypatch.setenv("ALLOW_ANONYMOUS_ADMIN", "1")
        assert deps.is_admin("anonymous") is True


class TestRequireAdmin:
    @pytest.mark.anyio
    async def test_admits_an_administrator(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ADMIN_UIDS", OPERATOR)
        assert await deps.require_admin(OPERATOR) == OPERATOR

    @pytest.mark.anyio
    async def test_refuses_with_403_not_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 401 means "I do not know you", 403 means "I know you and the answer
        # is no". Collapsing them would send a signed-in family member to
        # re-enter a password that was never the problem.
        monkeypatch.setenv("ADMIN_UIDS", OPERATOR)
        with pytest.raises(HTTPException) as e:
            await deps.require_admin(OTHER)
        assert e.value.status_code == 403
        assert "administrator" in e.value.detail

    @pytest.mark.anyio
    async def test_the_message_does_not_name_who_is_admin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A refusal must not enumerate the privileged accounts.
        monkeypatch.setenv("ADMIN_UIDS", f"{OPERATOR},{OTHER}")
        with pytest.raises(HTTPException) as e:
            await deps.require_admin("third-party")
        assert OPERATOR not in e.value.detail


class TestTheRightEndpointsAreGuarded:
    """A list is easy to get wrong in the direction nobody notices."""

    def _dep_names(self, fn) -> set[str]:
        import inspect

        out = set()
        for p in inspect.signature(fn).parameters.values():
            d = getattr(p.default, "dependency", None)
            if d is not None:
                out.add(d.__name__)
        return out

    @pytest.mark.parametrize(
        "module,handler",
        [
            ("orders", "approve_order"),
            ("orders", "reject_order"),
            ("orders", "cancel_order"),
            ("orders", "set_kill_switch"),
            ("analyze", "start_analysis"),
        ],
    )
    def test_mutating_endpoints_require_admin(self, module: str, handler: str) -> None:
        mod = __import__(f"api.routes.{module}", fromlist=[module])
        assert "require_admin" in self._dep_names(getattr(mod, handler))

    @pytest.mark.parametrize(
        "module,handler",
        [
            ("orders", "get_kill_switch"),
            ("analyze", "get_analysis"),
        ],
    )
    def test_reads_stay_open_to_every_signed_in_user(self, module: str, handler: str) -> None:
        # Everyone may watch. Restricting reads would make the app useless to
        # the people it was built for without protecting anything.
        mod = __import__(f"api.routes.{module}", fromlist=[module])
        assert "require_token" in self._dep_names(getattr(mod, handler))


@pytest.fixture
def anyio_backend():
    return "asyncio"


class TestAnonymousIsNotAutomaticallyAdmin:
    """Inferring intent from ABSENCE was the hole.

    "anonymous" was admitted on the reasoning that no auth configured means a
    local machine with no identity to check. But a box that boots with an
    unmounted or empty secrets.env has no auth configured either — and it would
    have published the kill switch: an unauthenticated POST could cancel every
    protective stop and market out the whole book, recorded as
    actor="anonymous".

    It contradicted the module's own refusal to fail open on a missing
    ADMIN_UIDS. A missing list failed closed while missing auth failed open.
    """

    def test_a_box_that_lost_its_secrets_locks_the_switch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for k in ("FIREBASE_PROJECT_ID", "COGNITO_USER_POOL_ID", "DEV_API_TOKEN",
                  "ADMIN_UIDS", "ALLOW_ANONYMOUS_ADMIN"):
            monkeypatch.delenv(k, raising=False)
        assert deps.is_admin("anonymous") is False

    def test_local_development_says_so_in_one_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALLOW_ANONYMOUS_ADMIN", "1")
        assert deps.is_admin("anonymous") is True

    @pytest.mark.parametrize("value", ["0", "false", "", "no", "maybe"])
    def test_anything_other_than_a_clear_yes_stays_closed(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("ALLOW_ANONYMOUS_ADMIN", value)
        assert deps.is_admin("anonymous") is False

    @pytest.mark.anyio
    async def test_require_admin_refuses_an_unconfigured_box(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALLOW_ANONYMOUS_ADMIN", raising=False)
        with pytest.raises(HTTPException) as e:
            await deps.require_admin("anonymous")
        assert e.value.status_code == 403
