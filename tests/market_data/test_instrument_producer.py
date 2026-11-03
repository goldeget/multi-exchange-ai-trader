"""Offline owner/state behavior through the internal MockTransport route only."""

import asyncio
import copy
import inspect
import json
from collections.abc import Callable, Coroutine
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Literal, cast

import httpx
import pytest

from multi_exchange_trader.account import bybit_readonly as client_module
from multi_exchange_trader.account.instrument_envelope import (
    CatalogReceiptView,
    CurrentCatalogReceipt,
    InstrumentEnvelopeRead,
    InstrumentSourceError,
    validate_catalog_receipt_view,
)
from multi_exchange_trader.execution import monitoring_budget as budget_module
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget
from multi_exchange_trader.market_data import instrument_producer as module
from multi_exchange_trader.market_data.instrument_catalog import (
    InstrumentCatalog,
    build_instrument_catalog,
)
from multi_exchange_trader.market_data.instrument_definition import InstrumentObservation
from multi_exchange_trader.trading_product import InstrumentKey

Source = Literal["PUBLIC_MAINNET", "PUBLIC_TESTNET"]
Handler = (
    Callable[[httpx.Request], httpx.Response]
    | Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]
)
SPOT = InstrumentKey("BYBIT", "SPOT", "BTCUSDT")
LINEAR = InstrumentKey("BYBIT", "LINEAR", "BTCUSDT")
TTL = timedelta(seconds=10)


def forge[T](value: T, **changes: object) -> T:
    result = object.__new__(type(value))
    for item in fields(type(value)):  # type: ignore[arg-type]
        object.__setattr__(result, item.name, changes.get(item.name, getattr(value, item.name)))
    return result


class Clocks:
    def __init__(self) -> None:
        self.wall = datetime(2024, 1, 1, 0, 0, 2, tzinfo=UTC)
        self.mono = 100.0

    def wall_now(self) -> datetime:
        return self.wall

    def mono_now(self) -> float:
        return self.mono

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.mono += seconds


@pytest.fixture
def clocks(monkeypatch: pytest.MonkeyPatch) -> Clocks:
    clock = Clocks()
    # Replace only these modules' clock references, never the global time module.
    monkeypatch.setattr(client_module, "time", SimpleNamespace(monotonic=clock.mono_now))
    monkeypatch.setattr(client_module, "datetime", SimpleNamespace(now=lambda _: clock.wall))
    monkeypatch.setattr(budget_module, "time", SimpleNamespace(monotonic=clock.mono_now))
    return clock


def row(product: str, symbol: str = "BTCUSDT") -> dict[str, object]:
    value: dict[str, object] = {
        "symbol": symbol,
        "baseCoin": symbol[:-4],
        "quoteCoin": "USDT",
        "status": "Trading",
        "priceFilter": {"tickSize": "0.10"},
        "lotSizeFilter": {
            "basePrecision": "0.001",
            "quotePrecision": "0.01",
            "minOrderAmt": "5",
            "maxLimitOrderQty": "100",
            "maxMarketOrderQty": "50",
        },
    }
    if product == "linear":
        value |= {
            "settleCoin": "USDT",
            "contractType": "LinearPerpetual",
            "priceScale": "2",
            "isPreListing": False,
            "priceFilter": {"tickSize": "0.10", "minPrice": "0.1", "maxPrice": "1000000"},
            "lotSizeFilter": {
                "qtyStep": "0.001",
                "minOrderQty": "0.001",
                "maxOrderQty": "100",
                "maxMktOrderQty": "50",
                "minNotionalValue": "5",
            },
            "leverageFilter": {"minLeverage": "1", "maxLeverage": "100", "leverageStep": "0.01"},
        }
    return value


def response(request: httpx.Request, *, age_ms: int = 2000) -> httpx.Response:
    category, symbol = request.url.params["category"], request.url.params["symbol"]
    body = {
        "retCode": 0,
        "time": 1704067202000 - age_ms,
        "result": {"category": category, "list": [row(category, symbol)]},
    }
    # Whitespace is intentional evidence of original body retention.
    return httpx.Response(200, content=json.dumps(body, indent=1).encode())


