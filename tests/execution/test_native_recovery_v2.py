"""Portable V2 restore boundaries; explicit fake selections, never SDK authority."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest
import test_bybit_fixed_limit as a
import test_native_recovery as f

from multi_exchange_trader.execution import bybit_native as n
from multi_exchange_trader.execution import native_recovery as recovery
from multi_exchange_trader.execution import native_restart_codec as codec
from multi_exchange_trader.execution.native_restart_store import (
    NativeRestartError,
    NativeRestartIdentity,
)


def test_exact_v2_expectation_is_accepted_without_sdk_import() -> None:
    request = a.request()
    expected = recovery.OwnedCacheExpectation(request, 'BYBIT-UNIFIED', 'TRADER-001',
        'STRATEGY-001', None, None)
    assert expected.request is request
    assert request.owned.quantity.as_tuple() == expected.request.owned.quantity.as_tuple()


def test_seed_strategy_getter_cannot_select_a_new_submit_owner() -> None:
    strategy: Any = None
    class MutatingSeed(SimpleNamespace):
        @property
        def strategy_id(self) -> str:
            strategy.id = 'OTHER-STRATEGY-001'
            return str(strategy.id)
    native = n.NativeBindings(str, str, str, str, str,
        n.FixedLimitBindingsV2(a.Limit, MutatingSeed, a.Updated, a.Kind.LIMIT, a.Tif.IOC))
    bridge, factory, strategy = a.setup(native)
    def transform(order: Any) -> None:
        values = vars(order.events[0]).copy()
        values.pop('strategy_id')
        order.events[0] = MutatingSeed(**values)
    factory.transform = transform
    with pytest.raises(ValueError, match='NATIVE_ORDER_BUILD_FAILED'):
        bridge.submit_fixed_limit_v2(a.request())
    assert not strategy.submits and not bridge._submissions


@pytest.fixture
def v2(monkeypatch: pytest.MonkeyPatch) -> Any:
    request = a.request()
    bindings = n.NativeBindings(f.InstrumentId, f.ClientOrderId, Decimal, Decimal,
        lambda side: f.OrderSide[side], n.FixedLimitBindingsV2(a.Limit, a.Init, a.Updated, a.Kind.LIMIT, a.Tif.IOC))
    factory = a.Factory()
    order: Any = factory.limit(instrument_id=f.InstrumentId(request.owned.instrument_id),
        order_side=f.OrderSide.BUY, quantity=request.owned.quantity,
        client_order_id=f.ClientOrderId(a.ID), reduce_only=False, price=request.limit_price,
        time_in_force=a.Tif.IOC)
    for item in (order, order.events[0]):
        item.strategy_id, item.trader_id = f.StrategyId('STRATEGY-001'), f.TraderId('TRADER-001')
    order.account_id, order.venue_order_id = f.AccountId('BYBIT-UNIFIED'), f.VenueOrderId('VENUE-001')
    order.status = f.OrderStatus.ACCEPTED
    expected = recovery.OwnedCacheExpectation(request, 'BYBIT-UNIFIED', 'TRADER-001', 'STRATEGY-001', 'VENUE-001', None)
    checkpoint = recovery.RecoveryCheckpoint(request.scope, 'GENERATION', 0, 'DIGEST', n.NATIVE_VERSION,
        'TRADER-001', 'STRATEGY-001')
    types = {**f.TYPES, 'LimitOrder': a.Limit, 'OrderType': a.Kind, 'OrderInitialized': a.Init}
    monkeypatch.setattr(recovery, '_native_types', lambda: types)
    def loader(*, include_fixed_limit_v2: bool = False) -> n.NativeBindings:
        assert include_fixed_limit_v2 is True
        return bindings
    monkeypatch.setattr(recovery, 'load_native_bindings', loader)
    cache = f.ReadOnlyCache([order])
    return SimpleNamespace(request=request, bindings=bindings, order=order, expected=expected,
        checkpoint=checkpoint, cache=cache, types=types, loader=loader)


def inspect_v2(h: Any, **changes: Any) -> Any:
    arguments = {'cache': h.cache, 'bindings': h.bindings, 'expectations': (h.expected,),
        'application_checkpoint': h.checkpoint, 'cache_checkpoint': h.checkpoint,
        'observed_revision_before': 0, 'observed_revision_after': 0}
    arguments.update(changes)
    result = recovery.inspect_native_recovery(**arguments)
    assert not h.cache.effects and result.production_supported is False
    return result


def test_v2_exact_current_and_original_ioc_seed_are_diagnostic_only(v2: Any) -> None:
    assert inspect_v2(v2).status == 'CONSISTENT'


@pytest.mark.parametrize('part', ['current', 'seed'])
def test_v2_gtc_is_rejected_even_with_matching_current_economics(v2: Any, part: str) -> None:
    target = v2.order if part == 'current' else v2.order.events[0]
    target.time_in_force = a.Tif.GTC
    assert inspect_v2(v2).status == 'BLOCKED'


@pytest.mark.parametrize('mutation', ['parent', 'class', 'converter', 'foreign_enum'])
def test_original_native_binding_selections_survive_last_cache_callback(v2: Any, mutation: str) -> None:
    original = v2.cache.position_id
    def position_id(key: Any) -> Any:
        result = original(key)
        if mutation == 'parent':
            object.__setattr__(v2.bindings, 'fixed_limit_v2', replace(v2.bindings.fixed_limit_v2))
        elif mutation == 'class':
            object.__setattr__(v2.bindings.fixed_limit_v2, 'initialized_event_type', type('ForeignInit', (SimpleNamespace,), {}))
        elif mutation == 'converter':
            object.__setattr__(v2.bindings, 'quantity', lambda text: Decimal(text))
        else:
            from enum import Enum
            ForeignKind = Enum('ForeignKind', {'LIMIT': 'LIMIT'})
            object.__setattr__(v2.bindings.fixed_limit_v2, 'order_type_limit', ForeignKind.LIMIT)
        return result
    v2.cache.position_id = position_id
    assert inspect_v2(v2).status == 'BLOCKED'


@pytest.mark.parametrize(('field', 'value'), [
    ('client_order_id', 'ec_' + 'f' * 32), ('position_id', 'OTHER_POSITION'),
    ('instrument_id', 'OTHERUSDT-LINEAR.BYBIT'), ('side', 'SELL'), ('role', 'EXIT'),
    ('quantity', Decimal('1.0')), ('reduce_only', True), ('generation', 1),
    ('policy_revision', 'OTHER_POLICY'), ('trigger_price', Decimal(90)), ('trigger_source', 'MarkPrice'),
])
def test_all_original_owned_fields_are_pinned_across_callbacks(v2: Any, field: str, value: Any) -> None:
    original = v2.cache.orders
    def orders() -> list[Any]:
        object.__setattr__(v2.request.owned, field, value)
        return cast(list[Any], original())
    v2.cache.orders = orders
    result = inspect_v2(v2)
    assert result.status == 'BLOCKED'
    assert all(issue.client_order_id in (None, a.ID) for issue in result.issues)


@pytest.mark.parametrize('field', ['product_scope', 'account', 'envelope', 'risk_scope', 'instrument', 'strategy', 'instrument_pin', 'pin_key', 'attached'])
def test_equal_looking_known_record_replacements_cannot_rebase_expected_refs(v2: Any, field: str) -> None:
    original = v2.cache.orders
    def orders() -> list[Any]:
        request = v2.request
        if field == 'product_scope':
            object.__setattr__(request, 'product_scope', replace(request.product_scope))
        elif field == 'account':
            object.__setattr__(request.product_scope, 'account', replace(request.scope))
        elif field == 'envelope':
            object.__setattr__(request, 'envelope', replace(request.envelope))
        elif field == 'risk_scope':
            object.__setattr__(request.envelope, 'scope', replace(request.envelope.scope))
        elif field in ('instrument', 'strategy'):
            object.__setattr__(request.envelope.scope, field, replace(getattr(request.envelope.scope, field)))
        elif field == 'instrument_pin':
            object.__setattr__(request.envelope, 'instrument_pin', replace(request.envelope.instrument_pin))
        elif field == 'pin_key':
            object.__setattr__(request.envelope.instrument_pin, 'key', replace(request.envelope.instrument_pin.key))
        else:
            object.__setattr__(request, 'attached', replace(request.attached))
        return cast(list[Any], original())
    v2.cache.orders = orders
    assert inspect_v2(v2).status == 'BLOCKED'


@pytest.mark.parametrize('field', ['native_account_id', 'trader_id', 'strategy_id', 'venue_order_id', 'expected_native_position_id'])
def test_original_owner_metadata_is_not_rebased(v2: Any, field: str) -> None:
    original = v2.cache.position_id
    def position_id(key: Any) -> Any:
        result = original(key)
        object.__setattr__(v2.expected, field, 'CHANGED_SAFE_LABEL')
        return result
    v2.cache.position_id = position_id
    result = inspect_v2(v2)
    assert result.status == 'BLOCKED' and all(issue.client_order_id in (None, a.ID) for issue in result.issues)


def test_duplicate_v2_quantity_representations_are_conflicting_expectations(v2: Any) -> None:
    duplicate = replace(v2.expected, request=replace(v2.request, owned=replace(v2.request.owned, quantity=Decimal('1.0'))))
    result = inspect_v2(v2, expectations=(v2.expected, duplicate))
    assert 'CONTRADICTORY_OWNED_EXPECTATIONS' in {issue.code for issue in result.issues}


def test_account_domain_and_product_literal_keep_the_accepted_scope_contract() -> None:
    from multi_exchange_trader.trading_product import TradingProduct
    request = a.request()
    product_label: TradingProduct = 'LINEAR'
    product = replace(request.product_scope, account=replace(request.scope, account_id='A' * 256), product=product_label)
    envelope = replace(request.envelope, scope=replace(request.envelope.scope, product_scope=product))
    request = replace(request, product_scope=product, envelope=envelope)
    expected = recovery.OwnedCacheExpectation(request, 'BYBIT-UNIFIED', 'TRADER-001', 'STRATEGY-001', None, None)
    assert type(expected.request) is n.LinearFixedLimitNativeOrderRequestV2
    assert expected.request.scope.account_id == 'A' * 256
    assert expected.request.product_scope.product == product_label


@pytest.mark.parametrize(('field', 'value'), [('quantity', '1'), ('generation', True), ('reduce_only', 0), ('policy_revision', 7)])
def test_invalid_v2_leaf_construction_retains_fixed_error(field: str, value: Any) -> None:
    request = a.request()
    object.__setattr__(request.owned, field, value)
    with pytest.raises(ValueError, match='^INVALID_RECOVERY_RECORD$'):
        recovery.OwnedCacheExpectation(request, 'BYBIT-UNIFIED', 'TRADER-001', 'STRATEGY-001', None, None)


def test_subclass_request_and_expectation_do_not_enter_v2_or_legacy_paths(v2: Any) -> None:
    class ForeignExpectation(recovery.OwnedCacheExpectation):
        pass
    with pytest.raises(ValueError, match='INVALID_RECOVERY_RECORD'):
        ForeignExpectation(v2.request, 'BYBIT-UNIFIED', 'TRADER-001', 'STRATEGY-001', None, None)
    object.__setattr__(v2.expected, 'request', object())
    with pytest.raises(ValueError, match='^INVALID_RECOVERY_INPUT$'):
        inspect_v2(v2)


@pytest.mark.parametrize('phase', ['sdk', 'target', 'candidate'])
def test_install_callback_mutation_is_blocked_before_target_population(v2: Any, monkeypatch: pytest.MonkeyPatch, phase: str) -> None:
    prepared, owner = prepared_empty(v2, monkeypatch)
    target = EmptyCache()
    original_sdk, original_instruments = codec._sdk, EmptyCache.instruments
    def mutate() -> None:
        object.__setattr__(owner, 'request', replace(owner.request))
    if phase == 'sdk':
        def sdk() -> Any:
            result = original_sdk()
            mutate()
            return result
        monkeypatch.setattr(codec, '_sdk', sdk)
    else:
        def instruments(cache: EmptyCache) -> list[Any]:
            result = original_instruments(cache)
            if (cache is target) == (phase == 'target'):
                mutate()
            return result
        monkeypatch.setattr(EmptyCache, 'instruments', instruments)
    with pytest.raises(NativeRestartError):
        codec.install_native_cache(target, prepared)
    assert not target.writes
    if phase == 'sdk':
        assert not target.reads


class Scalar:
    def __init__(self, text: str) -> None:
        self.value = Decimal(text)
    def __str__(self) -> str:
        return format(self.value, 'f')
    def as_decimal(self) -> Decimal:
        return self.value


def test_codec_checks_terminal_ioc_seed_and_partial_fill_against_original_whole_quantity(v2: Any) -> None:
    sdk = {**v2.types, **{name: type(name, (SimpleNamespace,), {}) for name in codec._ORDER_EVENTS if name != 'OrderInitialized'}}
    v2.order.quantity = v2.order.events[0].quantity = Scalar('1.00')
    v2.order.price = Scalar('100.00')
    fill = sdk['OrderFilled'](trader_id=v2.order.trader_id, strategy_id=v2.order.strategy_id,
        instrument_id=v2.order.instrument_id, client_order_id=v2.order.client_order_id,
        account_id=v2.order.account_id, venue_order_id=v2.order.venue_order_id,
        order_side=v2.order.side, order_type=v2.order.order_type,
        last_qty=Scalar('0.400'), last_px=Scalar('100.00'))
    cancel = sdk['OrderCanceled'](trader_id=v2.order.trader_id, strategy_id=v2.order.strategy_id,
        instrument_id=v2.order.instrument_id, client_order_id=v2.order.client_order_id,
        account_id=v2.order.account_id, venue_order_id=v2.order.venue_order_id)
    v2.order.events.extend((fill, cancel))
    v2.order.status, v2.order.is_closed = f.OrderStatus.CANCELED, True
    fixed = recovery._pin_bindings_v2(v2.bindings)
    codec._validate_order(v2.order, v2.expected, sdk, fixed)
    assert fill.last_qty.as_decimal() == Decimal('0.400') and v2.request.owned.quantity == Decimal(1)
    v2.order.events[0].time_in_force = a.Tif.GTC
    with pytest.raises(ValueError, match='NATIVE_ORDER_CHANGED'):
        codec._validate_order(v2.order, v2.expected, sdk, fixed)


def test_cached_cancel_seed_getter_drift_has_no_handoff() -> None:
    armed = [False]
    strategy: Any = None
    class Seed(SimpleNamespace):
        @property
        def strategy_id(self) -> str:
            if armed[0]:
                strategy.id = 'OTHER-STRATEGY-001'
            return str(strategy.id)
    bindings = n.NativeBindings(str, str, str, str, str,
        n.FixedLimitBindingsV2(a.Limit, Seed, a.Updated, a.Kind.LIMIT, a.Tif.IOC))
    bridge, factory, strategy = a.setup(bindings)
    def transform(order: Any) -> None:
        values = vars(order.events[0]).copy()
        values.pop('strategy_id')
        order.events[0] = Seed(**values)
    factory.transform = transform
    bridge.submit_fixed_limit_v2(a.request())
    a.activate(factory)
    assert bridge.cached_owned_order(a.ID) is factory.order
    armed[0] = True
    with pytest.raises(ValueError, match='NATIVE_ORDER_CHANGED'):
        bridge.cancel_owned(a.ID)
    assert not strategy.cancels and not bridge._cancellations and len(strategy.submits) == 1


@pytest.mark.parametrize('message,code', [('PINNED_NATIVE_VERSION_REQUIRED', 'NATIVE_VERSION_MISMATCH'), ('PRIVATE_PROVIDER_DETAIL', 'NATIVE_BINDINGS_UNAVAILABLE')])
def test_v2_loader_failure_has_fixed_error_and_never_legacy_fallback(v2: Any, monkeypatch: pytest.MonkeyPatch, message: str, code: str) -> None:
    def loader(*, include_fixed_limit_v2: bool = False) -> Any:
        assert include_fixed_limit_v2 is True
        raise ValueError(message)
    monkeypatch.setattr(recovery, 'load_native_bindings', loader)
    result = inspect_v2(v2)
    assert result.status == 'BLOCKED' and code in {issue.code for issue in result.issues}
    assert 'PRIVATE_PROVIDER_DETAIL' not in repr(result)


def test_valid_prepared_v2_uses_detached_bytes_and_new_loader_records_without_resubmission(v2: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def loader(*, include_fixed_limit_v2: bool = False) -> n.NativeBindings:
        assert include_fixed_limit_v2 is True
        return cast(n.NativeBindings, replace(v2.bindings, fixed_limit_v2=replace(v2.bindings.fixed_limit_v2)))
    v2.loader = loader
    prepared, owner = prepared_empty(v2, monkeypatch)
    assert prepared._v2_pins is not None and type(prepared._payload) is bytes
    target = EmptyCache()
    result = codec.install_native_cache(target, prepared)
    assert result.production_supported is False and result.missing_owned_orders == 1
    assert result.orders == result.positions == 0 and target.writes == ['build_index']
    assert prepared._owners[0] is owner and owner.request is v2.request


def test_actual8_pure_graph_recipe_registers_true_j_without_importing_native_module(tmp_path: Any) -> None:
    import ast
    from pathlib import Path

    from test_reservations_v2 import bound, fixture_values, register, snapshot
    from test_runtime import SCOPE

    from multi_exchange_trader.execution import linear_risk_evidence_v2 as e
    from multi_exchange_trader.execution import risk_v2_contracts as c
    from multi_exchange_trader.execution.journal import CommandJournal, ExecutionScope
    from multi_exchange_trader.execution.risk_v2_codec import encode_entry_intent_v2

    # Execute only the pure linked-graph factory; never import or collect actual8.
    source = Path(__file__).with_name('test_bybit_fixed_limit_linux.py')
    definition = next(node for node in ast.parse(source.read_text(encoding='utf-8')).body
        if isinstance(node, ast.FunctionDef) and node.name == '_durable_values')
    namespace: dict[str, Any] = {'Any': Any, 'replace': replace, 'fixture_values': fixture_values,
        'SCOPE': SCOPE, 'c': c, 'e': e, 'encode_entry_intent_v2': encode_entry_intent_v2}
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(source), 'exec'), namespace)  # noqa: S102 - trusted local pure fixture only; no module/SDK execution
    values = namespace['_durable_values']()
    assert values[0].envelope.scope.instrument.symbol == 'BTCUSDT'
    assert values[0].envelope.scope.product_scope.account == SCOPE
    with CommandJournal(tmp_path / 'execution.sqlite3', ExecutionScope(SCOPE.account_id, SCOPE.environment)) as journal:
        bound(journal)
        record = register(journal, values)
        held = snapshot(journal, values[0])
        assert record.reservation_v2 is not None and record.payload_json == encode_entry_intent_v2(values[0])
        assert held.held_quantity_sha256 and record.client_order_id.startswith('ec_')


@pytest.mark.parametrize('change', ['quantity_scale', 'request_clone', 'scope_clone', 'native_metadata'])
def test_cache_callback_cannot_rebase_original_v2_expectation(v2: Any, change: str) -> None:
    original = v2.cache.orders
    def orders() -> list[Any]:
        if change == 'quantity_scale':
            object.__setattr__(v2.request.owned, 'quantity', Decimal('1.0'))
        elif change == 'request_clone':
            object.__setattr__(v2.expected, 'request', replace(v2.request))
        elif change == 'scope_clone':
            object.__setattr__(v2.request, 'product_scope', replace(v2.request.product_scope))
        else:
            object.__setattr__(v2.expected, 'expected_native_position_id', v2.request.owned.instrument_id + '-STRATEGY-001')
        return cast(list[Any], original())
    v2.cache.orders = orders
    result = inspect_v2(v2)
    assert result.status == 'BLOCKED'
    assert all(issue.client_order_id in (None, a.ID) for issue in result.issues)


class EmptyCache:
    def __init__(self) -> None:
        self.writes: list[str] = []
        self.reads: list[str] = []
    def orders_total_count(self) -> int: return 0
    def positions_total_count(self) -> int: return 0
    def instruments(self) -> list[Any]: self.reads.append('instruments'); return []
    def orders(self) -> list[Any]: self.reads.append('orders'); return []
    def positions(self) -> list[Any]: self.reads.append('positions'); return []
    def position_snapshot_ids(self) -> set[Any]: return set()
    def client_order_ids(self) -> set[Any]: return set()
    def position_ids(self) -> set[Any]: return set()
    def accounts(self) -> list[Any]: return []
    def check_integrity(self) -> bool: return True
    def build_index(self) -> None: self.writes.append('build_index')


def prepared_empty(v2: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    sdk = {**v2.types, 'Cache': EmptyCache}
    monkeypatch.setattr(codec, '_sdk', lambda: sdk)
    monkeypatch.setattr(codec, 'load_native_bindings', v2.loader, raising=False)
    owner = replace(v2.expected, venue_order_id=None)
    identity = NativeRestartIdentity(v2.request.scope, 'BYBIT-UNIFIED', 'TRADER-001', 'STRATEGY-001',
        (v2.request.owned.instrument_id,))
    payload = {'schema': 1, 'encoding': 'msgpack-base64', 'instruments': [], 'orders': [], 'positions': []}
    return codec.prepare_native_cache(payload, identity=identity, owners=(owner,),
        allowed_missing_ids=frozenset({a.ID})), owner


@pytest.mark.parametrize('change', ['quantity_scale', 'request_clone', 'owner_clone', 'allowed_clone'])
def test_install_uses_prepare_time_v2_pin_before_sdk_or_target_access(
    v2: Any, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    prepared, owner = prepared_empty(v2, monkeypatch)
    target = EmptyCache()
    if change == 'quantity_scale':
        object.__setattr__(owner.request.owned, 'quantity', Decimal('1.0'))
    elif change == 'request_clone':
        object.__setattr__(owner, 'request', replace(owner.request))
    elif change == 'owner_clone':
        object.__setattr__(prepared, '_owners', (replace(owner),))
    else:
        object.__setattr__(prepared, '_allowed_missing_ids', frozenset([a.ID]))
    with pytest.raises(NativeRestartError, match='NATIVE_RESTART_BLOCKED'):
        codec.install_native_cache(target, prepared)
    assert not target.writes and not target.reads


def test_last_binding_member_getter_cannot_mutate_original_expected_scope(v2: Any) -> None:
    armed = [False]
    class Kind:
        LIMIT: Any
        @property
        def name(self) -> str:
            if armed[0]:
                armed[0] = False
                object.__setattr__(v2.request, 'product_scope', replace(v2.request.product_scope))
            return 'LIMIT'
    Kind.LIMIT = Kind()
    object.__setattr__(v2.bindings, 'fixed_limit_v2', n.FixedLimitBindingsV2(
        a.Limit, a.Init, a.Updated, Kind.LIMIT, a.Tif.IOC))
    v2.types['OrderType'] = Kind
    v2.order.order_type = v2.order.events[0].order_type = Kind.LIMIT
    original = v2.cache.position_id
    def position_id(key: Any) -> Any:
        value = original(key)
        armed[0] = True
        return value
    v2.cache.position_id = position_id
    assert inspect_v2(v2).status == 'BLOCKED'
