"""Genuine saved-format resolver and Session with intercepted HTTPX requests only."""

import asyncio
import importlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from multi_exchange_trader.account.bybit_readonly import sign_v5
from multi_exchange_trader.account_profiles import AccountProfileStore, _decode
from multi_exchange_trader.credential_resolution import CredentialResolver
from multi_exchange_trader.credentials import CredentialSnapshot, CredentialStore, _validate_values

KEY, SECRET = "fixture-demo-config-key", "fixture-demo-config-secret"
UID = "12345"


def saved_profile(*, pending: bool = False) -> bytes:
    stamp = "2026-11-03T00:00:00Z"
    evidence = "a24bd44b-422d-409c-8563-91458ed1eb18"
    identifier = "83a05b72-f883-451a-9e58-639bd9909912"
    return json.dumps({"schema": 1, "revision": 1, "selected_profile_id": identifier,
        "profiles": [{"profile_id": identifier, "exchange": "BYBIT", "environment": "DEMO",
        "uid": UID, "uid_evidence": {"basis": "OPERATOR_SELECTED", "evidence_id": evidence,
            "confirmed_at": stamp}, "relationship": None if pending else {
            "parent_uid": "0", "is_master": False,
            "basis": "PROVIDER_OBSERVED_OPERATOR_ADOPTED", "source": "BYBIT_DEMO_QUERY_API",
            "evidence_id": evidence, "confirmed_at": stamp, "provider_observed_at": stamp,
            "policy": "uid-anchored-adoption-v1"}, "last_observation": None if pending else {
            "source": "BYBIT_DEMO_QUERY_API", "parent_uid": "0", "is_master": False,
            "observed_at": stamp, "server_observed_at": stamp}}]}).encode()


