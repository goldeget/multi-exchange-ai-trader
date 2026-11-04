"""Explicit server-internal DEMO settings preparation, never an order capability.

Inspect/plan only GET. Apply independently authenticates the selected saved profile,
checks the whole affected account, and sends each fixed setting at most once. Reads
are sequential observations, not an atomic account lock or financial qualification.
The price-action endpoint's DEMO availability is not guaranteed by the DEMO list.
"""

import asyncio
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Self, cast

import httpx

from ..account_profiles import (
    AccountProfileSnapshot,
    AccountProfileStore,
    default_account_profile_store,
)
from ..credential_resolution import CredentialResolver
from ..execution.account_binding import AuthenticatedAccountBinding, BybitDemoAccountSession
from ..execution.protection import ProtectionScope
from .bybit_readonly import RECV_WINDOW, BybitReadOnlyClient, _parse_json, sign_v5
from .config import allowed_host

_POLICIES = frozenset({"independent-v1", "uid-anchored-adoption-v1"})
_HOST = allowed_host("demo")
_INFO = "/v5/account/info"
_SETTINGS = "/v5/account/user-setting-config"
_WALLET = "/v5/account/wallet-balance"
_POSITIONS = "/v5/position/list"
_ORDERS = "/v5/order/realtime"
_PRICE = "/v5/account/set-limit-px-action"
_MARGIN = "/v5/account/set-margin-mode"
_ACTIONS = ((_PRICE, '{"category":"linear","modifyEnable":false}'),
            (_MARGIN, '{"setMarginMode":"ISOLATED_MARGIN"}'))
_MAX_BYTES = 65_536
_MAX_PAGES = 8


class _Denied(ValueError):
    """Fixed local code only; provider bodies and exception contexts stay private."""


@dataclass(frozen=True, slots=True)
class DemoConfigurationStepV1:
    path: str
    outcome: str
    ret_code: int | None
    readback_target: bool | None


@dataclass(frozen=True, slots=True)
class DemoConfigurationReportV1:
    state: str = "BLOCKED"
    uid: str | None = None
    unified_margin_status: int | None = None
    margin_mode: str | None = None
    lpa_perp: bool | None = None
    blockers: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    steps: tuple[DemoConfigurationStepV1, ...] = ()
    effects: tuple[str, ...] = ("ALL_FUTURES_PRICE_BEHAVIOUR", "UNIFIED_ACCOUNT_MARGIN_MODE")
    demo_price_endpoint_support: str = "UNVERIFIED"
    financial_qualified: bool = field(default=False, init=False)
    atomic_account_snapshot: bool = field(default=False, init=False)


def _object(value: object) -> dict[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise _Denied("RESPONSE_INVALID")
    return cast(dict[str, object], value)


def _rows(value: object) -> list[object]:
    if type(value) is not list:
        raise _Denied("RESPONSE_INVALID")
    return cast(list[object], value)


def _number(value: object) -> Decimal:
    if type(value) is not str or re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value) is None:
        raise _Denied("RESPONSE_INVALID")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise _Denied("RESPONSE_INVALID") from None
    if not parsed.is_finite():
        raise _Denied("RESPONSE_INVALID")
    return parsed


def _mode(result: dict[str, object]) -> tuple[int, str]:
    status, mode = result.get("unifiedMarginStatus"), result.get("marginMode")
    if (type(status) is not int or status not in {3, 4, 5, 6}
            or type(mode) is not str or mode not in {"REGULAR_MARGIN", "ISOLATED_MARGIN"}):
        raise _Denied("ACCOUNT_MODE_UNSUPPORTED")
    return status, mode


def _reject(result: dict[str, object]) -> bool:
    value = result.get("lpaPerp")
    if type(value) is not bool:
        raise _Denied("TRADE_BEHAVIOUR_UNKNOWN")
    return value


class _Operation:
    def __init__(self, owner: "BybitDemoConfigurationV1", profile: AccountProfileSnapshot,
                 session: BybitDemoAccountSession, binding: AuthenticatedAccountBinding) -> None:
        self.owner, self.profile, self.session, self.binding = owner, profile, session, binding
        self.scope = ProtectionScope(profile._uid, "DEMO")
        self.client = session.read_client(binding, scope=self.scope)
        self.http = self.client._client
        self.pair = session._credential_pair
        self.store = owner._resolver._store
        self.credentials = owner._resolver._snapshot
        self.steps: list[DemoConfigurationStepV1] = []

    def check(self) -> None:
        if (self.owner._profiles.load() != self.profile
                or self.owner._resolver._store is not self.store
                or self.store is not None and self.store.load() != self.credentials):
            raise _Denied("SAVED_CONFIGURATION_CHANGED")
        self.session.require_binding(self.binding, scope=self.scope)
        # Original validator is last callbackful work. No guard/clock/SQL callback
        # follows these finite original owner/client/credential comparisons.
        if (self.session._closed or self.session._binding is not self.binding
                or self.session._client is not self.client
                or self.session._credential_client is not self.client
                or type(self.client) is not BybitReadOnlyClient
                or self.client._client is not self.http or self.http.is_closed
                or self.session._credential_pair is not self.pair
                or (self.session._api_key, self.session._api_secret) != self.pair
                or (self.client._api_key, self.client._api_secret) != self.pair
                or self.client.environment != "demo" or self.client.base_url != _HOST):
            raise _Denied("ORIGINAL_DEMO_SESSION_REQUIRED")

    async def request(self, path: str, params: tuple[tuple[str, str], ...] = (), *,
                      body: str | None = None) -> tuple[int, dict[str, object]]:
        self.check()
        if body is None:
            if path not in {_INFO, _SETTINGS, _WALLET, _POSITIONS, _ORDERS}:
                raise _Denied("REQUEST_NOT_ALLOWED")
            request = self.client._build_request(path, list(params), signed=True)
        else:
            if (path, body) not in _ACTIONS or params:
                raise _Denied("REQUEST_NOT_ALLOWED")
            timestamp = self.client._timestamp()
            request = httpx.Request("POST", _HOST + path, content=body.encode(), headers={
                "Content-Type": "application/json", "X-BAPI-API-KEY": self.pair[0],
                "X-BAPI-TIMESTAMP": timestamp, "X-BAPI-RECV-WINDOW": RECV_WINDOW,
                "X-BAPI-SIGN": sign_v5(self.pair[1], self.pair[0], timestamp, RECV_WINDOW, body)})
        request.headers["Accept-Encoding"] = "identity"
        # HTTPX's original transport has no retry configuration; no application
        # retry exists here, including on 429, 5xx, parse errors or cancellation.
        response = await self.http.send(request, stream=True, follow_redirects=False)
        try:
            if (response.request is not request or response.status_code != 200
                    or response.headers.get("content-encoding", "identity").lower() != "identity"):
                raise _Denied("RESPONSE_UNCONFIRMED")
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > _MAX_BYTES:
                    raise _Denied("RESPONSE_TOO_LARGE")
        finally:
            await response.aclose()
        self.check()
        envelope = _object(_parse_json(bytes(raw)))
        code, stamp = envelope.get("retCode"), envelope.get("time")
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        if (type(code) is not int or type(stamp) is not int
                or not -2_000 <= now_ms - stamp <= 30_000):
            raise _Denied("RESPONSE_UNCONFIRMED")
        return code, _object(envelope.get("result"))

    async def get(self, path: str, params: tuple[tuple[str, str], ...] = ()) -> dict[str, object]:
        code, result = await self.request(path, params)
        if code != 0:
            raise _Denied("GET_REJECTED_OR_UNSUPPORTED")
        return result

    async def pages(self, path: str, params: tuple[tuple[str, str], ...]) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        seen: set[str] = set()
        cursor = ""
        for _ in range(_MAX_PAGES):
            result = await self.get(path, params + ((("cursor", cursor),) if cursor else ()))
            if result.get("category") != dict(params)["category"]:
                raise _Denied("ACCOUNT_COVERAGE_UNKNOWN")
            rows.extend(_object(row) for row in _rows(result.get("list")))
            token = result.get("nextPageCursor")
            if type(token) is not str or len(token) > 2048:
                raise _Denied("ACCOUNT_COVERAGE_UNKNOWN")
            if not token:
                return rows
            if token in seen:
                raise _Denied("ACCOUNT_COVERAGE_UNKNOWN")
            seen.add(token)
            cursor = token
        raise _Denied("ACCOUNT_COVERAGE_UNKNOWN")

    async def read_modes(self) -> tuple[int, str, bool]:
        status, mode = _mode(await self.get(_INFO))
        reject = _reject(await self.get(_SETTINGS))
        return status, mode, reject

    async def preflight(self) -> DemoConfigurationReportV1:
        status, mode, reject = await self.read_modes()
        blockers: list[str] = []
        try:
            wallet = _rows((await self.get(_WALLET, (("accountType", "UNIFIED"),))).get("list"))
            if len(wallet) != 1 or _object(wallet[0]).get("accountType") != "UNIFIED":
                raise _Denied("WALLET_COVERAGE_UNKNOWN")
            seen: set[str] = set()
            for coin in _rows(_object(wallet[0]).get("coin")):
                item = _object(coin)
                name = item.get("coin")
                if type(name) is not str or not name or name in seen:
                    raise _Denied("WALLET_COVERAGE_UNKNOWN")
                seen.add(name)
                for key in ("borrowAmount", "accruedInterest", "spotBorrow", "locked"):
                    if _number(item.get(key)) != 0:
                        blockers.append("ACCOUNT_DEBT_OR_LOCKED_ASSETS")
                for key in ("walletBalance", "equity"):
                    _number(item.get(key))
        except Exception:  # noqa: BLE001 - no private response or provider error escapes.
            blockers.append("WALLET_COVERAGE_UNKNOWN")
        # Official linear products are USDT/USDC contracts. Inverse and option
        # use unfiltered all-position reads. No selected symbol can prove flatness.
        categories = (("linear", "USDT"), ("linear", "USDC"), ("inverse", ""), ("option", ""))
        for path in (_POSITIONS, _ORDERS):
            for category, settle in categories + ((("spot", ""),) if path == _ORDERS else ()):
                params: tuple[tuple[str, str], ...] = (("category", category),)
                if settle:
                    params += (("settleCoin", settle),)
                params += (("limit", "200" if path == _POSITIONS else "50"),)
                if path == _ORDERS:
                    params += (("openOnly", "0"),)
                try:
                    rows = await self.pages(path, params)
                    if (path == _ORDERS and rows or path == _POSITIONS
                            and any(_number(row.get("size")) != 0 for row in rows)):
                        blockers.append("OPEN_ORDERS" if path == _ORDERS else "OPEN_POSITIONS")
                except Exception:  # noqa: BLE001 - unsupported categories remain unknown, never zero.
                    blockers.append("ACCOUNT_COVERAGE_UNKNOWN")
        self.check()
        actions = tuple(path for path, _ in _ACTIONS
                        if path == _PRICE and not reject or path == _MARGIN and mode != "ISOLATED_MARGIN")
        return DemoConfigurationReportV1(state="BLOCKED" if blockers else "PLANNED" if actions else "VERIFIED",
            uid=self.profile._uid, unified_margin_status=status, margin_mode=mode, lpa_perp=reject,
            blockers=tuple(dict.fromkeys(blockers)), actions=actions)


