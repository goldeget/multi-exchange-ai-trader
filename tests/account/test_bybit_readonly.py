"""Mocked Bybit reads: signing, external rows, and errors. No network."""

import asyncio
import hashlib
import hmac
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from multi_exchange_trader.account.api import AccountReadGate
from multi_exchange_trader.account.bybit_readonly import (
    READ_ONLY_PATHS,
    BybitReadOnlyClient,
    ReadOnlyViolation,
    sign_v5,
)
from multi_exchange_trader.account.config import MissingBybitCredentials, require_bybit_credentials
from multi_exchange_trader.account.freshness import order_decision_allowed
from multi_exchange_trader.account.service import blank_view, build_account_view, publish
from multi_exchange_trader.api import create_app

KEY = "test-bybit-key"
SECRET = "test-bybit-secret"
ENV_SECRET = "env-secret-should-win12"
FILE_SECRET = "file-secret-should-not-win"
KNOWN_SIGN = "d69709048bfcc6a949f2781d2c18d99b30ce2613ea4b762d9d3b0056b8f84e4b"


def query_text(request: httpx.Request) -> str:
    raw = request.url.query
    if isinstance(raw, bytes):
        return raw.decode()
    return str(raw)


def assert_no_secret(request: httpx.Request, secret: str = SECRET) -> None:
    assert secret not in str(request.url)
    assert secret.encode() not in request.content
    for name, value in request.headers.items():
        assert secret not in name
        assert secret not in value
    assert request.method == "GET"


def assert_signed(request: httpx.Request, secret: str = SECRET, key: str = KEY) -> None:
    assert_no_secret(request, secret)
    query = query_text(request)
    timestamp = request.headers["X-BAPI-TIMESTAMP"]
    recv = request.headers["X-BAPI-RECV-WINDOW"]
    digest = hmac.new(
        secret.encode(),
        f"{timestamp}{key}{recv}{query}".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert request.headers["X-BAPI-API-KEY"] == key
    assert request.headers["X-BAPI-SIGN"] == digest
    assert secret not in digest


def envelope(
    result: object, *, ret: int = 0, msg: str = "OK", time_ms: int = 1700000000000
) -> dict[str, object]:
    return {"retCode": ret, "retMsg": msg, "result": result, "time": time_ms}


def ok(result: object, time_ms: int = 1700000000000) -> httpx.Response:
    return httpx.Response(200, json=envelope(result, time_ms=time_ms))


def success_handler(seen: list[httpx.Request], host: str = "api.bybit.com") -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.host == host
        path = request.url.path
        if path == "/v5/market/time":
            assert "X-BAPI-API-KEY" not in request.headers
            return ok({"timeSecond": "1700000000", "timeNano": "1700000000000000000"})
        if path == "/v5/market/instruments-info":
            return ok(
                {
                    "category": "linear",
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "leverageFilter": {
                                "minLeverage": "1",
                                "maxLeverage": "125.5",
                                "leverageStep": "0.01",
                            },
                        }
                    ],
                }
            )
        if path == "/v5/account/wallet-balance":
            assert_signed(request)
            assert query_text(request) == "accountType=UNIFIED"
            return ok(
                {
                    "list": [
                        {
                            "accountType": "UNIFIED",
                            "totalEquity": "20.5",
                            "totalWalletBalance": "20",
                            "totalAvailableBalance": "18",
                            "coin": [{"coin": "USDT", "walletBalance": "12.50", "equity": "12.50"}],
                        }
                    ]
                }
            )
        if path == "/v5/account/info":
            assert_signed(request)
            return ok(
                {
                    "marginMode": "REGULAR_MARGIN",
                    "updatedTime": "1697078946000",
                    "unifiedMarginStatus": 4,
                }
            )
        if path == "/v5/position/list":
            assert_signed(request)
            return ok(
                {
                    "category": "linear",
                    "nextPageCursor": "",
                    "list": [
                        {"symbol": "BTCUSDT", "side": "Buy", "size": "0", "origin": "program"},
                        {
                            "symbol": "BTCUSDT",
                            "side": "Buy",
                            "size": "0.01",
                            "leverage": "3",
                            "avgPrice": "60000",
                            "markPrice": "61000",
                            "liqPrice": "50000",
                            "unrealisedPnl": "1.2",
                            "tradeMode": 0,
                            "positionIdx": 0,
                            "origin": "program",
                            "managed": True,
                        },
                    ],
                }
            )
        if path == "/v5/order/realtime":
            assert_signed(request)
            return ok(
                {
                    "category": "linear",
                    "nextPageCursor": "",
                    "list": [
                        {
                            "symbol": "ETHUSDT",
                            "side": "Sell",
                            "orderType": "Limit",
                            "qty": "0.2",
                            "price": "3000",
                            "orderStatus": "New",
                            "reduceOnly": False,
                            "adopt": True,
                        }
                    ],
                }
            )
        raise AssertionError(path)

    return httpx.MockTransport(handler)