def producer(
    clocks: Clocks, handler: Handler = response, *, source: Source = "PUBLIC_MAINNET"
) -> module.BybitInstrumentCatalogProducer:
    return module.BybitInstrumentCatalogProducer._for_testing(
        source_environment=source,
        transport=httpx.MockTransport(handler),
        wall_clock=clocks.wall_now,
        monotonic_clock=clocks.mono_now,
    )


async def refresh(
    owner: module.BybitInstrumentCatalogProducer,
    *,
    keys: tuple[InstrumentKey, ...] = (SPOT,),
    ttl: timedelta = TTL,
    budget: MonitoringReadBudget | None = None,
) -> CurrentCatalogReceipt:
    return await owner.refresh(
        declared_universe=keys,
        max_age=ttl,
        budget=budget if budget is not None else MonitoringReadBudget(),
    )


def view(
    owner: module.BybitInstrumentCatalogProducer, handle: CurrentCatalogReceipt
) -> CatalogReceiptView:
    # Test-only verifier is deliberately separate from production require_current.
    current = owner._current
    assert current is not None
    return owner._require_current_for_testing(
        handle, expected_revision=current.view.catalog.revision
    )


def assert_code(caught: pytest.ExceptionInfo[InstrumentSourceError], code: str) -> None:
    error = caught.value
    assert error.code == "INSTRUMENT_SOURCE_" + code
    assert error.args == (error.code,)
    assert error.__context__ is None and error.__cause__ is None


@pytest.mark.parametrize(
    "source,host",
    [("PUBLIC_MAINNET", "api.bybit.com"), ("PUBLIC_TESTNET", "api-testnet.bybit.com")],
)
async def test_complete_mixed_same_symbol_originals_and_exact_cost(
    clocks: Clocks,
    source: Source,
    host: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response(request)

    owner = producer(clocks, handler, source=source)
    budget = MonitoringReadBudget()
    handle = await refresh(owner, keys=(SPOT, LINEAR), budget=budget)
    result = view(owner, handle)
    assert result.catalog.declared_universe == (LINEAR, SPOT)
    assert [request.url.host for request in requests] == [host, host]
    assert [tuple(request.url.params.multi_items()) for request in requests] == [
        (("category", "linear"), ("symbol", "BTCUSDT")),
        (("category", "spot"), ("symbol", "BTCUSDT")),
    ]
    assert budget.physical_attempts == 2
    assert [envelope.logical_ordinal for envelope in result.envelopes] == [1, 2]
    assert all(
        envelope.decoded_body_bytes != envelope.canonical_envelope_bytes
        for envelope in result.envelopes
    )
    assert all(
        observation.source.source_id == result.source_id
        and observation.source.source_environment == source
        for observation in result.catalog.observations
    )
    assert result.catalog.expires_at == datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC)
    assert (
        sum(
            len(e.decoded_body_bytes) + len(e.canonical_envelope_bytes) + len(o.raw_row_canonical)
            for e, o in zip(result.envelopes, result.catalog.observations, strict=True)
        )
        <= 3 * 65536 * 16
    )
    with pytest.raises(InstrumentSourceError) as caught:
        owner.require_current(handle, expected_revision=result.catalog.revision)
    assert_code(caught, "INJECTED_TRANSPORT")
    await owner.aclose()


async def test_accepted_invalid_refresh_retires_current_before_validation(clocks: Clocks) -> None:
    owner = producer(clocks)
    handle = await refresh(owner)
    revision = view(owner, handle).catalog.revision
    with pytest.raises(InstrumentSourceError) as caught:
        await refresh(owner, keys=())
    assert_code(caught, "INPUT_INVALID")
    assert owner._current is None and owner._pending is None
    with pytest.raises(InstrumentSourceError) as caught:
        owner._require_current_for_testing(handle, expected_revision=revision)
    assert_code(caught, "RECEIPT_REQUIRED")
    await owner.aclose()