class BybitDemoConfigurationV1:
    """Callable internal tool. Construction never loads credentials or starts I/O."""

    def __init__(self, *, resolver: CredentialResolver, profile_store: AccountProfileStore) -> None:
        self._resolver, self._profiles = resolver, profile_store
        self._busy = False
        self._apply_consumed = False

    def __repr__(self) -> str:
        return "BybitDemoConfigurationV1(<private>, financial_qualified=False)"

    @classmethod
    def from_environment(cls, *, environ: Mapping[str, str] | None = None) -> Self:
        source = dict(os.environ if environ is None else environ)
        profiles = default_account_profile_store(source)
        if profiles is None:
            raise _Denied("SAVED_DEMO_PROFILE_REQUIRED")
        return cls(resolver=CredentialResolver.from_environment(environ=source), profile_store=profiles)

    async def inspect(self) -> DemoConfigurationReportV1:
        return await self._run(expected_uid=None, apply=False)

    async def plan(self) -> DemoConfigurationReportV1:
        return await self.inspect()

    async def apply(self, *, expected_uid: str) -> DemoConfigurationReportV1:
        """Explicit operator action; fixed settings only, no implicit repeat/resume."""
        if self._apply_consumed:
            return DemoConfigurationReportV1(blockers=("APPLY_ALREADY_CONSUMED",))
        self._apply_consumed = True
        return await self._run(expected_uid=expected_uid, apply=True)

    async def _run(self, *, expected_uid: str | None, apply: bool) -> DemoConfigurationReportV1:
        if self._busy:
            return DemoConfigurationReportV1(blockers=("OPERATION_IN_PROGRESS",))
        self._busy = True
        session: BybitDemoAccountSession | None = None
        operation: _Operation | None = None
        report = DemoConfigurationReportV1()
        try:
            profile = self._profiles.load()
            if profile is None:
                raise _Denied("SAVED_DEMO_PROFILE_REQUIRED")
            expected = profile.resolve_expectations(supported_policies=_POLICIES)
            if apply and (type(expected_uid) is not str or expected_uid != expected._uid):
                raise _Denied("EXPLICIT_SAVED_UID_CONFIRMATION_REQUIRED")
            key, secret = self._resolver.bybit_pair("demo")
            if not key or not secret:
                raise _Denied("SAVED_DEMO_CREDENTIALS_REQUIRED")
            async with asyncio.timeout(20):
                session = BybitDemoAccountSession(api_key=key, api_secret=secret)
                binding = await session.authenticate(expected_uid=expected._uid,
                    expected_parent_uid=expected._parent_uid, expected_is_master=expected._is_master)
                operation = _Operation(self, profile, session, binding)
                report = await operation.preflight()
                if apply and not report.blockers:
                    if session.require_binding(binding, scope=operation.scope).read_only:
                        report = replace(report, state="BLOCKED", blockers=("CONFIGURATION_WRITE_PERMISSION_REQUIRED",))
                    else:
                        report = await self._apply(operation, report)
        except _Denied as error:
            steps = tuple(operation.steps) if operation is not None else ()
            report = replace(report, state="INCOMPLETE" if steps else "BLOCKED",
                             steps=steps, blockers=(str(error),))
        except Exception:  # noqa: BLE001 - profile, authentication and provider text may contain secrets.
            steps = tuple(operation.steps) if operation is not None else ()
            report = replace(report, state="INCOMPLETE" if steps else "BLOCKED",
                             steps=steps, blockers=("DEMO_CONFIGURATION_UNCONFIRMED",))
        finally:
            if session is not None:
                try:
                    await session.aclose()
                except Exception:  # noqa: BLE001 - close diagnostics must not expose credentials.
                    report = replace(report, state="BLOCKED", blockers=("DEMO_SESSION_CLOSE_UNCONFIRMED",))
            self._busy = False
        return report

    async def _apply(self, operation: _Operation,
                     report: DemoConfigurationReportV1) -> DemoConfigurationReportV1:
        steps = operation.steps
        for path, body in _ACTIONS:
            if path not in report.actions:
                continue
            # A fresh whole-account check precedes each of the two possible writes.
            report = replace(await operation.preflight(), steps=tuple(steps))
            if report.blockers:
                return report
            if path not in report.actions:
                continue
            code: int | None = None
            steps.append(DemoConfigurationStepV1(path, "ATTEMPT_OUTCOME_UNKNOWN", None, None))
            try:
                code, _ = await operation.request(path, body=body)
            except Exception:  # noqa: BLE001 - ambiguous writes are read back, never retransmitted.
                code = None
            target: bool | None = None
            try:
                status, mode, reject = await operation.read_modes()
                target = reject if path == _PRICE else mode == "ISOLATED_MARGIN"
                report = replace(report, unified_margin_status=status, margin_mode=mode, lpa_perp=reject)
            except Exception:  # noqa: BLE001 - acknowledgement alone cannot establish the new setting.
                target = None
            if target is None:
                outcome = "READBACK_UNKNOWN"
            elif code is None:
                outcome = "UNCERTAIN_READBACK_CONFIRMED" if target else "UNCERTAIN_NOT_AT_TARGET"
            elif code != 0:
                outcome = "VENUE_REJECTED"
            elif target:
                outcome = "ACKNOWLEDGED_AND_VERIFIED"
            else:
                outcome = "ACKNOWLEDGED_BUT_NOT_AT_TARGET"
            steps[-1] = DemoConfigurationStepV1(path, outcome, code, target)
            report = replace(report, steps=tuple(steps))
            if outcome != "ACKNOWLEDGED_AND_VERIFIED":
                return replace(report, state="INCOMPLETE", blockers=(outcome,))
        operation.check()
        if report.margin_mode != "ISOLATED_MARGIN" or report.lpa_perp is not True:
            return replace(report, state="INCOMPLETE", blockers=("TARGETS_NOT_RETAINED",))
        return replace(report, state="VERIFIED", actions=(), steps=tuple(steps),
            demo_price_endpoint_support="OBSERVED_SUPPORTED" if any(
                step.path == _PRICE and step.ret_code == 0 for step in steps) else "UNVERIFIED")
