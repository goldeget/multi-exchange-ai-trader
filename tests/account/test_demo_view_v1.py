"""Original stored-format decoders, resolver and Session; intercepted GETs only."""

import asyncio
import importlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

from multi_exchange_trader.account import bybit_readonly
from multi_exchange_trader.account.config import MissingBybitCredentials, require_demo_credentials
from multi_exchange_trader.account_profiles import AccountProfileError, AccountProfileStore, _decode
from multi_exchange_trader.credential_resolution import CredentialResolver
from multi_exchange_trader.credentials import CredentialSnapshot, CredentialStore, _validate_values

NOW = datetime(2026, 11, 4, 6, tzinfo=UTC)
MS = int(NOW.timestamp() * 1000)
KEY, SECRET = "fixture-demo-key", "fixture-demo-secret"


def module() -> ModuleType:
    return importlib.import_module("multi_exchange_trader.account.demo_view_v1")


def profile(*, pending: bool = False) -> bytes:
    stamp = "2026-11-03T00:00:00Z"
    evidence = "a24bd44b-422d-409c-8563-91458ed1eb18"
    identifier = "83a05b72-f883-451a-9e58-639bd9909912"
    return json.dumps({"schema": 1, "revision": 1, "selected_profile_id": identifier,
        "profiles": [{"profile_id": identifier, "exchange": "BYBIT", "environment": "DEMO",
        "uid": "12345", "uid_evidence": {"basis": "OPERATOR_SELECTED",
            "evidence_id": evidence, "confirmed_at": stamp},
        "relationship": None if pending else {"parent_uid": "0", "is_master": False,
            "basis": "PROVIDER_OBSERVED_OPERATOR_ADOPTED", "source": "BYBIT_DEMO_QUERY_API",
            "evidence_id": evidence, "confirmed_at": stamp, "provider_observed_at": stamp,
            "policy": "uid-anchored-adoption-v1"},
        "last_observation": None if pending else {"source": "BYBIT_DEMO_QUERY_API",
            "parent_uid": "0", "is_master": False, "observed_at": stamp,
            "server_observed_at": stamp}}]}).encode()


