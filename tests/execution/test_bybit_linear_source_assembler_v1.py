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
from risk_v2_support import policy_v2, proposal_v2
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
        self.mark_price = "50"
        self.last_trade_price = "50"
        self.missing_funding = False
        self.missing_funding_bound = False
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
        host, path = request.url.host, request.url.path
        if host == "api-demo.bybit.com":
            demo_paths = {
                "/v5/market/time", "/v5/user/query-api", "/v5/account/info",
                "/v5/account/wallet-balance", "/v5/account/fee-rate",
                "/v5/account/user-setting-config", "/v5/position/list",
                "/v5/order/realtime",
            }
            if path not in demo_paths or kwargs != {"stream": True}:
                raise AssertionError("unexpected DEMO fixture request")
        elif host == "api.bybit.com":
            readonly_stream_paths = {
                "/v5/market/time", "/v5/market/instruments-info",
                "/v5/market/risk-limit", "/v5/market/tickers",
            }
            adapter_get_paths = {"/v5/market/kline", "/v5/market/tickers"}
            readonly_stream = path in readonly_stream_paths and kwargs == {"stream": True}
            adapter_get = (path in adapter_get_paths
                           and kwargs == {"auth": None, "follow_redirects": False})
            if not (readonly_stream or adapter_get):
                raise AssertionError("unexpected public fixture request")
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
            instrument = row("linear", params["symbol"])
            instrument["lowerFundingRate"] = "-0.003"
            instrument["upperFundingRate"] = "0.003"
            if self.missing_funding_bound:
                instrument.pop("upperFundingRate")
            result = {"category": "linear", "list": [instrument]}
        elif path == "/v5/market/risk-limit":
            result = {"category": "linear", "nextPageCursor": "", "list": [{"id": 1, "symbol": params["symbol"],
                "riskLimitValue": "1000000", "initialMargin": 0.1, "maintenanceMargin": 0.005,
                "maxLeverage": "10", "mmDeduction": "0", "isLowestRisk": 1}]}
        else:
            assert path == "/v5/market/tickers"
            stamp -= self.mark_age
            ticker = {"symbol": params["symbol"], "markPrice": self.mark_price,
                "lastPrice": self.last_trade_price, "fundingRate": "0.001",
                "nextFundingTime": str(int(stamp.timestamp() * 1000) + 8 * 60 * 60 * 1000)}
            if self.missing_funding:
                ticker.pop("fundingRate")
            result = {"category": "linear", "list": [ticker]}
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


async def test_cost_allowance_issuer_binds_original_funding_fee_catalog_and_explicit_caps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    fixture = Fixture(monkeypatch, tmp_path)
    try:
        binding = await fixture.auth()
        read = await fixture.capture(binding)
        now = datetime.now(UTC)
        mark_source = read.selected_mark.reference.source
        last_trade_quote = fixture.assembler.issue_current_last_trade_quote_v2(read)
        funding = fixture.marks.project_funding_v1(mark_source, symbol="BTCUSDT")
        policy = replace(policy_v2(), scope=SCOPE, max_stop_gap_quote_per_base=Decimal("0.25"))
        template = proposal_v2()
        proposal = replace(
            template,
            scope=SCOPE,
            side="LONG",
            stop_price=Decimal("40"),
            entry=replace(
                template.entry,
                side="BUY",
                limit_price=Decimal("50"),
                origin_quote_id=last_trade_quote.reference.observation_id,
                valid_until=now + timedelta(seconds=2),
            ),
        )
        cost = fixture.assembler.issue_cost_allowance_v2(
            read,
            policy=policy,
            proposal=proposal,
            holding_until=funding.next_funding_at,
        )
        with pytest.raises(TypeError):
            fixture.assembler.issue_cost_allowance_v2(
                read, policy=policy, proposal=proposal,
                holding_until=funding.next_funding_at,
                slippage_upper_quote_per_base=Decimal("0.00000001"),
            )
        assert cost.fee_reference.source_environment == "DEMO"
        assert cost.fee_reference.observation_id == read.venue.fee.account_taker_rate.reference.source.observation_id
        assert cost.funding_reference.source_environment == "PUBLIC_MAINNET"
        assert cost.funding_reference.observation_id == mark_source.observation_id
        assert cost.instrument_revision == read.catalog.observations[0].specification_revision
        assert cost.holding_until == funding.next_funding_at
        assert cost.fee_quote_per_base == Decimal("0.0495")
        assert cost.funding_quote_per_base == Decimal("3000")
        assert cost.stop_gap_quote_per_base == Decimal("0.25")
        assert fixture.assembler.require_cost_allowance_v2(read, cost, policy=policy) is cost
        with pytest.raises(RiskV2Rejected, match="RISK_V2_SOURCE_MISMATCH"):
            fixture.assembler.require_cost_allowance_v2(read, replace(cost), policy=policy)
    finally:
        await fixture.close()