class Wire:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.clients: list[httpx.AsyncClient] = []
        self.mode = "REGULAR_MARGIN"
        self.reject = False
        self.uid = int(UID)
        self.pending = False
        self.read_only = 0
        self.block: str | None = None
        self.post_failure: str | None = None
        self.readback_failure = False
        self.reset_price_on_margin = False
        self.writes = 0

    async def send(self, client: httpx.AsyncClient, request: httpx.Request,
                   **_kwargs: Any) -> httpx.Response:
        self.requests.append(request)
        self.clients.append(client)
        assert str(request.url).startswith("https://api-demo.bybit.com/")
        path, query = request.url.path, dict(request.url.params)
        if path != "/v5/market/time":
            payload = request.content.decode() if request.method == "POST" else request.url.query.decode()
            assert request.headers["x-bapi-api-key"] == KEY
            assert request.headers["x-bapi-sign"] == sign_v5(
                SECRET, KEY, request.headers["x-bapi-timestamp"],
                request.headers["x-bapi-recv-window"], payload)
        code = 0
        result: dict[str, Any]
        if path == "/v5/market/time":
            result = {}
        elif path == "/v5/user/query-api":
            result = {"apiKey": KEY, "secret": "", "userID": self.uid, "isMaster": False,
                "parentUid": "0", "readOnly": self.read_only,
                "permissions": {"ContractTrade": ["Order", "Position"], "Wallet": []}}
        elif request.method == "POST":
            self.writes += 1
            assert request.url.query == b""
            if self.post_failure == "reject":
                code = 10001
            elif self.post_failure != "timeout-unchanged":
                if path == "/v5/account/set-limit-px-action":
                    assert json.loads(request.content) == {"category": "linear", "modifyEnable": False}
                    self.reject = True
                else:
                    assert path == "/v5/account/set-margin-mode"
                    assert json.loads(request.content) == {"setMarginMode": "ISOLATED_MARGIN"}
                    self.mode = "ISOLATED_MARGIN"
                    if self.reset_price_on_margin:
                        self.reject = False
            if self.post_failure in {"timeout-applied", "timeout-unchanged"}:
                raise httpx.ReadTimeout(KEY + SECRET, request=request)
            result = {}
        elif path == "/v5/account/info":
            result = {"unifiedMarginStatus": 3, "marginMode": self.mode}
        elif path == "/v5/account/user-setting-config":
            if self.writes and self.readback_failure:
                raise httpx.ReadError(KEY + SECRET, request=request)
            result = {"lpaPerp": self.reject}
        elif path == "/v5/account/wallet-balance":
            assert query == {"accountType": "UNIFIED"}
            coin = {"coin": "USDT", "borrowAmount": "0", "accruedInterest": "0",
                    "spotBorrow": "0", "locked": "0", "walletBalance": "100", "equity": "100"}
            if self.block == "debt":
                coin["spotBorrow"] = "0.01"
            elif self.block == "missing-debt":
                del coin["borrowAmount"]
            result = {"list": [{"accountType": "UNIFIED", "coin": [coin]}]}
        else:
            assert path in {"/v5/position/list", "/v5/order/realtime"}
            assert "symbol" not in query and "baseCoin" not in query and "coin" not in query
            category = query["category"]
            rows: list[dict[str, str]] = []
            cursor = ""
            if path == "/v5/position/list" and self.block == category:
                rows = [{"symbol": "OTHER", "size": "0.1"}]
            if path == "/v5/order/realtime":
                assert query["openOnly"] == "0" and "orderFilter" not in query
                if self.block == "spot-order" and category == "spot":
                    rows = [{"orderStatus": "Untriggered"}]
                elif self.block == "second-page" and category == "inverse":
                    if query.get("cursor") == "next":
                        rows = [{"orderStatus": "New"}]
                    else:
                        cursor = "next"
                elif self.block == "unknown-category" and category == "inverse":
                    code = 10001
            result = {"category": category, "list": rows, "nextPageCursor": cursor}
        return httpx.Response(200, request=request, json={"retCode": code,
            "retMsg": KEY + SECRET if code else "OK", "time": int(datetime.now(UTC).timestamp() * 1000),
            "result": result})


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Any, Wire]:
    module = importlib.import_module("multi_exchange_trader.account.bybit_demo_configuration_v1")
    store = CredentialStore(tmp_path / "unused-credentials")
    snapshot = CredentialSnapshot(1, _validate_values({
        "BYBIT_DEMO_API_KEY": KEY, "BYBIT_DEMO_API_SECRET": SECRET}))
    monkeypatch.setattr(store, "load", lambda: snapshot)
    resolver = CredentialResolver(store=store, environ={"BYBIT_ENV_FILE": "off", "BYBIT_DEMO": "false"})
    profiles = AccountProfileStore(tmp_path / "unused-profile")
    wire = Wire()
    monkeypatch.setattr(profiles, "load", lambda: _decode(saved_profile(pending=wire.pending)))

    async def send(client: httpx.AsyncClient, request: httpx.Request,
                   **kwargs: Any) -> httpx.Response:
        return await wire.send(client, request, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    return module.BybitDemoConfigurationV1(resolver=resolver, profile_store=profiles), wire


async def test_inspect_is_lazy_get_only_and_explains_account_wide_scope(setup: tuple[Any, Wire]) -> None:
    tool, wire = setup
    assert wire.requests == []
    report = await tool.inspect()
    assert report.state == "PLANNED" and report.blockers == ()
    assert report.margin_mode == "REGULAR_MARGIN" and report.lpa_perp is False
    assert report.uid == UID and not report.financial_qualified
    assert report.effects == ("ALL_FUTURES_PRICE_BEHAVIOUR", "UNIFIED_ACCOUNT_MARGIN_MODE")
    assert report.demo_price_endpoint_support == "UNVERIFIED"
    assert all(request.method == "GET" for request in wire.requests)
    assert {dict(r.url.params).get("category") for r in wire.requests} >= {"linear", "inverse", "option", "spot"}
    assert all(client.is_closed for client in wire.clients)
    assert KEY not in str(asdict(report)) + repr(tool)
    assert SECRET not in str(asdict(report)) + repr(tool)


async def test_apply_has_only_fixed_setting_posts_and_independent_readback(setup: tuple[Any, Wire]) -> None:
    tool, wire = setup
    report = await tool.apply(expected_uid=UID)
    assert report.state == "VERIFIED" and report.blockers == ()
    assert report.margin_mode == "ISOLATED_MARGIN" and report.lpa_perp is True
    assert [step.outcome for step in report.steps] == ["ACKNOWLEDGED_AND_VERIFIED"] * 2
    posts = [request for request in wire.requests if request.method == "POST"]
    assert [r.url.path for r in posts] == ["/v5/account/set-limit-px-action", "/v5/account/set-margin-mode"]
    assert wire.requests[-1].method == "GET" and not report.financial_qualified
    count = len(wire.requests)
    again = await tool.apply(expected_uid=UID)
    assert again.blockers == ("APPLY_ALREADY_CONSUMED",) and len(wire.requests) == count


@pytest.mark.parametrize("block", ["linear", "inverse", "option", "spot-order", "debt",
    "missing-debt", "unknown-category", "second-page"])
async def test_unproven_or_nonflat_whole_account_never_posts(setup: tuple[Any, Wire], block: str) -> None:
    tool, wire = setup
    wire.block = block
    report = await tool.apply(expected_uid=UID)
    assert report.state == "BLOCKED" and report.blockers and wire.writes == 0
    expected = {"linear": "OPEN_POSITIONS", "inverse": "OPEN_POSITIONS",
        "option": "OPEN_POSITIONS", "spot-order": "OPEN_ORDERS", "debt": "ACCOUNT_DEBT_OR_LOCKED_ASSETS",
        "missing-debt": "WALLET_COVERAGE_UNKNOWN", "unknown-category": "ACCOUNT_COVERAGE_UNKNOWN",
        "second-page": "OPEN_ORDERS"}[block]
    assert expected in report.blockers
    assert any(r.url.path == "/v5/account/wallet-balance" for r in wire.requests)
    assert all(request.method == "GET" for request in wire.requests)
    if block == "second-page":
        assert any(r.url.params.get("cursor") == "next" for r in wire.requests)


@pytest.mark.parametrize("failure,verified", [("timeout-applied", True), ("timeout-unchanged", False), ("reject", False)])
async def test_unknown_or_rejected_post_is_not_retransmitted(setup: tuple[Any, Wire], failure: str, verified: bool) -> None:
    tool, wire = setup
    wire.post_failure = failure
    report = await tool.apply(expected_uid=UID)
    assert len(report.steps) == 1 and wire.writes == 1
    assert report.steps[0].readback_target is verified
    assert report.state == "INCOMPLETE" and report.blockers
    assert report.margin_mode == "REGULAR_MARGIN"
    assert all(r.method == "GET" for r in wire.requests[wire.requests.index(next(r for r in wire.requests if r.method == "POST")) + 1:])
    assert KEY not in str(asdict(report)) and SECRET not in str(asdict(report))


async def test_ack_without_independent_readback_stops_and_reports_unknown(setup: tuple[Any, Wire]) -> None:
    tool, wire = setup
    wire.readback_failure = True
    report = await tool.apply(expected_uid=UID)
    assert report.state == "INCOMPLETE" and wire.writes == 1
    assert report.steps[0].readback_target is None
    assert report.steps[0].outcome == "READBACK_UNKNOWN"


async def test_both_targets_must_survive_final_readback(setup: tuple[Any, Wire]) -> None:
    tool, wire = setup
    wire.reset_price_on_margin = True
    report = await tool.apply(expected_uid=UID)
    assert wire.writes == 2 and len(report.steps) == 2
    assert report.state == "INCOMPLETE" and report.blockers == ("TARGETS_NOT_RETAINED",)
    assert report.margin_mode == "ISOLATED_MARGIN" and report.lpa_perp is False


@pytest.mark.parametrize("case", ["pending", "foreign-uid", "wrong-confirmation", "read-only"])
async def test_original_profile_identity_and_explicit_apply_boundary(setup: tuple[Any, Wire], case: str) -> None:
    tool, wire = setup
    wire.pending = case == "pending"
    wire.uid = 67890 if case == "foreign-uid" else int(UID)
    wire.read_only = int(case == "read-only")
    report = await tool.apply(expected_uid="99999" if case == "wrong-confirmation" else UID)
    assert report.state == "BLOCKED" and report.blockers and wire.writes == 0
    if case in {"pending", "wrong-confirmation"}:
        assert wire.requests == []


async def test_cancellation_closes_original_session_without_post(setup: tuple[Any, Wire], monkeypatch: pytest.MonkeyPatch) -> None:
    tool, wire = setup
    original = wire.send

    async def cancel(client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        if request.url.path == "/v5/account/info":
            wire.clients.append(client)
            raise asyncio.CancelledError
        return await original(client, request, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", cancel)
    with pytest.raises(asyncio.CancelledError):
        await tool.inspect()
    assert wire.writes == 0 and all(client.is_closed for client in wire.clients)