@pytest.mark.parametrize(
    "keys",
    [
        (),
        (SPOT, SPOT),
        [SPOT],
        (None,),
        tuple(InstrumentKey("BYBIT", "SPOT", f"X{i}USDT") for i in range(17)),
    ],
)
async def test_invalid_universe_is_zero_send(clocks: Clocks, keys: object) -> None:
    owner = producer(clocks)
    budget = MonitoringReadBudget()
    with pytest.raises(InstrumentSourceError):
        await owner.refresh(
            declared_universe=cast(tuple[InstrumentKey, ...], keys), max_age=TTL, budget=budget
        )
    assert budget.physical_attempts == 0 and owner._current is None
    await owner.aclose()


@pytest.mark.parametrize(
    "ttl", [None, True, 10, timedelta(0), timedelta(seconds=-1), timedelta.max]
)
async def test_explicit_exact_ttl_and_overflow_zero_send(clocks: Clocks, ttl: object) -> None:
    owner = producer(clocks)
    budget = MonitoringReadBudget()
    with pytest.raises(InstrumentSourceError):
        await refresh(owner, ttl=cast(timedelta, ttl), budget=budget)
    assert budget.physical_attempts == 0
    await owner.aclose()


async def test_ttl_cutoff_underflow_is_preflight_zero_send(clocks: Clocks) -> None:
    owner = producer(clocks)
    budget = MonitoringReadBudget()
    with pytest.raises(InstrumentSourceError) as caught:
        await refresh(owner, ttl=timedelta(days=365 * 3000), budget=budget)
    assert_code(caught, "CLOCK_INVALID")
    assert budget.physical_attempts == 0
    await owner.aclose()


async def test_maximum_sixteen_keys_and_retry_cost(clocks: Clocks) -> None:
    counts: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        symbol = request.url.params["symbol"]
        counts[symbol] = counts.get(symbol, 0) + 1
        return httpx.Response(500) if counts[symbol] < 3 else response(request)

    owner = producer(clocks, handler)
    budget = MonitoringReadBudget()
    keys = tuple(InstrumentKey("BYBIT", "SPOT", f"X{i}USDT") for i in range(16))
    result = view(owner, await refresh(owner, keys=keys, budget=budget))
    assert len(result.envelopes) == 16 and budget.physical_attempts == 48
    assert all(
        e.accepted_attempt == 2 and e.physical_end - e.physical_start == 3 for e in result.envelopes
    )
    await owner.aclose()


@pytest.mark.parametrize(
    "change",
    [
        "empty",
        "multi",
        "cursor",
        "symbol",
        "category",
        "status",
        "quote",
        "settle",
        "filter",
        "prelisting",
        "future",
        "invalid_raw",
    ],
)
async def test_incomplete_or_invalid_last_key_never_publishes(
    clocks: Clocks,
    change: str,
) -> None:
    sent = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        reply = response(request)
        if sent == 1:
            return reply
        if change == "invalid_raw":
            return httpx.Response(200, content=b'{"retCode":0,"retCode":0}')
        data = json.loads(reply.content)
        result = data["result"]
        item = result["list"][0]
        if change == "empty":
            result["list"] = []
        elif change == "multi":
            result["list"] *= 2
        elif change == "cursor":
            result["nextPageCursor"] = "more"
        elif change == "category":
            result["category"] = "spot"
        elif change == "future":
            data["time"] = 1704067203000
        else:
            item[
                {
                    "symbol": "symbol",
                    "status": "status",
                    "quote": "quoteCoin",
                    "settle": "settleCoin",
                    "filter": "lotSizeFilter",
                    "prelisting": "isPreListing",
                }[change]
            ] = {
                "symbol": "BTCUSDT",
                "status": "Closed",
                "quote": "USDC",
                "settle": "USDC",
                "filter": {},
                "prelisting": True,
            }[change]
        return httpx.Response(200, json=data)

    owner = producer(clocks, handler)
    # Both are LINEAR so the second requested symbol is ETHUSDT.
    keys = (LINEAR, InstrumentKey("BYBIT", "LINEAR", "ETHUSDT"))
    with pytest.raises(InstrumentSourceError) as caught:
        await refresh(owner, keys=keys)
    assert caught.value.__context__ is None and owner._current is None
    assert sent == 2 and owner._pending is None
    await owner.aclose()