@pytest.fixture
def bybit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BYBIT_ENV_FILE", "off")
    monkeypatch.setenv("BYBIT_API_KEY", KEY)
    monkeypatch.setenv("BYBIT_API_SECRET", SECRET)
    monkeypatch.setenv("BYBIT_TESTNET", "false")
    monkeypatch.delenv("BYBIT_DEMO", raising=False)
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)


def test_sign_v5_known_digest_hides_the_secret() -> None:
    digest = sign_v5(SECRET, KEY, "1700000000000", "10000", "accountType=UNIFIED")
    assert digest == KNOWN_SIGN
    assert SECRET not in digest
    assert KEY not in digest


def test_allowlist_has_no_order_paths() -> None:
    assert READ_ONLY_PATHS == frozenset(
        {
            "/v5/market/time",
            "/v5/market/instruments-info",
            "/v5/account/wallet-balance",
            "/v5/account/info",
            "/v5/position/list",
            "/v5/order/realtime",
        }
    )
    names = {name.lower() for name in dir(BybitReadOnlyClient)}
    for banned in ("place_order", "submit_order", "cancel_order", "set_leverage"):
        assert banned not in names


def test_gitignore_covers_env_file() -> None:
    text = Path(__file__).resolve().parents[2].joinpath(".gitignore").read_text(encoding="utf-8")
    assert any(line.strip() == ".env" for line in text.splitlines())