class Wire:
    def __init__(self) -> None:
        self.paths: list[str] = []
        self.clients: list[httpx.AsyncClient] = []
        self.now = NOW
        self.mono = 10.0
        self.uid = 12345
        self.delay = False
        self.block = False
        self.wallet_error = False
        self.entered = asyncio.Event()

    async def send(self, client: httpx.AsyncClient, request: httpx.Request,
                   **_kwargs: Any) -> httpx.Response:
        assert request.method == "GET" and request.url.host == "api-demo.bybit.com"
        self.clients.append(client)
        path = request.url.path
        self.paths.append(path)
        if path not in {"/v5/market/time", "/v5/market/instruments-info"}:
            assert request.headers["X-BAPI-API-KEY"] == KEY
            assert request.headers["X-BAPI-SIGN"] == bybit_readonly.sign_v5(
                SECRET, KEY, request.headers["X-BAPI-TIMESTAMP"],
                request.headers["X-BAPI-RECV-WINDOW"], request.url.query.decode())
        if path == "/v5/user/query-api":
            result: dict[str, Any] = {"apiKey": KEY, "secret": "", "userID": self.uid,
                "isMaster": False, "parentUid": "0", "readOnly": 1,
                "permissions": {"ContractTrade": [], "Wallet": []}}
        elif path == "/v5/market/time":
            result = {}
        elif path == "/v5/market/instruments-info":
            result = {"category": "linear", "list": [{"symbol": "BTCUSDT",
                "leverageFilter": {"maxLeverage": "100", "minLeverage": "1",
                                   "leverageStep": "0.01"}}]}
        elif path == "/v5/account/wallet-balance":
            assert list(request.url.params.multi_items()) == [("accountType", "UNIFIED")]
            if self.wallet_error:
                return httpx.Response(200, request=request, json={"retCode": 10003,
                    "retMsg": KEY + SECRET, "time": int(self.now.timestamp() * 1000), "result": {}})
            self.entered.set()
            if self.block:
                await asyncio.Event().wait()
            result = {"list": [{"accountType": "UNIFIED", "totalEquity": "100.5",
                "totalWalletBalance": "100", "totalAvailableBalance": "80",
                "coin": [{"coin": "USDT", "walletBalance": "100", "equity": "100.5"}]}]}
        elif path == "/v5/account/info":
            result = {"unifiedMarginStatus": 3, "marginMode": "REGULAR_MARGIN",
                      "updatedTime": str(MS)}
        elif path == "/v5/position/list":
            result = {"list": [{"symbol": "BTCUSDT", "size": "0.01", "side": "Buy",
                "positionIdx": 0, "leverage": "2", "avgPrice": "50000",
                "markPrice": "50050", "unrealisedPnl": "0.5", "liqPrice": "25000"}],
                "nextPageCursor": ""}
        else:
            assert path == "/v5/order/realtime"
            if self.delay:
                self.now += timedelta(seconds=31)
                self.mono += 31
            result = {"list": [{"symbol": "BTCUSDT", "side": "Sell", "orderType": "Limit",
                "qty": "0.01", "price": "55000", "orderStatus": "New", "reduceOnly": True}],
                "nextPageCursor": ""}
        return httpx.Response(200, request=request,
            json={"retCode": 0, "time": int(self.now.timestamp() * 1000), "result": result})


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Any, Wire, CredentialResolver]:
    """Load seams supply real immutable decoders; no secret file is read or written."""
    store = CredentialStore(tmp_path / "unused-credentials")
    snapshot = CredentialSnapshot(1, _validate_values({
        "BYBIT_DEMO_API_KEY": KEY, "BYBIT_DEMO_API_SECRET": SECRET}))
    monkeypatch.setattr(store, "load", lambda: snapshot)
    resolver = CredentialResolver(store=store, environ={"BYBIT_DEMO": "false",
        "BYBIT_ENV_FILE": "off", "AI_ENV_FILE": "off", "ANTHROPIC_ENV_FILE": "off"})
    profiles = AccountProfileStore(tmp_path / "unused-profiles")
    monkeypatch.setattr(profiles, "load", lambda: _decode(profile()))
    wire = Wire()

    async def send(client: httpx.AsyncClient, request: httpx.Request,
                   **kwargs: Any) -> httpx.Response:
        return await wire.send(client, request, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    service = module().BybitDemoAccountViewV1(resolver=resolver, profile_store=profiles,
        clock=lambda: wire.now, monotonic=lambda: wire.mono)
    return service, wire, resolver


async def test_saved_demo_read_is_explicit_lazy_and_preserves_practice_flag(setup: Any) -> None:
    service, wire, resolver = setup
    assert wire.paths == []
    with pytest.raises(MissingBybitCredentials, match="BYBIT_DEMO is not enabled"):
        require_demo_credentials(resolver=resolver)
    view = await service.view()
    assert view.상태코드 == "ok"
    assert view.환경코드 == "demo" and view.계좌마진모드 == "교차"
    assert view.잔고.총자산평가 == "100.5"
    assert view.포지션[0].수량 == "0.01" and view.미체결주문[0].줄이기만 == "예"
    assert view.실거래켜짐 is False and view.주문보냄 is False and not view.주문판단에사용
    assert KEY not in view.model_dump_json() and SECRET not in view.model_dump_json()
    assert all(client.is_closed for client in wire.clients)
    assert set(wire.paths) == {"/v5/market/time", "/v5/user/query-api",
        "/v5/market/instruments-info", "/v5/account/wallet-balance", "/v5/account/info",
        "/v5/position/list", "/v5/order/realtime"}


@pytest.mark.parametrize("kind", ["missing", "pending", "invalid"])
async def test_bad_profile_stops_before_credentials_or_http(
    setup: Any, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    service, wire, resolver = setup

    def load() -> Any:
        if kind == "invalid":
            raise AccountProfileError("INVALID_STORE")
        return None if kind == "missing" else _decode(profile(pending=True))

    monkeypatch.setattr(service._profile_store, "load", load)
    monkeypatch.setattr(resolver, "bybit_pair", lambda _: pytest.fail("credentials read"))
    view = await service.view()
    assert view.상태코드 == "config" and view.환경코드 == "demo"
    assert view.잔고 is None and not wire.paths


async def test_uid_mismatch_never_reads_or_exposes_account_data(setup: Any) -> None:
    service, wire, _resolver = setup
    wire.uid = 67890
    view = await service.view()
    assert view.상태코드 == "error" and view.잔고 is None
    assert wire.paths == ["/v5/market/time", "/v5/user/query-api"]
    assert all(client.is_closed for client in wire.clients)


async def test_missing_demo_pair_never_falls_back_to_live_or_starts_http(setup: Any) -> None:
    service, wire, _resolver = setup
    service._resolver = CredentialResolver(store=None, environ={"BYBIT_ENV_FILE": "off",
        "BYBIT_API_KEY": "fixture-live-key", "BYBIT_API_SECRET": "fixture-live-secret"})
    view = await service.view()
    assert view.상태코드 == "missing_credentials" and view.환경코드 == "demo"
    assert view.잔고 is None and not wire.paths


async def test_provider_error_payload_is_not_published_or_partially_shown(setup: Any) -> None:
    service, wire, _resolver = setup
    wire.wallet_error = True
    view = await service.view()
    assert view.상태코드 == "error" and view.잔고 is None
    assert view.포지션 is None and view.미체결주문 is None
    assert KEY not in view.model_dump_json() and SECRET not in view.model_dump_json()
    assert all(client.is_closed for client in wire.clients)


async def test_authentication_expiry_during_reads_discards_all_numbers(setup: Any) -> None:
    service, wire, _resolver = setup
    wire.delay = True
    view = await service.view()
    assert view.상태코드 == "error" and view.오류코드 == "DEMO_AUTHENTICATION_REQUIRED"
    assert view.잔고 is None and view.포지션 is None and view.미체결주문 is None
    assert all(client.is_closed for client in wire.clients)


async def test_cancellation_propagates_and_closes_original_session(setup: Any) -> None:
    service, wire, _resolver = setup
    wire.block = True
    task = asyncio.create_task(service.view())
    await wire.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(client.is_closed for client in wire.clients)


def test_default_factory_does_not_load_stores_or_start_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(CredentialStore, "load", lambda _: pytest.fail("credential I/O"))
    monkeypatch.setattr(AccountProfileStore, "load", lambda _: pytest.fail("profile I/O"))
    monkeypatch.setattr(httpx.AsyncClient, "__init__", lambda *_a, **_k: pytest.fail("HTTP startup"))
    service = module().BybitDemoAccountViewV1.from_environment(environ={
        "TRADER_CREDENTIAL_FILE": "off", "TRADER_ACCOUNT_PROFILE_FILE": "off"})
    assert service is not None
