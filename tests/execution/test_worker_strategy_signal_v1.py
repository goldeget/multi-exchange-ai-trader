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

from multi_exchange_trader.market_data.adapters.bybit import BybitAdapter
from multi_exchange_trader.market_data.bybit_public_contracts import CANDLE_URL, TICKER_URL
from multi_exchange_trader.market_data.models import Candle, Ticker
from multi_exchange_trader.market_data.service import MarketDataService

model_worker = original_worker_fixture.model_worker


class Bars:
    def __init__(self, now: Any, *, on_candle: Any = None,
                 bar_age: timedelta = timedelta(0), async_gate: tuple[Any, Any] | None = None) -> None:
        self._now = now
        self._on_candle = on_candle
        anchor = (now - bar_age).replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
        self.rows = [Candle(timestamp=anchor - timedelta(minutes=15 * (5 - index)),
            open=Decimal(100 + index), high=Decimal(101 + index), low=Decimal(99 + index),
            close=Decimal(100 + index), volume=Decimal(1), is_closed=True) for index in range(5)]
        self.calls = 0
        transport: httpx.AsyncBaseTransport = (httpx.MockTransport(self._respond)
            if async_gate is None else _BlockingSyntheticTransport(self, async_gate))
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
            body = {"retCode": 0, "time": now_ms,
                "result": {"category": "linear", "list": [{"symbol": "BTCUSDT",
                    "bid1Price": "100", "bid1Size": "2", "ask1Price": "101", "ask1Size": "3"}]}}
        return httpx.Response(200, json=body, request=request)

    async def get_ticker(self, *_args: object, **_kwargs: object) -> Ticker:
        raise AssertionError("No ticker is required for a closed-bar signal")


class _BlockingSyntheticTransport(httpx.AsyncBaseTransport):
    def __init__(self, source: Bars, gate: tuple[Any, Any]) -> None:
        self._source, self._entered, self._release = source, gate[0], gate[1]

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if str(request.url).split("?")[0] == CANDLE_URL:
            self._entered.set()
            await self._release.wait()
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
        policy_sections_version=1)
    return scope


async def captured(state: Any, *, raw_config: bytes | None = None) -> tuple[Any, Any, Bars]:
    scope = approved_fixture(state, raw_config=raw_config)
    market, source = install(state)
    handle = await state.worker.capture_builtin_signal_v1(scope=scope, market=market)
    return scope, handle, source


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
        state.book.save_v2(current["revision"], draft, expected_strategy_generation=2, policy_sections_version=1)
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


async def test_final_capture_callback_cannot_revoke_catalog_receipt_before_signal_issue(
    model_worker: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.execution.worker_strategy_signal_v1 import (
        WorkerStrategySignalOwnerV1,
    )

    state = model_worker
    scope = approved_fixture(state)
    market, source = install(state)
    original = WorkerStrategySignalOwnerV1._closed_guard
    calls = 0

    def revoke_after_last_guard(owner: Any, token: object, epoch: object,
                                roots: tuple[object, ...]) -> None:
        nonlocal calls
        original(owner, token, epoch, roots)
        calls += 1
        if calls == 2:
            owner._catalog_producer._revoke()

    monkeypatch.setattr(WorkerStrategySignalOwnerV1, "_closed_guard", revoke_after_last_guard)
    with pytest.raises(ValueError):
        await state.worker.capture_builtin_signal_v1(scope=scope, market=market)
    assert calls == 2 and source.calls == 1
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
