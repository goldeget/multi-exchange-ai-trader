"""Portable, explicit fake-native cache diagnostics; no effects are permitted."""
from __future__ import annotations

import importlib
from dataclasses import replace
from decimal import Decimal
from enum import Enum
from types import SimpleNamespace
from typing import Any

import pytest
from test_bybit_native import CLIENT_ID, INSTRUMENT, SCOPE, request

from multi_exchange_trader.execution.bybit_native import NativeBindings


class ClientOrderId(str):
    pass


class InstrumentId(str):
    @classmethod
    def from_str(cls, value: str) -> InstrumentId:
        return cls(value)


class TraderId(str):
    pass


class StrategyId(str):
    pass


class AccountId(str):
    pass


class VenueOrderId(str):
    pass


class PositionId(str):
    pass


class OrderStatus(Enum):
    INITIALIZED = 0
    ACCEPTED = 1
    CANCELED = 2
    PENDING_CANCEL = 3


class OrderType(Enum):
    MARKET = 0
    LIMIT = 1


class OrderSide(Enum):
    BUY = 0
    SELL = 1


class MarketOrder(SimpleNamespace):
    pass


class LimitOrder(SimpleNamespace):
    pass


TYPES = {item.__name__: item for item in (ClientOrderId, InstrumentId, TraderId, StrategyId,
    AccountId, VenueOrderId, PositionId, OrderStatus, OrderType, OrderSide, MarketOrder, LimitOrder)}
NETTING_ID = INSTRUMENT + "-Strategy-000"


def api() -> Any:
    name = "multi_exchange_trader.execution.native_recovery"
    assert importlib.util.find_spec(name) is not None, "native recovery inspector is missing"
    return importlib.import_module(name)


class ReadOnlyCache:
    def __init__(self, orders: list[Any], links: dict[str, object] | None = None) -> None:
        self.entries = {str(order.client_order_id): order for order in orders}
        self.links = links or {}
        self.effects: list[str] = []
        self.fail: str | None = None

    def order(self, client_order_id: object) -> object | None:
        assert type(client_order_id) is ClientOrderId
        if self.fail == "order":
            raise RuntimeError("SECRET provider payload")
        return self.entries.get(str(client_order_id))

    def position_id(self, client_order_id: object) -> object | None:
        assert type(client_order_id) is ClientOrderId
        if self.fail == "position_id":
            raise RuntimeError("SECRET provider payload")
        return self.links.get(str(client_order_id))

    def orders(self) -> list[object]:
        if self.fail == "orders":
            raise RuntimeError("SECRET provider payload")
        return list(self.entries.values())

    def __getattr__(self, name: str) -> Any:
        if name in {"submit", "cancel", "submit_order", "cancel_order", "load_cache",
                    "add_order", "build_index", "reconcile"}:
            def forbidden(*args: object, **kwargs: object) -> None:
                self.effects.append(name)
                raise AssertionError("recovery must read only")
            return forbidden
        raise AttributeError(name)


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch) -> Any:
    recovery = api()
    native_loader = recovery._native_types
    monkeypatch.setattr(recovery, "_native_types", lambda: TYPES)
    bindings = NativeBindings(InstrumentId, ClientOrderId, Decimal, Decimal,
        lambda side: OrderSide[side])
    expected = recovery.OwnedCacheExpectation(request(), "BYBIT-UNIFIED", "TRADER-001",
        "Strategy-000", "venue-1", NETTING_ID)
    checkpoint = recovery.RecoveryCheckpoint(SCOPE, "generation-1", 4, "digest-1", "1.231.0",
        "TRADER-001", "Strategy-000")
    order = LimitOrder(client_order_id=ClientOrderId(CLIENT_ID), instrument_id=InstrumentId(INSTRUMENT),
        trader_id=TraderId("TRADER-001"), strategy_id=StrategyId("Strategy-000"),
        account_id=AccountId("BYBIT-UNIFIED"), venue_order_id=VenueOrderId("venue-1"),
        side=OrderSide.BUY, order_type=OrderType.LIMIT, quantity=Decimal("0.010"),
        price=Decimal("50000.0"), is_reduce_only=False, status=OrderStatus.ACCEPTED,
        is_closed=False, is_pending_cancel=False)
    cache = ReadOnlyCache([order], {CLIENT_ID: PositionId(NETTING_ID)})
    return SimpleNamespace(api=recovery, bindings=bindings, expected=expected,
        checkpoint=checkpoint, cache=cache, order=order, native_loader=native_loader)