def test_missing_secret_refuses_startup_without_echo(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BYBIT_ENV_FILE", "off")
    monkeypatch.setenv("BYBIT_API_KEY", KEY)
    monkeypatch.delenv("BYBIT_API_SECRET", raising=False)
    with pytest.raises(MissingBybitCredentials) as exc:
        BybitReadOnlyClient.from_environment()
    assert str(exc.value) == "BYBIT_API_SECRET is not set"
    assert KEY not in str(exc.value)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_env_file_loads_only_allowlisted_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = tmp_path / "sample.env"
    env_file.write_text(
        f"BYBIT_API_KEY={KEY}\nBYBIT_API_SECRET={SECRET}\nUNRELATED_BYBIT_TEST_TOKEN=should-not-load\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BYBIT_ENV_FILE", str(env_file))
    monkeypatch.delenv("BYBIT_API_KEY", raising=False)
    monkeypatch.delenv("BYBIT_API_SECRET", raising=False)
    monkeypatch.delenv("UNRELATED_BYBIT_TEST_TOKEN", raising=False)
    key, secret = require_bybit_credentials()
    assert key == KEY
    assert secret == SECRET
    assert "UNRELATED_BYBIT_TEST_TOKEN" not in os.environ
    captured = capsys.readouterr()
    assert captured.out == ""
    assert SECRET not in captured.err


def test_env_file_does_not_override_or_reject_bad_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / "sample.env"
    env_file.write_text(f"BYBIT_API_SECRET={FILE_SECRET}\n", encoding="utf-8")
    monkeypatch.setenv("BYBIT_ENV_FILE", str(env_file))
    monkeypatch.setenv("BYBIT_API_KEY", KEY)
    monkeypatch.setenv("BYBIT_API_SECRET", ENV_SECRET)
    with pytest.raises(ValueError) as bad_host:
        BybitReadOnlyClient(
            api_key=KEY,
            api_secret=SECRET,
            base_url="https://evil.example",
            environment="mainnet",
        )
    assert "evil" not in str(bad_host.value)
    assert SECRET not in str(bad_host.value)


def test_publish_hides_a_view_that_contains_the_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BYBIT_API_SECRET", SECRET)
    leaked = blank_view("missing_credentials", "mainnet", live_trading=False).model_copy(
        update={"메시지": f"oops {SECRET}"}
    )
    safe = publish(leaked)
    assert SECRET not in safe.메시지
    assert safe.잔고 is None
    assert safe.포지션 is None
    assert safe.상태코드 == "error"


async def test_mutating_path_does_not_touch_the_network(bybit_env: None) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(500)

    client = BybitReadOnlyClient.from_environment(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ReadOnlyViolation) as exc:
            await client.read("/v5/order/create", [], signed=False)
        assert SECRET not in str(exc.value)
        assert seen == []
        assert "test-bybit-secret" not in repr(client)
        assert KEY not in repr(client)
    finally:
        await client.aclose()


async def test_log_filter_drops_key_and_secret(
    bybit_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    client = BybitReadOnlyClient.from_environment(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    )
    logger = logging.getLogger("httpx")
    try:
        with caplog.at_level(logging.INFO, logger="httpx"):
            logger.info("plain account read")
            logger.info("header %s", SECRET)
            logger.info("key %s", KEY)
        assert "plain account read" in caplog.text
        assert SECRET not in caplog.text
        assert KEY not in caplog.text
    finally:
        await client.aclose()


async def test_success_tags_external_and_does_not_adopt(bybit_env: None) -> None:
    seen: list[httpx.Request] = []
    client = BybitReadOnlyClient.from_environment(transport=success_handler(seen))
    try:
        view = publish(await build_account_view(client, None))
    finally:
        await client.aclose()
    rendered = view.model_dump_json(by_alias=True)
    assert SECRET not in rendered
    assert KEY not in rendered
    assert "program" not in rendered
    assert "adopt" not in rendered
    assert view.상태코드 == "ok"
    assert view.환경코드 == "mainnet"
    assert view.페이퍼 is False
    assert view.주문보냄 is False
    assert view.실거래켜짐 is False
    assert view.최대레버리지 is not None
    assert view.최대레버리지.최대 == "125.5"
    assert view.잔고 is not None
    assert view.잔고.코인[0].지갑잔고 == "12.50"
    assert view.포지션 is not None
    assert len(view.포지션) == 1
    assert view.포지션[0].출처 == "external"
    assert view.포지션[0].관리함 is False
    assert view.포지션[0].구분 == "외부에서 연 포지션"
    assert view.포지션[0].마진모드 == "확인 불가"
    assert view.계좌마진모드 == "교차"
    assert view.주문판단에사용 is False
    assert view.순차조회 is True
    assert view.계정갱신시각 is not None and view.계정갱신시각.startswith("2023-10-12T")
    assert view.수신시각 != view.계정갱신시각
    assert view.미체결주문 is not None
    assert view.미체결주문[0].출처 == "external"
    assert view.미체결주문[0].관리함 is False
    assert any("가상 거래가 아닙니다" in item for item in view.구분)
    assert any("실계좌입니다" in item for item in view.구분)
    assert [request.url.path for request in seen] == [
        "/v5/market/time",
        "/v5/market/instruments-info",
        "/v5/account/wallet-balance",
        "/v5/account/info",
        "/v5/position/list",
        "/v5/order/realtime",
    ]


def _utc(ms: int) -> datetime:
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=ms)


async def test_account_info_without_time_keeps_balances(bybit_env: None) -> None:
    """Wallet time stays usable when account info omits its envelope time."""
    public_ms = 1_700_000_000_000
    wallet_ms = 1_700_000_005_000
    later_ms = 1_700_000_009_000
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(path)
        if path == "/v5/market/time":
            return ok({"timeSecond": "1700000000"}, time_ms=public_ms)
        if path == "/v5/market/instruments-info":
            return ok(
                {
                    "category": "linear",
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "leverageFilter": {
                                "minLeverage": "1",
                                "maxLeverage": "100",
                                "leverageStep": "1",
                            },
                        }
                    ],
                },
                time_ms=public_ms,
            )
        if path == "/v5/account/wallet-balance":
            assert_signed(request)
            return ok(
                {
                    "list": [
                        {
                            "accountType": "UNIFIED",
                            "totalEquity": "20.5",
                            "totalWalletBalance": "20",
                            "totalAvailableBalance": "18",
                            "coin": [{"coin": "USDT", "walletBalance": "12.50", "equity": "12.50"}],
                        }
                    ]
                },
                time_ms=wallet_ms,
            )
        if path == "/v5/account/info":
            assert_signed(request)
            body = {
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "marginMode": "REGULAR_MARGIN",
                    "updatedTime": "1697078946000",
                },
            }
            assert "time" not in body
            return httpx.Response(200, json=body)
        if path == "/v5/position/list":
            assert_signed(request)
            return ok(
                {
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "side": "Buy",
                            "size": "0.01",
                            "positionIdx": 0,
                        }
                    ],
                    "nextPageCursor": "",
                },
                time_ms=later_ms,
            )
        if path == "/v5/order/realtime":
            assert_signed(request)
            return ok(
                {
                    "list": [
                        {
                            "symbol": "ETHUSDT",
                            "side": "Sell",
                            "orderType": "Limit",
                            "qty": "0.2",
                            "price": "3000",
                            "orderStatus": "New",
                            "reduceOnly": False,
                        }
                    ],
                    "nextPageCursor": "",
                },
                time_ms=later_ms,
            )
        raise AssertionError(path)

    client = BybitReadOnlyClient.from_environment(transport=httpx.MockTransport(handler))
    try:
        view = await build_account_view(client, None)
    finally:
        await client.aclose()
    assert view.상태코드 == "ok"
    assert view.오류코드 is None
    assert view.오류코드 != "invalid_json"
    assert "숨겼습니다" not in view.메시지
    assert view.잔고 is not None
    assert view.잔고.총자산평가 == "20.5"
    assert view.잔고.코인[0].지갑잔고 == "12.50"
    assert view.포지션 is not None
    assert len(view.포지션) == 1
    assert view.미체결주문 is not None
    assert len(view.미체결주문) == 1
    assert view.계좌마진모드 == "교차"
    assert view.거래소응답시각 == _utc(wallet_ms).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert view.거래소응답시각 != _utc(public_ms).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert view.거래소응답시각 != _utc(later_ms).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert view.서버시각 == _utc(public_ms).strftime("%Y-%m-%d %H:%M:%S") + " 세계 표준시"
    assert seen == [
        "/v5/market/time",
        "/v5/market/instruments-info",
        "/v5/account/wallet-balance",
        "/v5/account/info",
        "/v5/position/list",
        "/v5/order/realtime",
    ]


