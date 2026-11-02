import asyncio
import importlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from multi_exchange_trader.account.bybit_readonly import sign_v5
from multi_exchange_trader.execution.protection import ProtectionScope

NOW = datetime(2026, 11, 2, 1, tzinfo=UTC)
MS = int(NOW.timestamp() * 1000)
KEY, SECRET = "fake-binding-api-key", "fake-binding-secret"
SCOPE = ProtectionScope("12345", "DEMO")


def payload() -> dict[str, Any]:
    return {"apiKey": KEY, "secret": "", "userID": 12345, "isMaster": False,
            "parentUid": "56789", "readOnly": 0,
            "permissions": {"ContractTrade": ["Order", "Position"], "Wallet": [],
                            "Derivatives": ["DerivativesTrade"], "Spot": []}}


def session(result: dict[str, Any], *, clock: Any = lambda: NOW,
            monotonic: Any = lambda: 10.0, server_ms: int = MS) -> Any:
    from multi_exchange_trader.execution.account_binding import BybitDemoAccountSession

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.host == "api-demo.bybit.com"
        assert not request.url.query
        if request.url.path == "/v5/market/time":
            assert "X-BAPI-API-KEY" not in request.headers
            body: dict[str, Any] = {}
        else:
            assert request.url.path == "/v5/user/query-api"
            assert request.headers["X-BAPI-API-KEY"] == KEY
            assert request.headers["X-BAPI-SIGN"] == sign_v5(
                SECRET, KEY, request.headers["X-BAPI-TIMESTAMP"],
                request.headers["X-BAPI-RECV-WINDOW"], "")
            body = result
        return httpx.Response(200, json={"retCode": 0, "time": server_ms, "result": body})

    return BybitDemoAccountSession(api_key=KEY, api_secret=SECRET, clock=clock,
        monotonic=monotonic, max_age=timedelta(seconds=10),
        transport=httpx.MockTransport(handler))


async def authenticate(owner: Any) -> Any:
    return await owner.authenticate(expected_uid="12345", expected_parent_uid="56789",
                                    expected_is_master=False)


@pytest.mark.parametrize("parent,is_master", [("0", False), ("56789", True),
                                              ("0", True), ("56789", False)])
async def test_uid_parent_and_master_are_independently_attested(
    parent: str, is_master: bool,
) -> None:
    # False/zero is the sanitized shape of the actual DEMO GET, with fake IDs/key.
    owner = session(payload() | {"parentUid": parent, "isMaster": is_master})
    try:
        binding = await owner.authenticate(expected_uid="12345", expected_parent_uid=parent,
                                           expected_is_master=is_master)
        identity = owner.require_binding(binding, scope=SCOPE)
        assert (identity.parent_uid, identity.is_master) == (parent, is_master)
        for expected in (
            {"expected_parent_uid": "999", "expected_is_master": is_master},
            {"expected_parent_uid": parent, "expected_is_master": not is_master},
        ):
            with pytest.raises(ValueError, match="ACCOUNT_AUTHENTICATION_FAILED"):
                await owner.authenticate(expected_uid="12345", **expected)
            with pytest.raises(ValueError, match="BINDING_REQUIRED"):
                owner.require_binding(binding, scope=SCOPE)
    finally:
        await owner.aclose()


@pytest.mark.parametrize("expected", [None, 0, 1, "false"])
async def test_master_attestation_requires_an_explicit_boolean(expected: Any) -> None:
    owner = session(payload())
    try:
        binding = await authenticate(owner)
        with pytest.raises(ValueError, match="ACCOUNT_MASTER_ATTESTATION_REQUIRED"):
            await owner.authenticate(expected_uid="12345", expected_parent_uid="56789",
                                     expected_is_master=expected)
        with pytest.raises(ValueError, match="BINDING_REQUIRED"):
            owner.require_binding(binding, scope=SCOPE)
    finally:
        await owner.aclose()


async def test_close_error_is_sanitized_and_prior_binding_remains_revoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.execution.account_binding import AccountBindingError

    owner = session(payload())
    binding = await authenticate(owner)

    async def failed_close() -> None:
        raise RuntimeError(KEY + SECRET)

    monkeypatch.setattr(owner._client, "aclose", failed_close)
    with pytest.raises(AccountBindingError, match="^ACCOUNT_SESSION_CLOSE_FAILED$") as error:
        await owner.aclose()
    assert KEY not in str(error.value) and SECRET not in str(error.value)
    assert error.value.__cause__ is None and error.value.__context__ is None
    with pytest.raises(ValueError, match="SESSION_MISMATCH_OR_CLOSED"):
        owner.require_binding(binding, scope=SCOPE)


