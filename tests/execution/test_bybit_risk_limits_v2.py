"""Raw/v2 History orchestration; genuine injected owners confer no trading authority."""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from multi_exchange_trader.account import bybit_readonly as client_module
from multi_exchange_trader.account.instrument_envelope import InstrumentSourceError
from multi_exchange_trader.execution import bybit_linear_model_inputs_v1 as models
from multi_exchange_trader.execution import history as module
from multi_exchange_trader.execution import monitoring_budget as budget_module
from multi_exchange_trader.execution.account_binding import (
    AccountBindingError,
    BybitDemoAccountSession,
)
from multi_exchange_trader.execution.bybit_linear_model_inputs_v1 import BybitRiskLimitCaptureV1
from multi_exchange_trader.execution.linear_risk_evidence_v2 import LinearTierSnapshotV2
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget, ReadGroupToken
from multi_exchange_trader.execution.protection import ProtectionScope
from multi_exchange_trader.execution.risk_v2_math import RiskV2Rejected
from multi_exchange_trader.market_data import instrument_producer as producer_module
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
        self.owner = producer_module.BybitInstrumentCatalogProducer._for_testing(
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



SCOPE = ProtectionScope("12345", "DEMO")
KEY = "fake-e2-key"


class HistoryHarness(Harness):
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        super().__init__(monkeypatch)
        self.demo_requests: list[httpx.Request] = []
        self.session_callback: Callable[[], None] = lambda: None
        self.reader_callback: Callable[[], None] = lambda: None
        self.session = BybitDemoAccountSession(api_key=KEY, api_secret="fake-e2-secret",
            clock=self.session_clock, monotonic=self.clock.mono_now,
            transport=httpx.MockTransport(self.demo_handler), max_age=TTL)

    def session_clock(self) -> Any:
        self.session_callback()
        return self.clock.wall_now()

    def reader_clock(self) -> Any:
        self.reader_callback()
        return self.clock.wall_now()

    def demo_handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api-demo.bybit.com"
        self.demo_requests.append(request)
        if request.url.path == "/v5/market/time":
            result: dict[str, object] = {}
        else:
            assert request.url.path == "/v5/user/query-api"
            result = {"apiKey": KEY, "secret": "", "userID": 12345, "parentUid": "56789",
                      "isMaster": False, "readOnly": 1, "permissions": {"Wallet": []}}
        return httpx.Response(200, json={"retCode": 0, "result": result,
                                        "time": int(self.clock.wall.timestamp() * 1000)})

    async def setup(self) -> None:
        await super().setup()
        with self.session._monitoring_operation(self.budget):
            self.binding = await self.session.authenticate(expected_uid="12345", expected_parent_uid="56789",
                                                           expected_is_master=False)
        self.reader = module.BybitHistoryReader(self.session, self.binding, scope=SCOPE,
                                                clock=self.reader_clock, max_age=TTL)

    async def read(self, *, raw: bool = False, **changes: Any) -> Any:
        kwargs: dict[str, Any] = {"public_owner": self.owner, "catalog_receipt": self.handle,
                                 "expected_catalog_revision": self.view.catalog.revision,
                                 "budget": self.budget}
        kwargs.update(changes)
        method = self.reader.read_risk_limit_capture_v1 if raw else self.reader.read_risk_limits_v2
        return await method(self.member, **kwargs)

    async def close(self) -> None:
        await self.session.aclose()
        await self.owner.aclose()


@pytest.mark.parametrize("raw", [False, True])
async def test_genuine_session_same_outer_receipt_public_capture_and_injection_rejection(
    monkeypatch: pytest.MonkeyPatch, raw: bool,
) -> None:
    h = HistoryHarness(monkeypatch)
    try:
        with h.budget.reuse_or_open_receipt() as token:
            await h.setup()
            receipt = h.budget._receipt.get()
            assert receipt is not None
            with pytest.raises(AccountBindingError, match="NETWORK_TRANSPORT_REQUIRED"):
                h.session.require_observation_binding(h.binding, scope=SCOPE)
            result = await h.read(raw=raw)
            assert type(result) is (BybitRiskLimitCaptureV1 if raw else LinearTierSnapshotV2)
            if raw:
                assert type(result) is BybitRiskLimitCaptureV1
                assert tuple(p.decoded_body_bytes for p in result.raw_pages) == tuple(h.raw)
                assert [p.logical_ordinal for p in result.raw_pages] == [5, 6]
            else:
                assert type(result) is LinearTierSnapshotV2
                assert len(result.rows) == 2 and result.instrument == h.member.key
            assert h.budget._receipt.get() is receipt and receipt.token is token
            assert receipt.logical == 6 and h.budget.physical_attempts == 6
            assert len(h.demo_requests) == 2 and h.sends == 2
            with pytest.raises(AccountBindingError, match="NETWORK_TRANSPORT_REQUIRED"):
                h.session.require_observation_binding(h.binding, scope=SCOPE)
        assert receipt.active is False
    finally:
        await h.close()


@pytest.mark.parametrize("suffix,want", [
    ("INPUT_INVALID", "INPUT_INVALID"), ("JSON_INVALID", "INPUT_INVALID"),
    ("RESPONSE_INVALID", "INPUT_INVALID"), ("ENCODING_UNSUPPORTED", "INPUT_INVALID"),
    ("BUDGET_REQUIRED", "INPUT_INVALID"), ("GROUP_REQUIRED", "INPUT_INVALID"),
    ("OVERLAP", "INPUT_INVALID"), ("RESOURCE_LIMIT", "RESOURCE_LIMIT"),
    ("BUDGET_EXHAUSTED", "RESOURCE_LIMIT"), ("ORIGIN_MISMATCH", "SOURCE_MISMATCH"),
    ("CLOCK_INVALID", "CAUSAL_ORDER_INVALID"), ("EXPIRED", "EXPIRED"),
    ("SOURCE_UNSUPPORTED", "RISK_TIER_UNVERIFIED"), ("PROVIDER_REJECTED", "RISK_TIER_UNVERIFIED"),
    ("HTTP_REJECTED", "RISK_TIER_UNVERIFIED"), ("ACCESS_RESTRICTED", "RISK_TIER_UNVERIFIED"),
    ("RATE_LIMITED", "RISK_TIER_UNVERIFIED"), ("NETWORK", "RISK_TIER_UNVERIFIED"),
    ("TIMEOUT", "RISK_TIER_UNVERIFIED"), ("RETIRED", "RISK_TIER_UNVERIFIED"),
    ("CLOSED", "RISK_TIER_UNVERIFIED"), ("LOOP_REQUIRED", "RISK_TIER_UNVERIFIED"),
    ("RECEIPT_REQUIRED", "RISK_TIER_UNVERIFIED"), ("INJECTED_TRANSPORT", "RISK_TIER_UNVERIFIED"),
    ("INCOMPLETE", "RISK_TIER_UNVERIFIED"),
])
async def test_complete_finite_source_map_has_no_legacy_catch_all(
    monkeypatch: pytest.MonkeyPatch, suffix: str, want: str,
) -> None:
    h = HistoryHarness(monkeypatch)
    try:
        await h.setup()
        def fail() -> None:
            raise InstrumentSourceError("INSTRUMENT_SOURCE_" + suffix)
        h.reader_callback = fail
        with pytest.raises(RiskV2Rejected) as caught:
            await h.read()
        assert caught.value.code == "RISK_V2_" + want and h.sends == 0
    finally:
        await h.close()


@pytest.mark.parametrize("kind", ["unlisted", "binding", "programmer", "cancel", "system", "keyboard"])
async def test_unmapped_reader_callback_retains_exact_error_in_same_task(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    h = HistoryHarness(monkeypatch)
    faults: dict[str, BaseException] = {
        "unlisted": InstrumentSourceError("INSTRUMENT_SOURCE_NATIVE_CAPACITY"),
        "binding": AccountBindingError("SYNTHETIC_BINDING_FAILURE"),
        "programmer": ValueError("synthetic reader failure"),
        "cancel": asyncio.CancelledError("synthetic original cancellation"),
        "system": SystemExit("synthetic direct entry control"),
        "keyboard": KeyboardInterrupt("synthetic direct entry control"),
    }
    try:
        await h.setup()
        fault = faults[kind]
        def fail() -> None:
            raise fault
        h.reader_callback = fail
        with pytest.raises(type(fault)) as caught:
            await h.read()
        assert caught.value is fault and h.sends == 0 and h.budget._receipt.get() is None
    finally:
        await h.close()


@pytest.mark.parametrize("stage,target", [
    ("parse", "catalog"), ("parse", "session"), ("project", "catalog"),
    ("project", "session"), ("final_reader_clock", "catalog"), ("final_reader_clock", "session"),
])
async def test_parser_projector_and_final_clock_reprove_both_original_owners(
    monkeypatch: pytest.MonkeyPatch, stage: str, target: str,
) -> None:
    h = HistoryHarness(monkeypatch)
    counts = {"parse": 0, "project": 0}
    try:
        await h.setup()
        def lose() -> None:
            if target == "catalog":
                h.owner.retire()
            else:
                h.session._binding = None
        def parse(*args: Any, **kwargs: Any) -> BybitRiskLimitCaptureV1:
            counts["parse"] += 1
            assert args[0] and len(args[0]) == 2
            assert kwargs["instrument_pin"].catalog_revision == h.view.catalog.revision
            result = models.parse_bybit_risk_limit_table_v1(*args, **kwargs)
            if stage == "parse":
                lose()
            return result
        def project(table: Any, *, instrument: Any) -> LinearTierSnapshotV2:
            counts["project"] += 1
            assert instrument is h.member
            result = models.project_bybit_tier_snapshot_v1(table, instrument=instrument)
            if stage == "project":
                lose()
            elif stage == "final_reader_clock":
                h.reader_callback = lose
            return result
        monkeypatch.setattr(producer_module, "parse_bybit_risk_limit_table_v1", parse)
        monkeypatch.setattr(module, "project_bybit_tier_snapshot_v1", project)
        with pytest.raises((RiskV2Rejected, AccountBindingError)):
            await h.read()
        assert h.sends == 2 and h.closes == 2 and counts["parse"] == 1
        assert counts["project"] == (0 if stage == "parse" else 1)
        assert h.budget._receipt.get() is None
    finally:
        await h.close()


async def test_raw_parser_once_and_v2_single_projector_do_not_refetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = HistoryHarness(monkeypatch)
    parsed: list[BybitRiskLimitCaptureV1] = []
    projected: list[object] = []
    try:
        await h.setup()
        def parse(*args: Any, **kwargs: Any) -> BybitRiskLimitCaptureV1:
            result = models.parse_bybit_risk_limit_table_v1(*args, **kwargs)
            parsed.append(result)
            return result
        def project(table: Any, *, instrument: Any) -> LinearTierSnapshotV2:
            assert len(parsed) == 1 and table is parsed[0].table and instrument is h.member
            result = models.project_bybit_tier_snapshot_v1(table, instrument=instrument)
            projected.append(result)
            return result
        monkeypatch.setattr(producer_module, "parse_bybit_risk_limit_table_v1", parse)
        monkeypatch.setattr(module, "project_bybit_tier_snapshot_v1", project)
        result = await h.read()
        assert len(parsed) == len(projected) == 1 and projected[0] is result
        assert h.sends == 2 and len(h.requests) == 4
    finally:
        await h.close()


async def test_session_clock_credential_leaf_swap_never_calls_custom_equality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    class OpenText(str):
        def __eq__(self, other: object) -> bool:
            calls.append("eq")
            return True
        __hash__ = str.__hash__
    h = HistoryHarness(monkeypatch)
    try:
        await h.setup()
        h.session_callback = lambda: setattr(h.session, "_api_key", OpenText(KEY))
        with pytest.raises(AccountBindingError):
            await h.read()
        assert calls == [] and h.sends == 0
    finally:
        await h.close()


@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.parametrize("kind", ["binding", "programmer", "cancel"])
async def test_direct_reader_primary_survives_receipt_reset_fault_without_restoration(
    monkeypatch: pytest.MonkeyPatch, raw: bool, kind: str,
) -> None:
    h = HistoryHarness(monkeypatch)
    faults: dict[str, BaseException] = {
        "binding": AccountBindingError("SYNTHETIC_BINDING_PRIMARY"),
        "programmer": ValueError("synthetic original reader defect"),
        "cancel": asyncio.CancelledError("synthetic original reader cancellation"),
    }
    try:
        await h.setup()
        context = h.budget._receipt
        receipts: list[Any] = []
        fault = faults[kind]
        def fail() -> None:
            receipts.append(context.get())
            replacement: ContextVar[Any] = ContextVar("e2_reader_exit_fault", default=None)
            replacement.set(context.get())
            h.budget._receipt = replacement
            raise fault
        h.reader_callback = fail
        with pytest.raises(type(fault)) as caught:
            await h.read(raw=raw)
        assert caught.value is fault
        assert h.budget._receipt is not context and receipts[0].active is False and h.sends == 0
    finally:
        await h.close()


@pytest.mark.parametrize("kind", ["none", "ordinary", "cancel", "system", "keyboard"])
def test_existing_receipt_exit_fault_table_in_direct_same_task_helper(kind: str) -> None:
    budget = MonitoringReadBudget()
    original = budget._receipt
    faults: dict[str, BaseException | None] = {
        "none": None, "ordinary": ValueError("synthetic primary"),
        "cancel": asyncio.CancelledError("synthetic original cancellation"),
        "system": SystemExit("synthetic original control"),
        "keyboard": KeyboardInterrupt("synthetic original control"),
    }
    fault = faults[kind]
    receipt: Any = None
    with pytest.raises(ValueError if fault is None else type(fault)) as caught, producer_module._risk_receipt(budget):
        receipt = original.get()
        budget._receipt = ContextVar("e2_direct_exit_fault", default=receipt)
        if fault is not None:
            raise fault
    assert receipt.active is False and budget._receipt is not original
    if fault is not None:
        assert caught.value is fault


@pytest.mark.parametrize("cleanup_kind", ["ordinary", "cancel", "system", "keyboard"])
def test_fresh_exit_control_wins_only_at_bounded_direct_cleanup_seam(
    monkeypatch: pytest.MonkeyPatch, cleanup_kind: str,
) -> None:
    # Controlled exit-only observation hook; this does not qualify process
    # control raised inside HTTPX wait_for child tasks or replace real owners.
    primary = ValueError("synthetic ordinary primary")
    cleanup: BaseException = {
        "ordinary": RuntimeError("synthetic ordinary exit"),
        "cancel": asyncio.CancelledError("synthetic fresh exit cancellation"),
        "system": SystemExit("synthetic fresh exit control"),
        "keyboard": KeyboardInterrupt("synthetic fresh exit control"),
    }[cleanup_kind]
    original = MonitoringReadBudget.reuse_or_open_receipt
    @contextmanager
    def observed(budget: MonitoringReadBudget) -> Iterator[ReadGroupToken]:
        with original(budget) as token:
            try:
                yield token
            finally:
                raise cleanup
    monkeypatch.setattr(MonitoringReadBudget, "reuse_or_open_receipt", observed)
    budget = MonitoringReadBudget()
    want = primary if cleanup_kind == "ordinary" else cleanup
    with pytest.raises(type(want)) as caught, producer_module._risk_receipt(budget):
        raise primary
    assert caught.value is want and budget._receipt.get() is None


@pytest.mark.parametrize("stage", ["parse", "project"])
async def test_parser_or_projector_original_failure_identity_prevents_result(
    monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    h = HistoryHarness(monkeypatch)
    fault = RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
    try:
        await h.setup()
        def fail(*args: Any, **kwargs: Any) -> Any:
            raise fault
        if stage == "parse":
            monkeypatch.setattr(producer_module, "parse_bybit_risk_limit_table_v1", fail)
        else:
            monkeypatch.setattr(module, "project_bybit_tier_snapshot_v1", fail)
        with pytest.raises(RiskV2Rejected) as caught:
            await h.read()
        assert caught.value is fault and h.budget._receipt.get() is None
        assert h.sends == 2 and h.owner._risk_pending is None
    finally:
        await h.close()


@pytest.mark.parametrize("stage", ["send", "chunk", "close"])
async def test_session_loss_during_public_page_preserves_binding_family_no_next_send(
    monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    h = HistoryHarness(monkeypatch)
    try:
        await h.setup()
        h.hook = lambda at: setattr(h.session, "_binding", None) if at == stage else None
        with pytest.raises(AccountBindingError):
            await h.read()
        assert h.sends == 1 and h.closes == 1
    finally:
        await h.close()


@pytest.mark.parametrize("target", ["session", "catalog"])
async def test_session_clock_last_callback_cannot_return_after_either_owner_loss(
    monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    h = HistoryHarness(monkeypatch)
    try:
        await h.setup()
        def lose() -> None:
            if target == "session":
                h.session._binding = None
            else:
                h.owner.retire()
        h.session_callback = lose
        with pytest.raises((AccountBindingError, RiskV2Rejected)):
            await h.read()
        assert h.sends == 0
    finally:
        await h.close()


@pytest.mark.parametrize("raw", [False, True])
async def test_replaced_receipt_context_preserves_body_primary_without_caller_repair(
    monkeypatch: pytest.MonkeyPatch, raw: bool,
) -> None:
    h = HistoryHarness(monkeypatch)
    try:
        await h.setup()
        original_context = h.budget._receipt
        original_receipts: list[Any] = []
        def replace_context(at: str) -> None:
            if at == "chunk":
                original_receipts.append(original_context.get())
                h.budget._receipt = ContextVar("e2_replaced_receipt", default=None)
        h.hook = replace_context
        with pytest.raises(RiskV2Rejected, match="INPUT_INVALID"):
            await h.read(raw=raw)
        assert h.budget._receipt is not original_context
        assert original_receipts[0].active is False
        assert h.sends == 1 and h.closes == 1
    finally:
        await h.close()


async def test_open_initial_session_context_never_invokes_get_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = HistoryHarness(monkeypatch)
    calls: list[str] = []
    class OpenContext:
        def get(self) -> None:
            calls.append("get")
    try:
        await h.setup()
        h.session._client._operation = OpenContext()  # type: ignore[assignment]
        with pytest.raises(AccountBindingError):
            await h.read()
        assert calls == [] and h.sends == 0
    finally:
        await h.close()


@pytest.mark.parametrize("stage", ["parse", "project"])
async def test_after_e1_original_public_context_value_is_required_at_return(
    monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    h = HistoryHarness(monkeypatch)
    try:
        await h.setup()
        context = h.owner._client._operation
        def parse(*args: Any, **kwargs: Any) -> BybitRiskLimitCaptureV1:
            result = models.parse_bybit_risk_limit_table_v1(*args, **kwargs)
            assert context.get() is None
            context.set(h.budget)
            return result
        def project(table: Any, *, instrument: Any) -> LinearTierSnapshotV2:
            result = models.project_bybit_tier_snapshot_v1(table, instrument=instrument)
            assert context.get() is None
            context.set(h.budget)
            return result
        if stage == "parse":
            monkeypatch.setattr(producer_module, "parse_bybit_risk_limit_table_v1", parse)
        else:
            monkeypatch.setattr(module, "project_bybit_tier_snapshot_v1", project)
        with pytest.raises(RiskV2Rejected, match="INPUT_INVALID"):
            await h.read()
        assert context.get() is h.budget and h.owner._client._operation is context
        assert h.sends == 2 and h.budget._receipt.get() is None
    finally:
        await h.close()


@pytest.mark.parametrize("stage", ["parse", "project"])
@pytest.mark.parametrize("expired", [False, True])
async def test_post_e1_group_deadline_equality_prevents_capture_return(
    monkeypatch: pytest.MonkeyPatch, stage: str, expired: bool,
) -> None:
    h = HistoryHarness(monkeypatch)
    try:
        await h.setup()
        def expire() -> None:
            receipt = h.budget._receipt.get()
            assert receipt is not None
            h.clock.mono = receipt.deadline - (0.0 if expired else 1.0)
        def parse(*args: Any, **kwargs: Any) -> BybitRiskLimitCaptureV1:
            result = models.parse_bybit_risk_limit_table_v1(*args, **kwargs)
            expire()
            return result
        def project(table: Any, *, instrument: Any) -> LinearTierSnapshotV2:
            result = models.project_bybit_tier_snapshot_v1(table, instrument=instrument)
            expire()
            return result
        if stage == "parse":
            monkeypatch.setattr(producer_module, "parse_bybit_risk_limit_table_v1", parse)
        else:
            monkeypatch.setattr(module, "project_bybit_tier_snapshot_v1", project)
        if expired:
            with pytest.raises(RiskV2Rejected, match="RESOURCE_LIMIT"):
                await h.read()
        else:
            result = await h.read()
            assert type(result) is LinearTierSnapshotV2
        assert h.sends == 2 and h.budget._receipt.get() is None
    finally:
        await h.close()