@pytest.mark.parametrize(
    ("side", "limit", "stop", "expected_fee"),
    [("LONG", "100", "90", "0.1045")],
)
async def test_cost_issuer_accepts_distinct_source_ltp_favorable_limits_and_conservative_fees(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, side: str, limit: str, stop: str,
    expected_fee: str,
) -> None:
    fixture = Fixture(monkeypatch, tmp_path)
    fixture.mark_price = "140"
    fixture.last_trade_price = "95"
    try:
        binding = await fixture.auth()
        read = await fixture.capture(binding)
        now = fixture.clock()
        mark_source = read.selected_mark.reference.source
        last_trade_quote = fixture.assembler.issue_current_last_trade_quote_v2(read)
        funding = fixture.marks.project_funding_v1(mark_source, symbol="BTCUSDT")
        policy = replace(policy_v2(), scope=SCOPE, max_stop_gap_quote_per_base=Decimal("0.25"))
        template = proposal_v2()
        proposal = replace(template, scope=SCOPE, side=side, stop_price=Decimal(stop),
            entry=replace(template.entry, side="BUY" if side == "LONG" else "SELL",
                limit_price=Decimal(limit),
                origin_quote_id=last_trade_quote.reference.observation_id, valid_until=now + timedelta(seconds=2)))
        cost = fixture.assembler.issue_cost_allowance_v2(read, policy=policy, proposal=proposal,
            holding_until=funding.next_funding_at)
        assert read.selected_mark.value == Decimal("140")
        assert fixture.assembler.issue_current_last_trade_quote_v2(read).last_price == Decimal("95")
        assert proposal.entry.limit_price == Decimal(limit)
        assert cost.fee_quote_per_base == Decimal(expected_fee)
        assert fixture.assembler.require_cost_allowance_v2(read, cost, policy=policy) is cost
        assert fixture.journal.list_commands() == ()
    finally:
        await fixture.close()


@pytest.mark.parametrize(
    ("side", "limit", "stop"),
    [("LONG", "150", "145"), ("SHORT", "130", "140")],
)
async def test_cost_issuer_rejects_adverse_limit_vs_current_mark(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, side: str, limit: str, stop: str,
) -> None:
    fixture = Fixture(monkeypatch, tmp_path)
    fixture.mark_price = "140"
    fixture.last_trade_price = "95"
    try:
        binding = await fixture.auth()
        read = await fixture.capture(binding)
        last_trade_quote = fixture.assembler.issue_current_last_trade_quote_v2(read)
        funding = fixture.marks.project_funding_v1(read.selected_mark.reference.source, symbol="BTCUSDT")
        template = proposal_v2()
        proposal = replace(template, scope=SCOPE, side=side, stop_price=Decimal(stop),
            entry=replace(template.entry, side="BUY" if side == "LONG" else "SELL",
                limit_price=Decimal(limit),
                origin_quote_id=last_trade_quote.reference.observation_id,
                valid_until=fixture.clock() + timedelta(seconds=2)))
        policy = replace(policy_v2(), scope=SCOPE, max_stop_gap_quote_per_base=Decimal("0.25"))
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_LIMIT_NOT_MARK_FAVORABLE$"):
            fixture.assembler.issue_cost_allowance_v2(read, policy=policy, proposal=proposal,
                holding_until=funding.next_funding_at)
        assert fixture.journal.list_commands() == ()
    finally:
        await fixture.close()


