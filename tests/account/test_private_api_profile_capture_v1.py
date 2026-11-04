"""Fixed DEMO API-profile capture; dummy credentials stay in private memory."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from multi_exchange_trader.account.bybit_readonly import (
    BybitReadError,
    BybitReadOnlyClient,
    _private_account_endpoint_v1,
    _private_account_query_v1,
    _private_account_spec_v1,
    sign_v5,
)
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget

KEY, SECRET = "PROFILE-OFFLINE-KEY", "PROFILE-OFFLINE-SECRET"


def original_client() -> BybitReadOnlyClient:
    client = BybitReadOnlyClient(
        api_key=KEY, api_secret=SECRET, environment="demo", base_url="https://api-demo.bybit.com"
    )
    client.anchor_server_time(int(datetime.now(UTC).timestamp() * 1000))
    return client


async def test_profile_capture_retains_exact_signed_empty_query_raw_and_redacted_repr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = original_client()
    raw = json.dumps(
        {
            "retCode": 0,
            "time": int(datetime.now(UTC).timestamp() * 1000),
            "result": {"apiKey": KEY, "secret": "", "vipLevel": "VIP-1"},
        }
    ).encode()
    requests: list[httpx.Request] = []

    async def send(
        _client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any
    ) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET" and request.url.host == "api-demo.bybit.com"
        assert request.url.path == "/v5/user/query-api" and request.url.query == b""
        assert request.headers["X-BAPI-SIGN"] == sign_v5(
            SECRET,
            KEY,
            request.headers["X-BAPI-TIMESTAMP"],
            request.headers["X-BAPI-RECV-WINDOW"],
            "",
        )
        return httpx.Response(200, content=raw, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        method = getattr(client, "_capture_account_api_profile_v1", None)
        assert callable(method), "Fixed original PROFILE capture is absent"
        budget = MonitoringReadBudget()
        with client._monitoring_operation(budget), budget.reuse_or_open_receipt() as group:
            captured = await method(budget=budget, group=group, owner_guard=lambda: None)
        assert captured.raw_body == raw
        assert captured.raw_body_sha256 == hashlib.sha256(raw).hexdigest()
        assert captured.endpoint == "PROFILE" and captured.query == ()
        assert captured.request is requests[0] and captured.accepted_attempt == 1
        assert budget.physical_attempts == len(requests) == 1
        assert KEY not in repr(captured) and SECRET not in repr(captured)
        assert _private_account_spec_v1("PROFILE") == ("/v5/user/query-api", ())
        assert _private_account_endpoint_v1("/v5/user/query-api", ()) == "PROFILE"
        with pytest.raises(BybitReadError):
            _private_account_query_v1("PROFILE", (("symbol", "BTCUSDT"),))
        with pytest.raises(BybitReadError):
            _private_account_spec_v1("PROFILE", "BTCUSDT")
    finally:
        await client.aclose()


async def test_profile_capture_refuses_mainnet_without_sending_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[httpx.Request] = []

    async def send(
        _client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any
    ) -> httpx.Response:
        sent.append(request)
        raise AssertionError("PROFILE must stay on the DEMO private capture route")

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    client = BybitReadOnlyClient(
        api_key=KEY, api_secret=SECRET, environment="mainnet", base_url="https://api.bybit.com"
    )
    try:
        method = getattr(client, "_capture_account_api_profile_v1", None)
        assert callable(method)
        budget = MonitoringReadBudget()
        with (
            client._monitoring_operation(budget),
            budget.reuse_or_open_receipt() as group,
            pytest.raises(BybitReadError),
        ):
            await method(budget=budget, group=group, owner_guard=lambda: None)
        assert not sent
    finally:
        await client.aclose()