async def test_same_revision_new_generation_clone_foreign_and_forged_handles(
    clocks: Clocks,
) -> None:
    owner, other = producer(clocks), producer(clocks)
    old = await refresh(owner)
    old_view = view(owner, old)
    current = await refresh(owner)
    result = view(owner, current)
    assert result.catalog.revision == old_view.catalog.revision
    assert result.source_id != old_view.source_id
    foreign = await refresh(other)
    with pytest.raises(InstrumentSourceError) as caught:
        copy.copy(current)
    assert_code(caught, "RECEIPT_REQUIRED")
    for invalid in (old, foreign, object.__new__(CurrentCatalogReceipt), None):
        with pytest.raises(InstrumentSourceError) as caught:
            owner._require_current_for_testing(
                cast(CurrentCatalogReceipt, invalid), expected_revision=result.catalog.revision
            )
        assert_code(caught, "RECEIPT_REQUIRED")
    with pytest.raises(InstrumentSourceError) as caught:
        owner._require_current_for_testing(current, expected_revision="0" * 64)
    assert_code(caught, "RESPONSE_INVALID")
    await owner.aclose()
    await other.aclose()


@pytest.mark.parametrize(
    "mode",
    [
        "wall_equal",
        "mono_equal",
        "wall_rollback",
        "mono_rollback",
        "nan",
        "inf",
        "bad_wall",
        "bad_mono",
    ],
)
async def test_clock_and_strict_ttl_revoke_current(clocks: Clocks, mode: str) -> None:
    owner = producer(clocks)
    handle = await refresh(owner)
    revision = view(owner, handle).catalog.revision
    if mode == "wall_equal":
        clocks.wall += timedelta(seconds=8)
    elif mode == "mono_equal":
        clocks.mono += 8
    elif mode == "wall_rollback":
        clocks.wall -= timedelta(microseconds=1)
    elif mode == "mono_rollback":
        clocks.mono -= 0.001
    elif mode == "bad_wall":
        clocks.wall = datetime(2024, 1, 1)
    else:
        clocks.mono = {"nan": float("nan"), "inf": float("inf"), "bad_mono": 100}[mode]
    with pytest.raises(InstrumentSourceError) as caught:
        owner._require_current_for_testing(handle, expected_revision=revision)
    assert_code(caught, "EXPIRED" if mode.endswith("equal") else "CLOCK_INVALID")
    assert owner._current is None
    await owner.aclose()


async def test_exact_rational_mono_boundary_does_not_round_age(clocks: Clocks) -> None:
    owner = producer(clocks)
    handle = await refresh(owner, ttl=timedelta(seconds=2, microseconds=1))
    clocks.mono += 0.0000009
    assert view(owner, handle).catalog.max_age == timedelta(seconds=2, microseconds=1)
    clocks.mono += 0.0000002
    with pytest.raises(InstrumentSourceError) as caught:
        view(owner, handle)
    assert_code(caught, "EXPIRED")
    await owner.aclose()


async def test_last_await_expiry_cannot_reset_earliest_age(clocks: Clocks) -> None:
    sent = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        if sent == 2:
            clocks.advance(8)
        return response(request)

    owner = producer(clocks, handler)
    with pytest.raises(InstrumentSourceError) as caught:
        await refresh(owner, keys=(SPOT, LINEAR))
    assert_code(caught, "EXPIRED")
    assert owner._current is None
    await owner.aclose()


async def test_wall_expiry_after_second_await_stops_before_third_send(clocks: Clocks) -> None:
    sent = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        if sent == 2:
            clocks.wall += timedelta(seconds=8)
        return response(request, age_ms=-6000 if sent >= 2 else 2000)

    owner = producer(clocks, handler)
    keys = (
        LINEAR,
        InstrumentKey("BYBIT", "LINEAR", "ETHUSDT"),
        InstrumentKey("BYBIT", "LINEAR", "XRPUSDT"),
    )
    with pytest.raises(InstrumentSourceError) as caught:
        await refresh(owner, keys=keys)
    assert_code(caught, "EXPIRED")
    assert sent == 2
    await owner.aclose()


