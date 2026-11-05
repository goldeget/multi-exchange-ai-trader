"""Coherent original HTTPX owners with an actual NEW temporary journal only."""
from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from fixtures.bybit_instrument_v1 import row
from test_bybit_entry_market_projections_v1 import FLAT
from test_bybit_linear_model_inputs_v1 import fee_document
from test_bybit_position_order_projections_v1 import ORDER, POSITION
from test_private_account_sources_bundle_v1 import COIN, KEY, SECRET

from multi_exchange_trader.account.bybit_public_mark_source_v1 import BybitPublicMarkSessionV1
from multi_exchange_trader.account.bybit_readonly import BybitReadError, sign_v5
from multi_exchange_trader.execution.account_binding import (
    AccountBindingError,
    BybitDemoAccountSession,
)
from multi_exchange_trader.execution.bybit_linear_model_inputs_v1 import BybitNonVipFeeBasisV1
from multi_exchange_trader.execution.bybit_linear_source_assembler_v1 import (
    BybitLinearSourceAssemblerV1,
)
from multi_exchange_trader.execution.journal import (
    CommandJournal,
    ExecutionScope,
    _OwnPendingViewV2,
)
from multi_exchange_trader.execution.modes import StrategyReference
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget
from multi_exchange_trader.execution.product_scope import ProductScope
from multi_exchange_trader.execution.protection import ProtectionScope
from multi_exchange_trader.execution.risk_v2_contracts import RiskScopeV2
from multi_exchange_trader.execution.risk_v2_math import RiskV2Rejected
from multi_exchange_trader.market_data.instrument_producer import BybitInstrumentCatalogProducer
from multi_exchange_trader.trading_product import InstrumentKey

SCOPE = RiskScopeV2(ProductScope(ProtectionScope("12345", "DEMO"), "LINEAR"),
    InstrumentKey("BYBIT", "LINEAR", "BTCUSDT"), StrategyReference("strategy", "1"))


class Stream(httpx.AsyncByteStream):
    def __init__(self, raw: bytes) -> None:
        self.raw = raw

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.raw