async def test_short_cost_issuer_rejects_without_enforced_fill_fee_upper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    fixture = Fixture(monkeypatch, tmp_path)
    fixture.mark_price = "140"
    fixture.last_trade_price = "95"
    try:
        binding = await fixture.auth()
        read = await fixture.capture(binding)
        quote = fixture.assembler.issue_current_last_trade_quote_v2(read)
        template = proposal_v2()
        proposal = replace(template, scope=SCOPE, side="SHORT", stop_price=Decimal("160"),
            entry=replace(template.entry, side="SELL", limit_price=Decimal("150"),
                origin_quote_id=quote.reference.observation_id,
                valid_until=fixture.clock() + timedelta(seconds=2)))
        policy = replace(policy_v2(), scope=SCOPE, max_stop_gap_quote_per_base=Decimal("0.25"))
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_SELL_EXECUTION_UPPER_UNAVAILABLE$"):
            fixture.assembler.issue_cost_allowance_v2(read, policy=policy, proposal=proposal,
                holding_until=fixture.clock() + timedelta(hours=8))
        assert fixture.journal.list_commands() == ()
    finally:
        await fixture.close()


async def test_cost_issuer_rechecks_narrow_policy_expiry_after_final_owner_callback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    fixture = Fixture(monkeypatch, tmp_path)
    try:
        binding = await fixture.auth()
        read = await fixture.capture(binding)
        now = fixture.clock()
        mark_source = read.selected_mark.reference.source
        last_trade_quote = fixture.assembler.issue_current_last_trade_quote_v2(read)
        funding = fixture.marks.project_funding_v1(mark_source, symbol="BTCUSDT")
        policy = replace(policy_v2(), scope=SCOPE, max_quote_age_seconds=Decimal("2.5"),
            max_evidence_age_seconds=Decimal(30))
        template = proposal_v2()
        proposal = replace(
            template,
            scope=SCOPE,
            side="LONG",
            stop_price=Decimal("40"),
            entry=replace(
                template.entry,
                side="BUY",
                limit_price=Decimal("50"),
                origin_quote_id=last_trade_quote.reference.observation_id,
                valid_until=now + timedelta(seconds=4),
            ),
        )
        policy_deadline = mark_source.request_started_at + timedelta(seconds=2.5)
        assert policy_deadline < proposal.entry.valid_until < read.venue.valid_until
        current_time = [now]
        fixture.assembler.clock = lambda: current_time[0]
        original_guard = fixture.assembler.owner_guard
        calls = 0

        def advance_final_guard() -> None:
            nonlocal calls
            original_guard()
            calls += 1
            if calls == 2:
                current_time[0] += timedelta(seconds=3.5)

        fixture.assembler.owner_guard = advance_final_guard
        with pytest.raises(RiskV2Rejected, match="RISK_V2_STALE_EVIDENCE"):
            fixture.assembler.issue_cost_allowance_v2(
                read,
                policy=policy,
                proposal=proposal,
                holding_until=funding.next_funding_at,
            )
        assert calls == 2 and current_time[0] < read.venue.valid_until
        assert fixture.assembler._current is not None and fixture.assembler._current.issued_cost is None
        assert fixture.journal.list_commands() == ()
    finally:
        await fixture.close()


async def test_cost_admission_rechecks_narrow_proposal_expiry_after_final_owner_callback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    fixture = Fixture(monkeypatch, tmp_path)
    try:
        binding = await fixture.auth()
        read = await fixture.capture(binding)
        now = fixture.clock()
        mark_source = read.selected_mark.reference.source
        last_trade_quote = fixture.assembler.issue_current_last_trade_quote_v2(read)
        funding = fixture.marks.project_funding_v1(mark_source, symbol="BTCUSDT")
        policy = replace(policy_v2(), scope=SCOPE, max_quote_age_seconds=Decimal(10),
            max_evidence_age_seconds=Decimal(30))
        template = proposal_v2()
        proposal = replace(
            template,
            scope=SCOPE,
            side="LONG",
            stop_price=Decimal("40"),
            entry=replace(
                template.entry,
                side="BUY",
                limit_price=Decimal("50"),
                origin_quote_id=last_trade_quote.reference.observation_id,
                valid_until=now + timedelta(seconds=2),
            ),
        )
        cost = fixture.assembler.issue_cost_allowance_v2(
            read,
            policy=policy,
            proposal=proposal,
            holding_until=funding.next_funding_at,
        )
        assert cost.valid_until == proposal.entry.valid_until < read.venue.valid_until
        current_time = [fixture.clock()]
        fixture.assembler.clock = lambda: current_time[0]
        original_guard = fixture.assembler.owner_guard
        calls = 0

        def advance_final_guard() -> None:
            nonlocal calls
            original_guard()
            calls += 1
            if calls == 2:
                current_time[0] += timedelta(seconds=3)

        fixture.assembler.owner_guard = advance_final_guard
        with pytest.raises(RiskV2Rejected, match="RISK_V2_STALE_EVIDENCE"):
            fixture.assembler.require_cost_allowance_v2(read, cost, policy=policy)
        assert calls == 2 and current_time[0] < read.venue.valid_until
        assert fixture.journal.list_commands() == ()
    finally:
        await fixture.close()