async def test_missing_wallet_field_stays_null(bybit_env: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v5/market/time":
            return ok({"timeSecond": "1700000000"})
        if path == "/v5/market/instruments-info":
            return ok(
                {
                    "category": "linear",
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "leverageFilter": {
                                "minLeverage": "1",
                                "maxLeverage": "125.5",
                                "leverageStep": "0.01",
                            },
                        }
                    ],
                }
            )
        if path == "/v5/account/wallet-balance":
            return ok(
                {
                    "list": [
                        {
                            "accountType": "UNIFIED",
                            "totalEquity": "4.25",
                            "totalWalletBalance": "4.25",
                            "totalAvailableBalance": "",
                            "coin": [{"coin": "USDT", "equity": "4.25"}],
                        }
                    ]
                }
            )
        if path == "/v5/account/info":
            return ok({"marginMode": "REGULAR_MARGIN", "updatedTime": "1697078946000"})
        if path in {"/v5/position/list", "/v5/order/realtime"}:
            return ok({"category": "linear", "list": [], "nextPageCursor": ""})
        raise AssertionError(path)

    client = BybitReadOnlyClient.from_environment(transport=httpx.MockTransport(handler))
    try:
        view = await build_account_view(client, None)
    finally:
        await client.aclose()
    assert view.잔고 is not None
    assert view.잔고.코인[0].지갑잔고 is None
    assert view.잔고.코인[0].자산평가 == "4.25"
    assert view.잔고.총사용가능 is None
    assert view.포지션 == []
    assert view.미체결주문 == []


async def test_blocked_stops_and_hides_numbers(bybit_env: None) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(
            403, text='restricted location "walletBalance":"12345.67" leverage 125.5'
        )

    client = BybitReadOnlyClient.from_environment(transport=httpx.MockTransport(handler))
    try:
        view = await build_account_view(client, None)
    finally:
        await client.aclose()
    rendered = view.model_dump_json(by_alias=True)
    assert view.상태코드 == "BLOCKED"
    assert view.잔고 is None
    assert view.포지션 is None
    assert view.미체결주문 is None
    assert view.최대레버리지 is None
    assert view.서버시각 is None
    assert "12345.67" not in rendered
    assert "125.5" not in rendered
    assert "연결이 막혔습니다" in view.메시지
    assert "BLOCKED" not in view.메시지
    assert seen == ["/v5/market/time"]


async def test_region_message_is_blocked_without_copying_it(bybit_env: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=envelope({}, ret=10027, msg="Trading is not allowed in your region"),
        )

    client = BybitReadOnlyClient.from_environment(transport=httpx.MockTransport(handler))
    try:
        view = await build_account_view(client, None)
    finally:
        await client.aclose()
    rendered = view.model_dump_json(by_alias=True)
    assert view.상태코드 == "BLOCKED"
    assert "not allowed in your region" not in rendered
    assert view.잔고 is None