async def test_close_cancellation_propagates_after_revocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = session(payload())
    binding = await authenticate(owner)

    async def cancelled_close() -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(owner._client, "aclose", cancelled_close)
    with pytest.raises(asyncio.CancelledError):
        await owner.aclose()
    with pytest.raises(ValueError, match="SESSION_MISMATCH_OR_CLOSED"):
        owner.require_binding(binding, scope=SCOPE)


async def test_signed_get_binds_actual_subaccount_and_full_permissions_without_secrets() -> None:
    owner = session(payload())
    try:
        binding = await authenticate(owner)
        identity = owner.require_binding(binding, scope=SCOPE)
        assert identity.uid == "12345"
        assert identity.parent_uid == "56789"
        assert not identity.is_master
        assert identity.scope == SCOPE
        assert identity.has_permission("ContractTrade", "Order")
        assert not identity.has_permission("Wallet", "Withdraw")
        assert not identity.prohibited_permissions
        assert identity.transport_injected
        for value in (binding, identity, owner):
            assert KEY not in repr(value) and SECRET not in repr(value)
        with pytest.raises(ValueError, match="NETWORK_TRANSPORT_REQUIRED"):
            owner.require_execution_binding(binding, scope=SCOPE)
    finally:
        await owner.aclose()


@pytest.mark.parametrize("changes", [
    {"apiKey": "different-key"}, {"userID": 987}, {"userID": True},
    {"userID": "12345"}, {"parentUid": "999"}, {"parentUid": "0"},
    {"isMaster": True}, {"isMaster": 0}, {"readOnly": True}, {"secret": SECRET},
    {"permissions": {"ContractTrade": "Order"}},
    {"permissions": {"Wallet": [SECRET]}},
])
async def test_spoofed_or_malformed_identity_is_rejected_without_echo(changes: dict[str, Any]) -> None:
    result = payload() | changes
    owner = session(result)
    try:
        with pytest.raises(ValueError) as error:
            await authenticate(owner)
        assert KEY not in str(error.value) and SECRET not in str(error.value)
    finally:
        await owner.aclose()


@pytest.mark.parametrize("grant", ["Withdraw", "AccountTransfer", "SubMemberTransfer",
                                   "SubMemberTransferList"])
async def test_all_wallet_privileges_remain_visible_and_prohibit_execution(grant: str) -> None:
    result = payload()
    result["permissions"]["Wallet"] = [grant]
    owner = session(result)
    try:
        binding = await authenticate(owner)
        identity = owner.require_binding(binding, scope=SCOPE)
        assert identity.has_permission("Wallet", grant)
        assert identity.prohibited_permissions == (("Wallet", grant),)
        assert not identity.linear_execution_permitted
    finally:
        await owner.aclose()


@pytest.mark.parametrize("changes", [{"readOnly": 1},
    {"permissions": {"ContractTrade": ["Order"], "Wallet": []}},
    {"permissions": {"ContractTrade": ["Order", "Position"]}},
    {"permissions": {"ContractTrade": ["Order", "Position"], "Wallet": [],
                     "Spot": ["SpotTrade"]}}])
async def test_incomplete_readonly_or_excess_permissions_never_authorize_linear(
    changes: dict[str, Any],
) -> None:
    owner = session(payload() | changes)
    try:
        binding = await authenticate(owner)
        assert not owner.require_binding(binding, scope=SCOPE).linear_execution_permitted
    finally:
        await owner.aclose()


async def test_expiry_uses_wall_and_monotonic_time_and_refresh_revokes_old_handle() -> None:
    wall, mono = [NOW], [10.0]
    result = payload()
    owner = session(result, clock=lambda: wall[0], monotonic=lambda: mono[0])
    try:
        binding = await authenticate(owner)
        mono[0] = 21.0
        with pytest.raises(ValueError, match="STALE"):
            owner.require_binding(binding, scope=SCOPE)
        mono[0] = 10.0
        wall[0] = NOW - timedelta(seconds=1)
        with pytest.raises(ValueError, match="STALE"):
            owner.require_binding(binding, scope=SCOPE)
        wall[0] = NOW
        result["apiKey"] = "rotated-key"
        with pytest.raises(ValueError):
            await authenticate(owner)
        with pytest.raises(ValueError, match="BINDING_REQUIRED"):
            owner.require_binding(binding, scope=SCOPE)
    finally:
        await owner.aclose()


