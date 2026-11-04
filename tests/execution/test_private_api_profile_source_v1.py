"""Original Session PROFILE projections, unknown wire values and source lifetimes."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import asdict
from typing import Any

import httpx
import pytest
from test_private_account_sources_bundle_v1 import (
    KEY,
    SCOPE,
    SECRET,
    authenticate,
    capture_all,
    require_one,
)
from test_private_flat_source_v1 import flat_session

from multi_exchange_trader.execution.account_binding import AccountBindingError
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget
from multi_exchange_trader.execution.protection import ProtectionScope


def profile_session(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, list[httpx.Request], dict[str, Any]]:
    owner, requests = flat_session(monkeypatch)
    fields: dict[str, Any] = {
        "vipLevel": "VIP-1",
        "mktMakerLevel": "0",
        "kycLevel": "LEVEL_1",
        "kycRegion": "MYS",
    }
    original = httpx.AsyncClient.send

    async def send(
        client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any
    ) -> httpx.Response:
        response = await original(client, request, **kwargs)
        if request.url.path != "/v5/user/query-api":
            return response
        body = json.loads(response.content)
        body["result"].update(fields)
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    return owner, requests, fields


async def capture(owner: Any, binding: Any) -> Any:
    method = getattr(owner, "_capture_linear_api_profile_v1", None)
    assert callable(method), "Original Session PROFILE source is absent"
    return await method(
        binding, scope=SCOPE, budget=MonitoringReadBudget(), owner_guard=lambda: None
    )


async def test_profile_keeps_original_source_identity_and_four_documented_wire_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, requests, _fields = profile_session(monkeypatch)
    try:
        binding = await authenticate(owner)
        source = await capture(owner, binding)
        owner._require_linear_api_profile_v1(source, binding=binding, scope=SCOPE)
        result = owner._project_linear_api_profile_v1(source, binding=binding, scope=SCOPE)
        assert result.source is source
        assert (result.uid, result.parent_uid, result.is_master) == ("12345", "56789", False)
        assert (result.vip_level, result.mkt_maker_level, result.kyc_level, result.kyc_region) == (
            "VIP-1",
            "0",
            "LEVEL_1",
            "MYS",
        )
        assert not result.fee_applicability and not result.financial_authority
        assert not result.live_permission
        safe = repr(result) + json.dumps(asdict(result), default=str)
        assert KEY not in safe and SECRET not in safe
        assert source.path == "/v5/user/query-api" and source.query == ()
        assert len(requests) == 3
        with pytest.raises(AccountBindingError):
            owner._project_linear_api_profile_v1(copy.copy(source), binding=binding, scope=SCOPE)
        owner._require_linear_api_profile_v1(source, binding=binding, scope=SCOPE)
    finally:
        await owner.aclose()


@pytest.mark.parametrize("values", [None, "", "FUTURE_UNRECOGNIZED_WIRE_VALUE"])
async def test_missing_empty_and_future_profile_fields_are_preserved_without_defaults(
    monkeypatch: pytest.MonkeyPatch,
    values: str | None,
) -> None:
    owner, _requests, fields = profile_session(monkeypatch)
    try:
        binding = await authenticate(owner)
        if values is None:
            fields.clear()
        else:
            fields.update({key: values for key in fields})
        source = await capture(owner, binding)
        profile = owner._project_linear_api_profile_v1(source, binding=binding, scope=SCOPE)
        assert (
            profile.vip_level,
            profile.mkt_maker_level,
            profile.kyc_level,
            profile.kyc_region,
        ) == (values, values, values, values)
    finally:
        await owner.aclose()


@pytest.mark.parametrize(
    "key,value",
    [
        ("vipLevel", 0),
        ("mktMakerLevel", True),
        ("kycLevel", None),
        ("kycRegion", ["MYS"]),
        ("userID", 99999),
        ("parentUid", "99999"),
        ("isMaster", True),
        ("userID", "12345"),
        ("vipLevel", KEY),
        ("kycRegion", SECRET),
    ],
)
async def test_invalid_types_identity_mismatch_and_credential_echo_never_become_profile_data(
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: Any,
) -> None:
    owner, _requests, fields = profile_session(monkeypatch)
    try:
        binding = await authenticate(owner)
        fields[key] = value
        source = await capture(owner, binding)
        with pytest.raises(AccountBindingError) as caught:
            owner._project_linear_api_profile_v1(source, binding=binding, scope=SCOPE)
        assert KEY not in str(caught.value) and SECRET not in str(caught.value)
        assert str(caught.value) == "ACCOUNT_API_PROFILE_PROJECTION_REQUIRED"
    finally:
        await owner.aclose()


async def test_profile_refresh_retires_only_profile_and_all_seven_original_families_coexist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, _requests, _fields = profile_session(monkeypatch)
    try:
        binding = await authenticate(owner)
        others = await capture_all(owner, binding)
        positions = await owner._capture_linear_positions_v1(
            binding, scope=SCOPE, budget=MonitoringReadBudget(), owner_guard=lambda: None
        )
        orders = await owner._capture_linear_orders_v1(
            binding, scope=SCOPE, budget=MonitoringReadBudget(), owner_guard=lambda: None
        )
        flat = await owner._capture_linear_flat_leverage_v1(
            binding,
            symbol="BTCUSDT",
            scope=SCOPE,
            budget=MonitoringReadBudget(),
            owner_guard=lambda: None,
        )
        old, new = await capture(owner, binding), await capture(owner, binding)
        with pytest.raises(AccountBindingError):
            owner._require_linear_api_profile_v1(old, binding=binding, scope=SCOPE)
        owner._require_linear_api_profile_v1(new, binding=binding, scope=SCOPE)
        owner._require_linear_positions_v1(positions, binding=binding, scope=SCOPE)
        owner._require_linear_orders_v1(orders, binding=binding, scope=SCOPE)
        owner._require_linear_flat_leverage_v1(flat, symbol="BTCUSDT", binding=binding, scope=SCOPE)
        for kind, source in others.items():
            require_one(owner, binding, kind, source)
    finally:
        await owner.aclose()


@pytest.mark.parametrize("change", ["reauth", "session-close", "client-close", "owner", "scope"])
async def test_profile_obeys_original_reauthentication_close_owner_and_scope_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    owner, _requests, _fields = profile_session(monkeypatch)
    original = owner._client
    task: asyncio.Task[Any] | None = None
    try:
        binding = await authenticate(owner)
        source = await capture(owner, binding)
        if change == "reauth":
            await owner._refresh_lock.acquire()
            task = asyncio.create_task(authenticate(owner))
            await asyncio.sleep(0)
        elif change == "session-close":
            await owner.aclose()
        elif change == "client-close":
            await owner._client.aclose()
        elif change == "owner":
            owner._client = copy.copy(original)
        with pytest.raises(AccountBindingError):
            owner._require_linear_api_profile_v1(
                source,
                binding=binding,
                scope=ProtectionScope("99999", "DEMO") if change == "scope" else SCOPE,
            )
    finally:
        owner._client = original
        if owner._refresh_lock.locked():
            owner._refresh_lock.release()
        if task is not None:
            await task
        await owner.aclose()


async def test_inflight_wallet_overlap_retires_profile_without_partial_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, _requests, _fields = profile_session(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original = httpx.AsyncClient.send

    async def send(
        client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any
    ) -> httpx.Response:
        if request.url.path == "/v5/account/wallet-balance" and not release.is_set():
            entered.set()
            await release.wait()
        return await original(client, request, **kwargs)

    task: asyncio.Task[Any] | None = None
    try:
        binding = await authenticate(owner)
        profile = await capture(owner, binding)
        monkeypatch.setattr(httpx.AsyncClient, "send", send)
        task = asyncio.create_task(
            owner._capture_linear_wallet_v1(
                binding, scope=SCOPE, budget=MonitoringReadBudget(), owner_guard=lambda: None
            )
        )
        await entered.wait()
        with pytest.raises(AccountBindingError, match="OVERLAP"):
            await capture(owner, binding)
        with pytest.raises(AccountBindingError):
            owner._require_linear_api_profile_v1(profile, binding=binding, scope=SCOPE)
        release.set()
        with pytest.raises(AccountBindingError):
            await task
        assert owner._linear_info_pending is None
    finally:
        release.set()
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)
        await owner.aclose()