async def test_exchange_error_hides_partial_numbers(bybit_env: None) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/v5/market/time":
            return ok({"timeSecond": "1700000000"})
        if request.url.path == "/v5/market/instruments-info":
            return ok(
                {
                    "category": "linear",
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "leverageFilter": {
                                "minLeverage": "1",
                                "maxLeverage": "125.5",
                                "leverageStep": "0.01",
                            },
                        }
                    ],
                }
            )
        if request.url.path == "/v5/account/wallet-balance":
            return httpx.Response(200, json=envelope({}, ret=10005, msg="Permission denied"))
        raise AssertionError(request.url.path)

    client = BybitReadOnlyClient.from_environment(transport=httpx.MockTransport(handler))
    try:
        view = await build_account_view(client, None)
    finally:
        await client.aclose()
    rendered = view.model_dump_json(by_alias=True)
    assert view.상태코드 == "error"
    assert view.오류코드 == "ret_10005"
    assert "권한" in view.메시지
    assert view.잔고 is None
    assert view.최대레버리지 is None
    assert "125.5" not in rendered
    assert "Permission denied" not in rendered
    assert "/v5/position/list" not in seen


async def test_redirect_is_not_followed(bybit_env: None) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

    client = BybitReadOnlyClient.from_environment(transport=httpx.MockTransport(handler))
    try:
        view = await build_account_view(client, None)
    finally:
        await client.aclose()
    rendered = view.model_dump_json(by_alias=True)
    assert view.오류코드 == "redirect"
    assert view.잔고 is None
    assert "evil.example" not in rendered
    assert len(seen) == 1


