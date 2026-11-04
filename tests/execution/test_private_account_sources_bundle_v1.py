"""Original auth/read/require path intercepted at HTTPX, not source qualification."""
import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from multi_exchange_trader.account.bybit_readonly import sign_v5
from multi_exchange_trader.execution.account_binding import (
    AccountBindingError,
    BybitDemoAccountSession,
)
from multi_exchange_trader.execution.bybit_linear_model_inputs_v1 import BybitModelSourceRefV1
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget
from multi_exchange_trader.execution.protection import ProtectionScope

KEY, SECRET = "BUNDLE-SESSION-KEY", "BUNDLE-SESSION-SECRET"
SCOPE = ProtectionScope("12345", "DEMO")
COIN = {"coin": "USDT", "walletBalance": "100", "equity": "100", "totalPositionIM": "0",
        "totalOrderIM": "0", "locked": "0", "bonus": "0", "borrowAmount": "0",
        "spotBorrow": "0", "accruedInterest": "0", "unrealisedPnl": "0"}


def intercepted_session(monkeypatch: pytest.MonkeyPatch) -> tuple[BybitDemoAccountSession, list[httpx.Request]]:
    now = datetime.now(UTC)
    stamp = int(now.timestamp() * 1000)
    requests: list[httpx.Request] = []

    async def send(_self: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET" and request.url.host == "api-demo.bybit.com"
        if request.url.path == "/v5/market/time":
            result: object = {}
        else:
            assert request.headers["X-BAPI-SIGN"] == sign_v5(SECRET, KEY,
                request.headers["X-BAPI-TIMESTAMP"], request.headers["X-BAPI-RECV-WINDOW"],
                request.url.query.decode())
            if request.url.path == "/v5/user/query-api":
                result = {"apiKey": KEY, "secret": "", "userID": 12345, "parentUid": "56789",
                    "isMaster": False, "readOnly": 0, "permissions": {
                        "ContractTrade": ["Order", "Position"], "Wallet": [],
                        "Derivatives": ["DerivativesTrade"], "Spot": []}}
            elif request.url.path == "/v5/account/info":
                result = {"unifiedMarginStatus": 5, "marginMode": "ISOLATED_MARGIN", "updatedTime": "1"}
            elif request.url.path == "/v5/account/wallet-balance":
                assert request.url.query == b"accountType=UNIFIED"
                result = {"list": [{"accountType": "UNIFIED", "coin": [COIN]}]}
            elif request.url.path == "/v5/account/fee-rate":
                result = {"list": [{"symbol": request.url.params["symbol"],
                    "takerFeeRate": "0.00055", "makerFeeRate": "0.0002"}]}
            else:
                assert request.url.path == "/v5/account/user-setting-config"
                result = {"lpaPerp": True, "fmsef": False}
        return httpx.Response(200, content=json.dumps({"retCode": 0, "result": result, "time": stamp}).encode(), request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    return BybitDemoAccountSession(api_key=KEY, api_secret=SECRET, clock=lambda: now), requests


async def authenticate(owner: BybitDemoAccountSession) -> Any:
    return await owner.authenticate(expected_uid="12345", expected_parent_uid="56789", expected_is_master=False)


@pytest.mark.parametrize("kind", ["wallet", "fee_rate", "settings"])
async def test_current_info_and_new_endpoint_survive_together_with_data_projection(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    owner, requests = intercepted_session(monkeypatch)
    try:
        binding = await authenticate(owner)
        info = await owner._capture_linear_account_info_v1(binding, scope=SCOPE,
            budget=MonitoringReadBudget(), owner_guard=lambda: None)
        method = getattr(owner, "_capture_linear_" + kind + "_v1", None)
        assert callable(method), "Fixed original-Session source API is absent"
        arguments = {"symbol": "BTCUSDT"} if kind == "fee_rate" else {}
        original = await method(binding, scope=SCOPE, budget=MonitoringReadBudget(),
                                owner_guard=lambda: None, **arguments)
        owner._require_linear_account_info_v1(info, binding=binding, scope=SCOPE)
        getattr(owner, "_require_linear_" + kind + "_v1")(original, binding=binding, scope=SCOPE, **arguments)
        assert owner._project_linear_account_mode_v1(info, binding=binding, scope=SCOPE).updated_time_ms == 1
        project_name = {"wallet": "wallet", "fee_rate": "fee_rates", "settings": "trade_behaviour"}[kind]
        projected = getattr(owner, "_project_linear_" + project_name + "_v1")(
            original, binding=binding, scope=SCOPE, **arguments)
        if kind == "fee_rate":
            assert projected[0].reference.source is projected[1].reference.source is original
        else:
            assert projected.source is original
        assert len(requests) == 4
    finally:
        await owner.aclose()


async def capture_one(owner: BybitDemoAccountSession, binding: Any, kind: str) -> BybitModelSourceRefV1:
    name = "account_info" if kind == "info" else kind
    captured: BybitModelSourceRefV1 = await getattr(owner, "_capture_linear_" + name + "_v1")(
        binding, scope=SCOPE, budget=MonitoringReadBudget(), owner_guard=lambda: None,
        **({"symbol": "BTCUSDT"} if kind == "fee_rate" else {}))
    return captured


def require_one(owner: BybitDemoAccountSession, binding: Any, kind: str, source: BybitModelSourceRefV1) -> None:
    name = "account_info" if kind == "info" else kind
    getattr(owner, "_require_linear_" + name + "_v1")(source, binding=binding, scope=SCOPE,
        **({"symbol": "BTCUSDT"} if kind == "fee_rate" else {}))


async def capture_all(owner: BybitDemoAccountSession, binding: Any) -> dict[str, BybitModelSourceRefV1]:
    values = {}
    for kind in ("info", "wallet", "fee_rate", "settings"):
        values[kind] = await capture_one(owner, binding, kind)
    for kind, source in values.items():
        require_one(owner, binding, kind, source)
    return values


@pytest.mark.parametrize("replaced", ["info", "wallet", "fee_rate", "settings"])
async def test_new_issuance_replaces_only_its_endpoint_and_retains_other_sources(
    monkeypatch: pytest.MonkeyPatch, replaced: str,
) -> None:
    owner, requests = intercepted_session(monkeypatch)
    try:
        binding = await authenticate(owner)
        values = await capture_all(owner, binding)
        newer = await capture_one(owner, binding, replaced)
        assert newer is not values[replaced]
        with pytest.raises(AccountBindingError):
            require_one(owner, binding, replaced, values[replaced])
        require_one(owner, binding, replaced, newer)
        for kind, source in values.items():
            if kind != replaced:
                require_one(owner, binding, kind, source)
        assert len(requests) == 7
    finally:
        await owner.aclose()


async def test_reauthentication_withdraws_all_sources_before_refresh_lock_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, requests = intercepted_session(monkeypatch)
    task: asyncio.Task[Any] | None = None
    try:
        binding = await authenticate(owner)
        values = await capture_all(owner, binding)
        await owner._refresh_lock.acquire()
        task = asyncio.create_task(authenticate(owner))
        await asyncio.sleep(0)
        for kind, source in values.items():
            with pytest.raises(AccountBindingError):
                require_one(owner, binding, kind, source)
        assert len(requests) == 6
        owner._refresh_lock.release()
        replacement = await task
        for kind, source in values.items():
            with pytest.raises(AccountBindingError):
                require_one(owner, replacement, kind, source)
    finally:
        if owner._refresh_lock.locked():
            owner._refresh_lock.release()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await owner.aclose()


@pytest.mark.parametrize("target", ["session", "client"])
async def test_original_close_withdraws_every_endpoint(
    monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    owner, requests = intercepted_session(monkeypatch)
    try:
        binding = await authenticate(owner)
        values = await capture_all(owner, binding)
        await (owner.aclose() if target == "session" else owner._client.aclose())
        for kind, source in values.items():
            with pytest.raises(AccountBindingError):
                require_one(owner, binding, kind, source)
        assert len(requests) == 6
    finally:
        await owner.aclose()


@pytest.mark.parametrize("change", ["client", "credentials"])
async def test_shared_owner_change_withdraws_all_and_restore_does_not_revive(
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    owner, requests = intercepted_session(monkeypatch)
    original_client, original_key = owner._client, owner._api_key
    try:
        binding = await authenticate(owner)
        values = await capture_all(owner, binding)
        if change == "client":
            owner._client = copy.copy(original_client)
        else:
            owner._api_key = "CHANGED-OFFLINE-KEY"
        with pytest.raises(AccountBindingError):
            require_one(owner, binding, "wallet", values["wallet"])
        owner._client, owner._api_key = original_client, original_key
        for kind, source in values.items():
            with pytest.raises(AccountBindingError):
                require_one(owner, binding, kind, source)
        assert len(requests) == 6
    finally:
        owner._client, owner._api_key = original_client, original_key
        await owner.aclose()


async def test_cross_endpoint_inflight_overlap_retires_original_until_receive_drains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, requests = intercepted_session(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original_send = httpx.AsyncClient.send

    async def send(client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        if request.url.path == "/v5/account/wallet-balance" and not release.is_set():
            entered.set()
            await release.wait()
        return await original_send(client, request, **kwargs)

    task: asyncio.Task[BybitModelSourceRefV1] | None = None
    try:
        binding = await authenticate(owner)
        info = await capture_one(owner, binding, "info")
        monkeypatch.setattr(httpx.AsyncClient, "send", send)
        task = asyncio.create_task(capture_one(owner, binding, "wallet"))
        await entered.wait()
        with pytest.raises(AccountBindingError, match="OVERLAP"):
            await capture_one(owner, binding, "settings")
        with pytest.raises(AccountBindingError):
            require_one(owner, binding, "info", info)
        release.set()
        with pytest.raises(AccountBindingError):
            await task
        assert owner._linear_info_pending is None
        newer = await capture_one(owner, binding, "wallet")
        require_one(owner, binding, "wallet", newer)
        assert len(requests) == 5
    finally:
        release.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await owner.aclose()


async def test_inner_caught_require_of_another_endpoint_taints_outer_and_all_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, _requests = intercepted_session(monkeypatch)
    enabled = False
    refused: list[str] = []
    values: dict[str, BybitModelSourceRefV1] = {}
    binding: Any = None

    def guard() -> None:
        if enabled:
            try:
                require_one(owner, binding, "wallet", values["wallet"])
            except AccountBindingError as error:
                refused.append(str(error))

    try:
        binding = await authenticate(owner)
        values = await capture_all(owner, binding)
        info = await owner._capture_linear_account_info_v1(binding, scope=SCOPE,
            budget=MonitoringReadBudget(), owner_guard=guard)
        values["info"] = info
        enabled = True
        with pytest.raises(AccountBindingError):
            require_one(owner, binding, "info", info)
        enabled = False
        assert refused == ["ACCOUNT_INFO_SOURCE_OVERLAP"]
        for kind, source in values.items():
            with pytest.raises(AccountBindingError):
                require_one(owner, binding, kind, source)
    finally:
        enabled = False
        await owner.aclose()


@pytest.mark.parametrize("kind", ["wallet", "fee_rate", "settings"])
async def test_projection_rechecks_genuine_binding_after_data_constructor_work(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    owner, requests = intercepted_session(monkeypatch)
    original_clock = owner._clock
    calls = 0

    def clock() -> datetime:
        nonlocal calls
        calls += 1
        return original_clock() + (timedelta(seconds=31) if calls == 2 else timedelta())

    try:
        binding = await authenticate(owner)
        values = await capture_all(owner, binding)
        owner._clock = clock
        name = {"wallet": "wallet", "fee_rate": "fee_rates", "settings": "trade_behaviour"}[kind]
        with pytest.raises(AccountBindingError, match="ACCOUNT_DATA_PROJECTION_REQUIRED") as caught:
            getattr(owner, "_project_linear_" + name + "_v1")(values[kind], binding=binding, scope=SCOPE,
                **({"symbol": "BTCUSDT"} if kind == "fee_rate" else {}))
        assert calls == 2 and caught.value.__context__ is caught.value.__cause__ is None
        owner._clock = original_clock
        for endpoint, source in values.items():
            with pytest.raises(AccountBindingError):
                require_one(owner, binding, endpoint, source)
        assert len(requests) == 6
    finally:
        owner._clock = original_clock
        await owner.aclose()


async def test_fee_requires_same_requested_symbol_and_new_symbol_retires_only_fee(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, requests = intercepted_session(monkeypatch)
    try:
        binding = await authenticate(owner)
        values = await capture_all(owner, binding)
        with pytest.raises(AccountBindingError):
            owner._require_linear_fee_rate_v1(values["fee_rate"], symbol="ETHUSDT", binding=binding, scope=SCOPE)
        require_one(owner, binding, "fee_rate", values["fee_rate"])
        eth = await owner._capture_linear_fee_rate_v1(binding, symbol="ETHUSDT", scope=SCOPE,
            budget=MonitoringReadBudget(), owner_guard=lambda: None)
        owner._require_linear_fee_rate_v1(eth, symbol="ETHUSDT", binding=binding, scope=SCOPE)
        with pytest.raises(AccountBindingError):
            require_one(owner, binding, "fee_rate", values["fee_rate"])
        for kind in ("info", "wallet", "settings"):
            require_one(owner, binding, kind, values[kind])
        assert len(requests) == 7
    finally:
        await owner.aclose()
