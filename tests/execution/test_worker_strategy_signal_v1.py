"""Original worker/publication/bar owners; temporary approval fixtures never enable orders."""
from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
import test_worker_entry_model_v2 as original_worker_fixture
from test_bybit_linear_source_assembler_v1 import SCOPE

from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget
from multi_exchange_trader.market_data.adapters.bybit import BybitAdapter
from multi_exchange_trader.market_data.bybit_public_contracts import CANDLE_URL, TICKER_URL
from multi_exchange_trader.market_data.models import Candle, Ticker
from multi_exchange_trader.market_data.service import MarketDataService

model_worker = original_worker_fixture.model_worker


class Bars:
    def __init__(self, now: Any, *, on_candle: Any = None,
                 bar_age: timedelta = timedelta(0), async_gate: tuple[Any, Any] | None = None,
                 quote_gate: tuple[Any, Any] | None = None) -> None:
        self._now = now
        self._on_candle = on_candle
        anchor = (now - bar_age).replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
        self.rows = [Candle(timestamp=anchor - timedelta(minutes=15 * (5 - index)),
            open=Decimal(100 + index), high=Decimal(101 + index), low=Decimal(99 + index),
            close=Decimal(100 + index), volume=Decimal(1), is_closed=True) for index in range(5)]
        self.calls = 0
        self.quote_calls = 0
        transport: httpx.AsyncBaseTransport = (httpx.MockTransport(self._respond)
            if async_gate is None and quote_gate is None
            else _BlockingSyntheticTransport(self, async_gate, quote_gate))
        self.client = httpx.AsyncClient(transport=transport)
        self.adapter = BybitAdapter(self.client, clock=lambda: self._now)

    def _respond(self, request: httpx.Request) -> httpx.Response:
        route = str(request.url).split("?")[0]
        if request.method != "GET" or route not in (CANDLE_URL, TICKER_URL):
            raise AssertionError("unexpected synthetic Bybit route")
        now_ms = int(self._now.timestamp() * 1000)
        if route == CANDLE_URL:
            if dict(request.url.params) != {"symbol": "BTCUSDT", "category": "linear",
                    "interval": "15", "limit": "5"}:
                raise AssertionError("unexpected synthetic linear-kline query")
            self.calls += 1
            if self._on_candle is not None:
                self._on_candle()
            rows = [[str(int(bar.timestamp.timestamp() * 1000)), str(bar.open), str(bar.high),
                str(bar.low), str(bar.close), str(bar.volume), "0"] for bar in reversed(self.rows)]
            body = {"retCode": 0, "time": now_ms,
                "result": {"category": "linear", "symbol": "BTCUSDT", "list": rows}}
        else:
            if dict(request.url.params) != {"symbol": "BTCUSDT", "category": "linear"}:
                raise AssertionError("unexpected synthetic linear-ticker query")
            self.quote_calls += 1
            body = {"retCode": 0, "time": now_ms,
                "result": {"category": "linear", "list": [{"symbol": "BTCUSDT",
                    "bid1Price": "100", "bid1Size": "2", "ask1Price": "101", "ask1Size": "3"}]}}
        return httpx.Response(200, json=body, request=request)

    async def get_ticker(self, *_args: object, **_kwargs: object) -> Ticker:
        raise AssertionError("No ticker is required for a closed-bar signal")


class _BlockingSyntheticTransport(httpx.AsyncBaseTransport):
    def __init__(self, source: Bars, candle_gate: tuple[Any, Any] | None,
                 quote_gate: tuple[Any, Any] | None) -> None:
        self._source = source
        self._candle_gate = candle_gate
        self._quote_gate = quote_gate

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        route = str(request.url).split("?")[0]
        gate = (self._candle_gate if route == CANDLE_URL else
            self._quote_gate if route == TICKER_URL else None)
        if gate is not None:
            gate[0].set()
            await gate[1].wait()
        return self._source._respond(request)


def install(state: Any, source: Bars | None = None) -> tuple[MarketDataService, Bars]:
    if state.worker._entry_model_owner_v2 is None:
        source = source or Bars(state.fixture.clock())
        state.signal_market_clients.append(source.client)
        market = MarketDataService({"bybit": source.adapter})
        state.worker.install_entry_model_v2(public_marks=state.fixture.marks,
            catalog_producer=state.fixture.catalog, market=market)
        state._signal_test_market_v1 = market
        state._signal_test_source_v1 = source
    else:
        if source is not None and source is not state._signal_test_source_v1:
            raise AssertionError("test source owner is already pinned")
        market = state._signal_test_market_v1
        source = state._signal_test_source_v1
    return market, source


async def test_no_approved_builtin_artifact_cannot_create_signal_or_entry(model_worker: Any) -> None:
    state = model_worker
    market, source = install(state)
    before = state.worker.runtime.journal.list_commands()
    with pytest.raises(ValueError, match="BUILTIN_STRATEGY_APPROVAL_REQUIRED"):
        await state.worker.capture_builtin_signal_v1(scope=SCOPE, market=market)
    assert source.calls == 0
    assert state.worker.runtime.journal.list_commands() == before
    assert not state.strategy.commands


def test_builtin_source_bytes_are_actual_implementation_not_executable_input() -> None:
    api = importlib.import_module("multi_exchange_trader.execution.builtin_sma_atr_v1")
    from pathlib import Path

    assert isinstance(api.__file__, str)
    assert api.implementation_bytes_v1() == Path(api.__file__).read_bytes()

def config_bytes(**changes: object) -> bytes:
    import json

    value = {"schema_version": 1, "interval": "15m", "fast": 2, "slow": 4, "atr_bars": 3,
        "stop_atr_multiple": "2", "target_r": "2", "max_bar_age_seconds": 900, "signal_ttl_seconds": 30}
    value.update(changes)
    return json.dumps(value).encode()