async def test_dns_failure_is_not_blocked_and_hides_numbers(bybit_env: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")

    client = BybitReadOnlyClient.from_environment(transport=httpx.MockTransport(handler))
    try:
        view = await build_account_view(client, None)
    finally:
        await client.aclose()
    rendered = view.model_dump_json()
    assert view.오류코드 == "dns"
    assert view.상태코드 == "error"
    assert view.잔고 is None
    assert "name resolution" not in rendered
    assert SECRET not in rendered


async def test_proxy_403_without_a_response_is_not_called_a_country_block(bybit_env: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ProxyError("403 Forbidden")

    client = BybitReadOnlyClient.from_environment(transport=httpx.MockTransport(handler))
    try:
        view = await build_account_view(client, None)
    finally:
        await client.aclose()
    rendered = view.model_dump_json()
    assert view.상태코드 == "error"
    assert view.오류코드 == "proxy"
    assert view.잔고 is None
    assert "Forbidden" not in rendered
    assert "단정하지 않습니다" in view.메시지


async def test_unexpected_error_does_not_echo_the_secret(bybit_env: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError(f"boom {SECRET}")

    client = BybitReadOnlyClient.from_environment(transport=httpx.MockTransport(handler))
    try:
        view = await build_account_view(client, None)
    finally:
        await client.aclose()
    rendered = view.model_dump_json(by_alias=True)
    assert view.오류코드 == "unexpected"
    assert SECRET not in rendered
    assert view.잔고 is None


async def test_demo_is_not_mainnet_or_paper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BYBIT_ENV_FILE", "off")
    monkeypatch.setenv("BYBIT_API_KEY", KEY)
    monkeypatch.setenv("BYBIT_API_SECRET", SECRET)
    monkeypatch.setenv("BYBIT_TESTNET", "false")
    monkeypatch.setenv("BYBIT_DEMO", "true")
    seen: list[httpx.Request] = []
    client = BybitReadOnlyClient.from_environment(
        transport=success_handler(seen, "api-demo.bybit.com")
    )
    try:
        view = await build_account_view(client, None)
    finally:
        await client.aclose()
    assert view.환경코드 == "demo"
    assert view.환경 == "연습 계좌"
    assert view.페이퍼 is False
    assert any("연습 계좌입니다" in item and "실계좌가 아닙니다" in item for item in view.구분)
    assert seen[0].url.host == "api-demo.bybit.com"


async def test_file_secret_does_not_replace_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / "sample.env"
    env_file.write_text(f"BYBIT_API_SECRET={FILE_SECRET}\n", encoding="utf-8")
    monkeypatch.setenv("BYBIT_ENV_FILE", str(env_file))
    monkeypatch.setenv("BYBIT_API_KEY", KEY)
    monkeypatch.setenv("BYBIT_API_SECRET", ENV_SECRET)
    monkeypatch.setenv("BYBIT_TESTNET", "false")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v5/account/wallet-balance":
            assert_signed(request, ENV_SECRET, KEY)
            assert FILE_SECRET not in str(request.url)
            for value in request.headers.values():
                assert FILE_SECRET not in value
        if request.url.path == "/v5/market/time":
            return ok({"timeSecond": "1700000000"})
        return ok({"list": []})

    client = BybitReadOnlyClient.from_environment(transport=httpx.MockTransport(handler))
    try:
        _result, server_ms = await client.read("/v5/market/time", [], signed=False)
        client.anchor_server_time(server_ms)
        await client.read("/v5/account/wallet-balance", [("accountType", "UNIFIED")], signed=True)
    finally:
        await client.aclose()
    assert seen[1].url.path == "/v5/account/wallet-balance"


@asynccontextmanager
async def api_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: httpx.MockTransport | None,
    base_url: str = "http://127.0.0.1",
) -> AsyncIterator[httpx.AsyncClient]:
    if handler is not None:

        async def wire(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
            return await handler.handle_async_request(request)

        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", wire)
    app = create_app()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url) as client,
    ):
        yield client


async def test_api_blocked_host_list_still_allows_authorized_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BYBIT_API_KEY", KEY)
    monkeypatch.setenv("BYBIT_API_SECRET", SECRET)
    monkeypatch.setenv("BYBIT_TESTNET", "false")
    monkeypatch.setenv("MARKET_DATA_BLOCKED_HOSTS", "fapi.binance.com,api.bybit.com")
    seen: list[httpx.Request] = []
    async with api_client(monkeypatch, success_handler(seen)) as client:
        response = await client.get("/api/v1/account/bybit/view")
    assert response.status_code == 200
    body = response.json()
    rendered = response.text
    assert SECRET not in rendered
    assert KEY not in rendered
    assert body["상태코드"] == "ok"
    assert body["환경코드"] == "mainnet"
    assert body["페이퍼"] is False
    assert body["실거래켜짐"] is False
    assert body["주문보냄"] is False
    assert body["최대레버리지"]["최대"] == "125.5"
    assert body["포지션"][0]["출처"] == "external"
    assert body["포지션"][0]["관리함"] is False
    assert "program" not in rendered
    assert seen[0].url.host == "api.bybit.com"


async def test_api_missing_credentials_makes_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []
    monkeypatch.setenv("MARKET_DATA_BLOCKED_HOSTS", "")
    async with api_client(monkeypatch, success_handler(seen)) as client:
        response = await client.get("/api/v1/account/bybit/view")
    body = response.json()
    assert body["상태코드"] == "missing_credentials"
    assert body["잔고"] is None
    assert body["포지션"] is None
    assert "비밀키" in body["메시지"]
    assert SECRET not in response.text
    assert seen == []


async def test_api_rejects_non_local_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BYBIT_API_KEY", KEY)
    monkeypatch.setenv("BYBIT_API_SECRET", SECRET)
    monkeypatch.setenv("BYBIT_TESTNET", "false")
    seen: list[httpx.Request] = []
    async with api_client(monkeypatch, success_handler(seen), "http://local.test") as client:
        response = await client.get("/api/v1/account/bybit/view")
    assert response.status_code == 403
    assert response.json()["상태코드"] == "local_only"
    assert response.json()["잔고"] is None
    assert seen == []


async def test_order_endpoints_refuse_even_when_flag_is_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BYBIT_API_KEY", KEY)
    monkeypatch.setenv("BYBIT_API_SECRET", SECRET)
    monkeypatch.setenv("BYBIT_TESTNET", "false")
    seen: list[httpx.Request] = []
    async with api_client(monkeypatch, success_handler(seen)) as client:
        refused = await client.post("/api/v1/account/bybit/orders", json={"symbol": "BTCUSDT"})
        assert refused.status_code == 403
        assert refused.json()["실거래켜짐"] is False
        assert refused.json()["주문보냄"] is False
        assert refused.json()["이유"] == "live_trading_disabled"
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
        still = await client.post("/api/v1/account/bybit/leverage", json={"leverage": "10"})
        orders = await client.post("/api/v1/account/bybit/orders", json={"side": "Buy"})
    assert still.status_code == 403
    assert still.json()["이유"] == "read_only_client"
    assert still.json()["주문보냄"] is False
    assert orders.json()["실거래켜짐"] is True
    assert orders.json()["주문보냄"] is False
    assert seen == []
    assert SECRET not in refused.text
    assert SECRET not in still.text


async def test_both_environment_flags_do_not_call_bybit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BYBIT_API_KEY", KEY)
    monkeypatch.setenv("BYBIT_API_SECRET", SECRET)
    monkeypatch.setenv("BYBIT_TESTNET", "true")
    monkeypatch.setenv("BYBIT_DEMO", "true")
    seen: list[httpx.Request] = []
    async with api_client(monkeypatch, success_handler(seen)) as client:
        response = await client.get("/api/v1/account/bybit/view")
    body = response.json()
    assert body["상태코드"] == "config"
    assert body["잔고"] is None
    assert body["최대레버리지"] is None
    assert seen == []


def _paths(handler_seen: list[httpx.Request], path: str) -> int:
    return sum(1 for request in handler_seen if request.url.path == path)


async def test_trade_mode_zero_is_not_cross_and_unknown_margin_stays_unknown(
    bybit_env: None,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v5/market/time":
            return ok({"timeSecond": "1700000000"})
        if path == "/v5/market/instruments-info":
            return ok(
                {
                    "category": "linear",
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "leverageFilter": {
                                "minLeverage": "1",
                                "maxLeverage": "100",
                                "leverageStep": "1",
                            },
                        }
                    ],
                }
            )
        if path == "/v5/account/wallet-balance":
            return ok({"list": [{"accountType": "UNIFIED", "totalEquity": "1", "coin": []}]})
        if path == "/v5/account/info":
            return ok({"marginMode": "SOMETHING_ELSE", "updatedTime": "1697078946000"})
        if path in {"/v5/position/list", "/v5/order/realtime"}:
            return ok(
                {
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "side": "Buy",
                            "size": "0.01",
                            "tradeMode": 0,
                            "positionIdx": 0,
                        }
                    ],
                    "nextPageCursor": "",
                }
                if path.endswith("list")
                else {"list": [], "nextPageCursor": ""}
            )
        raise AssertionError(path)

    client = BybitReadOnlyClient.from_environment(transport=httpx.MockTransport(handler))
    try:
        view = await build_account_view(client, None)
    finally:
        await client.aclose()
    assert view.상태코드 == "ok"
    assert view.계좌마진모드 == "확인 불가"
    assert view.포지션 is not None
    assert view.포지션[0].마진모드 == "확인 불가"
    assert "교차" not in view.포지션[0].마진모드