def inspect(setup: Any, **changes: Any) -> Any:
    arguments = {"cache": setup.cache, "bindings": setup.bindings,
        "expectations": (setup.expected,), "application_checkpoint": setup.checkpoint,
        "cache_checkpoint": setup.checkpoint,
        "observed_revision_before": 4, "observed_revision_after": 4}
    arguments.update(changes)
    result = setup.api.inspect_native_recovery(**arguments)
    assert result.production_supported is False
    assert setup.cache.effects == []
    assert "SECRET" not in repr(result)
    assert result.status == ("BLOCKED" if result.issues else "CONSISTENT")
    return result


def codes(result: Any) -> set[str]:
    return {issue.code for issue in result.issues}


def test_exact_cache_agreement_is_only_diagnostic(setup: Any) -> None:
    result = inspect(setup)
    assert result.status == "CONSISTENT"
    assert result.issues == ()
    for name in ("recovered", "ready", "qualified", "authenticated", "allow_execution"):
        assert not hasattr(result, name)
    with pytest.raises((AttributeError, TypeError)):
        result.production_supported = True


def test_prefill_absent_link_is_consistent_without_substituting_logical_position(setup: Any) -> None:
    setup.cache.links.clear()
    result = inspect(setup, expectations=(replace(setup.expected,
        expected_native_position_id=None),))
    assert result.status == "CONSISTENT"


def test_missing_order_and_partial_restore_are_reported_per_owned_id(setup: Any) -> None:
    second = replace(setup.expected, request=replace(request(), owned=replace(request().owned,
        client_order_id="ec_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")))
    result = inspect(setup, expectations=(setup.expected, second))
    assert "MISSING_CACHED_ORDER" in codes(result)
    assert {item.client_order_id for item in result.issues} == {second.request.owned.client_order_id}


@pytest.mark.parametrize("field,value", [
    ("client_order_id", ClientOrderId("ec_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")),
    ("instrument_id", InstrumentId("ETHUSDT-LINEAR.BYBIT")),
    ("trader_id", TraderId("OTHER-001")), ("strategy_id", StrategyId("Other-000")),
    ("account_id", AccountId("OTHER-ACCOUNT")), ("venue_order_id", VenueOrderId("other-venue")),
    ("venue_order_id", None), ("side", OrderSide.SELL), ("quantity", Decimal("0.02")),
    ("price", Decimal(50001)), ("is_reduce_only", True), ("order_type", OrderType.MARKET),
    ("client_order_id", CLIENT_ID), ("instrument_id", INSTRUMENT),
    ("account_id", "BYBIT-UNIFIED"), ("trader_id", "TRADER-001"),
    ("strategy_id", "Strategy-000"), ("quantity", "0.010"), ("price", "50000.0"),
])
def test_exact_native_identity_and_economics_required(setup: Any, field: str, value: Any) -> None:
    setattr(setup.order, field, value)
    assert "CACHED_ORDER_IDENTITY_MISMATCH" in codes(inspect(setup))


