"""Offline original Session/HTTPX interception; mode data is never financial authority."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from multi_exchange_trader.account.bybit_readonly import sign_v5
from multi_exchange_trader.execution.account_binding import (
    AccountBindingError,
    AuthenticatedAccountBinding,
    BybitDemoAccountSession,
)
from multi_exchange_trader.execution.bybit_linear_model_inputs_v1 import (
    BybitModelSourceRefV1,
    BybitUtaModeV1,
)
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget
from multi_exchange_trader.execution.protection import ProtectionScope

KEY, SECRET = "M3B-OFFLINE-KEY", "M3B-OFFLINE-SECRET"
SCOPE = ProtectionScope("12345", "DEMO")
RAW = b'{"retCode":0,"result":{"unifiedMarginStatus":5,"marginMode":"ISOLATED_MARGIN","updatedTime":"1"}}'


def intercepted_session(
    monkeypatch: pytest.MonkeyPatch, raw: bytes = RAW,
) -> tuple[BybitDemoAccountSession, list[httpx.Request]]:
    """Reuse M3-A's genuine auth/read path; replace only external HTTPX send."""
    now = datetime.now(UTC)
    stamp = int(now.timestamp() * 1000)
    requests: list[httpx.Request] = []

    async def send(_self: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET" and request.url.host == "api-demo.bybit.com"
        assert not request.url.query
        if request.url.path == "/v5/market/time":
            return httpx.Response(200, json={"retCode": 0, "result": {}, "time": stamp}, request=request)
        assert request.headers["X-BAPI-SIGN"] == sign_v5(
            SECRET, KEY, request.headers["X-BAPI-TIMESTAMP"],
            request.headers["X-BAPI-RECV-WINDOW"], "")
        if request.url.path == "/v5/user/query-api":
            return httpx.Response(200, json={"retCode": 0, "time": stamp, "result": {
                "apiKey": KEY, "secret": "", "userID": 12345, "parentUid": "56789",
                "isMaster": False, "readOnly": 0,
                "permissions": {"ContractTrade": ["Order", "Position"], "Wallet": [],
                                "Derivatives": ["DerivativesTrade"], "Spot": []}}}, request=request)
        assert request.url.path == "/v5/account/info"
        return httpx.Response(200, content=raw, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    return BybitDemoAccountSession(api_key=KEY, api_secret=SECRET, clock=lambda: now), requests


async def authenticate(owner: BybitDemoAccountSession) -> AuthenticatedAccountBinding:
    return await owner.authenticate(expected_uid="12345", expected_parent_uid="56789",
                                    expected_is_master=False)


async def source_for(
    owner: BybitDemoAccountSession, binding: AuthenticatedAccountBinding, *,
    scope: ProtectionScope = SCOPE, guard: Callable[[], None] = lambda: None,
    budget: MonitoringReadBudget | None = None,
) -> BybitModelSourceRefV1:
    return await owner._capture_linear_account_info_v1(binding, scope=scope,
        budget=MonitoringReadBudget() if budget is None else budget, owner_guard=guard)


def assert_private_failure(error: AccountBindingError) -> None:
    assert error.args == ("ACCOUNT_MODE_PROJECTION_REQUIRED",)
    assert error.__cause__ is None and error.__context__ is None
    assert all(value not in repr(error) for value in (KEY, SECRET, "PRIVATE-WIRE-MARKER"))


@pytest.mark.parametrize("status", [5, 6])
async def test_original_current_raw_projects_mode_without_another_http_read(
    monkeypatch: pytest.MonkeyPatch, status: int,
) -> None:
    raw = json.dumps({"retCode": 0, "result": {"unifiedMarginStatus": status,
        "marginMode": "ISOLATED_MARGIN", "updatedTime": "1"}}).encode()
    owner, requests = intercepted_session(monkeypatch, raw)
    try:
        binding = await authenticate(owner)
        source = await owner._capture_linear_account_info_v1(binding, scope=SCOPE,
            budget=MonitoringReadBudget(), owner_guard=lambda: None)
        mode = owner._project_linear_account_mode_v1(source, binding=binding, scope=SCOPE)
        assert type(mode) is BybitUtaModeV1 and mode.source is source
        assert (mode.unified_margin_status, mode.margin_mode, mode.updated_time_ms) == (
            status, "ISOLATED_MARGIN", 1)
        assert len(requests) == 3
        slot = owner._linear_info_current
        assert slot is not None and slot.capture is not None and slot.capture.raw_body == raw
        assert owner._project_linear_account_mode_v1(source, binding=binding, scope=SCOPE) == mode
        assert len(requests) == 3
    finally:
        await owner.aclose()


@pytest.mark.parametrize("wire,want", [
    ("0001", 1), ("000000000000001", 1), ("253402300799999", 253402300799999),
])
async def test_updated_time_keeps_bounded_wire_value_instead_of_capture_time(
    monkeypatch: pytest.MonkeyPatch, wire: str, want: int,
) -> None:
    raw = json.dumps({"retCode": 0, "result": {"unifiedMarginStatus": 6,
        "marginMode": "ISOLATED_MARGIN", "updatedTime": wire},
        "futureUnknown": {"nested": [True, None, "kept"]}}).encode()
    owner, requests = intercepted_session(monkeypatch, raw)
    try:
        binding = await authenticate(owner)
        source = await source_for(owner, binding)
        mode = owner._project_linear_account_mode_v1(source, binding=binding, scope=SCOPE)
        assert mode.updated_time_ms == want and mode.source is source
        assert mode.updated_time_ms != int(source.received_at.timestamp() * 1000)
        assert len(requests) == 3
        assert owner._linear_info_current is not None
        assert owner._linear_info_current.capture is not None
        assert owner._linear_info_current.capture.raw_body == raw
    finally:
        await owner.aclose()


@pytest.mark.parametrize("field,value", [
    ("unifiedMarginStatus", True), ("unifiedMarginStatus", False),
    ("unifiedMarginStatus", 5.0), ("unifiedMarginStatus", "5"),
    ("unifiedMarginStatus", 1), ("unifiedMarginStatus", 4),
    ("unifiedMarginStatus", 7), ("unifiedMarginStatus", None),
    ("unifiedMarginStatus", {}), ("unifiedMarginStatus", []),
    ("marginMode", "REGULAR_MARGIN"), ("marginMode", "PORTFOLIO_MARGIN"),
    ("marginMode", "isolated_margin"), ("marginMode", " ISOLATED_MARGIN"),
    ("marginMode", "ISOLATED_MARGIN "), ("marginMode", ""),
    ("marginMode", None), ("marginMode", True), ("marginMode", 5),
    ("updatedTime", "0"), ("updatedTime", "000"), ("updatedTime", ""),
    ("updatedTime", "+1"), ("updatedTime", "-1"), ("updatedTime", "1.0"),
    ("updatedTime", "1e3"), ("updatedTime", " 1"), ("updatedTime", "1 "),
    ("updatedTime", "\u0661"), ("updatedTime", "\uff11"),
    ("updatedTime", "253402300800000"), ("updatedTime", "1000000000000000"),
    ("updatedTime", "0000000000000001"), ("updatedTime", True),
    ("updatedTime", 1), ("updatedTime", 1.0), ("updatedTime", None),
    ("updatedTime", []), ("updatedTime", {}),
])
async def test_unsupported_or_wrong_wire_mode_is_one_safe_projection_error(
    monkeypatch: pytest.MonkeyPatch, field: str, value: object,
) -> None:
    result: dict[str, object] = {"unifiedMarginStatus": 5, "marginMode": "ISOLATED_MARGIN",
                               "updatedTime": "1", "private": "PRIVATE-WIRE-MARKER"}
    result[field] = value
    raw = json.dumps({"retCode": 0, "result": result}).encode()
    owner, requests = intercepted_session(monkeypatch, raw)
    try:
        binding = await authenticate(owner)
        source = await source_for(owner, binding)
        fields_before = set(owner.__dict__)
        with pytest.raises(AccountBindingError) as caught:
            owner._project_linear_account_mode_v1(source, binding=binding, scope=SCOPE)
        assert_private_failure(caught.value)
        assert set(owner.__dict__) == fields_before and len(requests) == 3
    finally:
        await owner.aclose()


@pytest.mark.parametrize("missing", ["unifiedMarginStatus", "marginMode", "updatedTime"])
async def test_missing_mode_field_has_no_default(
    monkeypatch: pytest.MonkeyPatch, missing: str,
) -> None:
    result: dict[str, object] = {"unifiedMarginStatus": 5, "marginMode": "ISOLATED_MARGIN",
                               "updatedTime": "1"}
    result.pop(missing)
    owner, requests = intercepted_session(monkeypatch, json.dumps({"retCode": 0, "result": result}).encode())
    try:
        binding = await authenticate(owner)
        source = await source_for(owner, binding)
        with pytest.raises(AccountBindingError) as caught:
            owner._project_linear_account_mode_v1(source, binding=binding, scope=SCOPE)
        assert_private_failure(caught.value)
        assert len(requests) == 3
    finally:
        await owner.aclose()


@pytest.mark.parametrize("raw", [
    b'[]', b'{"retCode":false,"result":{}}', b'{"retCode":0.0,"result":{}}',
    b'{"retCode":1,"result":{}}', b'{"retCode":0,"result":[]}',
    b'{"retCode":0,"result":null}', b'{"retCode":0}', b'{"result":{}}',
    b'{"retCode":0,"result":{"unifiedMarginStatus":5,"unifiedMarginStatus":6}}',
    b'{"retCode":0,"result":{},"unknown":1,"unknown":2}',
    b'{"retCode":0,"result":{"unknown":NaN}}', b'{"retCode":0,"result":{"unknown":Infinity}}',
    b'{bad-json',
])
async def test_original_decoder_refuses_malformed_wire_before_any_source_is_issued(
    monkeypatch: pytest.MonkeyPatch, raw: bytes,
) -> None:
    owner, requests = intercepted_session(monkeypatch, raw)
    try:
        binding = await authenticate(owner)
        with pytest.raises(AccountBindingError):
            await source_for(owner, binding)
        assert owner._linear_info_current is owner._linear_info_pending is None
        assert len(requests) >= 3
    finally:
        await owner.aclose()


@pytest.mark.parametrize("kind", ["copy", "reconstructed", "foreign", "copied-session"])
async def test_projection_requires_original_source_and_session_not_value_equality(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    owner, requests = intercepted_session(monkeypatch)
    foreign: BybitDemoAccountSession | None = None
    try:
        binding = await authenticate(owner)
        source = await source_for(owner, binding)
        original_mode = owner._project_linear_account_mode_v1(source, binding=binding, scope=SCOPE)
        if kind == "foreign":
            foreign, _ = intercepted_session(monkeypatch)
            foreign_binding = await authenticate(foreign)
            with pytest.raises(AccountBindingError) as caught:
                foreign._project_linear_account_mode_v1(source, binding=foreign_binding, scope=SCOPE)
        elif kind == "copied-session":
            copied_owner = copy.copy(owner)
            with pytest.raises(AccountBindingError) as caught:
                copied_owner._project_linear_account_mode_v1(source, binding=binding, scope=SCOPE)
        else:
            candidate = copy.copy(source) if kind == "copy" else replace(source)
            assert candidate == source and candidate is not source
            with pytest.raises(AccountBindingError) as caught:
                owner._project_linear_account_mode_v1(candidate, binding=binding, scope=SCOPE)
        assert_private_failure(caught.value)
        # A mode is constructible data; its retained original source is checked on use.
        assert replace(original_mode) == original_mode and len(requests) == 3
    finally:
        await owner.aclose()
        if foreign is not None:
            await foreign.aclose()


@pytest.mark.parametrize("change", ["client", "http-client", "binding"])
async def test_replaced_original_owner_links_retire_and_restore_cannot_revive(
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    owner, requests = intercepted_session(monkeypatch)
    undo: Callable[[], None] = lambda: None
    try:
        binding = await authenticate(owner)
        source = await source_for(owner, binding)
        slot = owner._linear_info_current
        assert slot is not None and slot.capture is not None
        if change == "client":
            original = owner._client
            owner._client = copy.copy(original)
            undo = lambda: setattr(owner, "_client", original)
        elif change == "http-client":
            original_http = owner._client._client
            owner._client._client = copy.copy(original_http)
            undo = lambda: setattr(owner._client, "_client", original_http)
        else:
            owner._binding = None
            undo = lambda: setattr(owner, "_binding", binding)
        with pytest.raises(AccountBindingError) as caught:
            owner._project_linear_account_mode_v1(source, binding=binding, scope=SCOPE)
        assert_private_failure(caught.value)
        undo()
        with pytest.raises(AccountBindingError) as restored:
            owner._project_linear_account_mode_v1(source, binding=binding, scope=SCOPE)
        assert_private_failure(restored.value)
        assert len(requests) == 3
    finally:
        undo()
        await owner.aclose()


@pytest.mark.parametrize("change", ["same-data-new-source", "reauthenticate", "session-close", "client-close"])
async def test_new_generation_refresh_or_close_withdraws_old_mode_source(
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    owner, requests = intercepted_session(monkeypatch)
    try:
        binding = await authenticate(owner)
        source = await source_for(owner, binding)
        mode = owner._project_linear_account_mode_v1(source, binding=binding, scope=SCOPE)
        if change == "same-data-new-source":
            newer = await source_for(owner, binding)
            assert newer is not source and newer.raw_body_sha256 == source.raw_body_sha256
            newer_mode = owner._project_linear_account_mode_v1(newer, binding=binding, scope=SCOPE)
            assert newer_mode.source is newer
            assert (newer_mode.unified_margin_status, newer_mode.margin_mode,
                    newer_mode.updated_time_ms) == (5, "ISOLATED_MARGIN", 1)
        elif change == "reauthenticate":
            binding = await authenticate(owner)
        elif change == "session-close":
            await owner.aclose()
        else:
            await owner._client.aclose()
        before = len(requests)
        with pytest.raises(AccountBindingError) as caught:
            owner._project_linear_account_mode_v1(mode.source, binding=binding, scope=SCOPE)
        assert_private_failure(caught.value)
        assert len(requests) == before
    finally:
        await owner.aclose()


async def test_inner_caught_projection_reentry_taints_outer_and_has_no_partial_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, requests = intercepted_session(monkeypatch)
    enabled = False
    source: BybitModelSourceRefV1
    binding: AuthenticatedAccountBinding
    errors: list[AccountBindingError] = []

    def guard() -> None:
        if enabled:
            try:
                owner._project_linear_account_mode_v1(source, binding=binding, scope=SCOPE)
            except AccountBindingError as error:
                errors.append(error)

    try:
        binding = await authenticate(owner)
        source = await source_for(owner, binding, guard=guard)
        enabled = True
        with pytest.raises(AccountBindingError) as caught:
            owner._project_linear_account_mode_v1(source, binding=binding, scope=SCOPE)
        enabled = False
        assert_private_failure(caught.value)
        assert len(errors) == 1
        assert_private_failure(errors[0])
        assert owner._linear_info_current is None and len(requests) == 3
        with pytest.raises(AccountBindingError):
            owner._project_linear_account_mode_v1(source, binding=binding, scope=SCOPE)
    finally:
        enabled = False
        await owner.aclose()


@pytest.mark.parametrize("error_kind", ["ordinary", "cancel", "interrupt"])
async def test_final_owner_error_is_private_or_preserves_original_base_exception(
    monkeypatch: pytest.MonkeyPatch, error_kind: str,
) -> None:
    owner, requests = intercepted_session(monkeypatch)
    marker = (RuntimeError("PRIVATE-WIRE-MARKER " + SECRET) if error_kind == "ordinary" else
              asyncio.CancelledError("PRIVATE-WIRE-MARKER") if error_kind == "cancel" else
              KeyboardInterrupt("PRIVATE-WIRE-MARKER"))
    enabled = False
    calls = 0

    def guard() -> None:
        nonlocal calls
        if enabled:
            calls += 1
            if calls == 2:
                raise marker

    try:
        binding = await authenticate(owner)
        source = await source_for(owner, binding, guard=guard)
        fields_before = set(owner.__dict__)
        enabled = True
        expected = AccountBindingError if error_kind == "ordinary" else type(marker)
        with pytest.raises(expected) as caught:
            owner._project_linear_account_mode_v1(source, binding=binding, scope=SCOPE)
        enabled = False
        if error_kind == "ordinary":
            assert isinstance(caught.value, AccountBindingError)
            assert_private_failure(caught.value)
        else:
            assert caught.value is marker
        assert calls == 2 and set(owner.__dict__) == fields_before
        assert owner._linear_info_current is None and len(requests) == 3
        with pytest.raises(AccountBindingError):
            owner._project_linear_account_mode_v1(source, binding=binding, scope=SCOPE)
    finally:
        enabled = False
        await owner.aclose()


@pytest.mark.parametrize("clock_kind", ["wall", "mono"])
@pytest.mark.parametrize("change", ["source", "scope", "raw", "current-slot", "capture"])
async def test_last_original_binding_clock_mutation_is_refused_and_restore_cannot_revive(
    monkeypatch: pytest.MonkeyPatch, clock_kind: str, change: str,
) -> None:
    owner, requests = intercepted_session(monkeypatch)
    scope = ProtectionScope("12345", "DEMO")
    original_wall, original_mono = owner._clock, owner._monotonic
    enabled = False
    calls = 0
    source: BybitModelSourceRefV1
    capture: Any = None
    slot: Any = None

    def mutate() -> None:
        nonlocal calls
        if not enabled:
            return
        calls += 1
        if calls == 2:
            if change == "source":
                object.__setattr__(source, "observation_id", "changed-at-final-original-clock")
            elif change == "scope":
                object.__setattr__(scope, "account_id", "99999")
            elif change == "raw":
                replacement = b'{"retCode":0,"result":{"unifiedMarginStatus":6,"marginMode":"ISOLATED_MARGIN","updatedTime":"2"}}'
                digest = hashlib.sha256(replacement).hexdigest()
                object.__setattr__(capture, "raw_body", replacement)
                object.__setattr__(capture, "raw_body_sha256", digest)
                object.__setattr__(source, "raw_body_sha256", digest)
            elif change == "current-slot":
                owner._linear_info_current = copy.copy(slot)
            else:
                slot.capture = copy.copy(capture)

    def wall() -> datetime:
        mutate()
        return original_wall()

    def mono() -> float:
        mutate()
        return original_mono()

    try:
        binding = await authenticate(owner)
        source = await source_for(owner, binding, scope=scope)
        slot = owner._linear_info_current
        assert slot is not None and slot.capture is not None
        capture = slot.capture
        old_source_id, old_source_hash = source.observation_id, source.raw_body_sha256
        old_raw, old_capture_hash = capture.raw_body, capture.raw_body_sha256
        if clock_kind == "wall":
            owner._clock = wall
        else:
            owner._monotonic = mono
        enabled = True
        with pytest.raises(AccountBindingError) as caught:
            owner._project_linear_account_mode_v1(source, binding=binding, scope=scope)
        enabled = False
        assert_private_failure(caught.value)
        assert calls == 2 and owner._linear_info_current is None
        owner._clock, owner._monotonic = original_wall, original_mono
        object.__setattr__(source, "observation_id", old_source_id)
        object.__setattr__(source, "raw_body_sha256", old_source_hash)
        object.__setattr__(scope, "account_id", "12345")
        object.__setattr__(capture, "raw_body", old_raw)
        object.__setattr__(capture, "raw_body_sha256", old_capture_hash)
        slot.capture = capture
        with pytest.raises(AccountBindingError) as restored:
            owner._project_linear_account_mode_v1(source, binding=binding, scope=scope)
        assert_private_failure(restored.value)
        assert len(requests) == 3
    finally:
        enabled = False
        owner._clock, owner._monotonic = original_wall, original_mono
        object.__setattr__(scope, "account_id", "12345")
        await owner.aclose()


async def test_success_has_only_pure_suffix_after_last_original_clock_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    from multi_exchange_trader.account import bybit_readonly as low
    from multi_exchange_trader.execution import bybit_linear_model_inputs_v1 as model

    owner, requests = intercepted_session(monkeypatch)
    budget = MonitoringReadBudget()
    original_wall, original_mono = owner._clock, owner._monotonic
    original_parse, original_init = low._parse_json, model._ValueV1.__post_init__
    original_hash, original_dumps = hashlib.sha256, json.dumps
    original_import, original_canonical = builtins.__import__, model.bybit_source_ref_sha256_v1
    original_active = budget.require_active_group
    enabled = after_final_pair = False
    wall_calls = mono_calls = parsed = constructed = 0
    late: list[str] = []

    def note(name: str) -> None:
        if enabled and after_final_pair:
            late.append(name)

    def guard() -> None:
        note("original-owner")

    def wall() -> datetime:
        nonlocal wall_calls
        if enabled:
            wall_calls += 1
        return original_wall()

    def mono() -> float:
        nonlocal mono_calls, after_final_pair
        result = original_mono()
        if enabled:
            mono_calls += 1
            if mono_calls == 2:
                after_final_pair = True
        return result

    def parse(raw: bytes) -> object:
        nonlocal parsed
        note("decoder")
        parsed += 1
        return original_parse(raw)

    def init(value: Any) -> None:
        nonlocal constructed
        note("constructor")
        if type(value) is BybitUtaModeV1:
            constructed += 1
        original_init(value)

    def digest(*args: Any, **kwargs: Any) -> Any:
        note("hash")
        return original_hash(*args, **kwargs)

    def dumps(*args: Any, **kwargs: Any) -> Any:
        note("serializer")
        return original_dumps(*args, **kwargs)

    def imports(*args: Any, **kwargs: Any) -> Any:
        note("import")
        return original_import(*args, **kwargs)

    def canonical(*args: Any, **kwargs: Any) -> str:
        note("canonical")
        return original_canonical(*args, **kwargs)

    def active(*args: Any, **kwargs: Any) -> Any:
        note("active-budget")
        return original_active(*args, **kwargs)

    try:
        binding = await authenticate(owner)
        source = await source_for(owner, binding, guard=guard, budget=budget)
        owner._clock, owner._monotonic = wall, mono
        monkeypatch.setattr(low, "_parse_json", parse)
        monkeypatch.setattr(model._ValueV1, "__post_init__", init)
        monkeypatch.setattr(hashlib, "sha256", digest)
        monkeypatch.setattr(json, "dumps", dumps)
        monkeypatch.setattr(builtins, "__import__", imports)
        monkeypatch.setattr(model, "bybit_source_ref_sha256_v1", canonical)
        monkeypatch.setattr(budget, "require_active_group", active)
        enabled = True
        mode = owner._project_linear_account_mode_v1(source, binding=binding, scope=SCOPE)
        enabled = False
        assert mode.source is source and mode.updated_time_ms == 1
        assert (wall_calls, mono_calls, parsed, constructed) == (2, 2, 1, 1)
        assert late == [] and len(requests) == 3
    finally:
        enabled = False
        owner._clock, owner._monotonic = original_wall, original_mono
        await owner.aclose()