async def test_broken_coin_list_is_not_ok_or_zero(bybit_env: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v5/market/time":
            return ok({"timeSecond": "1700000000"})
        if path == "/v5/market/instruments-info":
            return ok(
                {
                    "category": "linear",
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "leverageFilter": {
                                "minLeverage": "1",
                                "maxLeverage": "100",
                                "leverageStep": "1",
                            },
                        }
                    ],
                }
            )
        if path == "/v5/account/wallet-balance":
            return ok({"list": [{"accountType": "UNIFIED", "totalEquity": "0", "coin": "USDT"}]})
        raise AssertionError(path)

    client = BybitReadOnlyClient.from_environment(transport=httpx.MockTransport(handler))
    try:
        view = await build_account_view(client, None)
    finally:
        await client.aclose()
    rendered = view.model_dump_json()
    assert view.상태코드 == "error"
    assert view.잔고 is None
    assert view.포지션 is None
    assert '"0"' not in rendered


async def test_ret_10010_is_registered_address_not_clock(bybit_env: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v5/market/time":
            return ok({"timeSecond": "1700000000"})
        return httpx.Response(200, json=envelope({}, ret=10010, msg="Unmatched IP secret-looking"))

    client = BybitReadOnlyClient.from_environment(transport=httpx.MockTransport(handler))
    try:
        view = await build_account_view(client, None)
    finally:
        await client.aclose()
    rendered = view.model_dump_json()
    assert view.오류코드 == "ret_10010"
    assert "주소" in view.메시지
    assert "시각" not in view.메시지
    assert "Unmatched" not in rendered
    assert "secret" not in rendered


async def test_http_403_without_region_text_is_not_a_country_block(bybit_env: None) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(403, json={"retCode": 403, "retMsg": "no reason given"})

    client = BybitReadOnlyClient.from_environment(transport=httpx.MockTransport(handler))
    try:
        view = await build_account_view(client, None)
    finally:
        await client.aclose()
    rendered = view.model_dump_json()
    assert view.상태코드 == "error"
    assert view.오류코드 == "http_403"
    assert "확인하지 못했습니다" in view.메시지
    assert "no reason given" not in rendered
    assert seen == ["/v5/market/time"]


async def test_429_retries_are_finite(bybit_env: None) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(429)

    client = BybitReadOnlyClient.from_environment(transport=httpx.MockTransport(handler))
    client.retry_waits = (0.0, 0.0)
    try:
        view = await build_account_view(client, None)
    finally:
        await client.aclose()
    assert view.오류코드 == "ret_10006"
    assert seen == ["/v5/market/time", "/v5/market/time", "/v5/market/time"]


async def test_503_then_success_stays_inside_the_deadline(bybit_env: None) -> None:
    calls = {"time": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v5/market/time":
            calls["time"] += 1
            if calls["time"] < 3:
                return httpx.Response(503)
            return ok({"timeSecond": "1700000000"})
        if request.url.path == "/v5/market/instruments-info":
            return ok(
                {
                    "category": "linear",
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "leverageFilter": {
                                "minLeverage": "1",
                                "maxLeverage": "2",
                                "leverageStep": "1",
                            },
                        }
                    ],
                }
            )
        if request.url.path == "/v5/account/wallet-balance":
            return ok({"list": [{"accountType": "UNIFIED", "totalEquity": "1", "coin": []}]})
        if request.url.path == "/v5/account/info":
            return ok({"marginMode": "ISOLATED_MARGIN", "updatedTime": "1697078946000"})
        if request.url.path in {"/v5/position/list", "/v5/order/realtime"}:
            return ok({"list": [], "nextPageCursor": ""})
        raise AssertionError(request.url.path)

    client = BybitReadOnlyClient.from_environment(transport=httpx.MockTransport(handler))
    client.retry_waits = (0.0, 0.0)
    try:
        view = await build_account_view(client, None)
    finally:
        await client.aclose()
    assert calls["time"] == 3
    assert view.상태코드 == "ok"
    assert view.계좌마진모드 == "격리"


