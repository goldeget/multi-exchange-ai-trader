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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, Self
from uuid import uuid4

import httpx
from httpx._client import ClientState

from ..account.bybit_readonly import (
    BybitReadOnlyClient,
    _account_info_capture_data_v1,
    _account_info_capture_preimage,
    _AccountInfoCaptureV1,
    _private_account_endpoint_v1,
    _private_account_spec_v1,
    _PrivateAccountEndpointV1,
)
from ..account.config import allowed_host
from ..account.instrument_envelope import HttpsOrigin
from .bybit_native import build_demo_exec_config
from .history_receipts import AuthenticatedHistoryReceipt, HistoryReceiptView
from .monitoring_budget import MonitoringReadBudget
from .monitoring_models import (
    MonitoringReceiptView,
    MonitoringStatus,
    MonitoringTarget,
    _MonitoringTargetInvalidated,
)
from .monitoring_receipts import AuthenticatedMonitoringReceipt
from .protection import ProtectionScope

if TYPE_CHECKING:
    from .bybit_linear_model_inputs_v1 import (
        BybitDecimalFieldV1,
        BybitModelSourceRefV1,
        BybitTradeBehaviourV1,
        BybitUsdtWalletV1,
        BybitUtaModeV1,
    )

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


@dataclass(frozen=True, repr=False)
class _HistoryReceiptSlot:
    binding: AuthenticatedAccountBinding
    scope: ProtectionScope
    generation: object
    started_at: datetime
    started_mono: float
    binding_reference: str
    view: HistoryReceiptView | None = None


@dataclass(frozen=True, repr=False)
class _MonitoringSlot:
    binding: AuthenticatedAccountBinding
    target: MonitoringTarget
    fingerprint: str
    phase: Literal["PRE", "POST"]
    generation: object
    started_at: datetime
    started_mono: float
    view: MonitoringReceiptView | None = None


@dataclass(repr=False)
class _LinearAccountInfoSlot:
    issuer: BybitDemoAccountSession
    binding: AuthenticatedAccountBinding
    scope: ProtectionScope
    identity: BybitAccountIdentity | None
    client: object
    http_client: object
    credential_pair: tuple[str, str]
    owner_guard: Callable[[], None]
    generation: object
    loop: asyncio.AbstractEventLoop
    phase: Literal["PENDING", "ISSUED"] = "PENDING"
    retired: bool = False
    source: BybitModelSourceRefV1 | None = None
    capture: _AccountInfoCaptureV1 | None = None
    capture_preimage: tuple[object, ...] | None = None
    source_preimage: tuple[object, ...] | None = None
    source_sha256: str | None = None
    endpoint: _PrivateAccountEndpointV1 = "INFO"
    query: tuple[tuple[str, str], ...] = ()


def _linear_source_preimage(source: BybitModelSourceRefV1) -> tuple[object, ...]:
    from .bybit_linear_model_inputs_v1 import BybitModelSourceRefV1

    return _linear_source_data_v1(source, BybitModelSourceRefV1)


def _linear_source_data_v1(
    source: BybitModelSourceRefV1, source_type: type[BybitModelSourceRefV1],
) -> tuple[object, ...]:
    """Closed leaves only; the original model class was retained before callbacks."""
    if type(source) is not source_type:
        raise AccountBindingError("ACCOUNT_INFO_SOURCE_REQUIRED")
    texts = (source.observation_id, source.source_id, source.source_generation,
             source.source_environment, source.path, source.raw_body_sha256)
    if any(type(value) is not str for value in texts):
        raise AccountBindingError("ACCOUNT_INFO_SOURCE_REQUIRED")
    scope, origin = source.account_scope, source.origin
    if (type(scope) is not ProtectionScope or type(scope.account_id) is not str
            or type(scope.environment) is not str or type(origin) is not HttpsOrigin
            or type(origin.scheme) is not str or type(origin.host) is not str
            or type(origin.port) is not int or type(source.query) is not tuple
            or type(source.page_ordinal) is not int
            or type(source.accepted_attempt) is not int
            or source.envelope_time_ms is not None and type(source.envelope_time_ms) is not int):
        raise AccountBindingError("ACCOUNT_INFO_SOURCE_REQUIRED")
    _private_account_endpoint_v1(source.path, source.query)
    for stamp in (source.request_started_at, source.received_at):
        if type(stamp) is not datetime or stamp.tzinfo is not UTC:
            raise AccountBindingError("ACCOUNT_INFO_SOURCE_REQUIRED")
    return (*texts, scope.account_id, scope.environment, origin.scheme, origin.host, origin.port,
            source.query, source.request_started_at, source.received_at,
            source.envelope_time_ms, source.page_ordinal, source.accepted_attempt)


def _linear_capture_refs_v1(capture: _AccountInfoCaptureV1) -> tuple[object, ...]:
    if (type(capture) is not _AccountInfoCaptureV1 or type(capture.request) is not httpx.Request
            or type(capture.response) is not httpx.Response):
        raise AccountBindingError("ACCOUNT_INFO_SOURCE_REQUIRED")
    return (capture.owner, capture.http_client, capture.generation, capture.request,
            capture.response, capture.request.url, capture.request.headers, capture.response.headers,
            capture.wire, capture.response_headers, capture.response_header_wire, capture.budget,
            capture.group, capture.operation_context, capture.receipt_context, capture.receipt)


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