class Fixture:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
        self.now = datetime.now(UTC)
        self.requests: list[httpx.Request] = []
        self.delta: bool | None = False
        self.mark_age = timedelta(0)
        self.break_owner = False
        self.session = BybitDemoAccountSession(api_key=KEY, api_secret=SECRET, clock=self.clock)
        self.catalog = BybitInstrumentCatalogProducer(source_environment="PUBLIC_MAINNET")
        self.marks = BybitPublicMarkSessionV1(source_environment="PUBLIC_MAINNET", symbols=("BTCUSDT", "ETHUSDT"))
        self.journal = CommandJournal(path / "journal.sqlite", ExecutionScope("12345", "DEMO"))
        self.journal.bind_owner_epoch_v2("OWNER", expected_previous_owner_epoch=None, expected_journal_revision=0)
        self.basis = BybitNonVipFeeBasisV1(Decimal("0.00055"), replace(fee_document(),
            captured_at=self.now, displayed_rate_spelling="0.00055", displayed_unit="FRACTION",
            applicability_revision="fixture-fee-v1"))
        self.assembler = BybitLinearSourceAssemblerV1(session=self.session, public_marks=self.marks,
            catalog_producer=self.catalog, journal=self.journal, clock=self.clock, owner_guard=self.guard)
        original_send = httpx.AsyncClient.send
        async def intercepted(client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
            if not isinstance(client._transport, httpx.AsyncHTTPTransport):
                return await original_send(client, request, **kwargs)
            return await self.send(client, request, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "send", intercepted)

    def guard(self) -> None:
        if self.break_owner:
            raise ValueError("fixture owner retired")

    @staticmethod
    def clock() -> datetime:
        return datetime.now(UTC)

    async def send(self, client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        assert type(client) is httpx.AsyncClient and request.method == "GET"
        if request.url.host == "api-demo.bybit.com":
            assert kwargs == {"stream": True}
        elif request.url.host == "api.bybit.com":
            assert kwargs == {"auth": None, "follow_redirects": False}
        else:
            raise AssertionError("unexpected fixture request host")
        self.requests.append(request)
        path, params = request.url.path, request.url.params
        if request.url.host == "api-demo.bybit.com" and path != "/v5/market/time":
            assert request.headers["X-BAPI-SIGN"] == sign_v5(SECRET, KEY,
                request.headers["X-BAPI-TIMESTAMP"], request.headers["X-BAPI-RECV-WINDOW"], request.url.query.decode())
        elif request.url.host == "api.bybit.com":
            assert not any(name.lower().startswith("x-bapi") for name in request.headers)
        stamp = self.clock()
        result: object
        if path == "/v5/market/time":
            result = {}
        elif path == "/v5/user/query-api":
            result = {"apiKey": KEY, "secret": "", "userID": 12345, "parentUid": "56789",
                "isMaster": False, "readOnly": 0, "permissions": {"ContractTrade": ["Order", "Position"],
                    "Wallet": [], "Derivatives": ["DerivativesTrade"], "Spot": []}}
        elif path == "/v5/account/info":
            result = {"unifiedMarginStatus": 5, "marginMode": "ISOLATED_MARGIN", "updatedTime": "1"}
        elif path == "/v5/account/wallet-balance":
            result = {"list": [{"accountType": "UNIFIED", "coin": [{**COIN, "walletBalance": "1000",
                "equity": "1000", "totalPositionIM": "10", "totalOrderIM": "2"}]}]}
        elif path == "/v5/account/fee-rate":
            result = {"list": [{"symbol": params["symbol"], "takerFeeRate": "0.00055", "makerFeeRate": "0.0002"}]}
        elif path == "/v5/account/user-setting-config":
            result = {"lpaPerp": True, "fmsef": False}
            if self.delta is not None:
                result["deltaEnable"] = self.delta
        elif path == "/v5/position/list":
            result = {"category": "linear", "nextPageCursor": "", "list": [
                {**FLAT, "symbol": params["symbol"], "leverage": "10", "autoAddMargin": 0}
                if "symbol" in params else {**POSITION, "symbol": "ETHUSDT", "avgPrice": "50", "markPrice": "50",
                    "positionIM": "10", "positionIMByMp": "10", "positionMM": "1", "positionMMByMp": "1"}]}
        elif path == "/v5/order/realtime":
            result = {"category": "linear", "nextPageCursor": "", "list": [{**ORDER, "symbol": "ETHUSDT",
                "price": "50", "qty": "1", "leavesQty": "1"}]}
        elif path == "/v5/market/instruments-info":
            result = {"category": "linear", "list": [row("linear", params["symbol"])]}
        elif path == "/v5/market/risk-limit":
            result = {"category": "linear", "nextPageCursor": "", "list": [{"id": 1, "symbol": params["symbol"],
                "riskLimitValue": "1000000", "initialMargin": 0.1, "maintenanceMargin": 0.005,
                "maxLeverage": "10", "mmDeduction": "0", "isLowestRisk": 1}]}
        else:
            assert path == "/v5/market/tickers"
            stamp -= self.mark_age
            result = {"category": "linear", "list": [{"symbol": params["symbol"], "markPrice": "50"}]}
        raw = json.dumps({"retCode": 0, "result": result, "time": int(stamp.timestamp() * 1000)}).encode()
        return httpx.Response(200, stream=Stream(raw), request=request)

    async def auth(self) -> Any:
        return await self.session.authenticate(expected_uid="12345", expected_parent_uid="56789", expected_is_master=False)

    async def capture(self, binding: Any, **kwargs: Any) -> Any:
        return await self.assembler.capture_v1(binding, scope=SCOPE, owner_generation="OWNER",
            nonvip_fee_basis=self.basis, nonvip_applicability_revision="fixture-fee-v1",
            budget=MonitoringReadBudget(), max_age=timedelta(seconds=30), max_quote_age=timedelta(seconds=5), **kwargs)

    async def close(self) -> None:
        self.journal.close()
        await self.session.aclose()
        await self.catalog.aclose()
        await self.marks.aclose()


async def test_one_coherent_original_cycle_reaches_existing_venue_and_retains_every_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    fixture = Fixture(monkeypatch, tmp_path)
    try:
        binding = await fixture.auth()
        read = await fixture.capture(binding)
        assert fixture.assembler.require_current_v1(read) is read
        assert [m.instrument.symbol for m in read.marks] == ["BTCUSDT", "ETHUSDT"]
        assert [p.instrument_pin.key.symbol for p in read.venue.held_positions] == ["ETHUSDT"]
        assert read.venue.selected_profile.symbol == "BTCUSDT"
        assert read.venue.selected_profile.selected_position_quantity.reference.source is read.venue.flat_leverage.source
        assert read.venue.selected_profile.auto_add_margin.wire_spelling == "0"
        assert len(read.venue.source_records) == 11
        assert read.venue.fee.closing_order_reserve_nonvip_rate is fixture.basis
        assert read.full_reservations is read.evaluation_reservations
        assert fixture.requests[-1].url.path == fixture.requests[-2].url.path == "/v5/market/tickers"
        assert len(fixture.requests) == 15
        assert all(source.source_environment != "FIXTURE" for source in read.venue.source_records)
        newer = await fixture.capture(binding)
        with pytest.raises(RiskV2Rejected):
            fixture.assembler.require_current_v1(read)
        assert fixture.assembler.require_current_v1(newer) is newer
    finally:
        await fixture.close()


@pytest.mark.parametrize("failure", ["unknown-delta", "stale-mark", "forged-own-view", "retired-private", "retired-public", "owner"])
async def test_critical_missing_or_retired_original_inputs_are_not_a_model_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str,
) -> None:
    fixture = Fixture(monkeypatch, tmp_path)
    try:
        binding = await fixture.auth()
        if failure == "unknown-delta":
            fixture.delta = None
        elif failure == "stale-mark":
            fixture.mark_age = timedelta(seconds=6)
        if failure == "forged-own-view":
            with pytest.raises(RiskV2Rejected):
                await fixture.capture(binding, own_pending_view=_OwnPendingViewV2(),
                    own_pending_command_key="not-issued", own_pending_identity_sha256="a" * 64)
            assert len(fixture.requests) == 2
        elif failure in ("unknown-delta", "stale-mark"):
            with pytest.raises(RiskV2Rejected):
                await fixture.capture(binding)
        else:
            read = await fixture.capture(binding)
            if failure == "retired-private":
                await fixture.session._capture_linear_wallet_v1(binding, scope=SCOPE.product_scope.account,
                    budget=MonitoringReadBudget(), owner_guard=lambda: None)
            elif failure == "retired-public":
                await fixture.marks.capture_mark_v1("BTCUSDT", budget=MonitoringReadBudget(), owner_guard=lambda: None)
            else:
                fixture.break_owner = True
            expected_error = AccountBindingError if failure == "retired-private" else BybitReadError
            with pytest.raises(expected_error):
                fixture.assembler.require_current_v1(read)
    finally:
        await fixture.close()


async def test_shared_current_wallet_composes_genuine_derived_observer_without_refreshing_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from test_derived_daily_cash_v1 import CashFixture

    from multi_exchange_trader.execution.derived_daily_cash_v1 import DerivedDailyCashObserverV1

    fixture = CashFixture(monkeypatch, tmp_path)
    try:
        binding = await fixture.auth()
        observer = DerivedDailyCashObserverV1(assembler=fixture.assembler)
        daily = await observer.capture_v1(binding, scope=SCOPE.product_scope.account,
            symbol="BTCUSDT", budget=MonitoringReadBudget())
        original = fixture.session._linear_wallet_current
        assert original is not None and original.capture is not None
        capture, receipt, owner_guard = original.capture, original.capture.receipt, original.owner_guard
        first, current = daily.wallet_sources
        assert first is not current
        assert fixture.wallet_reads == 2
        read = await fixture.capture(binding, current_wallet_source=current)
        assert read.venue.wallet.source is current
        assert fixture.session._linear_wallet_current is original
        assert original.capture is capture and capture.receipt is receipt
        assert original.owner_guard is owner_guard
        assert current.request_started_at == capture.request_started_at
        assert read.venue.valid_until <= current.request_started_at + timedelta(seconds=30)
        assert fixture.wallet_reads == 2
        assert observer.require_current_v1(daily) is daily
        assert fixture.assembler.require_current_v1(read) is read
        assert not daily.direct_midnight_observation and not daily.financial_authority
    finally:
        await fixture.close()


@pytest.mark.parametrize("failure", ["copy", "other-session", "retired", "reauth", "historical-a", "stale", "scope"])
async def test_shared_current_wallet_rejects_copies_foreign_retired_and_expired_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str,
) -> None:
    from test_derived_daily_cash_v1 import CashFixture

    from multi_exchange_trader.execution.derived_daily_cash_v1 import DerivedDailyCashObserverV1

    fixture = CashFixture(monkeypatch, tmp_path)
    foreign: BybitDemoAccountSession | None = None
    try:
        binding = await fixture.auth()
        observer = DerivedDailyCashObserverV1(assembler=fixture.assembler)
        daily = await observer.capture_v1(binding, scope=SCOPE.product_scope.account,
            symbol="BTCUSDT", budget=MonitoringReadBudget())
        source = daily.wallet_sources[1]
        capture_scope = SCOPE
        if failure == "copy":
            source = copy.copy(source)
        elif failure == "other-session":
            foreign = BybitDemoAccountSession(api_key=KEY, api_secret=SECRET, clock=fixture.clock)
            foreign_binding = await foreign.authenticate(expected_uid="12345",
                expected_parent_uid="56789", expected_is_master=False)
            source = await foreign._capture_linear_wallet_v1(foreign_binding,
                scope=SCOPE.product_scope.account, budget=MonitoringReadBudget(), owner_guard=fixture.guard)
        elif failure == "retired":
            await fixture.session._capture_linear_wallet_v1(binding, scope=SCOPE.product_scope.account,
                budget=MonitoringReadBudget(), owner_guard=fixture.guard)
        elif failure == "reauth":
            binding = await fixture.auth()
        elif failure == "historical-a":
            source = daily.wallet_sources[0]
        elif failure == "scope":
            capture_scope = replace(SCOPE, product_scope=replace(SCOPE.product_scope,
                account=ProtectionScope("54321", "DEMO")))
        else:
            await asyncio.sleep(0.02)
        with pytest.raises((AccountBindingError, RiskV2Rejected)):
            await fixture.assembler.capture_v1(binding, scope=capture_scope, owner_generation="OWNER",
                nonvip_fee_basis=fixture.basis, nonvip_applicability_revision="fixture-fee-v1",
                budget=MonitoringReadBudget(), max_age=timedelta(milliseconds=5) if failure == "stale"
                    else timedelta(seconds=30), max_quote_age=timedelta(milliseconds=1),
                current_wallet_source=source)
        assert fixture.assembler._current is None
    finally:
        if foreign is not None:
            await foreign.aclose()
        await fixture.close()