async def test_lookup_rechecks_clocks_after_entire_view_validation(
    clocks: Clocks,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = producer(clocks)
    handle = await refresh(owner)
    revision = view(owner, handle).catalog.revision
    validate = validate_catalog_receipt_view

    def slow_validation(value: CatalogReceiptView) -> None:
        validate(value)
        clocks.advance(8)

    monkeypatch.setattr(module, "validate_catalog_receipt_view", slow_validation)
    with pytest.raises(InstrumentSourceError) as caught:
        owner._require_current_for_testing(handle, expected_revision=revision)
    assert_code(caught, "EXPIRED")
    assert owner._current is None
    await owner.aclose()


async def test_retire_during_retry_blocks_reentry_until_drain_no_next_key(clocks: Clocks) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    sends = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sends
        sends += 1
        if sends == 1:
            return httpx.Response(500)
        if sends == 2:
            entered.set()
            await release.wait()
        return response(request)

    owner = producer(clocks, handler)
    work = asyncio.create_task(refresh(owner, keys=(SPOT, LINEAR)))
    await entered.wait()
    marker = owner._pending
    owner.retire()
    assert owner._pending is marker and marker is not None and marker.envelopes == ()
    with pytest.raises(InstrumentSourceError) as caught:
        await refresh(owner, keys=())
    assert_code(caught, "OVERLAP")
    release.set()
    with pytest.raises(InstrumentSourceError) as caught:
        await work
    assert_code(caught, "RETIRED")
    assert owner._current is None and owner._pending is None and sends == 2
    assert view(owner, await refresh(owner))
    await owner.aclose()


async def test_overlap_does_not_mutate_live_generation_and_cancel_propagates(
    clocks: Clocks,
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return response(request)

    owner = producer(clocks, handler)
    work = asyncio.create_task(refresh(owner))
    await entered.wait()
    generation, marker = owner._generation, owner._pending
    with pytest.raises(InstrumentSourceError) as caught:
        await refresh(owner, keys=())
    assert_code(caught, "OVERLAP")
    assert owner._generation is generation and owner._pending is marker
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work
    assert owner._pending is None and owner._current is None
    release.set()
    assert view(owner, await refresh(owner))
    await owner.aclose()


async def test_close_is_immediate_single_shielded_and_never_cancels_refresh(
    clocks: Clocks,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release, close_entered, close_release = (asyncio.Event() for _ in range(4))
    close_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return response(request)

    owner = producer(clocks, handler)
    original_close = owner._client.aclose

    async def delayed_close() -> None:
        nonlocal close_calls
        close_calls += 1
        close_entered.set()
        await close_release.wait()
        await original_close()

    monkeypatch.setattr(owner._client, "aclose", delayed_close)
    work = asyncio.create_task(refresh(owner))
    await entered.wait()
    closing = asyncio.create_task(owner.aclose())
    await close_entered.wait()
    assert owner._closed and not work.cancelled() and owner._current is None
    with pytest.raises(InstrumentSourceError) as caught:
        await refresh(owner)
    assert_code(caught, "CLOSED")
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert owner._close_task is not None and not owner._close_task.cancelled()
    release.set()
    with pytest.raises(InstrumentSourceError) as caught:
        await work
    assert_code(caught, "CLOSED")
    close_release.set()
    await owner.aclose()
    await owner.aclose()
    assert close_calls == 1 and owner._pending is None


async def test_reuses_group_and_capacity_preflight_without_budget_reset(clocks: Clocks) -> None:
    owner = producer(clocks)
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as token:
        initial = budget.require_active_group(token)
        for _ in range(31):
            budget.begin_read()
        with pytest.raises(InstrumentSourceError) as caught:
            await refresh(owner, keys=(SPOT, LINEAR), budget=budget)
        assert_code(caught, "BUDGET_EXHAUSTED")
        remaining = budget.require_active_group(token)
        assert remaining.logical_gets == 31 and remaining.deadline == initial.deadline
        assert budget.physical_attempts == 0
        result = view(owner, await refresh(owner, budget=budget))
        assert result.envelopes[0].logical_ordinal == 32
        assert budget.require_active_group(token).logical_gets == 32
    await owner.aclose()


async def test_ended_child_context_cannot_open_replacement_group(clocks: Clocks) -> None:
    owner = producer(clocks)
    budget = MonitoringReadBudget()
    release = asyncio.Event()

    async def child() -> None:
        await release.wait()
        await refresh(owner, budget=budget)

    with budget.reuse_or_open_receipt():
        work = asyncio.create_task(child())
    release.set()
    with pytest.raises(InstrumentSourceError) as caught:
        await work
    assert_code(caught, "GROUP_REQUIRED")
    assert budget.physical_attempts == 0
    await owner.aclose()


@pytest.mark.parametrize("kind", ["none", "subclass", "terminal", "physical"])
async def test_wrong_or_terminal_budget_zero_send(clocks: Clocks, kind: str) -> None:
    class ChildBudget(MonitoringReadBudget):
        pass

    owner = producer(clocks)
    budget: object = (
        None if kind == "none" else ChildBudget() if kind == "subclass" else MonitoringReadBudget()
    )
    if kind in ("terminal", "physical"):
        exact = cast(MonitoringReadBudget, budget)
        if kind == "terminal":
            exact._terminal_failure = "MONITORING_READ_FAILED"
        else:
            exact.physical_attempts = 200
    with pytest.raises(InstrumentSourceError) as caught:
        await owner.refresh(
            declared_universe=(SPOT,), max_age=TTL, budget=cast(MonitoringReadBudget, budget)
        )
    assert_code(caught, "BUDGET_REQUIRED" if kind in ("none", "subclass") else "BUDGET_EXHAUSTED")
    await owner.aclose()


async def test_programming_guard_error_is_not_converted(clocks: Clocks) -> None:
    owner = producer(clocks)

    def broken() -> None:
        raise RuntimeError("owner programming bug")

    with pytest.raises(RuntimeError, match="owner programming bug"):
        await refresh(owner, budget=MonitoringReadBudget(guard=broken))
    assert owner._pending is None and owner._current is None
    await owner.aclose()


async def test_exact_owned_client_not_provenance_flags_alone(clocks: Clocks) -> None:
    owner, other = producer(clocks), producer(clocks)
    original = owner._client
    owner._client = other._client
    with pytest.raises(InstrumentSourceError) as caught:
        await refresh(owner)
    assert_code(caught, "RETIRED")
    owner._client = original
    await owner.aclose()
    await other.aclose()


async def test_current_revalidates_entire_view_not_only_revision(clocks: Clocks) -> None:
    owner = producer(clocks)
    handle = await refresh(owner)
    result = view(owner, handle)
    envelope = result.envelopes[0]
    object.__setattr__(envelope, "decoded_body_sha256", "0" * 64)
    with pytest.raises(InstrumentSourceError) as caught:
        owner._require_current_for_testing(handle, expected_revision=result.catalog.revision)
    assert_code(caught, "RESPONSE_INVALID")
    assert owner._current is None
    await owner.aclose()


@pytest.mark.parametrize("change", ["key", "source", "raw"])
async def test_post_route_envelope_mismatch_is_rejected_all_or_nothing(
    clocks: Clocks,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    owner = producer(clocks)
    read = owner._client.read_instrument_envelope

    async def corrupted_read(
        key: InstrumentKey, *, budget: MonitoringReadBudget, group: budget_module.ReadGroupToken
    ) -> InstrumentEnvelopeRead:
        envelope = await read(key, budget=budget, group=group)
        if change == "raw":
            return forge(envelope, decoded_body_bytes=b"{broken}")
        if change == "key":
            different = InstrumentKey("BYBIT", "SPOT", "ETHUSDT")
            return replace(
                envelope, key=different, params=(("category", "spot"), ("symbol", "ETHUSDT"))
            )
        origin = replace(envelope.sent_origin, host="api-testnet.bybit.com")
        return replace(envelope, sent_origin=origin, response_origin=origin)

    monkeypatch.setattr(owner._client, "read_instrument_envelope", corrupted_read)
    with pytest.raises(InstrumentSourceError) as caught:
        await refresh(owner)
    assert_code(caught, "JSON_INVALID" if change == "raw" else "ORIGIN_MISMATCH")
    assert owner._current is None and owner._pending is None
    await owner.aclose()


@pytest.mark.parametrize("change", ["source_id", "source", "revision", "generation", "missing"])
async def test_current_full_integrity_and_generation_fail_closed(
    clocks: Clocks, change: str
) -> None:
    owner = producer(clocks)
    handle = await refresh(owner)
    result = view(owner, handle)
    revision = result.catalog.revision
    if change == "generation":
        owner._generation = object()
    elif change == "missing":
        object.__delattr__(result, "envelopes")
        object.__setattr__(result, "__dataclass_fields__", {})
    elif change == "source_id":
        object.__setattr__(result, "source_id", "a" * 32)
    elif change == "source":
        object.__setattr__(result, "source_environment", "PUBLIC_TESTNET")
    else:
        object.__setattr__(result.catalog, "revision", "0" * 64)
    with pytest.raises(InstrumentSourceError) as caught:
        owner._require_current_for_testing(handle, expected_revision=revision)
    assert_code(caught, "RECEIPT_REQUIRED" if change == "generation" else "RESPONSE_INVALID")
    await owner.aclose()


@pytest.mark.parametrize("change", ["wall", "mono", "wall_nan", "mono_nan"])
async def test_own_clock_failure_after_await_never_publishes(clocks: Clocks, change: str) -> None:
    owner: module.BybitInstrumentCatalogProducer

    def handler(request: httpx.Request) -> httpx.Response:
        if change == "wall":
            owner._wall_clock = lambda: clocks.wall - timedelta(microseconds=1)
        elif change == "wall_nan":
            owner._wall_clock = lambda: cast(datetime, None)
        else:
            owner._monotonic_clock = lambda: clocks.mono - 1 if change == "mono" else float("nan")
        return response(request)

    owner = producer(clocks, handler)
    with pytest.raises(InstrumentSourceError) as caught:
        await refresh(owner)
    assert_code(caught, "CLOCK_INVALID")
    assert owner._current is None and owner._pending is None
    await owner.aclose()


@pytest.mark.parametrize("failure", ["network", "timeout", "programming"])
async def test_close_failure_never_reopens_and_reuses_one_task(
    clocks: Clocks,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    owner = producer(clocks)
    await refresh(owner)
    original_close = owner._client.aclose
    calls = 0

    async def broken_close() -> None:
        nonlocal calls
        calls += 1
        if failure == "network":
            raise httpx.NetworkError("raw provider detail")
        if failure == "programming":
            raise RuntimeError("programming failure")
        await asyncio.Event().wait()

    monkeypatch.setattr(owner._client, "aclose", broken_close)
    if failure == "timeout":
        real_timeout = asyncio.timeout

        def shortened_timeout(delay: float) -> asyncio.Timeout:
            assert delay == 10
            return real_timeout(0.001)

        # Only the producer module sees the accelerated timer; real timeout behavior remains.
        monkeypatch.setattr(
            module,
            "asyncio",
            SimpleNamespace(
                get_running_loop=asyncio.get_running_loop,
                timeout=shortened_timeout,
                shield=asyncio.shield,
            ),
        )
    for _ in range(2):
        if failure == "programming":
            with pytest.raises(RuntimeError, match="programming failure"):
                await owner.aclose()
        else:
            with pytest.raises(InstrumentSourceError) as caught:
                await owner.aclose()
            assert_code(caught, "NETWORK" if failure == "network" else "TIMEOUT")
        assert owner._closed and owner._current is None
    assert calls == 1
    with pytest.raises(InstrumentSourceError) as caught:
        await refresh(owner)
    assert_code(caught, "CLOSED")
    # Explicit harness cleanup of the injected client, not a production retry/reopen.
    await original_close()


async def test_exact_subclasses_rejected_and_no_defaults(clocks: Clocks) -> None:
    class ChildTTL(timedelta):
        pass

    class ChildKey(InstrumentKey):
        pass

    owner = producer(clocks)
    for kwargs in (
        {"max_age": ChildTTL(seconds=10), "declared_universe": (SPOT,)},
        {"max_age": TTL, "declared_universe": (ChildKey("BYBIT", "SPOT", "BTCUSDT"),)},
    ):
        with pytest.raises(InstrumentSourceError):
            await owner.refresh(**kwargs, budget=MonitoringReadBudget())  # type: ignore[arg-type]
    required = inspect.signature(owner.refresh).parameters
    assert all(parameter.default is inspect.Parameter.empty for parameter in required.values())
    await owner.aclose()


async def test_existing_group_guard_retirement_during_publish_prevents_late_issue(
    clocks: Clocks,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = producer(clocks)
    armed = False

    def guard() -> None:
        if armed:
            owner.retire()

    def arm_after_build(
        observations: tuple[InstrumentObservation, ...],
        *,
        declared_universe: tuple[InstrumentKey, ...],
        now: datetime,
        max_age: timedelta,
    ) -> InstrumentCatalog:
        nonlocal armed
        result = build_instrument_catalog(
            observations, declared_universe=declared_universe, now=now, max_age=max_age
        )
        armed = True
        return result

    monkeypatch.setattr(module, "build_instrument_catalog", arm_after_build)
    with pytest.raises(InstrumentSourceError) as caught:
        await refresh(owner, budget=MonitoringReadBudget(guard=guard))
    assert_code(caught, "RETIRED")
    assert owner._current is None and owner._pending is None
    await owner.aclose()


async def test_production_constructor_owns_real_public_client_without_network() -> None:
    owner = module.BybitInstrumentCatalogProducer(source_environment="PUBLIC_TESTNET")
    assert type(owner._client) is client_module.BybitReadOnlyClient
    assert owner._client._public_source_environment == "PUBLIC_TESTNET"
    assert owner._client._injected_transport is False
    assert owner._client is owner._owned_client
    assert not owner._testing
    assert tuple(inspect.signature(module.BybitInstrumentCatalogProducer).parameters) == (
        "source_environment",
    )
    assert tuple(inspect.signature(owner.require_current).parameters) == (
        "receipt",
        "expected_revision",
    )
    await owner.aclose()


def test_constructor_requires_loop_before_allocating_client() -> None:
    with pytest.raises(InstrumentSourceError) as caught:
        module.BybitInstrumentCatalogProducer(source_environment="PUBLIC_MAINNET")
    assert_code(caught, "LOOP_REQUIRED")


@pytest.mark.parametrize("source", [None, "mainnet", "DEMO", "FIXTURE"])
async def test_explicit_source_only_without_default(source: object) -> None:
    with pytest.raises(InstrumentSourceError) as caught:
        module.BybitInstrumentCatalogProducer(source_environment=cast(Source, source))
    assert_code(caught, "SOURCE_UNSUPPORTED")


async def test_wrong_loop_rejects_refresh_lookup_retire_and_close(clocks: Clocks) -> None:
    owner = producer(clocks)
    handle = await refresh(owner)
    revision = view(owner, handle).catalog.revision

    def other_thread() -> None:
        async def wrong_loop() -> None:
            operations: tuple[Callable[[], object], ...] = (
                lambda: owner._require_current_for_testing(handle, expected_revision=revision),
                owner.retire,
            )
            for operation in operations:
                with pytest.raises(InstrumentSourceError) as caught:
                    operation()
                assert_code(caught, "LOOP_REQUIRED")
            for action in (lambda: refresh(owner), owner.aclose):
                with pytest.raises(InstrumentSourceError) as caught:
                    await action()
                assert_code(caught, "LOOP_REQUIRED")

        asyncio.run(wrong_loop())

    await asyncio.to_thread(other_thread)
    assert view(owner, handle)
    await owner.aclose()