def approved_fixture(state: Any, *, raw_config: bytes | None = None, implementation: bytes | None = None) -> Any:
    """Temporary actual-byte approval fixture; never publishes into a user's store."""
    import hashlib
    import json
    from dataclasses import asdict, replace

    from multi_exchange_trader.execution.builtin_sma_atr_v1 import (
        ENGINE_CONTRACT_V1,
        implementation_bytes_v1,
    )
    from multi_exchange_trader.execution.modes import StrategyReference
    from multi_exchange_trader.execution.strategy_publication_v1 import StrategyArtifactBytesV1

    scope = replace(SCOPE, strategy=StrategyReference("test-builtin-sma-atr", "test-r1"))
    source = implementation_bytes_v1() if implementation is None else implementation
    configuration = config_bytes() if raw_config is None else raw_config
    approval = {"schema_version": 1, "action": "APPROVE_STRATEGY_V1",
        "reference": asdict(scope.strategy), "implementation_sha256": hashlib.sha256(source).hexdigest(),
        "config_sha256": hashlib.sha256(configuration).hexdigest(), "config_schema_version": 1,
        "engine_contract_version": ENGINE_CONTRACT_V1, "permitted_instruments": [asdict(scope.instrument)],
        "approval_reference": "test-only-approval", "approval_source_reference": "test-only-fixture",
        "approval_environments": ["DEMO"]}
    current = state.book.read()
    publication = state.publication.publish((StrategyArtifactBytesV1(source, configuration, json.dumps(approval).encode()),),
        expected_policy_revision=current["revision"], expected_generation=1)
    draft = state.book.read()["policy"]
    draft["strategy"].update(allowed=[asdict(scope.strategy)], selected=[asdict(scope.strategy)])
    state.book.save_v2(publication.policy_revision, draft, expected_strategy_generation=publication.generation,
        policy_sections_version=2)
    return scope


async def captured(state: Any, *, raw_config: bytes | None = None) -> tuple[Any, Any, Bars]:
    scope = approved_fixture(state, raw_config=raw_config)
    market, source = install(state)
    handle = await state.worker.capture_builtin_signal_v1(scope=scope, market=market)
    return scope, handle, source


async def captured_risk_assessment(state: Any, *, profile: str = "UTA2_V1",
                                   monkeypatch: pytest.MonkeyPatch | None = None) -> tuple[Any, Any, Any, Bars]:
    scope = approved_fixture(state)
    if profile == "DEMO_UTA1_V1":
        if monkeypatch is None:
            raise AssertionError("DEMO UTA1 fixture needs its wire wrapper")
        from test_bybit_linear_source_assembler_v1 import demo_uta1_wire

        demo_uta1_wire(monkeypatch)
    market, source = install(state)
    await state.worker.capture_entry_sources_v2(scope=scope, profile=profile,
        nonvip_fee_basis=state.fixture.basis, nonvip_applicability_revision="fixture-fee-v1",
        budget=MonitoringReadBudget())
    signal_handle = await state.worker.capture_builtin_signal_v1(scope=scope, market=market)
    assessment_handle = state.worker.assess_current_risk_v2(signal_handle)
    return scope, signal_handle, assessment_handle, source


def assessment_source_times(owner: Any, read: Any) -> tuple[tuple[datetime, ...], tuple[datetime, ...]]:
    """Original retained evidence times, partitioned by policy evidence/quote age."""
    slot = owner._assembler._current
    if slot is None:
        raise AssertionError("captured source slot is missing")
    evidence: list[datetime] = []
    quotes: list[datetime] = []

    def add_source(target: list[datetime], source: Any) -> None:
        target.extend((source.request_started_at, source.received_at))
        target.append(
            datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=source.envelope_time_ms)
            if source.envelope_time_ms is not None else source.received_at
        )

    for source in read.venue.source_records:
        if any(source is mark_source for mark_source in slot.mark_sources):
            continue
        add_source(evidence, source)
    for source in slot.mark_sources:
        add_source(quotes, source)
    for mark in read.marks:
        quotes.extend((mark.reference.server_observed_at, mark.reference.received_at))
    catalog_view = slot.catalog_current.view
    evidence.append(catalog_view.issued_at)
    for envelope in catalog_view.envelopes:
        evidence.extend((envelope.request_started_at, envelope.response_time, envelope.received_at))
    for observation in catalog_view.catalog.observations:
        evidence.extend((observation.response_time, observation.source.received_at))
    evidence.extend((read.full_reservations.observed_at, read.evaluation_reservations.observed_at))
    return tuple(evidence), tuple(quotes)