async def test_deadline_stops_before_a_request(bybit_env: None) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return ok({"timeSecond": "1700000000"})

    client = BybitReadOnlyClient.from_environment(transport=httpx.MockTransport(handler))
    try:
        view = await build_account_view(client, None, deadline_at=time.monotonic() - 1)
    finally:
        await client.aclose()
    assert seen == []
    assert view.오류코드 == "timeout"
    assert view.잔고 is None


def test_order_decision_rejects_stale_sequential_and_failed_reads() -> None:
    assert (
        order_decision_allowed(status="ok", age_seconds=0, sequential=True, marked_usable=True)
        is False
    )
    assert (
        order_decision_allowed(status="ok", age_seconds=45, sequential=False, marked_usable=True)
        is False
    )
    assert (
        order_decision_allowed(status="error", age_seconds=0, sequential=False, marked_usable=True)
        is False
    )
    assert (
        order_decision_allowed(status="ok", age_seconds=0, sequential=False, marked_usable=False)
        is False
    )
    assert (
        order_decision_allowed(status="ok", age_seconds=44, sequential=False, marked_usable=True)
        is True
    )


async def test_overlapping_account_reads_share_one_load() -> None:
    gate = AccountReadGate(8)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def load():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return blank_view("missing_credentials", "mainnet", live_trading=False)

    first = asyncio.create_task(gate.get(load))
    await started.wait()
    second = asyncio.create_task(gate.get(load))
    await asyncio.sleep(0.02)
    assert calls == 1
    release.set()
    left = await first
    right = await second
    assert calls == 1
    assert right.캐시사용 is True
    assert right.동시요청공유 is True
    assert left.메시지 == right.메시지


async def test_two_tabs_reuse_one_exchange_read_and_show_the_age(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BYBIT_API_KEY", KEY)
    monkeypatch.setenv("BYBIT_API_SECRET", SECRET)
    monkeypatch.setenv("BYBIT_TESTNET", "false")
    monkeypatch.setenv("BYBIT_ENV_FILE", "off")
    seen: list[httpx.Request] = []
    async with api_client(monkeypatch, success_handler(seen)) as client:
        first = await client.get("/api/v1/account/bybit/view")
        second = await client.get("/api/v1/account/bybit/view")
    assert first.status_code == 200
    assert second.json()["캐시사용"] is True
    assert second.json()["데이터나이초"] >= 0
    assert _paths(seen, "/v5/account/wallet-balance") == 1
    assert SECRET not in second.text
    assert second.json()["주문판단에사용"] is False
