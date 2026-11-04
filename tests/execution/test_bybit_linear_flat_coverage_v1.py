"""Original complete USDT-linear sources and an actually empty fixture journal."""

import importlib
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_bybit_linear_entry_producer_v1 import SourceFixture
from test_bybit_linear_source_assembler_v1 import SCOPE, Stream
from test_bybit_position_order_projections_v1 import POSITION

from multi_exchange_trader.execution.account_binding import AccountBindingError
from multi_exchange_trader.execution.journal import CommandKind
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget
from multi_exchange_trader.execution.risk_v2_math import RiskV2Rejected


def api() -> Any:
    return importlib.import_module("multi_exchange_trader.execution.bybit_linear_flat_coverage_v1")


def owner_for(fixture: SourceFixture) -> Any:
    return api().BybitLinearFlatCoverageOwnerV1(session=fixture.session, journal=fixture.journal,
        clock=fixture.clock, owner_guard=fixture.guard)


async def capture(owner: Any, fixture: SourceFixture, binding: Any,
                  *, budget: MonitoringReadBudget | None = None, **kwargs: Any) -> Any:
    return await owner.capture_v1(binding, scope=SCOPE, owner_generation="OWNER",
        budget=budget if budget is not None else MonitoringReadBudget(),
        max_age=timedelta(seconds=30), **kwargs)


async def test_whole_scope_empty_raw_and_real_journal_issue_only_linear_zeroes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    fixture = SourceFixture(monkeypatch, tmp_path)
    fixture.mode, fixture.margin, fixture.lpa, fixture.fee_error = 3, "REGULAR_MARGIN", False, True
    try:
        binding = await fixture.auth()
        owner, budget = owner_for(fixture), MonitoringReadBudget()
        result = await capture(owner, fixture, binding, budget=budget)
        assert owner.require_current_v1(result) is result
        assert (result.total_risk, result.total_margin, result.position_count) == (Decimal(0), Decimal(0), 0)
        assert result.coverage_revision == "BYBIT_DEMO_USDT_LINEAR_FIRST_FLAT_V1"
        assert result.scope == SCOPE.product_scope.account
        assert not hasattr(result, "daily_loss") and not hasattr(result, "ledger_qualified")
        assert not fixture.journal.list_commands()
        assert [r.url.path for r in fixture.requests[2:]] == ["/v5/position/list", "/v5/order/realtime"]
        assert result.positions_sources[0].query == (("category", "linear"), ("settleCoin", "USDT"))
        assert result.orders_sources[0].query == (("category", "linear"), ("settleCoin", "USDT"), ("openOnly", "0"))
        positions, orders = fixture.session._linear_positions_current, fixture.session._linear_orders_current
        assert positions is not None and orders is not None
        assert positions.pages[0].capture.budget is orders.pages[0].capture.budget is budget
        assert positions.pages[0].capture.receipt is orders.pages[0].capture.receipt
    finally:
        await fixture.close()


@pytest.mark.parametrize("fault", ["second-position-page", "orders", "journal"])
async def test_any_raw_row_or_any_original_journal_command_prevents_first_flat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str,
) -> None:
    fixture = SourceFixture(monkeypatch, tmp_path)
    original = httpx.AsyncClient.send

    async def send(client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        response = await original(client, request, **kwargs)
        body = json.loads(await response.aread())
        if request.url.path == "/v5/position/list" and fault == "second-position-page":
            body["result"].update(list=[POSITION] if "cursor" in request.url.params else [],
                nextPageCursor="" if "cursor" in request.url.params else "NEXT")
        if request.url.path == "/v5/order/realtime" and fault == "orders":
            body["result"]["list"] = [{"orderId": "not-empty"}]
        return httpx.Response(200, stream=Stream(json.dumps(body).encode()), request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        binding = await fixture.auth()
        owner = owner_for(fixture)
        if fault == "journal":
            fixture.journal.register("fixture-local-command", CommandKind.CANCEL, {"fixture": True})
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_HELD_COVERAGE_REQUIRED$"):
            await capture(owner, fixture, binding)
        assert owner._current is None
    finally:
        await fixture.close()


@pytest.mark.parametrize("fault", ["copy", "reauth", "close", "replace-pages"])
async def test_only_original_current_source_and_result_objects_remain_usable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str,
) -> None:
    fixture = SourceFixture(monkeypatch, tmp_path)
    try:
        binding = await fixture.auth()
        owner = owner_for(fixture)
        result = await capture(owner, fixture, binding)
        if fault == "copy":
            result = replace(result)
        elif fault == "reauth":
            await fixture.auth()
        elif fault == "close":
            await fixture.session.aclose()
        else:
            await fixture.session._capture_linear_positions_v1(binding, scope=SCOPE.product_scope.account,
                budget=MonitoringReadBudget(), owner_guard=fixture.guard)
        with pytest.raises((RiskV2Rejected, AccountBindingError)):
            owner.require_current_v1(result)
        assert owner._current is None
    finally:
        await fixture.close()


async def test_retained_original_whole_page_tuples_are_reused_without_http_or_renewal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    fixture = SourceFixture(monkeypatch, tmp_path)
    try:
        binding, budget = await fixture.auth(), MonitoringReadBudget()
        read = await fixture.assembler.capture_v1(binding, scope=SCOPE, owner_generation="OWNER",
            nonvip_fee_basis=fixture.basis, nonvip_applicability_revision="fixture-fee-v1",
            budget=budget, max_age=timedelta(seconds=30), max_quote_age=timedelta(seconds=5))
        original = fixture.assembler._current
        assert original is not None
        sources = dict(original.sources)
        before = len(fixture.requests)
        owner = owner_for(fixture)
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_SOURCE_MISMATCH$"):
            await capture(owner, fixture, binding, budget=MonitoringReadBudget(),
                assembler=fixture.assembler, read=read)
        result = await capture(owner, fixture, binding, budget=budget,
            assembler=fixture.assembler, read=read)
        assert result.positions_sources is sources["POSITIONS"]
        assert result.orders_sources is sources["ORDERS"]
        assert owner.require_current_v1(result) is result
        assert len(fixture.requests) == before
        assert result.valid_until <= read.venue.valid_until
    finally:
        await fixture.close()


async def test_a_new_real_journal_command_retires_previously_issued_zero_exposure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    fixture = SourceFixture(monkeypatch, tmp_path)
    try:
        binding = await fixture.auth()
        owner = owner_for(fixture)
        result = await capture(owner, fixture, binding)
        assert owner.require_current_v1(result) is result
        fixture.journal.register("new-local-command", CommandKind.CANCEL, {"fixture": True})
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_HELD_COVERAGE_REQUIRED$"):
            owner.require_current_v1(result)
        assert owner._current is None and len(fixture.journal.list_commands()) == 1
    finally:
        await fixture.close()