async def test_caller_forgery_cross_session_scope_and_closed_session_are_rejected() -> None:
    from multi_exchange_trader.execution.account_binding import AuthenticatedAccountBinding

    first, second = session(payload()), session(payload())
    try:
        binding = await authenticate(first)
        forged = object.__new__(AuthenticatedAccountBinding)
        for owner, candidate, scope in (
            (first, forged, SCOPE), (second, binding, SCOPE),
            (first, binding, ProtectionScope("BYBIT-UNIFIED", "DEMO")),
            (first, binding, ProtectionScope("12345", "LIVE")),
        ):
            with pytest.raises(ValueError):
                owner.require_binding(candidate, scope=scope)
        await first.aclose()
        with pytest.raises(ValueError):
            first.require_binding(binding, scope=SCOPE)
    finally:
        await first.aclose()
        await second.aclose()


@pytest.mark.parametrize("server_ms", [MS - 11000, MS + 1])
async def test_stale_or_future_server_identity_cannot_be_refreshed(server_ms: int) -> None:
    owner = session(payload(), server_ms=server_ms)
    try:
        with pytest.raises(ValueError):
            await authenticate(owner)
    finally:
        await owner.aclose()


@pytest.mark.parametrize("attribute,value", [("_api_key", "rotated"),
    ("_api_secret", "rotated"), ("environment", "mainnet"),
    ("base_url", "https://api.bybit.com")])
async def test_client_drift_revokes_binding_before_readback(attribute: str, value: str) -> None:
    owner = session(payload())
    try:
        binding = await authenticate(owner)
        client = owner.read_client(binding, scope=SCOPE)
        setattr(client, attribute, value)
        with pytest.raises(ValueError, match="MISMATCH"):
            owner.read_client(binding, scope=SCOPE)
    finally:
        await owner.aclose()


async def test_native_config_uses_authenticated_pair_and_has_no_connect_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fake HTTP/native modules exercise construction, never certify deployed DEMO."""
    from multi_exchange_trader.execution import account_binding, bybit_native

    original_http = httpx.AsyncClient
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        if request.url.path == "/v5/user/query-api":
            assert request.headers["X-BAPI-API-KEY"] == KEY
            assert request.headers["X-BAPI-SIGN"] == sign_v5(SECRET, KEY,
                request.headers["X-BAPI-TIMESTAMP"], request.headers["X-BAPI-RECV-WINDOW"], "")
        return httpx.Response(200, json={"retCode": 0, "time": MS, "result": payload()})

    def fake_http(**kwargs: Any) -> httpx.AsyncClient:
        return original_http(**(kwargs | {"transport": httpx.MockTransport(respond)}))

    monkeypatch.setattr(httpx, "AsyncClient", fake_http)
    original_import = importlib.import_module

    def native_import(name: str) -> Any:
        if name == "nautilus_trader.adapters.bybit.config":
            return SimpleNamespace(BybitExecClientConfig=lambda **kwargs: kwargs)
        if name == "nautilus_trader.core.nautilus_pyo3":
            return SimpleNamespace(BybitEnvironment=SimpleNamespace(DEMO="DEMO"),
                                   BybitProductType=SimpleNamespace(LINEAR="LINEAR"))
        return original_import(name)

    monkeypatch.setattr(bybit_native, "version", lambda _: "1.231.0")
    monkeypatch.setattr(importlib, "import_module", native_import)
    owner = account_binding.BybitDemoAccountSession(api_key=" " + KEY + " ",
        api_secret=" " + SECRET + " ", clock=lambda: NOW)
    installed: list[Any] = []
    try:
        binding = await authenticate(owner)
        owner.configure_native_execution(binding, scope=SCOPE, install=installed.append)
        assert len(installed) == 1
        config = installed[0]
        assert (config["api_key"], config["api_secret"]) == (KEY, SECRET)
        assert (config["environment"], config["product_types"]) == ("DEMO", ("LINEAR",))
        assert all(config[name] is None for name in (
            "futures_leverages", "margin_mode", "position_mode", "base_url_http",
            "base_url_ws_private", "base_url_ws_trade", "proxy_url"))
        assert config["auto_repay_spot_borrows"] is False
        assert len(requests) == 2

        def failed_installer(_: object) -> None:
            raise RuntimeError(KEY + SECRET)

        with pytest.raises(ValueError) as error:
            owner.configure_native_execution(binding, scope=SCOPE, install=failed_installer)
        assert KEY not in str(error.value) and SECRET not in str(error.value)
    finally:
        await owner.aclose()


async def test_credentials_cannot_escape_through_uid_even_when_numeric() -> None:
    from multi_exchange_trader.execution.account_binding import BybitDemoAccountSession

    result = payload() | {"apiKey": "12345"}
    owner = BybitDemoAccountSession(api_key="12345", api_secret=SECRET, clock=lambda: NOW,
        transport=httpx.MockTransport(lambda _: httpx.Response(200,
            json={"retCode": 0, "time": MS, "result": result})))
    try:
        with pytest.raises(ValueError):
            await authenticate(owner)
    finally:
        await owner.aclose()