@pytest.mark.parametrize("field,value,code", [
    ("status", OrderStatus.INITIALIZED, "INITIALIZED_CACHED_PLACEHOLDER"),
    ("status", OrderStatus.CANCELED, "CACHED_ORDER_NOT_ACTIVE"),
    ("status", "ACCEPTED", "CACHED_ORDER_NOT_ACTIVE"),
    ("is_closed", True, "CACHED_ORDER_NOT_ACTIVE"),
    ("is_pending_cancel", True, "CACHED_ORDER_NOT_ACTIVE"),
])
def test_initialized_or_inactive_order_cannot_look_recovered(
    setup: Any, field: str, value: Any, code: str,
) -> None:
    setattr(setup.order, field, value)
    assert code in codes(inspect(setup))


def test_structural_order_lookalike_is_not_native(setup: Any) -> None:
    setup.cache.entries[CLIENT_ID] = SimpleNamespace(**vars(setup.order))
    assert "CACHED_ORDER_TYPE_MISMATCH" in codes(inspect(setup))


@pytest.mark.parametrize("link", [PositionId("position-1"), PositionId("wrong-Strategy-000"),
    NETTING_ID, None])
def test_native_position_link_requires_exact_netting_and_persisted_mapping(
    setup: Any, link: object,
) -> None:
    setup.cache.links[CLIENT_ID] = link
    result = inspect(setup)
    assert ("MISSING_EXPECTED_POSITION_LINK" if link is None else
        "NATIVE_POSITION_ID_MISMATCH") in codes(result)


def test_logical_position_id_is_never_a_permitted_persisted_native_id(setup: Any) -> None:
    result = inspect(setup, expectations=(replace(setup.expected,
        expected_native_position_id="position-1"),))
    assert "NATIVE_POSITION_ID_MISMATCH" in codes(result)


@pytest.mark.parametrize("field,value", [
    ("scope", type(SCOPE)("other", "DEMO")), ("generation_id", "other-generation"),
    ("journal_revision", 5), ("ownership_digest", "other-digest"),
    ("trader_id", "OTHER-001"), ("strategy_id", "Other-000"),
])
def test_every_checkpoint_dimension_must_agree(setup: Any, field: str, value: Any) -> None:
    result = inspect(setup, cache_checkpoint=replace(setup.checkpoint, **{field: value}))
    assert "RECOVERY_CHECKPOINT_MISMATCH" in codes(result)


def test_missing_native_checkpoint_blocks_even_exact_cache(setup: Any) -> None:
    assert "MISSING_NATIVE_CHECKPOINT" in codes(inspect(setup, cache_checkpoint=None))


def test_equal_labels_do_not_override_wrong_native_version(setup: Any) -> None:
    checkpoint = replace(setup.checkpoint, native_version="1.230.0")
    result = inspect(setup, application_checkpoint=checkpoint, cache_checkpoint=checkpoint)
    assert "NATIVE_VERSION_MISMATCH" in codes(result)


def test_expectations_must_match_checkpoint_scope_and_owner(setup: Any) -> None:
    expected = replace(setup.expected, strategy_id="Other-000",
        request=replace(request(), scope=type(SCOPE)("other", "DEMO")))
    assert "EXPECTATION_CHECKPOINT_MISMATCH" in codes(inspect(setup, expectations=(expected,)))


def test_duplicate_contradictory_expectations_do_not_hide_either_mismatch(setup: Any) -> None:
    conflict = replace(setup.expected, native_account_id="OTHER-ACCOUNT")
    first = inspect(setup, expectations=(setup.expected, conflict))
    second = inspect(setup, expectations=(conflict, setup.expected))
    assert first == second
    assert "CONTRADICTORY_OWNED_EXPECTATIONS" in codes(first)
    assert "CACHED_ORDER_IDENTITY_MISMATCH" in codes(first)


def test_additional_manual_cache_order_is_unowned_and_redacted(setup: Any) -> None:
    manual = LimitOrder(**vars(setup.order))
    manual.client_order_id = ClientOrderId("SECRET-manual-id")
    setup.cache.entries["manual"] = manual
    result = inspect(setup)
    assert "UNOWNED_CACHED_ORDER" in codes(result)
    assert next(i for i in result.issues if i.code == "UNOWNED_CACHED_ORDER").client_order_id is None


