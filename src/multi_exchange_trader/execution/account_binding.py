"""Authenticated DEMO UID binding owned by the same credential session as native config.

GET only: this module neither connects a native client nor changes venue settings.
Adapter labels such as BYBIT-UNIFIED are deliberately not account identities.
Bindings are process-local capabilities, not serialized proof or deployment
qualification. Injected transports cannot authorize native execution.

Schema verified 2026-11-02:
https://bybit-exchange.github.io/docs/v5/user/apikey-info
https://bybit-exchange.github.io/docs/v5/demo (Get Demo Account API Key Info).
"""

from __future__ import annotations

import asyncio
import hmac
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Self

import httpx

from ..account.bybit_readonly import BybitReadOnlyClient
from ..account.config import allowed_host
from .bybit_native import build_demo_exec_config
from .protection import ProtectionScope

_PERMISSIONS = {
    "ContractTrade": frozenset({"Order", "Position"}),
    "Spot": frozenset({"SpotTrade"}),
    "Wallet": frozenset({"AccountTransfer", "SubMemberTransfer", "SubMemberTransferList",
                         "Withdraw"}),
    "Options": frozenset({"OptionsTrade"}),
    "Derivatives": frozenset({"DerivativesTrade"}),
    "Exchange": frozenset({"ExchangeHistory"}),
    "Earn": frozenset({"Earn"}),
    "FiatP2P": frozenset({"FiatP2POrder", "Advertising"}),
    "FiatBitPay": frozenset({"FaitPayOrder"}),
    "FiatConvertBroker": frozenset({"FiatConvertBrokerOrder"}),
    "BitCard": frozenset({"BitCard"}),
    "ByXPost": frozenset({"ByXPost"}),
    "Affiliate": frozenset(), "BlockTrade": frozenset(), "NFT": frozenset(),
    "CopyTrading": frozenset(), "FiatGlobalPay": frozenset(),
}
_LINEAR_GRANTS = frozenset({("ContractTrade", "Order"), ("ContractTrade", "Position"),
                           ("Derivatives", "DerivativesTrade")})


class AccountBindingError(ValueError):
    """Only fixed local codes cross the credential boundary."""


class AuthenticatedAccountBinding:
    """Opaque handle; only its issuing session can validate it."""

    __slots__ = ()

    def __new__(cls) -> Self:
        raise AccountBindingError("AUTHENTICATED_ACCOUNT_BINDING_REQUIRED")

    def __repr__(self) -> str:
        return "AuthenticatedAccountBinding(opaque=True)"


@dataclass(frozen=True)
class BybitAccountIdentity:
    """Safe observation, never authority when constructed/copied by a caller."""

    uid: str
    parent_uid: str
    is_master: bool
    read_only: bool
    permissions: tuple[tuple[str, tuple[str, ...]], ...]
    observed_at: datetime
    server_observed_at: datetime
    transport_injected: bool

    @property
    def scope(self) -> ProtectionScope:
        return ProtectionScope(self.uid, "DEMO")

    def has_permission(self, group: str, permission: str) -> bool:
        return permission in dict(self.permissions).get(group, ())

    @property
    def prohibited_permissions(self) -> tuple[tuple[str, str], ...]:
        """Check every grant, including transfers and unrelated trading products."""
        return tuple((group, grant) for group, grants in self.permissions for grant in grants
                     if (group, grant) not in _LINEAR_GRANTS)

    @property
    def linear_execution_permitted(self) -> bool:
        # Missing Wallet is unknown, not evidence of absent transfer/withdraw rights.
        return (not self.read_only and "Wallet" in dict(self.permissions)
                and self.has_permission("ContractTrade", "Order")
                and self.has_permission("ContractTrade", "Position")
                and not self.prohibited_permissions)


def _uid(value: object, *, allow_zero: bool = False) -> str:
    if ((type(value) is not str or re.fullmatch(r"[1-9][0-9]{0,19}", value) is None)
            and not (allow_zero and value == "0")):
        raise AccountBindingError("ACCOUNT_UID_REQUIRED")
    assert isinstance(value, str)
    return value


def _utc(value: datetime) -> datetime:
    if (type(value) is not datetime or value.tzinfo is None
            or value.utcoffset() != timedelta(0)):
        raise AccountBindingError("ACCOUNT_UTC_CLOCK_REQUIRED")
    return value


def _server_time(value: object) -> datetime:
    if type(value) is not int or not 0 < value < 253402300800000:
        raise AccountBindingError("ACCOUNT_SERVER_TIME_REQUIRED")
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=value)