async def test_shared_current_wallet_caught_reentry_cannot_publish_a_new_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    fixture = Fixture(monkeypatch, tmp_path)
    armed = [False]
    try:
        binding = await fixture.auth()
        previous = await fixture.capture(binding)

        def forwarding_owner_guard() -> None:
            fixture.guard()
            if armed[0]:
                with pytest.raises(RiskV2Rejected):
                    fixture.assembler.require_current_v1(previous)

        source = await fixture.session._capture_linear_wallet_v1(binding,
            scope=SCOPE.product_scope.account, budget=MonitoringReadBudget(),
            owner_guard=forwarding_owner_guard)
        armed[0] = True
        with pytest.raises(RiskV2Rejected):
            await fixture.capture(binding, current_wallet_source=source)
        assert fixture.assembler._current is None
    finally:
        await fixture.close()


def demo_uta1_wire(monkeypatch: pytest.MonkeyPatch, *, lpa: bool = True) -> None:
    """Forward every original fixture request, changing only observed mode fields."""
    original = httpx.AsyncClient.send

    async def send(client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        response = await original(client, request, **kwargs)
        if request.url.path in ("/v5/account/info", "/v5/account/user-setting-config"):
            body = json.loads(await response.aread())
            if request.url.path == "/v5/account/info":
                body["result"].update(unifiedMarginStatus=3, marginMode="ISOLATED_MARGIN")
            else:
                body["result"]["lpaPerp"] = lpa
            return httpx.Response(200, stream=Stream(json.dumps(body).encode()), request=request)
        return response

    monkeypatch.setattr(httpx.AsyncClient, "send", send)


@pytest.mark.parametrize("reuse_wallet", [False, True])
async def test_demo_uta1_explicit_original_cycle_and_shared_wallet_keep_actual_three(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reuse_wallet: bool,
) -> None:
    from test_derived_daily_cash_v1 import CashFixture

    from multi_exchange_trader.execution import bybit_demo_uta1_profile_v1 as p
    from multi_exchange_trader.execution.derived_daily_cash_v1 import DerivedDailyCashObserverV1

    fixture = CashFixture(monkeypatch, tmp_path) if reuse_wallet else Fixture(monkeypatch, tmp_path)
    demo_uta1_wire(monkeypatch)
    try:
        binding = await fixture.auth()
        capture = getattr(fixture.assembler, "capture_demo_uta1_v1", None)
        assert callable(capture), "Explicit original-source UTA1 capture is absent"
        current = None
        if reuse_wallet:
            observer = DerivedDailyCashObserverV1(assembler=fixture.assembler)
            daily = await observer.capture_v1(binding, scope=SCOPE.product_scope.account,
                symbol="BTCUSDT", budget=MonitoringReadBudget())
            current = daily.wallet_sources[1]
        read = await capture(binding, scope=SCOPE, owner_generation="OWNER",
            nonvip_fee_basis=fixture.basis, nonvip_applicability_revision="fixture-fee-v1",
            budget=MonitoringReadBudget(), max_age=timedelta(seconds=30),
            max_quote_age=timedelta(seconds=5), current_wallet_source=current)
        assert type(read.venue) is p.BybitDemoUta1VenueStateV1
        assert read.venue.account_mode.unified_margin_status == 3
        info = fixture.session._linear_info_current
        assert info is not None and info.capture is not None
        assert read.venue.account_mode.source is info.source
        assert json.loads(info.capture.raw_body)["result"]["unifiedMarginStatus"] == 3
        assert fixture.assembler._witness(read, ())[0] == p.bybit_demo_uta1_venue_sha256_v1(read.venue)
        assert fixture.assembler.require_current_v1(read) is read
        if reuse_wallet:
            assert read.venue.wallet.source is current
            assert sum(r.url.path == "/v5/account/wallet-balance" for r in fixture.requests) == 2
            assert observer.require_current_v1(daily) is daily
        await fixture.session._capture_linear_account_info_v1(binding,
            scope=SCOPE.product_scope.account, budget=MonitoringReadBudget(), owner_guard=fixture.guard)
        with pytest.raises((AccountBindingError, RiskV2Rejected)):
            fixture.assembler.require_current_v1(read)
        with pytest.raises(AccountBindingError):
            await fixture.capture(binding)  # Existing capture_v1 still requires status 5/6.
    finally:
        await fixture.close()


async def test_demo_uta1_original_wire_price_correction_false_remains_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    fixture = Fixture(monkeypatch, tmp_path)
    demo_uta1_wire(monkeypatch, lpa=False)
    try:
        binding = await fixture.auth()
        capture = getattr(fixture.assembler, "capture_demo_uta1_v1", None)
        assert callable(capture), "Explicit original-source UTA1 capture is absent"
        with pytest.raises(RiskV2Rejected, match="RISK_V2_UNSUPPORTED_MODE"):
            await capture(binding, scope=SCOPE, owner_generation="OWNER",
                nonvip_fee_basis=fixture.basis, nonvip_applicability_revision="fixture-fee-v1",
                budget=MonitoringReadBudget(), max_age=timedelta(seconds=30),
                max_quote_age=timedelta(seconds=5))
        assert fixture.assembler._current is None
    finally:
        await fixture.close()
