"""Explicit DEMO account display, independent of practice-order enablement.

Construction performs no credential/profile reads or network access. Each view
uses the selected saved profile and the same resolver's DEMO pair, authenticates
the original Session, and closes it after publishing a data-only AccountView.
This service never enables trading or supplies financial qualification.
"""

import os
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Self

from fastapi import APIRouter, FastAPI

from ..account_profiles import (
    AccountProfileError,
    AccountProfileStore,
    default_account_profile_store,
)
from ..credential_resolution import CredentialResolver, CredentialStartup
from ..credentials import CredentialStoreError
from ..execution.account_binding import AccountBindingError, BybitDemoAccountSession
from ..execution.protection import ProtectionScope
from .freshness import VIEW_DEADLINE_SECONDS
from .models import AccountView
from .service import blank_view, build_account_view, publish

_POLICIES = frozenset({"independent-v1", "uid-anchored-adoption-v1"})
_MESSAGES = {
    "DEMO_PROFILE_REQUIRED": "저장된 DEMO 계정 프로필을 확인하세요. 조회를 시작하지 않았습니다.",
    "DEMO_PROFILE_PENDING": "DEMO 계정 관계 확인이 아직 끝나지 않았습니다. 조회를 시작하지 않았습니다.",
    "DEMO_AUTHENTICATION_REQUIRED": "등록된 DEMO 계정 인증을 확인하지 못했습니다. 숫자를 숨겼습니다.",
    "DEMO_SESSION_CLOSE_FAILED": "DEMO 조회 연결 종료를 확인하지 못했습니다. 숫자를 숨겼습니다.",
}


def _failure(code: str, *, configuration: bool = False) -> AccountView:
    view = blank_view("config" if configuration else "unexpected", "demo", live_trading=False)
    return view.model_copy(update={"오류코드": code, "메시지": _MESSAGES[code]})


class BybitDemoAccountViewV1:
    """One request-owned authenticated read; no default LIVE client or saved data cache."""

    def __init__(self, *, resolver: CredentialResolver,
                 profile_store: AccountProfileStore | None,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._resolver = resolver
        self._profile_store = profile_store
        self._clock = clock
        self._monotonic = monotonic

    @classmethod
    def from_environment(cls, *, resolver: CredentialResolver | None = None,
                         environ: Mapping[str, str] | None = None) -> Self:
        """Select original store locations lazily; BYBIT_DEMO remains an order-path flag."""
        source = dict(os.environ if environ is None else environ)
        context = resolver if resolver is not None else CredentialResolver.from_environment(
            environ=source)
        try:
            profiles = default_account_profile_store(source)
        except AccountProfileError:
            profiles = None
        return cls(resolver=context, profile_store=profiles)

    async def view(self) -> AccountView:
        """Read only after complete saved expectations; cancellation closes and propagates."""
        session: BybitDemoAccountSession | None = None
        view = _failure("DEMO_PROFILE_REQUIRED", configuration=True)
        try:
            profiles = self._profile_store
            if profiles is None:
                return view
            snapshot = profiles.load()
            if snapshot is None:
                return view
            expected = snapshot.resolve_expectations(supported_policies=_POLICIES)
            key, secret = self._resolver.bybit_pair("demo")
            if not key or not secret:
                return blank_view("missing_credentials", "demo", live_trading=False)
            credential_check = self._resolver._bybit_publication_check()
            scope = ProtectionScope(expected._uid, "DEMO")
            deadline = time.monotonic() + VIEW_DEADLINE_SECONDS
            session = BybitDemoAccountSession(api_key=key, api_secret=secret,
                clock=self._clock, monotonic=self._monotonic)
            binding = await session.authenticate(expected_uid=expected._uid,
                expected_parent_uid=expected._parent_uid, expected_is_master=expected._is_master)
            client = session.read_client(binding, scope=scope)
            view = await build_account_view(client, None, live_trading=False, deadline_at=deadline)
            view = publish(view, credential_check=credential_check)
            # Final genuine scope/two-clock check follows all reads and DTO callbacks.
            # Closing the request-owned Session below revokes its binding; this DTO
            # remains an observation and can never authorize an order.
            session.require_binding(binding, scope=scope)
        except AccountProfileError as error:
            code = ("DEMO_PROFILE_PENDING" if error.code == "PROFILE_PENDING"
                    else "DEMO_PROFILE_REQUIRED")
            view = _failure(code, configuration=True)
        except CredentialStoreError:
            view = blank_view("missing_credentials", "demo", live_trading=False)
        except AccountBindingError:
            view = _failure("DEMO_AUTHENTICATION_REQUIRED")
        except Exception:  # noqa: BLE001 - provider/configuration text can contain secrets.
            view = blank_view("unexpected", "demo", live_trading=False)
        finally:
            if session is not None:
                try:
                    await session.aclose()
                except Exception:  # noqa: BLE001 - never expose transport close diagnostics.
                    view = _failure("DEMO_SESSION_CLOSE_FAILED")
        return view


def install(application: FastAPI, *, credential_context: CredentialStartup) -> None:
    """Install a lazy read endpoint under the existing account origin boundary."""
    router = APIRouter(prefix="/api/v1/account/bybit/demo")
    service: BybitDemoAccountViewV1 | None = None

    @router.get("/view", response_model=AccountView)
    async def demo_view() -> AccountView:
        nonlocal service
        if service is None:
            try:
                service = BybitDemoAccountViewV1.from_environment(resolver=credential_context.get())
            except Exception:  # noqa: BLE001 - configuration diagnostics can contain secrets.
                return _failure("DEMO_PROFILE_REQUIRED", configuration=True)
        return await service.view()

    application.include_router(router)
