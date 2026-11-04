"""Original worker/publication/bar owners; temporary approval fixtures never enable orders."""
from __future__ import annotations

import importlib
from datetime import timedelta
from typing import Any

import pytest
import test_worker_entry_model_v2 as original_worker_fixture
from test_bybit_linear_source_assembler_v1 import SCOPE

from multi_exchange_trader.market_data.models import Candle, Ticker
from multi_exchange_trader.market_data.service import MarketDataService

model_worker = original_worker_fixture.model_worker


class Bars:
    def __init__(self, now: Any) -> None:
        from decimal import Decimal

        anchor = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
        self.rows = [Candle(timestamp=anchor - timedelta(minutes=15 * (5 - index)),
            open=Decimal(100 + index), high=Decimal(101 + index), low=Decimal(99 + index),
            close=Decimal(100 + index), volume=Decimal(1), is_closed=True) for index in range(5)]
        self.calls = 0

    async def get_candles(self, *_args: object, **_kwargs: object) -> list[Candle]:
        self.calls += 1
        return list(self.rows)

    async def get_ticker(self, *_args: object, **_kwargs: object) -> Ticker:
        raise AssertionError("No ticker is required for a closed-bar signal")


async def test_no_approved_builtin_artifact_cannot_create_signal_or_entry(model_worker: Any) -> None:
    state = model_worker
    source = Bars(state.fixture.clock())
    before = state.worker.runtime.journal.list_commands()
    with pytest.raises(ValueError, match="BUILTIN_STRATEGY_APPROVAL_REQUIRED"):
        await state.worker.capture_builtin_signal_v1(scope=SCOPE, market=MarketDataService({"bybit": source}))
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
    source = Bars(state.fixture.clock())
    handle = await state.worker.capture_builtin_signal_v1(scope=scope, market=MarketDataService({"bybit": source}))
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
    scope = approved_fixture(state, implementation=b"raise AssertionError('this must never execute')")
    source = Bars(state.fixture.clock())
    with pytest.raises(ValueError, match="BUILTIN_STRATEGY_APPROVAL_REQUIRED"):
        await state.worker.capture_builtin_signal_v1(scope=scope, market=MarketDataService({"bybit": source}))
    assert source.calls == 0 and state.worker.runtime.journal.list_commands() == ()
    assert not state.strategy.commands


@pytest.mark.parametrize("fault", ["revoked", "policy-changed", "foreign-handle", "mutated-result", "retired", "expired"])
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
    from datetime import UTC, datetime
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

    class RevokingBars(Bars):
        async def get_candles(self, *_args: object, **_kwargs: object) -> list[Candle]:
            current = state.book.read()
            state.publication.revoke(scope.strategy, expected_policy_revision=current["revision"],
                expected_generation=2)
            return await super().get_candles(*_args, **_kwargs)

    source = RevokingBars(state.fixture.clock())
    with pytest.raises(ValueError):
        await state.worker.capture_builtin_signal_v1(scope=scope, market=MarketDataService({"bybit": source}))
    assert state.worker._strategy_signal_owner_v1._current is None
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


async def test_old_closed_bars_do_not_gain_freshness_from_a_new_fetch(model_worker: Any) -> None:
    state = model_worker
    scope = approved_fixture(state)
    source = Bars(state.fixture.clock() - timedelta(hours=2))
    with pytest.raises(ValueError, match="BUILTIN_STRATEGY_SIGNAL_EXPIRED"):
        await state.worker.capture_builtin_signal_v1(scope=scope, market=MarketDataService({"bybit": source}))
    assert source.calls == 1
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


async def test_same_watermark_keeps_first_signal_time_and_retires_previous_handle(model_worker: Any) -> None:
    state = model_worker
    scope, handle, source = await captured(state)
    first = state.worker.consume_builtin_signal_v1(handle)
    repeated = await state.worker.capture_builtin_signal_v1(scope=scope, market=MarketDataService({"bybit": source}))
    second = state.worker.consume_builtin_signal_v1(repeated)
    assert second == first and second.observed_at == first.observed_at
    with pytest.raises(ValueError):
        state.worker.consume_builtin_signal_v1(handle)
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands
