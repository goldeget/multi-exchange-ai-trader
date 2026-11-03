"""Genuine mixed catalog/raw risk capture on synthetic public bytes only."""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from contextvars import ContextVar
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from multi_exchange_trader.account import bybit_readonly as client_module
from multi_exchange_trader.account.instrument_envelope import InstrumentSourceError
from multi_exchange_trader.execution import bybit_linear_model_inputs_v1 as models
from multi_exchange_trader.execution import monitoring_budget as budget_module
from multi_exchange_trader.execution.bybit_linear_model_inputs_v1 import BybitRiskLimitCaptureV1
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget
from multi_exchange_trader.execution.risk_v2_contracts import pin_instrument_v2
from multi_exchange_trader.execution.risk_v2_math import RiskV2Rejected
from multi_exchange_trader.market_data import instrument_producer as module
from multi_exchange_trader.market_data.instrument_definition import InstrumentObservation
from multi_exchange_trader.trading_product import InstrumentKey

LINEAR = InstrumentKey("BYBIT", "LINEAR", "BTCUSDT")
SPOT = InstrumentKey("BYBIT", "SPOT", "BTCUSDT")
TTL = timedelta(seconds=30)


class Clocks:
    def __init__(self) -> None:
        self.wall = datetime.now(UTC).replace(microsecond=0)
        self.mono = time.monotonic()
        self.callback: Callable[[], None] = lambda: None

    def wall_now(self) -> datetime:
        self.callback()
        return self.wall

    def mono_now(self) -> float:
        self.callback()
        return self.mono

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.mono += seconds


def instrument_row(category: str) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT", "status": "Trading",
        "priceFilter": {"tickSize": "0.10"},
        "lotSizeFilter": {"basePrecision": "0.001", "quotePrecision": "0.01",
                          "minOrderAmt": "5", "maxLimitOrderQty": "100", "maxMarketOrderQty": "50"},
    }
    if category == "linear":
        row |= {"settleCoin": "USDT", "contractType": "LinearPerpetual", "priceScale": "2",
                "isPreListing": False,
                "priceFilter": {"tickSize": "0.10", "minPrice": "0.1", "maxPrice": "1000000"},
                "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "100",
                                  "maxMktOrderQty": "50", "minNotionalValue": "5"},
                "leverageFilter": {"minLeverage": "1", "maxLeverage": "100", "leverageStep": "0.01"}}
    return row


class Stream(httpx.AsyncByteStream):
    def __init__(self, raw: bytes, harness: "Harness") -> None:
        self.raw, self.harness = raw, harness

    async def __aiter__(self) -> AsyncIterator[bytes]:
        middle = len(self.raw) // 2
        yield self.raw[:middle]
        self.harness.hook("chunk")
        yield self.raw[middle:]

    async def aclose(self) -> None:
        self.harness.closes += 1
        self.harness.hook("close")


class Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.clock = Clocks()
        monkeypatch.setattr(client_module, "time", SimpleNamespace(monotonic=self.clock.mono_now))
        class DateTimes:
            def __call__(self, *args: Any, **kwargs: Any) -> datetime:
                return datetime(*args, **kwargs)

            def now(inner_self, _: object) -> datetime:
                return self.clock.wall_now()

        monkeypatch.setattr(client_module, "datetime", DateTimes())
        monkeypatch.setattr(budget_module, "time", SimpleNamespace(monotonic=self.clock.mono_now))
        self.hook: Callable[[str], None] = lambda _: None
        self.requests: list[httpx.Request] = []
        self.raw: list[bytes] = []
        self.sends = self.closes = 0
        self.next_cursor: Callable[[int], str] = lambda page: "next" if page == 1 else ""
        self.status = 200
        self.age_ms = 0
        self.padding = 0
        self.rows_per_page = 1
        self.owner = module.BybitInstrumentCatalogProducer._for_testing(
            source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(self.handler),
            wall_clock=self.clock.wall_now, monotonic_clock=self.clock.mono_now)
        self.budget = MonitoringReadBudget()

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.bybit.com" and request.method == "GET"
        assert not any(name.lower().startswith("x-bapi") for name in request.headers)
        self.requests.append(request)
        stamp = int(self.clock.wall.timestamp() * 1000)
        if request.url.path == "/v5/market/instruments-info":
            category = request.url.params["category"]
            return httpx.Response(200, content=json.dumps({"retCode": 0, "time": stamp,
                "result": {"category": category, "list": [instrument_row(category)]}}, indent=1).encode())
        assert request.url.path == "/v5/market/risk-limit"
        self.sends += 1
        self.hook("send")
        stamp = int(self.clock.wall.timestamp() * 1000)
        page = self.sends
        cursor = self.next_cursor(page)
        row = (f'{{"id":{page},"symbol":"BTCUSDT","riskLimitValue":"{page * 100000}.00",'
               f'"initialMargin":{page}e-2,"maintenanceMargin":{page * 5}e-3,'
               f'"maxLeverage":"{100 // page}","mmDeduction":"{(page - 1) * 500}",'
               f'"isLowestRisk":{1 if page == 1 else 0}}}')
        raw_rows = ",".join([row] * self.rows_per_page)
        raw = (f' {{"retCode":0,"result":{{"category":"linear","list":[{raw_rows}],'
               f'"nextPageCursor":{json.dumps(cursor)}}},"time":{stamp - self.age_ms}}} '
               + " " * self.padding).encode()
        self.raw.append(raw)
        return httpx.Response(self.status, stream=Stream(raw, self))

    async def setup(self) -> None:
        self.handle = await self.owner.refresh(declared_universe=(SPOT, LINEAR), max_age=TTL,
                                               budget=self.budget)
        current = self.owner._current
        assert current is not None
        self.view = self.owner._require_current_for_testing(
            self.handle, expected_revision=current.view.catalog.revision)
        self.member = next(o for o in self.view.catalog.observations if o.key == LINEAR)

    async def capture(self, **changes: Any) -> BybitRiskLimitCaptureV1:
        kwargs: dict[str, Any] = {"catalog_receipt": self.handle,
            "expected_catalog_revision": self.view.catalog.revision, "budget": self.budget,
            "max_age": TTL, "max_pages": 20}
        kwargs.update(changes)
        return await self.owner.capture_linear_risk_limit_v1(self.member, **kwargs)


async def test_genuine_mixed_capture_keeps_bytes_full_pin_same_group_and_taint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(monkeypatch)
    try:
        with h.budget.reuse_or_open_receipt() as token:
            await h.setup()
            receipt = h.budget._receipt.get()
            assert receipt is not None
            with pytest.raises(InstrumentSourceError, match="INJECTED_TRANSPORT"):
                h.owner.require_current(h.handle, expected_revision=h.view.catalog.revision)
            captured = await h.capture()
            assert type(captured) is BybitRiskLimitCaptureV1
            assert tuple(page.decoded_body_bytes for page in captured.raw_pages) == tuple(h.raw)
            assert [p.logical_ordinal for p in captured.raw_pages] == [3, 4]
            assert [p.params for p in captured.raw_pages] == [
                (("category", "linear"), ("symbol", "BTCUSDT")),
                (("category", "linear"), ("symbol", "BTCUSDT"), ("cursor", "next"))]
            assert captured.table.instrument_pin == pin_instrument_v2(
                h.member, catalog_revision=h.view.catalog.revision)
            assert len(fields(captured.table.instrument_pin)) == 11
            assert captured.table.rows[0].initial_margin_rate.reference.wire_spelling == "1e-2"
            assert captured.table.reference.observation_id not in {p.observation_id for p in captured.raw_pages}
            assert h.budget._receipt.get() is receipt and receipt.token is token
            assert receipt.logical == 4 and h.budget.physical_attempts == 4
            assert [r.url.path for r in h.requests].count("/v5/market/instruments-info") == 2
            with pytest.raises(InstrumentSourceError, match="INJECTED_TRANSPORT"):
                h.owner.require_current(h.handle, expected_revision=h.view.catalog.revision)
        assert receipt.active is False and h.budget._receipt.get() is None
    finally:
        await h.owner.aclose()