def _parse_account_metadata(result: dict[str, object], *, api_key: str, api_secret: str,
                            expected_uid: str, observed_at: datetime,
                            server_observed_at: datetime,
                            transport_injected: bool) -> BybitAccountIdentity:
    """Strict shared observation parser; it neither authenticates nor issues authority."""
    echoed_key = result.get("apiKey")
    user_id, master, read_only = result.get("userID"), result.get("isMaster"), result.get("readOnly")
    parent = _uid(result.get("parentUid"), allow_zero=True)
    if (type(echoed_key) is not str
            or not hmac.compare_digest(echoed_key.encode(), api_key.encode())
            or result.get("secret") != "" or type(user_id) is not int
            or user_id <= 0 or str(user_id) != expected_uid or type(master) is not bool
            or parent == expected_uid or type(read_only) is not int or read_only not in (0, 1)):
        raise AccountBindingError("AUTHENTICATED_ACCOUNT_IDENTITY_MISMATCH")
    identity = BybitAccountIdentity(expected_uid, parent, master, read_only == 1,
        _permissions(result.get("permissions")), observed_at, server_observed_at, transport_injected)
    if any(secret in repr(identity) for secret in (api_key, api_secret)):
        raise AccountBindingError("ACCOUNT_PUBLIC_IDENTITY_REDACTION_REQUIRED")
    return identity


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
        self._history_receipts: dict[object, _HistoryReceiptSlot] = {}
        self._history_generation = object()
        self._history_binding: AuthenticatedAccountBinding | None = None
        self._history_binding_reference = ""
        self._history_last_clock: tuple[datetime, float] | None = None
        self._attested: tuple[AuthenticatedAccountBinding, str, str, bool] | None = None
        self._credential_client = self._client
        self._credential_pair = self._api_key, self._api_secret
        self._binding_last_clock: tuple[datetime, float] | None = None
        self._binding_freshness_revoked = False
        self._monitoring_receipts: dict[object, _MonitoringSlot] = {}
        self._monitoring_generation = object()
        self._monitoring_last_clock: tuple[datetime, float] | None = None
        self._linear_info_self = self
        self._linear_info_generation = object()
        self._linear_info_pending: _LinearAccountInfoSlot | None = None
        self._linear_info_current: _LinearAccountInfoSlot | None = None
        self._linear_info_requiring: _LinearAccountInfoSlot | None = None
        self._linear_info_loop: asyncio.AbstractEventLoop | None = None
        self._linear_wallet_generation = object()
        self._linear_wallet_current: _LinearAccountInfoSlot | None = None
        self._linear_fee_generation = object()
        self._linear_fee_current: _LinearAccountInfoSlot | None = None
        self._linear_settings_generation = object()
        self._linear_settings_current: _LinearAccountInfoSlot | None = None

    def _retire_linear_account_info_v1(self) -> None:
        self._retire_linear_private_source_v1("INFO")

    def _linear_private_state_v1(
        self, endpoint: _PrivateAccountEndpointV1,
    ) -> tuple[object, _LinearAccountInfoSlot | None]:
        if type(endpoint) is not str:
            raise AccountBindingError("ACCOUNT_INFO_SOURCE_REQUIRED")
        if endpoint == "INFO":
            return self._linear_info_generation, self._linear_info_current
        if endpoint == "WALLET":
            return self._linear_wallet_generation, self._linear_wallet_current
        if endpoint == "FEE":
            return self._linear_fee_generation, self._linear_fee_current
        if endpoint == "SETTINGS":
            return self._linear_settings_generation, self._linear_settings_current
        raise AccountBindingError("ACCOUNT_INFO_SOURCE_REQUIRED")

    def _set_linear_private_current_v1(
        self, endpoint: _PrivateAccountEndpointV1, slot: _LinearAccountInfoSlot | None,
    ) -> None:
        if endpoint == "INFO":
            self._linear_info_current = slot
        elif endpoint == "WALLET":
            self._linear_wallet_current = slot
        elif endpoint == "FEE":
            self._linear_fee_current = slot
        elif endpoint == "SETTINGS":
            self._linear_settings_current = slot
        else:
            raise AccountBindingError("ACCOUNT_INFO_SOURCE_REQUIRED")

    def _retire_linear_private_source_v1(self, endpoint: _PrivateAccountEndpointV1) -> None:
        if endpoint == "INFO":
            self._linear_info_generation = object()
        elif endpoint == "WALLET":
            self._linear_wallet_generation = object()
        elif endpoint == "FEE":
            self._linear_fee_generation = object()
        elif endpoint == "SETTINGS":
            self._linear_settings_generation = object()
        else:
            raise AccountBindingError("ACCOUNT_INFO_SOURCE_REQUIRED")
        self._set_linear_private_current_v1(endpoint, None)
        if self._linear_info_pending is not None and self._linear_info_pending.endpoint == endpoint:
            self._linear_info_pending.retired = True
        if self._linear_info_requiring is not None and self._linear_info_requiring.endpoint == endpoint:
            self._linear_info_requiring.retired = True

    def _retire_linear_private_sources_v1(self) -> None:
        endpoints: tuple[_PrivateAccountEndpointV1, ...] = ("INFO", "WALLET", "FEE", "SETTINGS")
        for endpoint in endpoints:
            self._retire_linear_private_source_v1(endpoint)

    def _linear_private_shared_links_v1(self, slot: _LinearAccountInfoSlot) -> None:
        if (self._closed is not False or self._binding is not slot.binding
                or self._identity is not slot.identity or self._client is not slot.client
                or self._credential_client is not slot.client or type(slot.client) is not BybitReadOnlyClient
                or slot.client._client is not slot.http_client
                or self._credential_pair is not slot.credential_pair):
            self._retire_linear_private_sources_v1()
            raise AccountBindingError("ACCOUNT_INFO_SOURCE_RETIRED")

    def _linear_account_info_links_v1(self, slot: _LinearAccountInfoSlot) -> None:
        """Only original links, closed phases and primitive credential values."""
        if type(slot) is not _LinearAccountInfoSlot:
            raise AccountBindingError("ACCOUNT_INFO_SOURCE_RETIRED")
        self._linear_private_shared_links_v1(slot)
        generation, current = self._linear_private_state_v1(slot.endpoint)
        if (type(self) is not BybitDemoAccountSession or self._linear_info_self is not self
                or slot.issuer is not self
                or self._closed is not False
                or slot.retired is not False or slot.generation is not generation
                or self._binding is not slot.binding or self._identity is not slot.identity
                or self._client is not slot.client or self._credential_client is not slot.client
                or type(slot.client) is not BybitReadOnlyClient
                or slot.client._account_info_self is not slot.client
                or slot.client._client is not slot.http_client
                or type(slot.http_client) is not httpx.AsyncClient
                or type(slot.http_client._state) is not ClientState
                or slot.http_client._state not in (ClientState.UNOPENED, ClientState.OPENED)
                or self._credential_pair is not slot.credential_pair
                or self._linear_info_loop is not slot.loop
                or type(slot.phase) is not str
                or slot.phase == "PENDING" and self._linear_info_pending is not slot
                or slot.phase == "ISSUED" and current is not slot
                or slot.phase not in ("PENDING", "ISSUED")):
            raise AccountBindingError("ACCOUNT_INFO_SOURCE_RETIRED")
        if (type(slot.credential_pair) is not tuple or len(slot.credential_pair) != 2
                or any(type(value) is not str for value in slot.credential_pair)
                or type(self._api_key) is not str or type(self._api_secret) is not str
                or (self._api_key, self._api_secret) != slot.credential_pair
                or type(slot.client._api_key) is not str or type(slot.client._api_secret) is not str
                or (slot.client._api_key, slot.client._api_secret) != slot.credential_pair):
            self._retire_linear_private_sources_v1()
            raise AccountBindingError("ACCOUNT_INFO_SOURCE_RETIRED")
        if slot.capture is not None:
            generation, generation_text, source_id = slot.client._private_account_state_v1(slot.endpoint)
            if (type(slot.capture) is not _AccountInfoCaptureV1
                    or slot.capture.endpoint != slot.endpoint or slot.capture.query != slot.query
                    or type(slot.capture.source_id) is not str
                    or type(slot.capture.source_generation) is not str
                    or generation is not slot.capture.generation
                    or type(source_id) is not str or type(generation_text) is not str
                    or source_id != slot.capture.source_id or generation_text != slot.capture.source_generation):
                raise AccountBindingError("ACCOUNT_INFO_SOURCE_RETIRED")

    def _linear_account_info_current_v1(self, slot: _LinearAccountInfoSlot) -> None:
        self._linear_account_info_links_v1(slot)
        assert type(slot.client) is BybitReadOnlyClient
        if asyncio.get_running_loop() is not slot.loop:
            raise AccountBindingError("ACCOUNT_INFO_SOURCE_RETIRED")

    def _check_linear_account_info_v1(
        self, slot: _LinearAccountInfoSlot, *, scope: ProtectionScope | None = None,
    ) -> None:
        self._linear_account_info_current_v1(slot)
        slot.owner_guard()
        self._linear_account_info_current_v1(slot)
        source_type: type[BybitModelSourceRefV1] | None = None
        if slot.phase == "ISSUED":
            from . import bybit_linear_model_inputs_v1 as a

            source_type = a.BybitModelSourceRefV1
            if (slot.source is None or slot.capture is None
                    or type(slot.source_preimage) is not tuple or type(slot.capture_preimage) is not tuple
                    or _linear_source_preimage(slot.source) != slot.source_preimage
                    or a.bybit_source_ref_sha256_v1(slot.source) != slot.source_sha256
                    or _account_info_capture_preimage(slot.capture) != slot.capture_preimage):
                raise AccountBindingError("ACCOUNT_INFO_SOURCE_RETIRED")
            self._linear_account_info_current_v1(slot)
        selected_scope = slot.scope if scope is None else scope
        original_scope, original_source, original_capture, original_phase = (
            slot.scope, slot.source, slot.capture, slot.phase)
        source_witness, capture_witness = slot.source_preimage, slot.capture_preimage
        scope_values: list[tuple[str, str]] = []
        for checked_scope in (slot.scope, selected_scope):
            if (type(checked_scope) is not ProtectionScope
                    or type(checked_scope.account_id) is not str
                    or type(checked_scope.environment) is not str):
                raise AccountBindingError("ACCOUNT_INFO_SOURCE_REQUIRED")
            scope_values.append((checked_scope.account_id, checked_scope.environment))
        capture_refs = (() if original_capture is None else _linear_capture_refs_v1(original_capture))
        identity = self.require_observation_binding(slot.binding, scope=selected_scope)
        if identity is not slot.identity:
            raise AccountBindingError("ACCOUNT_INFO_SOURCE_RETIRED")
        self._linear_account_info_links_v1(slot)
        # Finite pure suffix: no guard, clock, active budget, serializer, hash,
        # JSON or import may follow the last genuine binding callback.
        if (slot.scope is not original_scope or slot.source is not original_source
                or slot.capture is not original_capture or slot.phase != original_phase
                or slot.source_preimage is not source_witness
                or slot.capture_preimage is not capture_witness):
            raise AccountBindingError("ACCOUNT_INFO_SOURCE_RETIRED")
        for checked_scope, expected in zip((slot.scope, selected_scope), scope_values, strict=True):
            if (type(checked_scope) is not ProtectionScope
                    or type(checked_scope.account_id) is not str
                    or type(checked_scope.environment) is not str
                    or (checked_scope.account_id, checked_scope.environment) != expected):
                raise AccountBindingError("ACCOUNT_INFO_SOURCE_RETIRED")
        if (type(identity) is not BybitAccountIdentity or type(identity.uid) is not str
                or identity.uid != scope_values[0][0]):
            raise AccountBindingError("ACCOUNT_INFO_SOURCE_RETIRED")
        if original_phase == "ISSUED":
            assert original_source is not None and original_capture is not None and source_type is not None
            refs = _linear_capture_refs_v1(original_capture)
            if (any(current is not prior for current, prior in zip(refs, capture_refs, strict=True))
                    or _linear_source_data_v1(original_source, source_type) != source_witness
                    or _account_info_capture_data_v1(original_capture) != capture_witness):
                raise AccountBindingError("ACCOUNT_INFO_SOURCE_RETIRED")

    async def _capture_linear_account_info_v1(
        self, binding: AuthenticatedAccountBinding, *, scope: ProtectionScope,
        budget: MonitoringReadBudget, owner_guard: Callable[[], None],
    ) -> BybitModelSourceRefV1:
        return await self._capture_linear_private_source_v1("INFO", binding, scope=scope,
            budget=budget, owner_guard=owner_guard)

    async def _capture_linear_wallet_v1(
        self, binding: AuthenticatedAccountBinding, *, scope: ProtectionScope,
        budget: MonitoringReadBudget, owner_guard: Callable[[], None],
    ) -> BybitModelSourceRefV1:
        return await self._capture_linear_private_source_v1("WALLET", binding, scope=scope,
            budget=budget, owner_guard=owner_guard)

    async def _capture_linear_fee_rate_v1(
        self, binding: AuthenticatedAccountBinding, *, symbol: str, scope: ProtectionScope,
        budget: MonitoringReadBudget, owner_guard: Callable[[], None],
    ) -> BybitModelSourceRefV1:
        return await self._capture_linear_private_source_v1("FEE", binding, symbol=symbol,
            scope=scope, budget=budget, owner_guard=owner_guard)

    async def _capture_linear_settings_v1(
        self, binding: AuthenticatedAccountBinding, *, scope: ProtectionScope,
        budget: MonitoringReadBudget, owner_guard: Callable[[], None],
    ) -> BybitModelSourceRefV1:
        return await self._capture_linear_private_source_v1("SETTINGS", binding, scope=scope,
            budget=budget, owner_guard=owner_guard)

    async def _capture_linear_private_source_v1(
        self, endpoint: _PrivateAccountEndpointV1, binding: AuthenticatedAccountBinding, *,
        symbol: str | None = None, scope: ProtectionScope,
        budget: MonitoringReadBudget, owner_guard: Callable[[], None],
    ) -> BybitModelSourceRefV1:
        if type(self) is not BybitDemoAccountSession or self._linear_info_self is not self:
            raise AccountBindingError("ACCOUNT_INFO_SOURCE_REQUIRED")
        if self._linear_info_pending is not None or self._linear_info_requiring is not None:
            self._retire_linear_private_sources_v1()
            raise AccountBindingError("ACCOUNT_INFO_SOURCE_OVERLAP")
        path, query = _private_account_spec_v1(endpoint, symbol)
        self._retire_linear_private_source_v1(endpoint)
        generation, _current = self._linear_private_state_v1(endpoint)
        loop = asyncio.get_running_loop()
        if self._linear_info_loop is None:
            self._linear_info_loop = loop
        client = self._client
        slot = _LinearAccountInfoSlot(self, binding, scope, self._identity, client,
            client._client if type(client) is BybitReadOnlyClient else None,
            self._credential_pair, owner_guard, generation, loop, endpoint=endpoint, query=query)
        self._linear_info_pending = slot
        complete = False
        try:
            from . import bybit_linear_model_inputs_v1 as a

            if type(budget) is not MonitoringReadBudget or not callable(owner_guard):
                raise AccountBindingError("ACCOUNT_INFO_SOURCE_REQUIRED")
            self._check_linear_account_info_v1(slot)
            assert type(client) is BybitReadOnlyClient

            def guard() -> None:
                self._check_linear_account_info_v1(slot)

            with client._monitoring_operation(budget), budget.reuse_or_open_receipt() as group:
                captured = await client._capture_private_account_v1(endpoint, symbol=symbol,
                    budget=budget, group=group, owner_guard=guard)
                guard()
                preimage = _account_info_capture_preimage(captured)
                source = a.BybitModelSourceRefV1(captured.observation_id, captured.source_id,
                    captured.source_generation, scope, "DEMO", captured.sent_origin,
                    path, query, captured.request_started_at, captured.received_at,
                    captured.envelope_time_ms, captured.raw_body_sha256, 1,
                    captured.accepted_attempt)
                source_hash = a.bybit_source_ref_sha256_v1(source)
                source_preimage = _linear_source_preimage(source)
                if _account_info_capture_preimage(captured) != preimage:
                    raise AccountBindingError("ACCOUNT_INFO_SOURCE_RETIRED")
                guard()
            # Receipt closes normally. Issued require checks the retained owner,
            # not the historical group's now-inactive flag.
            guard()
            slot.source, slot.capture = source, captured
            slot.capture_preimage, slot.source_preimage = preimage, source_preimage
            slot.source_sha256 = source_hash
            slot.phase = "ISSUED"
            self._set_linear_private_current_v1(endpoint, slot)
            self._require_linear_private_source_v1(endpoint, source, binding=binding, scope=scope, symbol=symbol)
            complete = True
            return source
        except Exception:  # noqa: BLE001 - no private body/guard exception context escapes.
            complete = False
        finally:
            if self._linear_info_pending is slot:
                if not complete:
                    self._retire_linear_private_source_v1(endpoint)
                self._linear_info_pending = None
        raise AccountBindingError("ACCOUNT_INFO_SOURCE_CAPTURE_FAILED") from None

    def _require_linear_account_info_v1(
        self, source: BybitModelSourceRefV1, *, binding: AuthenticatedAccountBinding,
        scope: ProtectionScope,
    ) -> None:
        self._require_linear_private_source_v1("INFO", source, binding=binding, scope=scope)

    def _require_linear_wallet_v1(
        self, source: BybitModelSourceRefV1, *, binding: AuthenticatedAccountBinding, scope: ProtectionScope,
    ) -> None:
        self._require_linear_private_source_v1("WALLET", source, binding=binding, scope=scope)

    def _require_linear_fee_rate_v1(
        self, source: BybitModelSourceRefV1, *, symbol: str,
        binding: AuthenticatedAccountBinding, scope: ProtectionScope,
    ) -> None:
        self._require_linear_private_source_v1("FEE", source, symbol=symbol, binding=binding, scope=scope)

    def _require_linear_settings_v1(
        self, source: BybitModelSourceRefV1, *, binding: AuthenticatedAccountBinding, scope: ProtectionScope,
    ) -> None:
        self._require_linear_private_source_v1("SETTINGS", source, binding=binding, scope=scope)

    def _require_linear_private_source_v1(
        self, endpoint: _PrivateAccountEndpointV1, source: BybitModelSourceRefV1, *,
        symbol: str | None = None, binding: AuthenticatedAccountBinding,
        scope: ProtectionScope,
    ) -> None:
        if type(self) is not BybitDemoAccountSession or self._linear_info_self is not self:
            raise AccountBindingError("ACCOUNT_INFO_SOURCE_REQUIRED")
        if self._linear_info_requiring is not None:
            self._retire_linear_private_sources_v1()
            raise AccountBindingError("ACCOUNT_INFO_SOURCE_OVERLAP")
        _path, query = _private_account_spec_v1(endpoint, symbol)
        _generation, slot = self._linear_private_state_v1(endpoint)
        if (slot is None or source is not slot.source or binding is not slot.binding
                or type(scope) is not ProtectionScope or slot.endpoint != endpoint or slot.query != query):
            raise AccountBindingError("ACCOUNT_INFO_SOURCE_REQUIRED")
        self._linear_info_requiring = slot
        try:
            self._check_linear_account_info_v1(slot, scope=scope)
            return
        except Exception:  # noqa: BLE001 - currentness errors reveal no private transport data.
            self._retire_linear_private_source_v1(endpoint)
        except BaseException:
            self._retire_linear_private_source_v1(endpoint)
            raise
        finally:
            if self._linear_info_requiring is slot:
                self._linear_info_requiring = None
        raise AccountBindingError("ACCOUNT_INFO_SOURCE_REQUIRED") from None

    def _project_linear_account_mode_v1(
        self, source: BybitModelSourceRefV1, *, binding: AuthenticatedAccountBinding,
        scope: ProtectionScope,
    ) -> BybitUtaModeV1:
        """Project data from the original current capture, without issuing authority."""
        mode: BybitUtaModeV1 | None = None
        try:
            from ..account.bybit_readonly import _parse_json
            from .bybit_linear_model_inputs_v1 import BybitUtaModeV1

            self._require_linear_account_info_v1(source, binding=binding, scope=scope)
            slot = self._linear_info_current
            if type(slot) is not _LinearAccountInfoSlot or slot.source is not source:
                raise AccountBindingError("ACCOUNT_MODE_PROJECTION_REQUIRED")
            capture = slot.capture
            if type(capture) is not _AccountInfoCaptureV1 or type(capture.raw_body) is not bytes:
                raise AccountBindingError("ACCOUNT_MODE_PROJECTION_REQUIRED")
            raw = capture.raw_body
            envelope = _parse_json(raw)
            if (type(envelope) is not dict or type(envelope.get("retCode")) is not int
                    or envelope["retCode"] != 0 or type(envelope.get("result")) is not dict):
                raise AccountBindingError("ACCOUNT_MODE_PROJECTION_REQUIRED")
            result = envelope["result"]
            status = result.get("unifiedMarginStatus")
            margin = result.get("marginMode")
            updated = result.get("updatedTime")
            if (type(status) is not int or status not in (5, 6)
                    or type(margin) is not str or margin != "ISOLATED_MARGIN"
                    or type(updated) is not str or not 1 <= len(updated) <= 15
                    or not updated.isascii() or not updated.isdigit()):
                raise AccountBindingError("ACCOUNT_MODE_PROJECTION_REQUIRED")
            updated_ms = int(updated)
            if not 1 <= updated_ms <= 253402300799999:
                raise AccountBindingError("ACCOUNT_MODE_PROJECTION_REQUIRED")
            mode = BybitUtaModeV1(source, 5 if status == 5 else 6, "ISOLATED_MARGIN", updated_ms)
            self._require_linear_account_info_v1(source, binding=binding, scope=scope)
            # Only original local links/exact bytes after the final binding callbacks.
            if (self._linear_info_current is not slot or slot.source is not source
                    or slot.capture is not capture or type(capture.raw_body) is not bytes
                    or capture.raw_body is not raw):
                raise AccountBindingError("ACCOUNT_MODE_PROJECTION_REQUIRED")
            return mode
        except Exception:  # noqa: BLE001 - private wire/guard context must not escape.
            mode = None
        raise AccountBindingError("ACCOUNT_MODE_PROJECTION_REQUIRED") from None

    def __repr__(self) -> str:
        return "BybitDemoAccountSession(environment=DEMO, credentials=REDACTED)"

    def _project_linear_wallet_v1(
        self, source: BybitModelSourceRefV1, *, binding: AuthenticatedAccountBinding, scope: ProtectionScope,
    ) -> BybitUsdtWalletV1:
        from .bybit_linear_model_inputs_v1 import BybitUsdtWalletV1

        result = self._project_linear_private_data_v1("WALLET", source, binding=binding, scope=scope)
        if type(result) is not BybitUsdtWalletV1:
            raise AccountBindingError("ACCOUNT_DATA_PROJECTION_REQUIRED")
        return result

    def _project_linear_fee_rates_v1(
        self, source: BybitModelSourceRefV1, *, symbol: str,
        binding: AuthenticatedAccountBinding, scope: ProtectionScope,
    ) -> tuple[BybitDecimalFieldV1, BybitDecimalFieldV1]:
        result = self._project_linear_private_data_v1("FEE", source, symbol=symbol, binding=binding, scope=scope)
        if type(result) is not tuple:
            raise AccountBindingError("ACCOUNT_DATA_PROJECTION_REQUIRED")
        return result

    def _project_linear_trade_behaviour_v1(
        self, source: BybitModelSourceRefV1, *, binding: AuthenticatedAccountBinding, scope: ProtectionScope,
    ) -> BybitTradeBehaviourV1:
        from .bybit_linear_model_inputs_v1 import BybitTradeBehaviourV1

        result = self._project_linear_private_data_v1("SETTINGS", source, binding=binding, scope=scope)
        if type(result) is not BybitTradeBehaviourV1:
            raise AccountBindingError("ACCOUNT_DATA_PROJECTION_REQUIRED")
        return result

    def _project_linear_private_data_v1(
        self, endpoint: _PrivateAccountEndpointV1, source: BybitModelSourceRefV1, *,
        binding: AuthenticatedAccountBinding, scope: ProtectionScope, symbol: str | None = None,
    ) -> BybitUsdtWalletV1 | BybitTradeBehaviourV1 | tuple[BybitDecimalFieldV1, BybitDecimalFieldV1]:
        result: BybitUsdtWalletV1 | BybitTradeBehaviourV1 | tuple[BybitDecimalFieldV1, BybitDecimalFieldV1] | None = None
        try:
            from . import bybit_account_projections_v1 as projection

            self._require_linear_private_source_v1(endpoint, source, symbol=symbol, binding=binding, scope=scope)
            _generation, slot = self._linear_private_state_v1(endpoint)
            if type(slot) is not _LinearAccountInfoSlot or slot.source is not source:
                raise AccountBindingError("ACCOUNT_DATA_PROJECTION_REQUIRED")
            capture = slot.capture
            if type(capture) is not _AccountInfoCaptureV1 or type(capture.raw_body) is not bytes:
                raise AccountBindingError("ACCOUNT_DATA_PROJECTION_REQUIRED")
            raw = capture.raw_body
            if endpoint == "WALLET":
                result = projection.project_bybit_usdt_wallet_v1(source, raw)
            elif endpoint == "FEE" and type(symbol) is str:
                result = projection.project_bybit_linear_fee_rates_v1(source, raw, symbol=symbol)
            elif endpoint == "SETTINGS":
                result = projection.project_bybit_trade_behaviour_v1(source, raw)
            else:
                raise AccountBindingError("ACCOUNT_DATA_PROJECTION_REQUIRED")
            self._require_linear_private_source_v1(endpoint, source, symbol=symbol, binding=binding, scope=scope)
            # Only original current links/bytes after the final genuine binding callbacks.
            if (self._linear_private_state_v1(endpoint)[1] is not slot or slot.source is not source
                    or slot.capture is not capture or type(capture.raw_body) is not bytes
                    or capture.raw_body is not raw):
                raise AccountBindingError("ACCOUNT_DATA_PROJECTION_REQUIRED")
            return result
        except Exception:  # noqa: BLE001 - never retain private body/guard error context.
            result = None
        raise AccountBindingError("ACCOUNT_DATA_PROJECTION_REQUIRED") from None

    def _guard(self) -> None:
        if (self._closed or (self._api_key, self._api_secret) != self._credential_pair
                or self._client is not self._credential_client or type(self._client) is not BybitReadOnlyClient
                or self._client.environment != "demo"
                or self._client.base_url != allowed_host("demo")
                or self._client._api_key != self._api_key
                or self._client._api_secret != self._api_secret):
            self._binding = None
            self._identity = None
            self._attested = None
            self._revoke_history_receipts()
            raise AccountBindingError("ACCOUNT_SESSION_MISMATCH_OR_CLOSED")

    def _revoke_history_receipts(self) -> None:
        self._retire_linear_private_sources_v1()
        self._revoke_monitoring_receipts()
        self._history_receipts.clear()
        self._history_generation = object()
        self._history_binding = None
        self._history_binding_reference = ""
        self._history_last_clock = None

    def _revoke_monitoring_receipts(self) -> None:
        self._monitoring_receipts.clear()
        self._monitoring_generation = object()
        self._monitoring_last_clock = None

    def _prune_monitoring_receipts(self) -> tuple[datetime, float]:
        try:
            now, mono = _utc(self._clock()), self._mono()
            prior = self._monitoring_last_clock
            if prior is not None and (now < prior[0] or mono < prior[1]):
                raise AccountBindingError("MONITORING_CLOCK_INVALID")
        except Exception:  # noqa: BLE001 - fixed codes prevent credential/provider disclosure.
            self._revoke_monitoring_receipts()
            self._attested = None
            raise AccountBindingError("MONITORING_CLOCK_INVALID") from None
        self._monitoring_last_clock = now, mono
        ttl = min(self._max_age, timedelta(seconds=30))
        for key, slot in tuple(self._monitoring_receipts.items()):
            valid = (slot.generation is self._monitoring_generation
                     and timedelta(0) <= now - slot.started_at <= ttl
                     and 0 <= mono - slot.started_mono <= ttl.total_seconds())
            if slot.view is not None:
                valid = valid and all(timedelta(0) <= now - _server_time(stamp) <= ttl
                                      for stamp in slot.view.observation.response_times_ms)
            if not valid:
                self._monitoring_receipts.pop(key, None)
        return now, mono

    @contextmanager
    def _monitoring_operation(self, budget: MonitoringReadBudget) -> Iterator[None]:
        """Worker's shared scope spans refresh, PRE, native startup and POST."""
        self._guard()
        with self._client._monitoring_operation(budget):
            yield

    def _prune_history_receipts(self) -> tuple[datetime, float]:
        """Prune expired pending/issued slots without evicting live handles."""
        try:
            now, mono = _utc(self._clock()), self._mono()
            if self._history_last_clock is not None:
                last_wall, last_mono = self._history_last_clock
                if now < last_wall or mono < last_mono:
                    raise AccountBindingError("AUTHENTICATED_HISTORY_CLOCK_INVALID")
        except Exception:  # noqa: BLE001 - clock callbacks cannot expose private data.
            self._revoke_history_receipts()
        else:
            self._history_last_clock = now, mono
            ttl = min(self._max_age, timedelta(seconds=30))
            for key, slot in tuple(self._history_receipts.items()):
                valid = (slot.generation is self._history_generation
                         and timedelta(0) <= now - slot.started_at <= ttl
                         and 0 <= mono - slot.started_mono <= ttl.total_seconds())
                if slot.view is not None:
                    snapshot = slot.view.snapshot
                    valid = (valid and timedelta(0) <= now - snapshot.observed_at <= ttl
                             and timedelta(0) <= now - snapshot.server_observed_at <= ttl)
                if not valid:
                    self._history_receipts.pop(key, None)
            return now, mono
        raise AccountBindingError("AUTHENTICATED_HISTORY_CLOCK_INVALID") from None

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
        """Compare all three explicit expectations; every attempt revokes prior proof.

        DEMO can return isMaster=False with parentUid="0". Never derive either
        expected field from the other or use the returned value as its own expectation.
        High-level callers retain their source context: independently confirmed
        expectations, or explicitly enrolled uid-anchored-adoption-v1 configuration.
        The latter is provider-observed/operator-adopted, not independent evidence.
        """
        self._retire_linear_private_sources_v1()
        async with self._refresh_lock:
            self._revoke_history_receipts()
            self._attested = None
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
                identity = _parse_account_metadata(result, api_key=self._api_key,
                    api_secret=self._api_secret, expected_uid=uid, observed_at=now,
                    server_observed_at=observed, transport_injected=self._transport_injected)
                if identity.is_master != expected_is_master or identity.parent_uid != parent:
                    raise AccountBindingError("AUTHENTICATED_ACCOUNT_IDENTITY_MISMATCH")
            except Exception:  # noqa: BLE001 - no provider error/body/credential can escape.
                raise AccountBindingError("ACCOUNT_AUTHENTICATION_FAILED") from None
            self._binding = object.__new__(AuthenticatedAccountBinding)
            self._identity, self._issued_mono = identity, mono
            self._binding_last_clock = now, mono
            self._binding_freshness_revoked = False
            self._attested = self._binding, uid, parent, expected_is_master
            return self._binding

    async def refresh_monitoring_binding(self, prior_binding: AuthenticatedAccountBinding, *,
                                         scope: ProtectionScope) -> AuthenticatedAccountBinding:
        self._guard()
        attested = self._attested
        if (attested is None or prior_binding is not attested[0]
                or type(prior_binding) is not AuthenticatedAccountBinding
                or type(scope) is not ProtectionScope or scope != ProtectionScope(attested[1], "DEMO")
                or self._transport_injected):
            raise AccountBindingError("MONITORING_PRIOR_ATTESTATION_REQUIRED")
        return await self.authenticate(expected_uid=attested[1], expected_parent_uid=attested[2],
                                       expected_is_master=attested[3])

    def configure_native_monitoring(self, binding: AuthenticatedAccountBinding, *,
                                    scope: ProtectionScope, install: Callable[[object], None]) -> None:
        self.require_observation_binding(binding, scope=scope)
        try:
            config = build_demo_exec_config(api_key=self._api_key, api_secret=self._api_secret)
            self.require_observation_binding(binding, scope=scope)
            install(config)
        except Exception:  # noqa: BLE001 - fixed codes prevent credential/provider disclosure.
            failure = AccountBindingError("ACCOUNT_NATIVE_CONFIGURATION_FAILED")
        else:
            return
        raise failure from None

    async def read_monitoring_receipt(self, binding: AuthenticatedAccountBinding, *,
                                      target: MonitoringTarget,
                                      phase: Literal["PRE", "POST"]) -> AuthenticatedMonitoringReceipt:
        from .monitoring_readback import BybitMonitoringReader
        from .monitoring_reconciliation import compare_restored_state, require_current_target

        if type(target) is not MonitoringTarget:
            raise AccountBindingError("MONITORING_TARGET_REQUIRED")
        now, mono = self._prune_monitoring_receipts()
        self.require_observation_binding(binding, scope=target.scope)
        require_current_target(target, self, phase)
        if len(self._monitoring_receipts) >= 16:
            raise AccountBindingError("MONITORING_RECEIPT_CAPACITY")
        pending = object()
        slot = _MonitoringSlot(binding, target, target.fingerprint, phase,
                               self._monitoring_generation, now, mono)
        self._monitoring_receipts[pending] = slot
        receipt: AuthenticatedMonitoringReceipt | None = None

        def require_owner() -> None:
            self._prune_monitoring_receipts()
            self.require_observation_binding(binding, scope=target.scope)
            assert target._witness is not None
            target._witness.require()
            if (self._monitoring_receipts.get(pending) is not slot
                    or slot.generation is not self._monitoring_generation):
                raise AccountBindingError("MONITORING_RECEIPT_REVOKED")

        def guard() -> None:
            require_owner()
            require_current_target(target, self, phase)

        failure: ValueError
        try:
            observation = await BybitMonitoringReader(self, binding, target=target,
                guard=guard, owner_guard=require_owner).read()
            guard()
            assessment = compare_restored_state(target, observation)
            if assessment.status is MonitoringStatus.BLOCKED:
                raise AccountBindingError("MONITORING_STATE_MISMATCH")
            view = MonitoringReceiptView(observation, assessment, slot.fingerprint, phase)
            receipt = object.__new__(AuthenticatedMonitoringReceipt)
            self._monitoring_receipts.pop(pending)
            self._monitoring_receipts[receipt] = _MonitoringSlot(binding, target, slot.fingerprint,
                phase, slot.generation, now, mono, view)
            self.require_monitoring_receipt(receipt, binding=binding, target=target, phase=phase)
            return receipt
        except _MonitoringTargetInvalidated:
            failure = _MonitoringTargetInvalidated("MONITORING_TARGET_CHANGED")
        except Exception:  # noqa: BLE001 - fixed codes prevent credential/provider disclosure.
            failure = AccountBindingError("MONITORING_READ_FAILED")
        finally:
            self._monitoring_receipts.pop(pending, None)
        if receipt is not None:
            self._monitoring_receipts.pop(receipt, None)
        raise failure from None

    def require_monitoring_receipt(self, receipt: AuthenticatedMonitoringReceipt, *,
                                   binding: AuthenticatedAccountBinding, target: MonitoringTarget,
                                   phase: Literal["PRE", "POST"]) -> MonitoringReceiptView:
        from .monitoring_reconciliation import require_current_target

        if type(target) is not MonitoringTarget:
            raise AccountBindingError("MONITORING_TARGET_REQUIRED")
        self._prune_monitoring_receipts()
        self.require_observation_binding(binding, scope=target.scope)
        require_current_target(target, self, phase)
        if type(receipt) is not AuthenticatedMonitoringReceipt:
            raise AccountBindingError("AUTHENTICATED_MONITORING_RECEIPT_REQUIRED")
        slot = self._monitoring_receipts.get(receipt)
        if (slot is None or slot.view is None or slot.binding is not binding
                or slot.target is not target or slot.fingerprint != target.fingerprint
                or slot.phase != phase or slot.generation is not self._monitoring_generation):
            raise AccountBindingError("AUTHENTICATED_MONITORING_RECEIPT_REQUIRED")
        return slot.view

    def consume_monitoring_receipt(self, receipt: AuthenticatedMonitoringReceipt, *,
                                   binding: AuthenticatedAccountBinding, target: MonitoringTarget,
                                   phase: Literal["PRE", "POST"]) -> MonitoringReceiptView:
        view = self.require_monitoring_receipt(receipt, binding=binding, target=target, phase=phase)
        self._monitoring_receipts.pop(receipt)
        return view

    def discard_monitoring_receipt(self, receipt: AuthenticatedMonitoringReceipt) -> None:
        if type(receipt) is AuthenticatedMonitoringReceipt:
            self._monitoring_receipts.pop(receipt, None)

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
        try:
            now, mono = _utc(self._clock()), self._mono()
            prior = self._binding_last_clock
            if prior is not None and (now < prior[0] or mono < prior[1]):
                self._attested = None
                raise AccountBindingError("ACCOUNT_BINDING_CLOCK_INVALID")
            self._binding_last_clock = now, mono
            if self._binding_freshness_revoked:
                raise AccountBindingError("ACCOUNT_BINDING_STALE")
            self._fresh(identity.observed_at, now)
            self._fresh(identity.server_observed_at, now)
            if not 0 <= mono - self._issued_mono <= self._max_age.total_seconds():
                raise AccountBindingError("ACCOUNT_BINDING_STALE")
        except Exception:  # noqa: BLE001 - revoke before reporting a fixed freshness code.
            self._binding_freshness_revoked = True
            self._retire_linear_private_sources_v1()
            self._revoke_monitoring_receipts()
            raise AccountBindingError("ACCOUNT_BINDING_STALE") from None
        return identity

    def require_execution_binding(self, binding: AuthenticatedAccountBinding, *,
                                  scope: ProtectionScope) -> BybitAccountIdentity:
        identity = self.require_binding(binding, scope=scope)
        if identity.transport_injected:
            raise AccountBindingError("ACCOUNT_NETWORK_TRANSPORT_REQUIRED")
        if not identity.linear_execution_permitted:
            raise AccountBindingError("ACCOUNT_LINEAR_PERMISSIONS_REQUIRED")
        return identity

    def require_observation_binding(self, binding: AuthenticatedAccountBinding, *,
                                    scope: ProtectionScope) -> BybitAccountIdentity:
        """Actual-session read authority, independent of permission to trade."""
        identity = self.require_binding(binding, scope=scope)
        if self._transport_injected or identity.transport_injected:
            raise AccountBindingError("ACCOUNT_NETWORK_TRANSPORT_REQUIRED")
        return identity

    async def read_history_receipt(self, binding: AuthenticatedAccountBinding, *,
                                   scope: ProtectionScope, symbol: str,
                                   start_ms: int, end_ms: int) -> AuthenticatedHistoryReceipt:
        """Read through the owned exact reader, reserving bounded GET-start lifetime."""
        from .history import BybitHistoryReader, validate_history_snapshot

        now, mono = self._prune_history_receipts()
        self.require_observation_binding(binding, scope=scope)
        if len(self._history_receipts) >= 4:
            raise AccountBindingError("AUTHENTICATED_HISTORY_RECEIPT_CAPACITY")
        if self._history_binding is not binding:
            self._history_binding = binding
            self._history_binding_reference = uuid4().hex
        pending = object()
        slot = _HistoryReceiptSlot(binding, scope, self._history_generation, now, mono,
                                   self._history_binding_reference)
        self._history_receipts[pending] = slot
        receipt: AuthenticatedHistoryReceipt | None = None
        try:
            snapshot = await BybitHistoryReader(self, binding, scope=scope, clock=self._clock,
                max_age=min(self._max_age, timedelta(seconds=30))).read_history(
                    symbol, start_ms=start_ms, end_ms=end_ms)
            self._prune_history_receipts()
            self.require_observation_binding(binding, scope=scope)
            if (self._history_receipts.get(pending) is not slot
                    or slot.generation is not self._history_generation):
                raise AccountBindingError("AUTHENTICATED_HISTORY_RECEIPT_REQUIRED")
            validate_history_snapshot(snapshot)
            if (snapshot.transport_injected or snapshot.scope != scope
                    or snapshot.symbol != symbol or snapshot.window.start_ms != start_ms
                    or snapshot.window.end_ms != end_ms):
                raise AccountBindingError("AUTHENTICATED_HISTORY_RECEIPT_REQUIRED")
            view = HistoryReceiptView(snapshot, slot.binding_reference)
            receipt = object.__new__(AuthenticatedHistoryReceipt)
            self._history_receipts.pop(pending, None)
            self._history_receipts[receipt] = _HistoryReceiptSlot(binding, scope,
                slot.generation, slot.started_at, slot.started_mono, slot.binding_reference, view)
            # The lookup applies all original observation-time checks before issuance.
            self.require_history_receipt(receipt, binding=binding, scope=scope)
            return receipt
        except Exception:  # noqa: BLE001 - reader/transport errors never cross this boundary.
            if receipt is not None:
                self._history_receipts.pop(receipt, None)
            # Outside the handler below: no retained provider exception context.
        finally:
            self._history_receipts.pop(pending, None)
        raise AccountBindingError("AUTHENTICATED_HISTORY_READ_FAILED") from None

    def require_history_receipt(self, receipt: AuthenticatedHistoryReceipt, *,
                                binding: AuthenticatedAccountBinding,
                                scope: ProtectionScope) -> HistoryReceiptView:
        """Validate exact session/handle/binding identity immediately before use."""
        self._prune_history_receipts()
        self.require_observation_binding(binding, scope=scope)
        if type(receipt) is not AuthenticatedHistoryReceipt:
            raise AccountBindingError("AUTHENTICATED_HISTORY_RECEIPT_REQUIRED")
        slot = self._history_receipts.get(receipt)
        if (slot is None or slot.view is None or slot.binding is not binding
                or slot.scope != scope or slot.generation is not self._history_generation):
            raise AccountBindingError("AUTHENTICATED_HISTORY_RECEIPT_REQUIRED")
        return slot.view

    def discard_history_receipt(self, receipt: AuthenticatedHistoryReceipt) -> None:
        """Idempotently release this session's handle; foreign data grants nothing."""
        self._prune_history_receipts()
        if type(receipt) is AuthenticatedHistoryReceipt:
            self._history_receipts.pop(receipt, None)

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
        self._revoke_history_receipts()
        self._binding = None
        self._identity = None
        self._closed = True
        self._attested = None
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001 - transport error may contain credentials.
            failure = AccountBindingError("ACCOUNT_SESSION_CLOSE_FAILED")
        else:
            return
        # Outside the handler: even __context__ must not retain the provider error.
        # BaseException (including cancellation) still propagates after revocation.
        raise failure from None