@pytest.mark.parametrize("failure", [
    "missing-funding", "missing-funding-bound", "expired-funding", "mixed-instrument-source",
    "missing-stop-gap-cap", "long-horizon", "wrong-policy-scope",
])
async def test_cost_allowance_issuer_refuses_missing_or_unbounded_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str,
) -> None:
    fixture = Fixture(monkeypatch, tmp_path)
    try:
        binding = await fixture.auth()
        if failure == "missing-funding":
            fixture.missing_funding = True
        elif failure == "missing-funding-bound":
            fixture.missing_funding_bound = True
        elif failure == "expired-funding":
            fixture.mark_age = timedelta(seconds=2)
        read = await fixture.capture(binding)
        now = datetime.now(UTC)
        mark_source = read.selected_mark.reference.source
        last_trade_quote = fixture.assembler.issue_current_last_trade_quote_v2(read)
        funding = fixture.marks.project_funding_v1(mark_source, symbol="BTCUSDT") if failure != "missing-funding" else None
        policy = replace(policy_v2(), scope=SCOPE)
        if failure == "missing-stop-gap-cap":
            policy = replace(policy, max_stop_gap_quote_per_base=None)
        if failure == "wrong-policy-scope":
            policy = policy_v2()
        elif failure == "expired-funding":
            policy = replace(policy, max_quote_age_seconds=Decimal(1))
            assert funding is not None and funding.source.envelope_time_ms is not None
            funding_server_time = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
                milliseconds=funding.source.envelope_time_ms
            )
            assert now - funding_server_time > timedelta(seconds=1)
        template = proposal_v2()
        origin_quote_id = last_trade_quote.reference.observation_id
        if failure == "mixed-instrument-source":
            origin_quote_id = next(
                mark.reference.observation_id for mark in read.marks if mark.instrument != SCOPE.instrument
            )
        proposal = replace(
            template,
            scope=SCOPE,
            side="LONG",
            stop_price=Decimal("40"),
            entry=replace(
                template.entry,
                side="BUY",
                limit_price=Decimal("50"),
                origin_quote_id=origin_quote_id,
                valid_until=now + timedelta(seconds=2),
            ),
        )
        holding_until = (funding.next_funding_at if funding is not None else now + timedelta(hours=8))
        if failure == "long-horizon":
            holding_until += timedelta(hours=8)
        if failure == "missing-stop-gap-cap":
            with pytest.raises(RiskV2Rejected, match="RISK_V2_SLIPPAGE_BOUND_UNAVAILABLE"):
                fixture.assembler.issue_cost_allowance_v2(
                    read,
                    policy=policy,
                    proposal=proposal,
                    holding_until=holding_until,
                )
        elif failure == "expired-funding":
            with pytest.raises(RiskV2Rejected, match="RISK_V2_STALE_EVIDENCE"):
                fixture.assembler.issue_cost_allowance_v2(
                    read,
                    policy=policy,
                    proposal=proposal,
                    holding_until=holding_until,
                )
        else:
            with pytest.raises((BybitReadError, RiskV2Rejected)):
                fixture.assembler.issue_cost_allowance_v2(
                    read,
                    policy=policy,
                    proposal=proposal,
                    holding_until=holding_until,
                )
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