@pytest.mark.parametrize("kind", ["source", "budget", "catalog", "risk", "programmer", "cancel"])
async def test_guard_origin_exact_object_and_charged_no_send(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    from multi_exchange_trader.execution.monitoring_budget import MonitoringBudgetError
    from multi_exchange_trader.market_data.instrument_definition import CatalogRejected

    faults: dict[str, BaseException] = {
        "source": InstrumentSourceError("INSTRUMENT_SOURCE_NETWORK"),
        "budget": MonitoringBudgetError("MONITORING_GROUP_REQUIRED"),
        "catalog": CatalogRejected("CATALOG_TIME_INVALID"),
        "risk": RiskV2Rejected("RISK_V2_INPUT_INVALID"),
        "programmer": ValueError("synthetic guard defect"),
        "cancel": asyncio.CancelledError("synthetic cancellation"),
    }
    h = Harness(monkeypatch)
    try:
        await h.setup()
        fault = faults[kind]
        def guard() -> None:
            if h.budget.physical_attempts == 3:
                raise fault
        with pytest.raises(type(fault)) as caught:
            await h.capture(owner_guard=guard)
        assert caught.value is fault
        assert h.sends == 0 and h.budget.physical_attempts == 3
        assert h.owner._risk_pending is None and h.owner._current is not None
    finally:
        await h.owner.aclose()


@pytest.mark.parametrize("axis", ["wall", "mono", "server"])
async def test_each_page_expiry_equality_rejects_without_tolerance(
    monkeypatch: pytest.MonkeyPatch, axis: str,
) -> None:
    h = Harness(monkeypatch)
    try:
        await h.setup()
        original = models.parse_bybit_risk_limit_table_v1
        def parse(*args: Any, **kwargs: Any) -> BybitRiskLimitCaptureV1:
            result = original(*args, **kwargs)
            if axis == "wall":
                h.clock.wall += timedelta(seconds=1)
            elif axis == "mono":
                h.clock.mono += 1.0
            return result
        monkeypatch.setattr(module, "parse_bybit_risk_limit_table_v1", parse)
        if axis == "server":
            h.age_ms = 1000
        with pytest.raises(InstrumentSourceError, match="EXPIRED"):
            await h.capture(max_age=timedelta(seconds=1))
        assert h.sends == (1 if axis == "server" else 2)
    finally:
        await h.owner.aclose()


async def test_old_first_page_rejected_when_latest_page_is_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(monkeypatch)
    try:
        await h.setup()
        h.hook = lambda stage: h.clock.advance(0.75) if stage == "send" and h.sends == 2 else None
        original = models.parse_bybit_risk_limit_table_v1
        def parse(*args: Any, **kwargs: Any) -> BybitRiskLimitCaptureV1:
            result = original(*args, **kwargs)
            h.clock.advance(0.25)
            return result
        monkeypatch.setattr(module, "parse_bybit_risk_limit_table_v1", parse)
        with pytest.raises(InstrumentSourceError, match="EXPIRED"):
            await h.capture(max_age=timedelta(seconds=1))
        assert h.sends == 2
    finally:
        await h.owner.aclose()


@pytest.mark.parametrize("cap,want", [("bytes", 17), ("rows", 4), ("group", 32), ("parent", 1)])
async def test_stricter_resource_caps_stop_next_send(
    monkeypatch: pytest.MonkeyPatch, cap: str, want: int,
) -> None:
    h = Harness(monkeypatch)
    try:
        await h.setup()
        h.next_cursor = lambda page: f"page-{page}"
        if cap == "bytes":
            h.padding = 65000
        elif cap == "rows":
            h.rows_per_page = 256
        elif cap == "parent":
            h.budget.physical_attempts = 199
        family = RiskV2Rejected if cap in {"bytes", "rows"} else InstrumentSourceError
        with pytest.raises(family, match="RESOURCE_LIMIT|BUDGET_EXHAUSTED"):
            await h.capture(max_pages=100)
        assert h.sends == want and h.closes == want
    finally:
        await h.owner.aclose()


async def test_overlap_keeps_original_marker_until_cancelled_body_drains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(monkeypatch)
    original_handler = h.handler
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v5/market/risk-limit":
            entered.set()
            await release.wait()
        return original_handler(request)
    # Construct a genuine second owner on the new explicit MockTransport.
    await h.owner.aclose()
    h.owner = module.BybitInstrumentCatalogProducer._for_testing(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(handler),
        wall_clock=h.clock.wall_now, monotonic_clock=h.clock.mono_now)
    try:
        await h.setup()
        task = asyncio.create_task(h.capture())
        await entered.wait()
        marker = h.owner._risk_pending
        with pytest.raises(InstrumentSourceError, match="OVERLAP"):
            await h.capture()
        assert h.owner._risk_pending is marker
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert h.owner._risk_pending is None
    finally:
        release.set()
        await h.owner.aclose()


@pytest.mark.parametrize("field", ["deadline", "guard", "owner_guard"])
async def test_original_receipt_fields_cannot_change_behind_same_token(
    monkeypatch: pytest.MonkeyPatch, field: str,
) -> None:
    h = Harness(monkeypatch)
    try:
        await h.setup()
        def change(stage: str) -> None:
            if stage == "close":
                receipt = h.budget._receipt.get()
                assert receipt is not None
                setattr(receipt, field, receipt.deadline + 1.0 if field == "deadline" else lambda: None)
        h.hook = change
        with pytest.raises(InstrumentSourceError, match="GROUP_REQUIRED"):
            await h.capture()
        assert h.sends == 1
    finally:
        await h.owner.aclose()


@pytest.mark.parametrize("when", ["first_clock", "chunk"])
async def test_original_receipt_context_even_with_same_receipt_token(
    monkeypatch: pytest.MonkeyPatch, when: str,
) -> None:
    h = Harness(monkeypatch)
    try:
        await h.setup()
        original = h.budget._receipt
        done = False
        def swap() -> None:
            nonlocal done
            if not done:
                done = True
                replacement: ContextVar[Any] = ContextVar("e2_same_receipt_new_context", default=None)
                replacement.set(original.get())
                h.budget._receipt = replacement
        if when == "first_clock":
            h.clock.callback = swap
        else:
            h.hook = lambda stage: swap() if stage == "chunk" else None
        with pytest.raises(InstrumentSourceError, match="GROUP_REQUIRED"):
            await h.capture()
        assert h.sends == (0 if when == "first_clock" else 1)
        assert h.budget._receipt is not original
    finally:
        await h.owner.aclose()


@pytest.mark.parametrize("status", [429, 503])
async def test_quota_first_and_retry_close_loss_do_not_send_again(
    monkeypatch: pytest.MonkeyPatch, status: int,
) -> None:
    h = Harness(monkeypatch)
    try:
        await h.setup()
        h.status = status
        def lose(stage: str) -> None:
            if stage == "close":
                h.owner.retire()
                if status == 429:
                    raise ValueError("synthetic ordinary close fault")
        h.hook = lose
        with pytest.raises(InstrumentSourceError) as caught:
            await h.capture()
        assert caught.value.code == ("INSTRUMENT_SOURCE_RATE_LIMITED" if status == 429
                                      else "INSTRUMENT_SOURCE_RETIRED")
        assert h.sends == 1 and h.closes == 1
        if status == 429:
            assert h.budget._terminal_failure == "MONITORING_QUOTA_OR_BAN"
    finally:
        await h.owner.aclose()


async def test_real_equal_revision_refresh_new_handle_invalidates_inflight_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(monkeypatch)
    original_handler = h.handler
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v5/market/risk-limit":
            entered.set()
            await release.wait()
        return original_handler(request)
    await h.owner.aclose()
    h.owner = module.BybitInstrumentCatalogProducer._for_testing(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(handler),
        wall_clock=h.clock.wall_now, monotonic_clock=h.clock.mono_now)
    try:
        await h.setup()
        old_handle, old_view = h.handle, h.view
        task = asyncio.create_task(h.capture())
        await entered.wait()
        new_handle = await h.owner.refresh(declared_universe=(SPOT, LINEAR), max_age=TTL, budget=h.budget)
        current = h.owner._current
        assert current is not None and current.view.catalog.revision == old_view.catalog.revision
        assert new_handle is not old_handle
        release.set()
        with pytest.raises(InstrumentSourceError, match="RETIRED"):
            await task
        assert h.sends == 1
    finally:
        release.set()
        await h.owner.aclose()


async def test_unused_spot_member_change_is_not_selected_only_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(monkeypatch)
    try:
        await h.setup()
        unused = next(o for o in h.view.catalog.observations if o.key == SPOT)
        def mutate(stage: str) -> None:
            if stage == "close":
                object.__setattr__(unused, "raw_row_sha256", "0" * 64)
        h.hook = mutate
        with pytest.raises((InstrumentSourceError, RiskV2Rejected)):
            await h.capture()
        assert h.sends == 1
    finally:
        await h.owner.aclose()


async def test_cursor_cycle_rejected_before_third_send(monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(monkeypatch)
    try:
        await h.setup()
        h.next_cursor = lambda _: "same"
        with pytest.raises(RiskV2Rejected, match="INPUT_INVALID"):
            await h.capture()
        assert h.sends == 2
    finally:
        await h.owner.aclose()


async def test_clock_reentry_cannot_start_second_capture_before_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(monkeypatch)
    original_handler = h.handler
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v5/market/risk-limit":
            await asyncio.sleep(0)  # Controlled wire scheduling point; permit the queued reentry task.
        return original_handler(request)
    await h.owner.aclose()
    h.owner = module.BybitInstrumentCatalogProducer._for_testing(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(handler),
        wall_clock=h.clock.wall_now, monotonic_clock=h.clock.mono_now)
    tasks: list[asyncio.Task[BybitRiskLimitCaptureV1]] = []
    try:
        await h.setup()
        def enter() -> None:
            h.clock.callback = lambda: None
            tasks.append(asyncio.create_task(h.capture()))
        h.clock.callback = enter
        await h.capture()
        assert len(tasks) == 1
        with pytest.raises(InstrumentSourceError, match="OVERLAP"):
            await tasks[0]
        assert h.sends == 2
    finally:
        await h.owner.aclose()


async def test_wrong_catalog_revision_rejects_without_transport_or_catalog_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(monkeypatch)
    try:
        await h.setup()
        current = h.owner._current
        with pytest.raises(InstrumentSourceError, match="RESPONSE_INVALID"):
            await h.capture(expected_catalog_revision="wrong-revision")
        assert h.sends == 0 and h.owner._current is current
    finally:
        await h.owner.aclose()


async def test_original_operation_context_swap_resets_original_and_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(monkeypatch)
    try:
        await h.setup()
        context = h.owner._client._operation
        h.hook = lambda stage: setattr(h.owner._client, "_operation", ContextVar(
            "e2_replaced_operation", default=h.budget)) if stage == "chunk" else None
        with pytest.raises(InstrumentSourceError, match="OVERLAP"):
            await h.capture()
        assert context.get() is None and h.owner._client._operation is not context and h.sends == 1
    finally:
        await h.owner.aclose()


@pytest.mark.parametrize("target,code", [("operation", "OVERLAP"), ("receipt", "GROUP_REQUIRED")])
async def test_open_initial_context_never_invokes_get_callback(
    monkeypatch: pytest.MonkeyPatch, target: str, code: str,
) -> None:
    h = Harness(monkeypatch)
    calls: list[str] = []
    class OpenContext:
        def get(self) -> None:
            calls.append("get")
    try:
        await h.setup()
        if target == "operation":
            h.owner._client._operation = OpenContext()  # type: ignore[assignment]
        else:
            h.budget._receipt = OpenContext()  # type: ignore[assignment]
        with pytest.raises(InstrumentSourceError, match=code):
            await h.capture()
        assert calls == [] and h.sends == 0
    finally:
        await h.owner.aclose()


@pytest.mark.parametrize("changes,code", [
    ({"max_age": timedelta(0)}, "INPUT_INVALID"),
    ({"max_age": timedelta(minutes=5, microseconds=1)}, "INPUT_INVALID"),
    ({"max_age": 30}, "INPUT_INVALID"),
    ({"max_pages": True}, "INPUT_INVALID"),
    ({"max_pages": 0}, "INPUT_INVALID"),
    ({"max_pages": 101}, "INPUT_INVALID"),
    ({"budget": None}, "BUDGET_REQUIRED"),
    ({"owner_guard": object()}, "INPUT_INVALID"),
])
async def test_invalid_configuration_sends_nothing_preserves_catalog(
    monkeypatch: pytest.MonkeyPatch, changes: dict[str, object], code: str,
) -> None:
    h = Harness(monkeypatch)
    try:
        await h.setup()
        original = h.owner._current
        with pytest.raises(InstrumentSourceError, match="INSTRUMENT_SOURCE_" + code):
            await h.capture(**changes)
        assert h.sends == 0 and h.owner._current is original
        await h.capture()
    finally:
        await h.owner.aclose()


@pytest.mark.parametrize("stage", ["send", "chunk", "close"])
async def test_retirement_during_page_prevents_next_send(
    monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    h = Harness(monkeypatch)
    try:
        await h.setup()
        h.hook = lambda at: h.owner.retire() if at == stage else None
        with pytest.raises(InstrumentSourceError, match="RETIRED|RECEIPT_REQUIRED"):
            await h.capture()
        assert h.sends == 1 and h.closes == 1
    finally:
        await h.owner.aclose()


async def test_configured_cap_nonterminal_and_small_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(monkeypatch)
    try:
        await h.setup()
        with pytest.raises(RiskV2Rejected, match="RESOURCE_LIMIT"):
            await h.capture(max_pages=1)
        assert h.sends == 1
        h.sends = 0
        h.raw.clear()
        h.next_cursor = lambda _: ""
        with h.budget.reuse_or_open_receipt():
            receipt = h.budget._receipt.get()
            assert receipt is not None
            receipt.logical = 31  # Existing budget boundary; one terminal page remains valid.
            result = await h.capture(max_pages=100)
            assert len(result.raw_pages) == 1 and receipt.logical == 32
    finally:
        await h.owner.aclose()


@pytest.mark.parametrize("field", [
    "native_instrument_id", "parser_revision", "source_rule_revision", "specification_revision",
    "response_time", "raw_row_sha256", "raw_row_canonical", "source_id", "source_environment",
    "received_at", "key",
])
async def test_each_original_member_source_pin_rejects_detached_changes(
    monkeypatch: pytest.MonkeyPatch, field: str,
) -> None:
    h = Harness(monkeypatch)
    try:
        await h.setup()
        member = h.member
        # Deliberately malformed caller DTO; owner/handle/current are genuine and untouched.
        candidate = object.__new__(InstrumentObservation)
        for item in fields(member):
            object.__setattr__(candidate, item.name, getattr(member, item.name))
        if field in {"source_id", "source_environment", "received_at"}:
            source = object.__new__(type(member.source))
            for item in fields(member.source):
                object.__setattr__(source, item.name, getattr(member.source, item.name))
            value: object = member.source.received_at - timedelta(microseconds=1) if field == "received_at" else "changed"
            object.__setattr__(source, field, value)
            object.__setattr__(candidate, "source", source)
        else:
            value = member.response_time - timedelta(microseconds=1) if field == "response_time" else "changed"
            object.__setattr__(candidate, field, value)
        h.member = candidate
        with pytest.raises(RiskV2Rejected, match="SOURCE_MISMATCH"):
            await h.capture()
        assert h.sends == 0
    finally:
        await h.owner.aclose()