@pytest.mark.parametrize("accessor", ["order", "position_id", "orders"])
def test_accessor_exceptions_are_blocked_with_redacted_diagnostics(setup: Any, accessor: str) -> None:
    setup.cache.fail = accessor
    assert "NATIVE_CACHE_READ_FAILED" in codes(inspect(setup))


def test_revision_change_blocks_and_keeps_other_diagnostics(setup: Any) -> None:
    setup.order.account_id = AccountId("OTHER-ACCOUNT")
    result = inspect(setup, observed_revision_after=5, cache_checkpoint=None)
    assert codes(result) >= {"INSPECTION_REVISION_CHANGED", "MISSING_NATIVE_CHECKPOINT",
        "CACHED_ORDER_IDENTITY_MISMATCH"}
    assert result.issues == tuple(sorted(result.issues,
        key=lambda issue: (issue.client_order_id or "", issue.code)))


@pytest.mark.parametrize("revision", [-1, True, "4"])
def test_invalid_revision_is_redacted_and_cannot_equal_a_valid_snapshot(
    setup: Any, revision: Any,
) -> None:
    assert "INVALID_INSPECTION_REVISION" in codes(inspect(setup,
        observed_revision_before=revision, observed_revision_after=revision))


def test_recovery_inspector_exists_without_native_imports() -> None:
    assert callable(api().inspect_native_recovery)


def test_market_exit_uses_exact_class_side_and_reduce_only(setup: Any) -> None:
    owned = replace(request().owned, role="EXIT", side="SELL", reduce_only=True)
    setup.expected = replace(setup.expected, request=request(owned=owned, order_type="MARKET",
        limit_price=None, attached=None))
    order = MarketOrder(**vars(setup.order))
    order.side, order.order_type, order.price, order.is_reduce_only = (
        OrderSide.SELL, OrderType.MARKET, None, True)
    setup.cache.entries[CLIENT_ID] = order
    assert inspect(setup).status == "CONSISTENT"
    order.is_reduce_only = False
    assert "CACHED_ORDER_IDENTITY_MISMATCH" in codes(inspect(setup))


def test_cache_inventory_cannot_omit_the_owned_lookup(setup: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(setup.cache, "orders", list)
    assert "NATIVE_CACHE_INDEX_MISMATCH" in codes(inspect(setup))


def test_cache_inventory_duplicate_is_a_conflict(setup: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(setup.cache, "orders", lambda: [setup.order, setup.order])
    assert "NATIVE_CACHE_INDEX_MISMATCH" in codes(inspect(setup))


def test_unavailable_native_types_never_produce_consistency(setup: Any, monkeypatch: Any) -> None:
    def unavailable() -> None:
        raise ImportError("SECRET local/provider text")
    monkeypatch.setattr(setup.api, "_native_types", unavailable)
    assert "NATIVE_BINDINGS_UNAVAILABLE" in codes(inspect(setup))


@pytest.mark.parametrize("field,value", [("journal_revision", True), ("journal_revision", -1),
    ("generation_id", ""), ("ownership_digest", " " * 257), ("trader_id", None)])
def test_invalid_checkpoint_data_is_rejected_with_fixed_error(
    setup: Any, field: str, value: Any,
) -> None:
    with pytest.raises(ValueError, match="^INVALID_RECOVERY_RECORD$"):
        replace(setup.checkpoint, **{field: value})
    assert setup.cache.effects == []


def test_native_version_mismatch_is_distinct_from_missing_dependency(
    setup: Any, monkeypatch: Any,
) -> None:
    monkeypatch.setattr(setup.api, "_native_types", setup.native_loader)
    monkeypatch.setattr(setup.api, "version", lambda _: "1.230.0")
    assert "NATIVE_VERSION_MISMATCH" in codes(inspect(setup))