def _permissions(value: object) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if type(value) is not dict or not value or len(value) > len(_PERMISSIONS):
        raise AccountBindingError("ACCOUNT_PERMISSIONS_INVALID")
    result: list[tuple[str, tuple[str, ...]]] = []
    for group, grants in value.items():
        if (type(group) is not str or group not in _PERMISSIONS or type(grants) is not list
                or len(grants) > len(_PERMISSIONS[group])
                or any(type(grant) is not str or grant not in _PERMISSIONS[group]
                       for grant in grants) or len(set(grants)) != len(grants)):
            raise AccountBindingError("ACCOUNT_PERMISSIONS_INVALID")
        result.append((group, tuple(sorted(grants))))
    return tuple(sorted(result))


class BybitDemoAccountSession:
    """Own explicit credentials, reads and native configuration for one DEMO UID.

    The session never loads .env, exports credentials, returns raw API responses,
    or accepts an independently constructed read client. Native configuration is
    delivered only to a trusted worker installer callback, never in a result/repr.
    The installer must not log the native configuration, which contains credentials.
    Python reflection/monkeypatching is outside this in-process trust boundary.
    """

    def __init__(self, *, api_key: str, api_secret: str,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 monotonic: Callable[[], float] = time.monotonic,
                 max_age: timedelta = timedelta(seconds=30),
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        if (type(api_key) is not str or not api_key.strip()
                or type(api_secret) is not str or not api_secret.strip()
                or type(max_age) is not timedelta or not timedelta(0) < max_age
                <= timedelta(minutes=5)):
            raise AccountBindingError("EXPLICIT_ACCOUNT_CREDENTIALS_AND_BOUNDED_TTL_REQUIRED")
        self._api_key, self._api_secret = api_key.strip(), api_secret.strip()
        self._clock, self._monotonic, self._max_age = clock, monotonic, max_age
        self._transport_injected = transport is not None
        self._client = BybitReadOnlyClient(api_key=self._api_key, api_secret=self._api_secret,
            environment="demo", base_url=allowed_host("demo"), transport=transport)
        self._binding: AuthenticatedAccountBinding | None = None
        self._identity: BybitAccountIdentity | None = None
        self._issued_mono = 0.0
        self._closed = False
        self._refresh_lock = asyncio.Lock()

    def __repr__(self) -> str:
        return "BybitDemoAccountSession(environment=DEMO, credentials=REDACTED)"

    def _guard(self) -> None:
        if (self._closed or type(self._client) is not BybitReadOnlyClient
                or self._client.environment != "demo"
                or self._client.base_url != allowed_host("demo")
                or self._client._api_key != self._api_key
                or self._client._api_secret != self._api_secret):
            self._binding = None
            self._identity = None
            raise AccountBindingError("ACCOUNT_SESSION_MISMATCH_OR_CLOSED")

    def _mono(self) -> float:
        value = self._monotonic()
        if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
            raise AccountBindingError("ACCOUNT_MONOTONIC_CLOCK_REQUIRED")
        return value

    def _fresh(self, observed: datetime, now: datetime) -> None:
        if not timedelta(0) <= now - observed <= self._max_age:
            raise AccountBindingError("ACCOUNT_BINDING_STALE")

    async def authenticate(self, *, expected_uid: str,
                           expected_parent_uid: str,
                           expected_is_master: bool) -> AuthenticatedAccountBinding:
        """Attest UID, parent and master independently; every attempt revokes prior proof.

        DEMO can return isMaster=False with parentUid="0". Never derive either
        expected field from the other or use the returned value as its own expectation.
        """
        async with self._refresh_lock:
            self._binding = None
            self._identity = None
            self._guard()
            uid = _uid(expected_uid)
            parent = _uid(expected_parent_uid, allow_zero=True)
            if type(expected_is_master) is not bool:
                raise AccountBindingError("ACCOUNT_MASTER_ATTESTATION_REQUIRED")
            try:
                started = self._mono()
                deadline = time.monotonic() + min(20.0, self._max_age.total_seconds())
                _, server_ms = await self._client.read("/v5/market/time", [], signed=False,
                                                      deadline=deadline)
                now = _utc(self._clock())
                self._fresh(_server_time(server_ms), now)
                assert server_ms is not None
                self._client.anchor_server_time(server_ms)
                result, server_ms = await self._client.read("/v5/user/query-api", [], signed=True,
                                                            deadline=deadline)
                self._guard()
                now, mono = _utc(self._clock()), self._mono()
                observed = _server_time(server_ms)
                self._fresh(observed, now)
                if not 0 <= mono - started <= self._max_age.total_seconds():
                    raise AccountBindingError("ACCOUNT_BINDING_STALE")
                echoed_key = result.get("apiKey")
                user_id, master, read_only = (result.get("userID"), result.get("isMaster"),
                                              result.get("readOnly"))
                if (type(echoed_key) is not str
                        or not hmac.compare_digest(echoed_key.encode(), self._api_key.encode())
                        or result.get("secret") != "" or type(user_id) is not int
                        or user_id <= 0 or str(user_id) != uid or type(master) is not bool
                        or master != expected_is_master or result.get("parentUid") != parent
                        or parent == uid or type(read_only) is not int or read_only not in (0, 1)):
                    raise AccountBindingError("AUTHENTICATED_ACCOUNT_IDENTITY_MISMATCH")
                identity = BybitAccountIdentity(uid, parent, master, read_only == 1,
                    _permissions(result.get("permissions")), now, observed, self._transport_injected)
                if any(secret in repr(identity) for secret in (self._api_key, self._api_secret)):
                    raise AccountBindingError("ACCOUNT_PUBLIC_IDENTITY_REDACTION_REQUIRED")
            except Exception:  # noqa: BLE001 - no provider error/body/credential can escape.
                raise AccountBindingError("ACCOUNT_AUTHENTICATION_FAILED") from None
            self._binding = object.__new__(AuthenticatedAccountBinding)
            self._identity, self._issued_mono = identity, mono
            return self._binding

    def require_binding(self, binding: AuthenticatedAccountBinding, *,
                        scope: ProtectionScope) -> BybitAccountIdentity:
        """Validate provenance, current credential pair, exact scope and two clocks."""
        self._guard()
        if (type(binding) is not AuthenticatedAccountBinding or binding is not self._binding
                or self._identity is None):
            raise AccountBindingError("AUTHENTICATED_ACCOUNT_BINDING_REQUIRED")
        identity = self._identity
        if type(scope) is not ProtectionScope or scope != identity.scope:
            raise AccountBindingError("AUTHENTICATED_ACCOUNT_SCOPE_MISMATCH")
        now = _utc(self._clock())
        self._fresh(identity.observed_at, now)
        self._fresh(identity.server_observed_at, now)
        if not 0 <= self._mono() - self._issued_mono <= self._max_age.total_seconds():
            raise AccountBindingError("ACCOUNT_BINDING_STALE")
        return identity

    def require_execution_binding(self, binding: AuthenticatedAccountBinding, *,
                                  scope: ProtectionScope) -> BybitAccountIdentity:
        identity = self.require_binding(binding, scope=scope)
        if identity.transport_injected:
            raise AccountBindingError("ACCOUNT_NETWORK_TRANSPORT_REQUIRED")
        if not identity.linear_execution_permitted:
            raise AccountBindingError("ACCOUNT_LINEAR_PERMISSIONS_REQUIRED")
        return identity

    def read_client(self, binding: AuthenticatedAccountBinding, *,
                    scope: ProtectionScope) -> BybitReadOnlyClient:
        """Give readback the exact authenticated client; revalidate every polling cycle."""
        self.require_binding(binding, scope=scope)
        return self._client

    def configure_native_execution(self, binding: AuthenticatedAccountBinding, *,
                                   scope: ProtectionScope,
                                   install: Callable[[object], None]) -> None:
        """Pass the pinned, nonmutating-on-connect DEMO config to a trusted installer.

        This is account configuration only, not a native worker qualification gate.
        Other runtime qualification requirements remain mandatory.
        """
        self.require_execution_binding(binding, scope=scope)
        try:
            config = build_demo_exec_config(api_key=self._api_key, api_secret=self._api_secret)
            self.require_execution_binding(binding, scope=scope)
            install(config)
        except Exception:  # noqa: BLE001 - native config/error repr may contain credentials.
            raise AccountBindingError("ACCOUNT_NATIVE_CONFIGURATION_FAILED") from None

    async def aclose(self) -> None:
        self._binding = None
        self._identity = None
        self._closed = True
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001 - transport error may contain credentials.
            failure = AccountBindingError("ACCOUNT_SESSION_CLOSE_FAILED")
        else:
            return
        # Outside the handler: even __context__ must not retain the provider error.
        # BaseException (including cancellation) still propagates after revocation.
        raise failure from None