async def test_worker_risk_assessment_uses_quote_age_separately_from_evidence_age(
    model_worker: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    import test_bybit_linear_source_assembler_v1 as original_source_fixture

    state = model_worker
    scope = approved_fixture(state)
    current = state.book.read()
    draft = current["policy"]
    draft["risk_v2"].update(max_evidence_age_seconds="10", max_quote_age_seconds="20")
    state.book.save_v2(current["revision"], draft,
        expected_strategy_generation=2, policy_sections_version=2)
    state.fixture.mark_age = timedelta(seconds=15)
    market, source = install(state)
    await state.worker.capture_entry_sources_v2(scope=scope, profile="UTA2_V1",
        nonvip_fee_basis=state.fixture.basis, nonvip_applicability_revision="fixture-fee-v1",
        budget=MonitoringReadBudget())
    signal_handle = await state.worker.capture_builtin_signal_v1(scope=scope, market=market)
    owner = state.worker._entry_model_owner_v2
    read = owner._read
    assert read is not None
    evidence_times, quote_times = assessment_source_times(owner, read)
    fee_document = read.venue.fee.closing_order_reserve_nonvip_rate.reference
    evidence_times = (*evidence_times, fee_document.captured_at)
    fresh_quote_io_times = tuple(
        stamp for source_ref in owner._assembler._current.mark_sources
        for stamp in (source_ref.request_started_at, source_ref.received_at)
    )
    stale_server_quotes = tuple(mark.reference.server_observed_at for mark in read.marks)
    stale_now = max((*evidence_times, *quote_times, *fresh_quote_io_times)) + timedelta(milliseconds=100)
    assert stale_now - min(evidence_times) < timedelta(seconds=10)
    assert all(timedelta(seconds=10) < stale_now - stamp <= timedelta(seconds=20)
        for stamp in stale_server_quotes)
    assert all(stale_now - stamp <= timedelta(seconds=20) for stamp in quote_times)
    calls_before = (len(state.fixture.requests), source.calls, source.quote_calls)
    commands_before = state.worker.runtime.journal.list_commands()

    with monkeypatch.context() as scoped_patch:
        scoped_patch.setattr(original_source_fixture, "datetime", SimpleNamespace(now=lambda _tz: stale_now))
        handle = state.worker.assess_current_risk_v2(signal_handle)
        view = state.worker.require_current_risk_assessment_v2(handle)

    assert view.stage == "PLAN" and view.numeric_assessment is None
    assert view.financial_authority is False and view.trading_enabled is False
    assert (len(state.fixture.requests), source.calls, source.quote_calls) == calls_before
    assert state.worker.runtime.journal.list_commands() == commands_before == ()
    assert not state.strategy.commands


async def test_worker_entry_capture_rejects_quote_older_than_quote_policy_even_if_evidence_allows(
    model_worker: Any,
) -> None:
    state = model_worker
    scope = approved_fixture(state)
    current = state.book.read()
    draft = current["policy"]
    draft["risk_v2"].update(max_evidence_age_seconds="10", max_quote_age_seconds="5")
    state.book.save_v2(current["revision"], draft,
        expected_strategy_generation=2, policy_sections_version=2)
    state.fixture.mark_age = timedelta(seconds=6)
    owner = state.worker._entry_model_owner_v2
    commands_before = state.worker.runtime.journal.list_commands()

    with pytest.raises(ValueError, match="RISK_V2_EXPIRED"):
        await state.worker.capture_entry_sources_v2(scope=scope, profile="UTA2_V1",
            nonvip_fee_basis=state.fixture.basis, nonvip_applicability_revision="fixture-fee-v1",
            budget=MonitoringReadBudget())

    assert owner._read is None
    assert owner._risk_assessment_current is None
    assert state.worker.runtime.journal.list_commands() == commands_before == ()
    assert not state.strategy.commands


async def test_original_worker_consumes_exact_approved_builtin_signal_without_entries(model_worker: Any) -> None:
    from fractions import Fraction

    state = model_worker
    scope, handle, source = await captured(state)
    value = state.worker.consume_builtin_signal_v1(handle)
    assert value.scope == scope and source.calls == 1
    assert value.direction.side == "LONG" and value.direction.entry_reference_price == Fraction(104)
    assert value.direction.stop_price == Fraction(100) and value.direction.take_profit_price == Fraction(112)
    assert not value.financial_authority and not value.trading_enabled
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


async def test_worker_risk_assessment_is_blocker_only_and_uses_current_owner_sources(model_worker: Any) -> None:
    from multi_exchange_trader.execution.worker_risk_assessment_v2 import (
        WORKER_RISK_ASSESSMENT_BLOCKERS_V2,
        WorkerRiskAssessmentPlanV2,
    )

    state = model_worker
    scope = approved_fixture(state)
    market, source = install(state)
    await state.worker.capture_entry_sources_v2(scope=scope, profile="UTA2_V1",
        nonvip_fee_basis=state.fixture.basis, nonvip_applicability_revision="fixture-fee-v1",
        budget=MonitoringReadBudget())
    signal_handle = await state.worker.capture_builtin_signal_v1(scope=scope, market=market)
    owner = state.worker._entry_model_owner_v2
    assert owner._read is not None and source.calls == 1 and source.quote_calls == 1

    calls_before = (len(state.fixture.requests), source.calls, source.quote_calls)
    commands_before = state.worker.runtime.journal.list_commands()
    handle = state.worker.assess_current_risk_v2(signal_handle)
    view = state.worker.require_current_risk_assessment_v2(handle)

    assert type(view) is WorkerRiskAssessmentPlanV2
    assert view.scope == scope and view.signal_id
    assert view.policy_revision == str(view.signal_policy_revision)
    assert view.source_profile == "UTA2_V1" and view.source_generation == owner._journal_epoch
    assert view.catalog_revision == owner._read.catalog.revision
    assert view.venue_source_sha256 and view.mark_set_sha256
    assert view.reservation_snapshot_id == owner._read.full_reservations.snapshot_id
    assert view.evaluation_reservation_snapshot_id == owner._read.evaluation_reservations.snapshot_id
    assert view.stage == "PLAN" and view.numeric_assessment is None
    assert view.blockers == WORKER_RISK_ASSESSMENT_BLOCKERS_V2
    assert view.financial_authority is False and view.trading_enabled is False
    assert (len(state.fixture.requests), source.calls, source.quote_calls) == calls_before
    assert state.worker.runtime.journal.list_commands() == commands_before
    assert not state.strategy.commands


async def test_worker_risk_assessment_rejects_sources_older_than_policy_evidence_age(
    model_worker: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    import test_bybit_linear_source_assembler_v1 as original_source_fixture

    from multi_exchange_trader.execution.runtime import RuntimeBlocked

    state = model_worker
    scope = approved_fixture(state)
    current = state.book.read()
    draft = current["policy"]
    draft["risk_v2"]["max_evidence_age_seconds"] = "1"
    state.book.save_v2(current["revision"], draft,
        expected_strategy_generation=2, policy_sections_version=2)
    market, source = install(state)
    await state.worker.capture_entry_sources_v2(scope=scope, profile="UTA2_V1",
        nonvip_fee_basis=state.fixture.basis, nonvip_applicability_revision="fixture-fee-v1",
        budget=MonitoringReadBudget())
    signal_handle = await state.worker.capture_builtin_signal_v1(scope=scope, market=market)
    owner = state.worker._entry_model_owner_v2
    read = owner._read
    assert read is not None  # capture accepts this read under worker.max_age (30 seconds)
    evidence_times, quote_times = assessment_source_times(owner, read)
    fee_document = read.venue.fee.closing_order_reserve_nonvip_rate.reference
    stale_now = max((*evidence_times, *quote_times, fee_document.captured_at)) + timedelta(seconds=2)
    calls_before = (len(state.fixture.requests), source.calls, source.quote_calls)
    commands_before = state.worker.runtime.journal.list_commands()

    with monkeypatch.context() as scoped_patch:
        scoped_patch.setattr(original_source_fixture, "datetime", SimpleNamespace(now=lambda _tz: stale_now))
        assert original_source_fixture.datetime.now(UTC) == stale_now
        with pytest.raises(RuntimeBlocked, match="^WORKER_RISK_ASSESSMENT_SOURCE_NOT_FRESH$"):
            state.worker.assess_current_risk_v2(signal_handle)

    assert owner._risk_assessment_current is None
    assert (len(state.fixture.requests), source.calls, source.quote_calls) == calls_before
    assert state.worker.runtime.journal.list_commands() == commands_before == ()
    assert not state.strategy.commands


async def test_worker_risk_assessment_rejects_stale_fee_document_only(
    model_worker: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace
    from types import SimpleNamespace

    import test_bybit_linear_source_assembler_v1 as original_source_fixture

    from multi_exchange_trader.execution.runtime import RuntimeBlocked

    state = model_worker
    scope = approved_fixture(state)
    current = state.book.read()
    draft = current["policy"]
    draft["risk_v2"]["max_evidence_age_seconds"] = "10"
    state.book.save_v2(current["revision"], draft,
        expected_strategy_generation=2, policy_sections_version=2)
    state.fixture.basis = replace(
        state.fixture.basis,
        reference=replace(
            state.fixture.basis.reference,
            captured_at=state.fixture.now - timedelta(seconds=15),
        ),
    )
    market, source = install(state)
    await state.worker.capture_entry_sources_v2(scope=scope, profile="UTA2_V1",
        nonvip_fee_basis=state.fixture.basis, nonvip_applicability_revision="fixture-fee-v1",
        budget=MonitoringReadBudget())
    signal_handle = await state.worker.capture_builtin_signal_v1(scope=scope, market=market)
    owner = state.worker._entry_model_owner_v2
    read = owner._read
    assert read is not None  # Capture allows 15 seconds under worker.max_age (30 seconds).
    evidence_times, quote_times = assessment_source_times(owner, read)
    stamps = (*evidence_times, *quote_times)
    stale_now = max(stamps) + timedelta(milliseconds=100)
    fee_document = read.venue.fee.closing_order_reserve_nonvip_rate.reference
    assert stale_now - min(stamps) < timedelta(seconds=10)
    assert stale_now - fee_document.captured_at > timedelta(seconds=10)
    calls_before = (len(state.fixture.requests), source.calls, source.quote_calls)
    commands_before = state.worker.runtime.journal.list_commands()

    with monkeypatch.context() as scoped_patch:
        scoped_patch.setattr(original_source_fixture, "datetime", SimpleNamespace(now=lambda _tz: stale_now))
        with pytest.raises(RuntimeBlocked, match="^WORKER_RISK_ASSESSMENT_SOURCE_NOT_FRESH$"):
            state.worker.assess_current_risk_v2(signal_handle)

    assert owner._risk_assessment_current is None
    assert (len(state.fixture.requests), source.calls, source.quote_calls) == calls_before
    assert state.worker.runtime.journal.list_commands() == commands_before == ()
    assert not state.strategy.commands


async def test_worker_risk_assessment_rejects_signal_data_and_revoked_or_foreign_handles(model_worker: Any) -> None:
    import copy

    from multi_exchange_trader.execution.runtime import RuntimeBlocked

    state = model_worker
    scope = approved_fixture(state)
    market, _source = install(state)
    await state.worker.capture_entry_sources_v2(scope=scope, profile="UTA2_V1",
        nonvip_fee_basis=state.fixture.basis, nonvip_applicability_revision="fixture-fee-v1",
        budget=MonitoringReadBudget())
    signal_handle = await state.worker.capture_builtin_signal_v1(scope=scope, market=market)
    signal_data = state.worker.consume_builtin_signal_v1(signal_handle)
    with pytest.raises(RuntimeBlocked, match="WORKER_RISK_ASSESSMENT_SOURCE_REQUIRED"):
        state.worker.assess_current_risk_v2(signal_data)  # type: ignore[arg-type]

    handle = state.worker.assess_current_risk_v2(signal_handle)
    with pytest.raises(RuntimeBlocked):
        copy.copy(handle)
    foreign = object.__new__(type(handle))
    with pytest.raises(RuntimeBlocked):
        state.worker.require_current_risk_assessment_v2(foreign)
    state.worker.retire_current_risk_assessment_v2(handle)
    with pytest.raises(RuntimeBlocked):
        state.worker.require_current_risk_assessment_v2(handle)


async def test_worker_risk_assessment_fails_closed_when_original_source_slot_changes(model_worker: Any) -> None:
    state = model_worker
    scope = approved_fixture(state)
    market, source = install(state)
    await state.worker.capture_entry_sources_v2(scope=scope, profile="UTA2_V1",
        nonvip_fee_basis=state.fixture.basis, nonvip_applicability_revision="fixture-fee-v1",
        budget=MonitoringReadBudget())
    signal_handle = await state.worker.capture_builtin_signal_v1(scope=scope, market=market)
    handle = state.worker.assess_current_risk_v2(signal_handle)
    calls_before = (len(state.fixture.requests), source.calls, source.quote_calls)
    state.worker._entry_model_owner_v2._assembler._current = None

    with pytest.raises((ValueError, RuntimeError)):
        state.worker.require_current_risk_assessment_v2(handle)
    assert (len(state.fixture.requests), source.calls, source.quote_calls) == calls_before
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


async def test_worker_risk_assessment_expiry_equality_is_checked_after_all_owner_callbacks(
    model_worker: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from multi_exchange_trader.execution import worker_entry_model_v2 as owner_module
    import test_bybit_linear_source_assembler_v1 as original_source_fixture

    state = model_worker
    _scope, _signal_handle, handle, _source = await captured_risk_assessment(state)
    plan = state.worker.require_current_risk_assessment_v2(handle)
    expiry = plan.valid_until
    original_datetime = original_source_fixture.datetime
    original_policy_hash = owner_module.c.risk_policy_sha256_v2
    crossed_expiry = [False]

    def clock_now(_tz: Any) -> datetime:
        return expiry if crossed_expiry[0] else original_datetime.now(UTC)

    def expire_after_policy_hash(policy: Any) -> str:
        digest = original_policy_hash(policy)
        crossed_expiry[0] = True
        return digest

    with monkeypatch.context() as scoped_patch:
        scoped_patch.setattr(original_source_fixture, "datetime", SimpleNamespace(now=clock_now))
        scoped_patch.setattr(owner_module.c, "risk_policy_sha256_v2", expire_after_policy_hash)
        with pytest.raises(ValueError, match="WORKER_RISK_ASSESSMENT_EXPIRED"):
            state.worker.require_current_risk_assessment_v2(handle)


async def test_worker_risk_assessment_issue_rejects_expiry_reached_during_final_hash(
    model_worker: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from multi_exchange_trader.execution import worker_entry_model_v2 as owner_module
    import test_bybit_linear_source_assembler_v1 as original_source_fixture

    state = model_worker
    scope = approved_fixture(state)
    market, source = install(state)
    await state.worker.capture_entry_sources_v2(scope=scope, profile="UTA2_V1",
        nonvip_fee_basis=state.fixture.basis, nonvip_applicability_revision="fixture-fee-v1",
        budget=MonitoringReadBudget())
    signal_handle = await state.worker.capture_builtin_signal_v1(scope=scope, market=market)
    owner = state.worker._entry_model_owner_v2
    signal = state.worker._strategy_signal_owner_v1._current.value
    expiry = min(signal.valid_until, owner._read.venue.valid_until)
    original_datetime = original_source_fixture.datetime
    original_reservation_hash = owner_module.c.reservation_snapshot_sha256_v2
    crossed_expiry = [False]
    calls_before = (len(state.fixture.requests), source.calls, source.quote_calls)
    commands_before = state.worker.runtime.journal.list_commands()

    def clock_now(_tz: Any) -> datetime:
        return expiry if crossed_expiry[0] else original_datetime.now(UTC)

    def expire_after_reservation_hash(snapshot: Any) -> str:
        digest = original_reservation_hash(snapshot)
        crossed_expiry[0] = True
        return digest

    with monkeypatch.context() as scoped_patch:
        scoped_patch.setattr(original_source_fixture, "datetime", SimpleNamespace(now=clock_now))
        scoped_patch.setattr(owner_module.c, "reservation_snapshot_sha256_v2", expire_after_reservation_hash)
        with pytest.raises(ValueError, match="WORKER_RISK_ASSESSMENT_EXPIRED"):
            state.worker.assess_current_risk_v2(signal_handle)

    assert owner._risk_assessment_current is None
    assert (len(state.fixture.requests), source.calls, source.quote_calls) == calls_before
    assert state.worker.runtime.journal.list_commands() == commands_before
    assert not state.strategy.commands


@pytest.mark.parametrize("replacement", [
    "generation", "policy", "catalog", "mark", "reservation", "owner", "signal-owner",
    "signal-revoked", "venue", "binding", "journal-epoch", "evaluation-reservation",
    "catalog-revision", "catalog-receipt", "mark-payload",
])
async def test_worker_risk_assessment_rejects_replaced_currentness_sources(
    model_worker: Any, replacement: str,
) -> None:
    state = model_worker
    _scope, _signal_handle, handle, source = await captured_risk_assessment(state)
    owner = state.worker._entry_model_owner_v2
    calls_before = (len(state.fixture.requests), source.calls, source.quote_calls)
    original_binding = state.worker.binding
    if replacement == "generation":
        state.worker._entry_publication_generation_v2 = object()
    elif replacement == "policy":
        current = state.book.read()
        draft = current["policy"]
        draft["risk_v2"]["per_trade_loss"] = "13"
        state.book.save_v2(current["revision"], draft,
            expected_strategy_generation=2, policy_sections_version=1)
    elif replacement == "catalog":
        state.fixture.catalog._current = None
    elif replacement == "catalog-revision":
        object.__setattr__(owner._read.catalog, "revision", "f" * 64)
    elif replacement == "catalog-receipt":
        owner._assembler._current.catalog_receipt = object()
    elif replacement == "mark":
        state.fixture.marks._slots["BTCUSDT"] = object()
    elif replacement == "mark-payload":
        mark = owner._read.marks[0]
        object.__setattr__(mark, "value", mark.value + Decimal(1))
    elif replacement == "reservation":
        object.__setattr__(owner._read.full_reservations, "snapshot_id", "replaced-reservation-snapshot")
    elif replacement == "evaluation-reservation":
        from dataclasses import replace

        object.__setattr__(owner._read, "evaluation_reservations",
            replace(owner._read.evaluation_reservations, snapshot_id="replaced-evaluation-snapshot"))
    elif replacement == "owner":
        state.worker._entry_model_owner_v2 = None
    elif replacement == "signal-owner":
        state.worker._strategy_signal_owner_v1 = None
    elif replacement == "signal-revoked":
        state.worker._strategy_signal_owner_v1.retire()
    elif replacement == "venue":
        venue = owner._read.venue
        object.__setattr__(venue, "valid_until", venue.valid_until - timedelta(seconds=1))
    elif replacement == "binding":
        state.worker.binding = object()
    else:
        journal = state.worker._journal
        epoch, revision = journal._v2_meta(required=True)
        journal.bind_owner_epoch_v2("assessment-test-replacement",
            expected_previous_owner_epoch=epoch, expected_journal_revision=revision)

    try:
        with pytest.raises(ValueError):
            state.worker.require_current_risk_assessment_v2(handle)
    finally:
        if replacement == "binding":
            state.worker.binding = original_binding
    assert (len(state.fixture.requests), source.calls, source.quote_calls) == calls_before
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


async def test_worker_risk_assessment_uses_current_demo_uta1_read(model_worker: Any,
        monkeypatch: pytest.MonkeyPatch) -> None:
    from multi_exchange_trader.execution.worker_risk_assessment_v2 import (
        WORKER_RISK_ASSESSMENT_BLOCKERS_V2,
    )

    state = model_worker
    state.fixture.mode = 3
    _scope, _signal_handle, handle, source = await captured_risk_assessment(
        state, profile="DEMO_UTA1_V1", monkeypatch=monkeypatch)
    calls_before = (len(state.fixture.requests), source.calls, source.quote_calls)
    view = state.worker.require_current_risk_assessment_v2(handle)

    assert view.source_profile == "DEMO_UTA1_V1"
    assert view.blockers == WORKER_RISK_ASSESSMENT_BLOCKERS_V2
    assert view.numeric_assessment is None and not view.financial_authority and not view.trading_enabled
    assert (len(state.fixture.requests), source.calls, source.quote_calls) == calls_before
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


@pytest.mark.parametrize("changes", [
    {"quantity": "1"}, {"capital_usdt": "100"}, {"leverage": 2}, {"fast": True},
    {"slow": 500}, {"atr_bars": 499}, {"stop_atr_multiple": "0"}, {"target_r": "NaN"},
])
def test_builtin_configuration_cannot_supply_financial_values_or_unbounded_windows(changes: dict[str, object]) -> None:
    from multi_exchange_trader.execution.builtin_sma_atr_v1 import parse_configuration_v1

    with pytest.raises(ValueError, match="BUILTIN_CONFIGURATION_INVALID"):
        parse_configuration_v1(config_bytes(**changes))


async def test_published_arbitrary_code_is_not_imported_or_executed(model_worker: Any) -> None:
    state = model_worker
    market, source = install(state)
    scope = approved_fixture(state, implementation=b"raise AssertionError('this must never execute')")
    with pytest.raises(ValueError, match="BUILTIN_STRATEGY_APPROVAL_REQUIRED"):
        await state.worker.capture_builtin_signal_v1(scope=scope, market=market)
    assert source.calls == 0 and state.worker.runtime.journal.list_commands() == ()
    assert not state.strategy.commands


@pytest.mark.parametrize("fault", ["revoked", "policy-changed", "foreign-handle", "mutated-result", "retired", "expired", "catalog-receipt"])
async def test_stale_or_replaced_original_signal_cannot_be_consumed(model_worker: Any, fault: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    from multi_exchange_trader.execution.worker_strategy_signal_v1 import WorkerStrategySignalV1

    state = model_worker
    scope, handle, _source = await captured(state, raw_config=config_bytes(signal_ttl_seconds=10) if fault == "expired" else None)
    if fault == "revoked":
        current = state.book.read()
        state.publication.revoke(scope.strategy, expected_policy_revision=current["revision"], expected_generation=2)
    elif fault == "policy-changed":
        current = state.book.read()
        draft = current["policy"]
        draft["strategy"]["selected"] = []
        state.book.save_v2(current["revision"], draft, expected_strategy_generation=2, policy_sections_version=2)
    elif fault == "foreign-handle":
        handle = object.__new__(WorkerStrategySignalV1)
    elif fault == "mutated-result":
        slot = state.worker._strategy_signal_owner_v1._current
        object.__setattr__(slot.value, "direction", replace(slot.value.direction, side="SHORT"))
    elif fault == "retired":
        state.worker._retire_entry_publications_v2()
    elif fault == "catalog-receipt":
        state.worker._strategy_signal_owner_v1._catalog_producer._revoke()
    else:
        from types import SimpleNamespace

        import test_bybit_linear_source_assembler_v1 as original_source_fixture

        original_clock = state.worker.clock
        expired = state.worker._strategy_signal_owner_v1._current.value.valid_until + timedelta(microseconds=1)
        monkeypatch.setattr(original_source_fixture, "datetime", SimpleNamespace(now=lambda _tz: expired))
        assert state.worker.clock is original_clock and original_clock() == expired
    with pytest.raises(ValueError):
        state.worker.consume_builtin_signal_v1(handle)
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


@pytest.mark.parametrize("trend,side,stop,target", [(1, "LONG", 100, 112), (-1, "SHORT", 100, 88), (0, "FLAT", None, None)])
def test_fixed_builtin_direction_and_true_range_prices(trend: int, side: str, stop: int | None, target: int | None) -> None:
    from decimal import Decimal
    from fractions import Fraction

    from multi_exchange_trader.execution.builtin_sma_atr_v1 import (
        derive_direction_v1,
        parse_configuration_v1,
    )

    source = Bars(datetime(2026, 11, 4, 12, tzinfo=UTC))
    rows = tuple(Candle(timestamp=bar.timestamp, open=Decimal(100 + trend * index),
        high=Decimal(101 + trend * index), low=Decimal(99 + trend * index),
        close=Decimal(100 + trend * index), volume=Decimal(1), is_closed=True)
        for index, bar in enumerate(source.rows))
    result = derive_direction_v1(rows, parse_configuration_v1(config_bytes()))
    assert result.side == side
    assert result.stop_price == (None if stop is None else Fraction(stop))
    assert result.take_profit_price == (None if target is None else Fraction(target))


async def test_current_policy_revocation_during_real_bar_await_refuses_publication(model_worker: Any) -> None:
    state = model_worker
    scope = approved_fixture(state)

    def revoke() -> None:
        current = state.book.read()
        state.publication.revoke(scope.strategy, expected_policy_revision=current["revision"],
            expected_generation=2)

    source = Bars(state.fixture.clock(), on_candle=revoke)
    market, _source = install(state, source)
    with pytest.raises(ValueError):
        await state.worker.capture_builtin_signal_v1(scope=scope, market=market)
    assert state.worker._strategy_signal_owner_v1._current is None
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


async def test_lease_change_during_catalog_await_stops_before_bar_source(
    model_worker: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from multi_exchange_trader.execution.runtime import RuntimeBlocked

    state = model_worker
    scope = approved_fixture(state)
    market, source = install(state)
    catalog = state.fixture.catalog
    original_refresh = catalog.refresh
    original_acquisition_id = state.worker._lock._acquisition_id
    entered, release = asyncio.Event(), asyncio.Event()
    refresh_calls = 0

    async def held_refresh(*args: object, **kwargs: object) -> Any:
        nonlocal refresh_calls
        result = await original_refresh(*args, **kwargs)
        refresh_calls += 1
        entered.set()
        await release.wait()
        return result

    monkeypatch.setattr(catalog, "refresh", held_refresh)
    task = asyncio.create_task(state.worker.capture_builtin_signal_v1(scope=scope, market=market))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        state.worker._lock._acquisition_id = original_acquisition_id + 1
        release.set()
        with pytest.raises(RuntimeBlocked, match="WORKER_ENTRY_PUBLICATION_OWNER_CHANGED"):
            await asyncio.wait_for(task, timeout=10)
    finally:
        release.set()
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        state.worker._lock._acquisition_id = original_acquisition_id

    owner = state.worker._strategy_signal_owner_v1
    assert refresh_calls == 1 and source.calls == 0 and source.quote_calls == 0
    assert owner._current is None
    assert state.worker._operations == 0
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


async def test_publication_generation_change_during_bar_await_stops_before_quote(
    model_worker: Any,
) -> None:
    import asyncio

    state = model_worker
    scope = approved_fixture(state)
    entered, release = asyncio.Event(), asyncio.Event()
    source = Bars(state.fixture.clock(), async_gate=(entered, release))
    market, _source = install(state, source)
    task = asyncio.create_task(state.worker.capture_builtin_signal_v1(scope=scope, market=market))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        state.worker._entry_publication_generation_v2 = object()
        release.set()
        with pytest.raises(ValueError, match="WORKER_SIGNAL_OWNER_CHANGED"):
            await asyncio.wait_for(task, timeout=10)
    finally:
        release.set()
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    owner = state.worker._strategy_signal_owner_v1
    assert source.calls == 1 and source.quote_calls == 0
    assert owner._current is None
    assert state.worker._operations == 0
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


async def test_publication_generation_change_during_quote_await_prevents_signal_issue(
    model_worker: Any,
) -> None:
    import asyncio

    state = model_worker
    scope = approved_fixture(state)
    entered, release = asyncio.Event(), asyncio.Event()
    source = Bars(state.fixture.clock(), quote_gate=(entered, release))
    market, _source = install(state, source)
    task = asyncio.create_task(state.worker.capture_builtin_signal_v1(scope=scope, market=market))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        assert not task.done()
        state.worker._entry_publication_generation_v2 = object()
        release.set()
        with pytest.raises(ValueError, match="WORKER_SIGNAL_OWNER_CHANGED"):
            await asyncio.wait_for(task, timeout=10)
    finally:
        release.set()
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    owner = state.worker._strategy_signal_owner_v1
    assert source.calls == 1 and source.quote_calls == 1
    assert owner._current is None
    assert state.worker._operations == 0
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


async def test_channel_replacement_in_final_catalog_callback_prevents_signal_issue(
    model_worker: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.execution.native_node import NativeChannel
    from multi_exchange_trader.execution.worker_strategy_signal_v1 import (
        WorkerStrategySignalOwnerV1,
    )

    state = model_worker
    scope = approved_fixture(state)
    market, source = install(state)
    assembly = state.worker._assembly
    original_channel = assembly.channel
    replacement = NativeChannel(original_channel.scope, original_channel.native_account_id)
    original_current = WorkerStrategySignalOwnerV1._require_catalog_current_v1
    calls = 0

    def replace_channel_after_catalog_current(
        owner: Any,
        receipt: Any,
        revision: str,
        entry_read: Any,
        entry_read_slot: object | None,
    ) -> None:
        nonlocal calls
        original_current(owner, receipt, revision, entry_read, entry_read_slot)
        calls += 1
        assembly.channel = replacement

    monkeypatch.setattr(
        WorkerStrategySignalOwnerV1,
        "_require_catalog_current_v1",
        replace_channel_after_catalog_current,
    )
    try:
        with pytest.raises(ValueError, match="WORKER_SIGNAL_OWNER_CHANGED"):
            await state.worker.capture_builtin_signal_v1(scope=scope, market=market)
    finally:
        assembly.channel = original_channel

    owner = state.worker._strategy_signal_owner_v1
    assert calls == 1 and source.calls == 1 and source.quote_calls == 1
    assert owner._current is None
    assert state.worker._operations == 0
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


async def test_catalog_revocation_after_quote_await_refuses_signal_publication(
    model_worker: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.execution.worker_strategy_signal_v1 import (
        WorkerStrategySignalOwnerV1,
    )

    state = model_worker
    scope = approved_fixture(state)
    market, source = install(state)
    original = WorkerStrategySignalOwnerV1._closed_guard
    revoked = False

    def revoke_after_last_guard(owner: Any, token: object, epoch: object,
                                roots: tuple[object, ...]) -> None:
        nonlocal revoked
        original(owner, token, epoch, roots)
        if not revoked and source.calls == 1 and source.quote_calls == 1:
            owner._catalog_producer._revoke()
            revoked = True

    monkeypatch.setattr(WorkerStrategySignalOwnerV1, "_closed_guard", revoke_after_last_guard)
    with pytest.raises(ValueError):
        await state.worker.capture_builtin_signal_v1(scope=scope, market=market)
    assert revoked and source.calls == 1 and source.quote_calls == 1
    assert state.worker._strategy_signal_owner_v1._current is None
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


async def test_final_consume_callback_cannot_revoke_catalog_receipt_before_signal_return(
    model_worker: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.execution.worker_strategy_signal_v1 import (
        WorkerStrategySignalOwnerV1,
    )

    state = model_worker
    _scope, handle, source = await captured(state)
    owner = state.worker._strategy_signal_owner_v1
    original = WorkerStrategySignalOwnerV1._closed_guard
    calls = 0

    def revoke_after_last_guard(current: Any, token: object, epoch: object,
                                roots: tuple[object, ...]) -> None:
        nonlocal calls
        original(current, token, epoch, roots)
        calls += 1
        if calls == 2:
            current._catalog_producer._revoke()

    monkeypatch.setattr(WorkerStrategySignalOwnerV1, "_closed_guard", revoke_after_last_guard)
    with pytest.raises(ValueError):
        state.worker.consume_builtin_signal_v1(handle)
    assert calls == 2 and source.calls == 1 and owner._current is None
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


async def test_old_closed_bars_do_not_gain_freshness_from_a_new_fetch(model_worker: Any) -> None:
    from multi_exchange_trader.market_data.adapters.base import MarketDataUnavailable

    state = model_worker
    scope = approved_fixture(state)
    source = Bars(state.fixture.clock(), bar_age=timedelta(hours=2))
    market, _source = install(state, source)
    with pytest.raises(MarketDataUnavailable) as error:
        await state.worker.capture_builtin_signal_v1(scope=scope, market=market)
    assert error.value.reason == "closed_bar_stale"
    assert source.calls == 1
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


async def test_legacy_source_less_adapter_cannot_issue_a_worker_signal(model_worker: Any) -> None:
    from multi_exchange_trader.market_data.adapters.base import MarketDataUnavailable

    state = model_worker
    market, _source = install(state)
    scope = approved_fixture(state)

    class LegacyOnly:
        calls = 0

        async def get_candles(self, *_args: object, **_kwargs: object) -> list[Candle]:
            self.calls += 1
            return []

        async def get_ticker(self, *_args: object, **_kwargs: object) -> Ticker:
            raise AssertionError("legacy signal lane must not request a ticker")

    source = LegacyOnly()
    with pytest.raises(MarketDataUnavailable) as caught:
        await state.worker.capture_builtin_signal_v1(scope=scope, market=MarketDataService({"bybit": source}))
    assert caught.value.reason == "public_source_required"
    assert source.calls == 0
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands
    assert state.worker._entry_model_market_v1 is market


async def test_same_watermark_uses_latest_source_window_and_retires_previous_handle(model_worker: Any) -> None:
    import asyncio

    state = model_worker
    scope, handle, source = await captured(state)
    first = state.worker.consume_builtin_signal_v1(handle)

    # Keep every immutable candle body and the watermark fixed while observing a fresh source window.
    unchanged_rows = tuple(source.rows)
    previous_source_time = source._now
    await asyncio.sleep(1.1)
    source._now = state.fixture.clock()
    assert source._now > previous_source_time
    repeated = await state.worker.capture_builtin_signal_v1(
        scope=scope, market=state.worker._entry_model_market_v1)
    second = state.worker.consume_builtin_signal_v1(repeated)
    assert tuple(source.rows) == unchanged_rows
    assert second.direction == first.direction and second.bar_watermark == first.bar_watermark
    assert second.snapshot_id != first.snapshot_id and second.observed_at >= first.observed_at
    with pytest.raises(ValueError):
        state.worker.consume_builtin_signal_v1(handle)
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


def model_phase_stop_context(state: Any, source: Bars | None = None) -> Any:
    """Bind the genuine negative cancellation owner; no native admission is fabricated."""
    import asyncio

    from multi_exchange_trader.execution.model_observation_phase_v2 import ModelObservationPhaseV2

    install(state, source)
    worker = state.worker
    stop = asyncio.Event()
    phase = ModelObservationPhaseV2(worker)
    phase._stop_event = phase._original_stop_event_v2 = stop
    phase._catalog = state.fixture.catalog
    worker._model_observation_phase_v2 = phase
    phase.require_not_stopped(worker)
    return stop


async def test_original_phase_stop_during_public_bar_await_rejects_signal(model_worker: Any) -> None:
    import asyncio
    from contextlib import suppress

    from multi_exchange_trader.execution.runtime import RuntimeBlocked

    state = model_worker
    scope = approved_fixture(state)
    entered, release = asyncio.Event(), asyncio.Event()
    source = Bars(state.fixture.clock(), async_gate=(entered, release))
    stop = model_phase_stop_context(state, source)
    market = state._signal_test_market_v1
    task = asyncio.create_task(state.worker.capture_builtin_signal_v1(
        scope=scope, market=market))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        assert not task.done() and not state.worker._stopping
        stop.set()
        release.set()
        with pytest.raises(RuntimeBlocked, match="MODEL_OBSERVATION_STOP_REQUESTED"):
            await asyncio.wait_for(task, timeout=10)
    finally:
        release.set()
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    assert state.worker._strategy_signal_owner_v1._current is None
    assert state.worker._operations == 0 and source.calls == 1
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


async def test_original_phase_stop_retires_previously_issued_signal(model_worker: Any) -> None:
    from multi_exchange_trader.execution.runtime import RuntimeBlocked

    state = model_worker
    stop = model_phase_stop_context(state)
    _scope, handle, _source = await captured(state)
    assert not state.worker.consume_builtin_signal_v1(handle).financial_authority
    stop.set()
    assert not state.worker._stopping
    with pytest.raises(RuntimeBlocked, match="MODEL_OBSERVATION_STOP_REQUESTED"):
        state.worker.consume_builtin_signal_v1(handle)
    assert state.worker._strategy_signal_owner_v1._current is None
    source = state._signal_test_source_v1
    calls_before_stop_capture = source.calls
    with pytest.raises(RuntimeBlocked, match="MODEL_OBSERVATION_STOP_REQUESTED"):
        await state.worker.capture_builtin_signal_v1(scope=_scope, market=state._signal_test_market_v1)
    assert source.calls == calls_before_stop_capture and state.worker._strategy_signal_owner_v1._current is None
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


async def test_original_phase_stop_after_final_callback_rejects_signal_consume(
    model_worker: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.execution.runtime import RuntimeBlocked

    state = model_worker
    stop = model_phase_stop_context(state)
    _scope, handle, _source = await captured(state)
    owner = state.worker._strategy_signal_owner_v1
    original_guard = owner._guard
    calls = 0

    def stop_after_final_original_guard() -> None:
        nonlocal calls
        original_guard()
        calls += 1
        if calls == 2:
            stop.set()

    monkeypatch.setattr(owner, "_guard", stop_after_final_original_guard)
    with pytest.raises(RuntimeBlocked, match="MODEL_OBSERVATION_STOP_REQUESTED"):
        state.worker.consume_builtin_signal_v1(handle)
    assert calls == 2 and owner._current is None
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


async def test_replaced_original_phase_during_public_bar_await_rejects_signal(model_worker: Any) -> None:
    import asyncio
    from contextlib import suppress

    state = model_worker
    scope = approved_fixture(state)
    entered, release = asyncio.Event(), asyncio.Event()
    source = Bars(state.fixture.clock(), async_gate=(entered, release))
    stop = model_phase_stop_context(state, source)
    market = state._signal_test_market_v1
    task = asyncio.create_task(state.worker.capture_builtin_signal_v1(
        scope=scope, market=market))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        stop.set()
        state.worker._model_observation_phase_v2 = None
        release.set()
        with pytest.raises(ValueError):
            await asyncio.wait_for(task, timeout=10)
    finally:
        release.set()
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    assert state.worker._strategy_signal_owner_v1._current is None
    assert source.calls == 1 and state.worker._operations == 0
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


async def test_rebound_original_phase_stop_event_during_public_bar_await_rejects_signal(
    model_worker: Any,
) -> None:
    import asyncio
    from contextlib import suppress

    state = model_worker
    scope = approved_fixture(state)
    entered, release = asyncio.Event(), asyncio.Event()
    source = Bars(state.fixture.clock(), async_gate=(entered, release))
    stop = model_phase_stop_context(state, source)
    phase = state.worker._model_observation_phase_v2
    market = state._signal_test_market_v1
    task = asyncio.create_task(state.worker.capture_builtin_signal_v1(
        scope=scope, market=market))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        stop.set()
        replacement = asyncio.Event()
        phase._stop_event = phase._original_stop_event_v2 = replacement
        release.set()
        with pytest.raises(ValueError):
            await asyncio.wait_for(task, timeout=10)
    finally:
        release.set()
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    assert state.worker._strategy_signal_owner_v1._current is None
    assert source.calls == 1 and state.worker._operations == 0
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands
