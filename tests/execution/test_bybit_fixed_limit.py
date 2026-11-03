"""Independent fake-only tests. No native dependency, provider, or activation."""
from __future__ import annotations

import builtins
import importlib
from collections.abc import Callable
from dataclasses import fields, replace
from decimal import Decimal
from enum import IntEnum
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest
from risk_v2_support import envelope_v2

from multi_exchange_trader.execution import bybit_native as n
from multi_exchange_trader.execution.native_events import NativeEventContext, NativeEventDecoder
from multi_exchange_trader.execution.protection import OwnedOrder

ID = 'ec_0123456789abcdef0123456789abcdef'
OTHER = 'ec_ffffffffffffffffffffffffffffffff'

class Kind(IntEnum):
    MARKET = 1
    LIMIT = 2

class Tif(IntEnum):
    GTC = 1
    IOC = 2

class Trigger(IntEnum):
    NO_TRIGGER = 0
    DEFAULT = 1

class Contingency(IntEnum):
    NO_CONTINGENCY = 0
    OCO = 1

class Status(IntEnum):
    INITIALIZED = 0
    ACCEPTED = 1

class Init(SimpleNamespace):
    pass

class Updated(SimpleNamespace):
    pass

class Limit(SimpleNamespace):
    pass

class Factory:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.order: Any = None
        self.callback: Any = None
        self.transform: Any = None

    def market(self, **kwargs: Any) -> object:
        raise AssertionError('MARKET ENTRY IS FORBIDDEN')

    def limit(self, *, instrument_id: object, order_side: object, quantity: object,
              client_order_id: object, reduce_only: bool, price: object,
              time_in_force: object = Tif.GTC) -> object:
        kwargs = {'instrument_id': instrument_id, 'order_side': order_side, 'quantity': quantity,
                      'client_order_id': client_order_id, 'reduce_only': reduce_only, 'price': price,
                      'time_in_force': time_in_force}
        self.calls.append(kwargs)
        common = {'instrument_id': instrument_id, 'side': order_side, 'quantity': quantity,
                      'client_order_id': client_order_id, 'strategy_id': 'STRATEGY-001',
                      'trader_id': 'TRADER-001', 'order_type': Kind.LIMIT, 'time_in_force': time_in_force,
                      'emulation_trigger': Trigger.NO_TRIGGER,
                      'contingency_type': Contingency.NO_CONTINGENCY, 'order_list_id': None,
                      'linked_order_ids': None, 'parent_order_id': None, 'exec_algorithm_id': None,
                      'exec_spawn_id': None}
        seed = Init(**common, post_only=False, reduce_only=reduce_only, quote_quantity=False,
                    options={'price': str(price), 'display_qty': None, 'expire_time_ns': 0})
        order = Limit(**common, price=price, is_post_only=False, is_reduce_only=reduce_only,
                      is_quote_quantity=False, display_qty=None, expire_time_ns=0,
                      account_id=None, venue_order_id=None, status=Status.INITIALIZED,
                      is_closed=False, is_pending_cancel=False, events=[seed])
        self.order = order
        if self.callback:
            self.callback()
        if self.transform:
            self.transform(order)
        return order

class Strategy:
    def __init__(self, factory: Factory) -> None:
        self.id = 'STRATEGY-001'
        self.submits: list[tuple[Any, dict[str, object]]] = []
        self.cancels: list[tuple[Any, dict[str, object]]] = []
        self.submit_callback: Any = None
        self.cancel_callback: Any = None
        self.cache_callback: Any = None
        def lookup(key: object) -> Any:
            if self.cache_callback:
                self.cache_callback()
            return factory.order if str(key) == ID else None
        self.cache = SimpleNamespace(order=lookup)

    def submit_order(self, order: object, *, params: dict[str, object]) -> None:
        self.submits.append((order, params))
        if self.submit_callback:
            self.submit_callback()

    def cancel_order(self, order: object, *, params: dict[str, object]) -> None:
        self.cancels.append((order, params))
        if self.cancel_callback:
            self.cancel_callback()


def request(side: str = 'BUY') -> Any:
    env = replace(envelope_v2(), side=cast(Literal['BUY', 'SELL'], side))
    owned = OwnedOrder(ID, 'POSITION-001', 'XAAUSDT-LINEAR.BYBIT', side, 'ENTRY',  # type: ignore[arg-type]
                       Decimal(1), False, 0, 'FIXTURE_POLICY_V1')
    stop, tp = (90, 120) if side == 'BUY' else (120, 90)
    return n.LinearFixedLimitNativeOrderRequestV2(env.scope.product_scope, owned, env,
               n.AttachedProtection(Decimal(stop), Decimal(tp), 'MarkPrice'), 0)


def setup(native: Any = None) -> tuple[Any, Factory, Strategy]:
    factory = Factory()
    strategy = Strategy(factory)
    if native is None:
        native = n.NativeBindings(str, str, str, str, str,
            n.FixedLimitBindingsV2(Limit, Init, Updated, Kind.LIMIT, Tif.IOC))
    bridge = n.NativeBybitBridge(scope=envelope_v2().scope.product_scope.account,
        native_account_id='BYBIT-UNIFIED', factory=factory, strategy=strategy, bindings=native)
    return bridge, factory, strategy


def activate(factory: Factory) -> None:
    factory.order.account_id = 'BYBIT-UNIFIED'
    factory.order.venue_order_id = 'VENUE-001'
    factory.order.status = Status.ACCEPTED


def legacy(r: Any) -> n.NativeOrderRequest:
    return n.NativeOrderRequest(r.scope, r.owned, 'LIMIT', Decimal(100), Decimal(100), r.attached)


@pytest.mark.parametrize('option', [None, 0, 1, 'False'])
def test_loader_rejects_nonbool_option_before_any_native_lookup(
    monkeypatch: pytest.MonkeyPatch, option: Any,
) -> None:
    def forbidden(*args: Any) -> Any:
        raise AssertionError('native lookup must not occur')
    monkeypatch.setattr(n, '_require_native_version', forbidden)
    monkeypatch.setattr(importlib, 'import_module', forbidden)
    with pytest.raises(ValueError, match='NATIVE_FIXED_LIMIT_OPTION_REQUIRED'):
        n.load_native_bindings(include_fixed_limit_v2=option)


@pytest.mark.parametrize('explicit', [False, True])
def test_loader_is_lazy_and_legacy_default_has_only_original_three_imports(
    monkeypatch: pytest.MonkeyPatch, explicit: bool,
) -> None:
    calls: list[str] = []
    modules = {
        'identifiers': SimpleNamespace(InstrumentId=SimpleNamespace(from_str=str), ClientOrderId=str),
        'objects': SimpleNamespace(Quantity=SimpleNamespace(from_str=str),
                                   Price=SimpleNamespace(from_str=str)),
        'enums': SimpleNamespace(OrderSide=SimpleNamespace(BUY='BUY', SELL='SELL'),
                                 OrderType=Kind, TimeInForce=Tif),
        'orders': SimpleNamespace(LimitOrder=Limit),
        'order': SimpleNamespace(OrderInitialized=Init, OrderUpdated=Updated),
    }
    def fake_import(name: str) -> Any:
        calls.append(name)
        return modules[name.rsplit('.', 1)[1]]
    monkeypatch.setattr(n, '_require_native_version', lambda: None)
    monkeypatch.setattr(importlib, 'import_module', fake_import)
    value = n.load_native_bindings(include_fixed_limit_v2=True) if explicit else n.load_native_bindings()
    assert calls[:3] == ['nautilus_trader.model.identifiers', 'nautilus_trader.model.objects',
                         'nautilus_trader.model.enums']
    if explicit:
        assert calls[3:] == ['nautilus_trader.model.orders', 'nautilus_trader.model.events.order']
        b, _, _ = setup(value)
        assert b.submit_fixed_limit_v2(request()).state == 'SUBMITTING'
    else:
        assert len(calls) == 3
        assert value.fixed_limit_v2 is None


def test_offline_reload_does_not_import_native(monkeypatch: pytest.MonkeyPatch) -> None:
    original = builtins.__import__
    def guard(name: str, *args: Any, **kwargs: Any) -> Any:
        assert not name.startswith('nautilus_trader')
        return original(name, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(builtins, '__import__', guard)
        importlib.reload(n)


@pytest.mark.parametrize('side,stop,tp', [('BUY', '90', '120'), ('SELL', '120', '90')])
def test_exact_entry_uses_same_writer_fixed_ioc_base_quantity_and_closed_full_params(
    side: str, stop: str, tp: str,
) -> None:
    b, f, s = setup()
    r = request(side)
    assert [field.name for field in fields(type(r))] == [
        'product_scope', 'owned', 'envelope', 'attached', 'position_idx']
    assert [field.name for field in fields(n.FixedLimitBindingsV2)] == [
        'limit_order_type', 'initialized_event_type', 'updated_event_type',
        'order_type_limit', 'time_in_force_ioc']
    result = b.submit_fixed_limit_v2(r)
    assert result.state == 'SUBMITTING'
    assert f.calls == [{'instrument_id': 'XAAUSDT-LINEAR.BYBIT', 'order_side': side,
        'quantity': '1', 'client_order_id': ID, 'reduce_only': False, 'price': '100', 'time_in_force': Tif.IOC}]
    assert s.submits == [(f.order, {'position_idx': 0, 'take_profit': tp, 'stop_loss': stop,
        'tp_trigger_by': 'MarkPrice', 'sl_trigger_by': 'MarkPrice', 'tp_order_type': 'Market',
        'sl_order_type': 'Market', 'tpsl_mode': 'Full'})]
    assert b.submit_fixed_limit_v2(replace(r)) is result
    assert len(s.submits) == len(f.calls) == 1
    assert b.production_supported is False


@pytest.mark.parametrize('version_first', [1, 2])
def test_cross_version_same_id_conflicts_and_legacy_entrypoints_reject_v2(version_first: int) -> None:
    b, _, _ = setup()
    r = request()
    with pytest.raises(ValueError):
        b.register_owned(r)
    with pytest.raises(ValueError):
        b.submit(r)
    if version_first == 1:
        b.register_owned(legacy(r))
        with pytest.raises(ValueError, match='IMMUTABLE_NATIVE_COMMAND_CONFLICT'):
            b.register_fixed_limit_v2(r)
    else:
        b.register_fixed_limit_v2(r)
        with pytest.raises(ValueError, match='IMMUTABLE_NATIVE_COMMAND_CONFLICT'):
            b.register_owned(legacy(r))


@pytest.mark.parametrize('target,field,value', [
    ('owned', 'position_id', 'bad space'), ('owned', 'policy_revision', 'bad space'),
    ('owned', 'position_id', 'x' * 97), ('owned', 'generation', True),
    ('owned', 'generation', -1), ('owned', 'generation', 2 ** 63),
    ('owned', 'side', 'HOLD'), ('owned', 'role', 'EXIT'), ('owned', 'reduce_only', 0),
    ('owned', 'trigger_price', Decimal(90)), ('owned', 'trigger_source', 'MarkPrice'),
    ('owned', 'client_order_id', 'ec_short'), ('owned', 'instrument_id', 'OTHER-LINEAR.BYBIT'),
    ('owned', 'quantity', Decimal(2)), ('owned', 'quantity', Decimal('NaN')),
    ('attached', 'trigger_source', 'other'), ('attached', 'stop_loss', Decimal(101)),
    ('attached', 'take_profit', Decimal(99)), ('request', 'position_idx', False),
    ('request', 'position_idx', 1), ('request', 'attached', None),
    ('envelope', 'time_in_force', 'GTC'), ('envelope', 'limit_price', Decimal('1e1001')),
    ('product_scope', 'product', 'SPOT'), ('account', 'environment', 'LIVE'),
    ('envelope', 'policy_revision', 'OTHER'),
])
def test_forged_or_mutated_request_is_revalidated_before_registration_or_writer(
    target: str, field: str, value: object,
) -> None:
    b, f, s = setup()
    r = request()
    obj = r if target == 'request' else r.product_scope.account if target == 'account' else getattr(r, target)
    object.__setattr__(obj, field, value)
    with pytest.raises(ValueError):
        b.submit_fixed_limit_v2(r)
    assert not f.calls and not s.submits


@pytest.mark.parametrize('component', ['request', 'owned', 'attached', 'envelope', 'bindings'])
def test_subclasses_do_not_supply_exact_contracts(component: str) -> None:
    b, f, s = setup()
    r = request()
    original = r if component == 'request' else b._bindings.fixed_limit_v2 if component == 'bindings' else getattr(r, component)
    subclass = type('Forged', (type(original),), {})
    forged: Any = object.__new__(subclass)
    for field in fields(type(original)):
        object.__setattr__(forged, field.name, getattr(original, field.name))
    if component == 'request':
        r = forged
    elif component == 'bindings':
        b._bindings = replace(b._bindings, fixed_limit_v2=forged)
    else:
        object.__setattr__(r, component, forged)
    with pytest.raises(ValueError):
        b.submit_fixed_limit_v2(r)
    assert not s.submits and not f.calls


@pytest.mark.parametrize('value', [Decimal('1e1001'), Decimal('1e-1001'),
                                  Decimal('1.' + '1' * 256), Decimal('Infinity'), Decimal(0)])
@pytest.mark.parametrize('field', ['quantity', 'limit_price', 'stop_loss', 'take_profit'])
def test_native_numeric_bounds_fail_before_any_format_or_conversion(field: str, value: Decimal) -> None:
    b, f, s = setup()
    r = request()
    if field == 'quantity':
        object.__setattr__(r.owned, 'quantity', value)
        object.__setattr__(r.envelope, 'base_quantity', value)
    elif field == 'limit_price':
        object.__setattr__(r.envelope, field, value)
    else:
        object.__setattr__(r.attached, field, value)
    with pytest.raises(ValueError):
        b.submit_fixed_limit_v2(r)
    assert not f.calls and not s.submits


@pytest.mark.parametrize('which,field,value', [
    ('current', 'order_type', Kind.MARKET), ('seed', 'order_type', Kind.MARKET),
    ('current', 'time_in_force', Tif.GTC), ('seed', 'time_in_force', Tif.GTC),
    ('current', 'order_type', 2), ('seed', 'time_in_force', 2),
    ('current', 'is_post_only', True), ('seed', 'post_only', True),
    ('current', 'is_reduce_only', 0), ('seed', 'reduce_only', 0),
    ('current', 'is_quote_quantity', True), ('seed', 'quote_quantity', True),
    ('current', 'display_qty', 0), ('seed_option', 'display_qty', 0),
    ('current', 'expire_time_ns', False), ('seed_option', 'expire_time_ns', False),
    ('current', 'expire_time_ns', 1), ('seed_option', 'expire_time_ns', 1),
    ('current', 'client_order_id', OTHER), ('seed', 'client_order_id', OTHER),
    ('current', 'instrument_id', 'OTHER'), ('seed', 'instrument_id', 'OTHER'),
    ('current', 'strategy_id', 'FOREIGN'), ('seed', 'strategy_id', 'FOREIGN'),
    ('current', 'trader_id', None), ('seed', 'trader_id', 'FOREIGN'),
    ('current', 'price', '100.1'), ('seed_option', 'price', '100.1'),
    ('current', 'quantity', '1.1'), ('seed', 'quantity', '1.1'),
    ('current', 'side', 'SELL'), ('seed', 'side', 'SELL'),
    ('current', 'account_id', 'BYBIT-UNIFIED'), ('current', 'venue_order_id', 'V1'),
    ('current', 'status', Status.ACCEPTED),
    ('current', 'emulation_trigger', Trigger.DEFAULT), ('seed', 'emulation_trigger', Trigger.DEFAULT),
    ('current', 'contingency_type', Contingency.OCO), ('seed', 'contingency_type', Contingency.OCO),
    ('current', 'order_list_id', 'LIST'), ('seed', 'order_list_id', 'LIST'),
    ('current', 'linked_order_ids', ['OTHER']), ('seed', 'linked_order_ids', ['OTHER']),
    ('current', 'parent_order_id', 'PARENT'), ('seed', 'parent_order_id', 'PARENT'),
    ('current', 'exec_algorithm_id', 'ALGO'), ('seed', 'exec_algorithm_id', 'ALGO'),
    ('current', 'exec_spawn_id', 'SPAWN'), ('seed', 'exec_spawn_id', 'SPAWN'),
])
def test_current_and_seed_native_contract_changes_never_reach_writer(
    which: str, field: str, value: object,
) -> None:
    b, f, s = setup()
    def transform(order: Any) -> None:
        obj = order if which == 'current' else order.events[0]
        if which == 'seed_option':
            obj.options[field] = value
        else:
            setattr(obj, field, value)
    f.transform = transform
    with pytest.raises(ValueError, match='NATIVE_ORDER_BUILD_FAILED'):
        b.submit_fixed_limit_v2(request())
    assert not s.submits


@pytest.mark.parametrize('which,field', [
    ('current', 'is_quote_quantity'), ('seed', 'quote_quantity'), ('current', 'display_qty'),
    ('current', 'expire_time_ns'), ('seed_option', 'display_qty'), ('seed_option', 'expire_time_ns'),
    ('seed_option', 'price'), ('current', 'account_id'), ('current', 'venue_order_id'),
    ('current', 'status'), ('current', 'trader_id'), ('seed', 'trader_id'),
    ('current', 'emulation_trigger'), ('seed', 'emulation_trigger'),
    ('current', 'contingency_type'), ('seed', 'contingency_type'),
    ('current', 'order_list_id'), ('seed', 'order_list_id'),
    ('current', 'linked_order_ids'), ('seed', 'linked_order_ids'),
    ('current', 'parent_order_id'), ('seed', 'parent_order_id'),
    ('current', 'exec_algorithm_id'), ('seed', 'exec_algorithm_id'),
    ('current', 'exec_spawn_id'), ('seed', 'exec_spawn_id'),
])
def test_missing_required_native_attribute_fails_closed(which: str, field: str) -> None:
    b, f, s = setup()
    def transform(order: Any) -> None:
        obj = order if which == 'current' else order.events[0]
        if which == 'seed_option':
            del obj.options[field]
        else:
            delattr(obj, field)
    f.transform = transform
    with pytest.raises(ValueError, match='NATIVE_ORDER_BUILD_FAILED'):
        b.submit_fixed_limit_v2(request())
    assert not s.submits


@pytest.mark.parametrize('change', ['subclass_order', 'subclass_seed', 'two_seeds', 'no_seed'])
def test_build_requires_exact_classes_and_single_initial_seed(change: str) -> None:
    b, f, s = setup()
    def transform(order: Any) -> None:
        if change == 'subclass_order':
            order.__class__ = type('ForeignLimit', (Limit,), {})
        elif change == 'subclass_seed':
            order.events[0].__class__ = type('ForeignInit', (Init,), {})
        elif change == 'two_seeds':
            order.events.append(order.events[0])
        else:
            order.events.clear()
    f.transform = transform
    with pytest.raises(ValueError):
        b.submit_fixed_limit_v2(request())
    assert not s.submits


@pytest.mark.parametrize('field', ['quantity', 'price'])
def test_precision_loss_is_rejected_even_when_factory_returns_that_conversion(field: str) -> None:
    b, _, s = setup()
    b._bindings = replace(b._bindings, **{field: lambda value: '1.1' if field == 'quantity' else '100.1'})
    with pytest.raises(ValueError, match='NATIVE_ORDER_BUILD_FAILED'):
        b.submit_fixed_limit_v2(request())
    assert not s.submits


@pytest.mark.parametrize('mutation', ['request', 'order', 'seed'])
def test_last_protection_conversion_cannot_mutate_already_built_identity(mutation: str) -> None:
    b, f, s = setup()
    r = request()
    def price(value: str) -> str:
        if value == '120':
            if mutation == 'request':
                object.__setattr__(r.owned, 'generation', 1)
            elif mutation == 'order':
                f.order.price = '101'
            else:
                f.order.events[0].options['price'] = '101'
        return value
    b._bindings = replace(b._bindings, price=price)
    with pytest.raises(ValueError, match='NATIVE_ORDER_BUILD_FAILED'):
        b.submit_fixed_limit_v2(r)
    assert not s.submits and not b._submissions


@pytest.mark.parametrize('component,field,value', [
    ('owned', 'generation', 1), ('owned', 'quantity', Decimal('1.0')),
    ('attached', 'stop_loss', Decimal(91)), ('envelope', 'limit_price', Decimal('100.0')),
])
def test_original_owner_stays_bound_across_exact_clone_and_valid_forced_mutation(
    component: str, field: str, value: object,
) -> None:
    b, _, _ = setup()
    r = request()
    b.register_fixed_limit_v2(r)
    b.register_fixed_limit_v2(request())
    object.__setattr__(getattr(r, component), field, value)
    with pytest.raises(ValueError, match='IMMUTABLE_NATIVE_COMMAND_CONFLICT'):
        b.register_fixed_limit_v2(request())
    with pytest.raises(ValueError):
        b.cached_owned_order(ID)


def test_original_key_and_mutated_new_key_both_reject_changed_registered_owner() -> None:
    b, _, s = setup()
    r = request()
    b.register_fixed_limit_v2(r)
    object.__setattr__(r.owned, 'client_order_id', OTHER)
    actions: list[Callable[[], Any]] = [lambda: b.submit_fixed_limit_v2(r),
        lambda: b.cached_owned_order(ID), lambda: b.cancel_owned(ID)]
    for action in actions:
        with pytest.raises(ValueError, match='IMMUTABLE_NATIVE_COMMAND_CONFLICT'):
            action()
    assert not s.submits and not s.cancels


def test_build_reentrancy_guard_is_owned_until_outer_build_finishes() -> None:
    b, f, s = setup()
    r = request()
    errors: list[str] = []
    def callback() -> None:
        for _ in range(2):
            try:
                b.submit_fixed_limit_v2(r)
            except ValueError as exc:
                errors.append(str(exc))
    f.callback = callback
    b.submit_fixed_limit_v2(r)
    assert errors == ['NATIVE_ORDER_BUILD_IN_PROGRESS', 'NATIVE_ORDER_BUILD_IN_PROGRESS']
    assert len(f.calls) == len(s.submits) == 1


def test_guard_is_installed_before_id_converter_and_removed_after_build_failure() -> None:
    b, f, s = setup()
    r = request()
    fail = True
    def converter(value: str) -> str:
        if not fail:
            return value
        with pytest.raises(ValueError, match='NATIVE_ORDER_BUILD_IN_PROGRESS'):
            b.submit_fixed_limit_v2(r)
        raise RuntimeError('untrusted')
    b._bindings = replace(b._bindings, instrument_id=converter)
    with pytest.raises(ValueError, match='NATIVE_ORDER_BUILD_FAILED'):
        b.submit_fixed_limit_v2(r)
    assert not s.submits and not b._submissions
    fail = False
    b.submit_fixed_limit_v2(r)
    assert len(f.calls) == len(s.submits) == 1


def test_writer_reentry_observes_submitting_and_outer_throw_persists_unknown_once() -> None:
    b, f, s = setup()
    r = request()
    observed: list[Any] = []
    def callback() -> None:
        observed.append(b.submit_fixed_limit_v2(request()))
        raise TimeoutError('untrusted provider text')
    s.submit_callback = callback
    final = b.submit_fixed_limit_v2(r)
    assert observed[0].state == 'SUBMITTING'
    assert final.state == 'UNKNOWN'
    assert b.submit_fixed_limit_v2(request()) is final
    assert observed[0].state == 'SUBMITTING'
    assert len(s.submits) == len(f.calls) == 1


@pytest.mark.parametrize('mutation', ['generation', 'attachment', 'client'])
def test_cancel_replay_revalidates_identity_before_memoized_return(mutation: str) -> None:
    b, f, s = setup()
    r = request()
    b.submit_fixed_limit_v2(r)
    activate(f)
    result = b.cancel_owned(ID)
    f.order.is_closed = True
    assert b.cancel_owned(ID) is result
    if mutation == 'generation':
        object.__setattr__(r.owned, 'generation', 1)
    elif mutation == 'attachment':
        object.__setattr__(r.attached, 'stop_loss', Decimal(91))
    else:
        object.__setattr__(r.owned, 'client_order_id', OTHER)
    with pytest.raises(ValueError, match='IMMUTABLE_NATIVE_COMMAND_CONFLICT'):
        b.cancel_owned(ID)
    assert len(s.cancels) == 1


@pytest.mark.parametrize('change', ['updated', 'account', 'strategy', 'closed', 'pending', 'seed'])
def test_cache_cancel_checks_real_active_order_and_exact_seed_without_reconstruction(change: str) -> None:
    b, f, s = setup()
    b.submit_fixed_limit_v2(request())
    activate(f)
    if change == 'updated':
        f.order.events.append(Updated())
    elif change == 'account':
        f.order.account_id = 'OTHER'
    elif change == 'strategy':
        f.order.strategy_id = 'OTHER'
    elif change == 'closed':
        f.order.is_closed = True
    elif change == 'pending':
        f.order.is_pending_cancel = True
    else:
        f.order.events[0].options['price'] = '101'
    with pytest.raises(ValueError):
        b.cancel_owned(ID)
    assert not s.cancels


def test_cache_and_id_converter_callbacks_cannot_mutate_owner_before_first_cancel() -> None:
    for boundary in ('cache', 'converter'):
        b, f, s = setup()
        r = request()
        b.submit_fixed_limit_v2(r)
        activate(f)
        def mutate(r: Any = r) -> None:
            object.__setattr__(r.owned, 'generation', 1)
        if boundary == 'cache':
            s.cache_callback = mutate
        else:
            def convert(value: str) -> str:
                mutate()
                return value
            b._bindings = replace(b._bindings, client_order_id=convert)
        with pytest.raises(ValueError):
            b.cancel_owned(ID)
        assert not s.cancels


def test_late_fill_uses_existing_decoder_without_requiring_active_cache() -> None:
    b, _, _ = setup()
    r = request()
    b.register_fixed_limit_v2(r)
    event = SimpleNamespace(account_id='BYBIT-UNIFIED', client_order_id=ID,
        instrument_id='XAAUSDT-LINEAR.BYBIT', ts_event=1700000000000000100,
        ts_init=1700000000000000200, venue_order_id='V1', trade_id='LATE-001',
        order_side='BUY', last_qty=Decimal('0.4'), last_px=Decimal(101),
        reconciliation=False, commission=SimpleNamespace(as_decimal=lambda: Decimal('0.01'),
                                                        currency='USDT'))
    ctx = NativeEventContext(r.scope, 'BYBIT-UNIFIED', 1700000000000000900)
    decoder = NativeEventDecoder(event_types={SimpleNamespace: 'OrderFilled'})
    result = b.consume(event, ctx, decoder=decoder)
    assert result.fill.execution_id == 'LATE-001'
    assert result.fill.quantity == Decimal('0.4')
    assert result.fill.to_owned_fill().position_id == 'POSITION-001'
    assert result.terminal_confirmed is False


def test_decoder_mutation_after_decode_is_rejected_without_rebinding_evidence() -> None:
    b, _, _ = setup()
    r = request()
    b.register_fixed_limit_v2(r)
    called: list[Any] = []
    class Decoder:
        def decode(self, event: object, context: object, owned: object) -> object:
            called.append(owned)
            object.__setattr__(r.owned, 'generation', 1)
            return object()
    ctx = NativeEventContext(r.scope, 'BYBIT-UNIFIED', 1700000000000000900)
    with pytest.raises(ValueError, match='IMMUTABLE_NATIVE_COMMAND_CONFLICT'):
        b.consume(SimpleNamespace(client_order_id=ID), ctx, decoder=Decoder())
    assert called == [r.owned]
    with pytest.raises(ValueError):
        b.consume(SimpleNamespace(client_order_id=ID), ctx, decoder=Decoder())
    assert len(called) == 1

@pytest.mark.parametrize('change', ['missing', 'record', 'same_classes', 'object_class',
                                  'not_class', 'wrong_kind', 'wrong_tif', 'same_enums'])
def test_invalid_fixed_bindings_fail_before_any_factory_or_writer(change: str) -> None:
    b, f, s = setup()
    fixed = b._bindings.fixed_limit_v2
    if change == 'missing':
        fixed = None
    elif change == 'record':
        fixed = SimpleNamespace()
    elif change == 'same_classes':
        fixed = replace(fixed, updated_event_type=Init)
    elif change == 'object_class':
        fixed = replace(fixed, limit_order_type=object)
    elif change == 'not_class':
        fixed = replace(fixed, limit_order_type='LimitOrder')
    elif change == 'wrong_kind':
        fixed = replace(fixed, order_type_limit=Kind.MARKET)
    elif change == 'wrong_tif':
        fixed = replace(fixed, time_in_force_ioc=Tif.GTC)
    else:
        fixed = replace(fixed, time_in_force_ioc=Kind.LIMIT)
    b._bindings = replace(b._bindings, fixed_limit_v2=fixed)
    with pytest.raises(ValueError, match='NATIVE_FIXED_LIMIT_BINDINGS_REQUIRED'):
        b.submit_fixed_limit_v2(request())
    assert not f.calls and not s.submits


def test_old_factory_without_tif_keeps_protocol_and_default_five_binding_compatibility() -> None:
    class OldFactory:
        def market(self, *, instrument_id: object, order_side: object, quantity: object,
                   client_order_id: object, reduce_only: bool) -> object:
            return SimpleNamespace(instrument_id=instrument_id, side=order_side, quantity=quantity,
                client_order_id=client_order_id, is_reduce_only=reduce_only, price=None)
        def limit(self, *, instrument_id: object, order_side: object, quantity: object,
                  client_order_id: object, reduce_only: bool, price: object) -> object:
            return SimpleNamespace(instrument_id=instrument_id, side=order_side, quantity=quantity,
                client_order_id=client_order_id, is_reduce_only=reduce_only, price=price)
    factory: n.NativeOrderFactory = OldFactory()
    s = Strategy(Factory())
    r = request()
    b = n.NativeBybitBridge(scope=r.scope, native_account_id='BYBIT-UNIFIED', factory=factory,
        strategy=s, bindings=n.NativeBindings(str, str, str, str, str))
    assert b.submit(legacy(r)).state == 'SUBMITTING'
    assert len(s.submits) == 1
    with pytest.raises(ValueError, match='NATIVE_FIXED_LIMIT_BINDINGS_REQUIRED'):
        b.submit_fixed_limit_v2(r)


def test_exact_other_enum_member_with_same_name_and_value_is_rejected() -> None:
    class ForeignKind(IntEnum):
        LIMIT = 2
    b, f, s = setup()
    f.transform = lambda order: setattr(order.events[0], 'order_type', ForeignKind.LIMIT)
    with pytest.raises(ValueError, match='NATIVE_ORDER_BUILD_FAILED'):
        b.submit_fixed_limit_v2(request())
    assert not s.submits


def test_protection_precision_loss_and_factory_failure_invoke_nothing() -> None:
    for boundary in ('stop', 'target', 'factory'):
        b, f, s = setup()
        if boundary == 'factory':
            def fail() -> None:
                raise TimeoutError('untrusted provider text')
            f.callback = fail
        else:
            bad = '90' if boundary == 'stop' else '120'
            b._bindings = replace(b._bindings,
                price=lambda value, bad=bad: value + '.1' if value == bad else value)
        with pytest.raises(ValueError, match='^NATIVE_ORDER_BUILD_FAILED$'):
            b.submit_fixed_limit_v2(request())
        assert not s.submits and not b._submissions


def test_constructor_requires_position_idx_and_exact_attachment() -> None:
    r = request()
    with pytest.raises(TypeError):
        n.LinearFixedLimitNativeOrderRequestV2(r.product_scope, r.owned, r.envelope, r.attached)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        replace(r, position_idx=False)
    with pytest.raises(ValueError):
        replace(r, attached=None)


def test_registered_scope_envelope_and_native_pin_mutations_are_rejected() -> None:
    for target, field, value in [('scope', 'account_id', 'OTHER'),
                                 ('envelope', 'origin_quote_id', 'OTHER_QUOTE'),
                                 ('pin', 'catalog_revision', 'OTHER_CATALOG')]:
        b, _, s = setup()
        r = request()
        b.register_fixed_limit_v2(r)
        obj = r.scope if target == 'scope' else r.envelope.instrument_pin if target == 'pin' else r.envelope
        object.__setattr__(obj, field, value)
        with pytest.raises(ValueError):
            b.cancel_owned(ID)
        assert not s.cancels

@pytest.mark.parametrize('component', ['request', 'owned', 'attached', 'bindings'])
def test_missing_dataclass_fields_are_rejected_as_value_errors(component: str) -> None:
    b, _, s = setup()
    r = request()
    obj = r if component == 'request' else b._bindings.fixed_limit_v2 if component == 'bindings' else getattr(r, component)
    # Bypass frozen constructors to exercise the bridge's independent boundary.
    delattr_name = {'request': 'envelope', 'owned': 'generation', 'attached': 'stop_loss',
                    'bindings': 'limit_order_type'}[component]
    object.__delattr__(obj, delattr_name)
    with pytest.raises(ValueError):
        b.submit_fixed_limit_v2(r)
    assert not s.submits

@pytest.mark.parametrize('mutate', ['original', 'incoming'])
def test_factory_callback_revalidates_original_owner_and_incoming_exact_clone(mutate: str) -> None:
    b, f, s = setup()
    original, incoming = request(), request()
    b.register_fixed_limit_v2(original)
    def callback() -> None:
        object.__setattr__((original if mutate == 'original' else incoming).owned, 'generation', 1)
    f.callback = callback
    with pytest.raises(ValueError, match='NATIVE_ORDER_BUILD_FAILED'):
        b.submit_fixed_limit_v2(incoming)
    assert not s.submits and not b._submissions


def test_cancel_cache_reentry_reuses_inner_invocation_without_second_writer_call() -> None:
    b, f, s = setup()
    b.submit_fixed_limit_v2(request())
    activate(f)
    observed: list[Any] = []
    def callback() -> None:
        s.cache_callback = None
        observed.append(b.cancel_owned(ID))
    s.cache_callback = callback
    outer = b.cancel_owned(ID)
    assert outer is observed[0]
    assert len(s.cancels) == 1


def test_cancel_writer_reentry_and_outer_error_preserve_unknown_without_resending() -> None:
    b, f, s = setup()
    b.submit_fixed_limit_v2(request())
    activate(f)
    observed: list[Any] = []
    def callback() -> None:
        observed.append(b.cancel_owned(ID))
        raise TimeoutError('untrusted')
    s.cancel_callback = callback
    result = b.cancel_owned(ID)
    assert observed[0].state == 'SUBMITTING'
    assert result.state == 'UNKNOWN'
    f.order.is_closed = True
    assert b.cancel_owned(ID) is result
    assert len(s.cancels) == 1


class FixTextSubclass(str):
    pass


class FixEqualAccountText(str):
    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False


@pytest.mark.parametrize('case', ['ioc', 'class', 'cancel_nested'])
def test_fix_review_original_binding_drift_rejects_before_new_memo_or_writer(case: str) -> None:
    b, f, s = setup()
    selected = b._bindings.fixed_limit_v2
    class ForeignLimit(Limit):
        pass
    class ForeignInit(Init):
        pass
    class ForeignUpdated(Updated):
        pass
    if case == 'cancel_nested':
        b.submit_fixed_limit_v2(request())
        activate(f)
        replacement = n.FixedLimitBindingsV2(ForeignLimit, ForeignInit, ForeignUpdated,
                                              Kind.LIMIT, Tif.IOC)
        def callback() -> None:
            s.cache_callback = None
            object.__setattr__(b._bindings, 'fixed_limit_v2', replacement)
            f.order.__class__ = ForeignLimit
            f.order.events[0].__class__ = ForeignInit
        s.cache_callback = callback
        with pytest.raises(ValueError):
            b.cancel_owned(ID)
        assert not s.cancels and not b._cancellations
        assert len(s.submits) == 1
    else:
        def transform(order: Any) -> None:
            if case == 'ioc':
                object.__setattr__(selected, 'time_in_force_ioc', Tif.GTC)
                order.time_in_force = Tif.GTC
                order.events[0].time_in_force = Tif.GTC
            else:
                object.__setattr__(selected, 'limit_order_type', ForeignLimit)
                order.__class__ = ForeignLimit
        f.transform = transform
        with pytest.raises(ValueError):
            b.submit_fixed_limit_v2(request())
        assert not s.submits and not b._submissions
    assert not b._building_v2


@pytest.mark.parametrize('text_type,raw', [
    (FixTextSubclass, 'FIXTURE_ACCOUNT'), (FixEqualAccountText, 'FOREIGN_ACCOUNT'),
])
def test_fix_review_independent_request_scope_rejects_nonbase_account_strings(
    text_type: type[str], raw: str,
) -> None:
    b, f, s = setup()
    original = request()
    account = type(original.scope)(raw, 'DEMO')
    product = type(original.product_scope)(account, 'LINEAR')
    object.__setattr__(account, 'account_id', text_type(raw))
    with pytest.raises(ValueError):
        incoming = replace(original, product_scope=product)
        b.submit_fixed_limit_v2(incoming)
    assert not f.calls and not s.submits and not b._submissions


@pytest.mark.parametrize('field', ['limit_order_type', 'initialized_event_type',
    'updated_event_type', 'order_type_limit', 'time_in_force_ioc'])
@pytest.mark.parametrize('operation', ['submit', 'cancel_replay'])
def test_fix_original_selected_member_identities_stay_pinned_across_operations(
    field: str, operation: str,
) -> None:
    b, f, s = setup()
    b.submit_fixed_limit_v2(request())
    activate(f)
    if operation == 'cancel_replay':
        b.cancel_owned(ID)
        f.order.is_closed = True
    class OtherLimit(Limit):
        pass
    class OtherInit(Init):
        pass
    class OtherUpdated(Updated):
        pass
    class OtherKind(IntEnum):
        LIMIT = 2
    class OtherTif(IntEnum):
        IOC = 2
    replacements: dict[str, object] = {'limit_order_type': OtherLimit,
        'initialized_event_type': OtherInit, 'updated_event_type': OtherUpdated,
        'order_type_limit': OtherKind.LIMIT, 'time_in_force_ioc': OtherTif.IOC}
    object.__setattr__(b._bindings.fixed_limit_v2, field, replacements[field])
    with pytest.raises(ValueError):
        if operation == 'submit':
            b.submit_fixed_limit_v2(request())
        else:
            b.cancel_owned(ID)
    assert len(s.submits) == 1
    assert len(s.cancels) == (1 if operation == 'cancel_replay' else 0)


@pytest.mark.parametrize('field', ['instrument_id', 'client_order_id', 'quantity', 'price', 'order_side'])
@pytest.mark.parametrize('operation', ['submit', 'cancel'])
def test_fix_each_original_converter_identity_is_pinned_even_for_equal_outputs(
    field: str, operation: str,
) -> None:
    b, f, s = setup()
    b.register_fixed_limit_v2(request())
    if operation == 'cancel':
        b.submit_fixed_limit_v2(request())
        activate(f)
    object.__setattr__(b._bindings, field, lambda value: value)
    with pytest.raises(ValueError):
        b.submit_fixed_limit_v2(request()) if operation == 'submit' else b.cancel_owned(ID)
    assert len(s.submits) == (1 if operation == 'cancel' else 0)
    assert not s.cancels and not b._cancellations
    assert not b._building_v2


@pytest.mark.parametrize('boundary', ['converter', 'factory', 'protection', 'cache', 'native_getter'])
@pytest.mark.parametrize('change', ['parent', 'nested', 'factory', 'strategy', 'scope', 'native_account'])
def test_fix_context_substitution_during_callbacks_never_adds_new_handoff_or_memo(
    boundary: str, change: str,
) -> None:
    b, f, s = setup()
    other_factory, other_strategy = Factory(), Strategy(f)
    parent = b._bindings
    def mutate() -> None:
        if change == 'parent':
            b._bindings = replace(parent)
        elif change == 'nested':
            object.__setattr__(parent, 'fixed_limit_v2', replace(parent.fixed_limit_v2))
        elif change == 'factory':
            b._factory = other_factory
        elif change == 'strategy':
            b._strategy = other_strategy
        elif change == 'scope':
            b.scope = type(b.scope)('OTHER_ACCOUNT', 'DEMO')
        else:
            b.native_account_id = 'OTHER-NATIVE-ACCOUNT'
    if boundary == 'converter':
        def convert(value: str) -> str:
            mutate()
            return value
        b._bindings = replace(parent, instrument_id=convert)
        parent = b._bindings
    elif boundary == 'factory':
        f.callback = mutate
    elif boundary == 'protection':
        def price(value: str) -> str:
            if value == '120':
                mutate()
            return value
        b._bindings = replace(parent, price=price)
        parent = b._bindings
    elif boundary == 'native_getter':
        class NativeLabel:
            def __str__(self) -> str:
                mutate()
                return 'TRADER-001'
            def __eq__(self, other: object) -> bool:
                mutate()
                return True
            def __ne__(self, other: object) -> bool:
                mutate()
                return False
        def transform(order: Any) -> None:
            label = NativeLabel()
            order.trader_id = label
            order.events[0].trader_id = label
        f.transform = transform
    if boundary == 'cache':
        b.submit_fixed_limit_v2(request())
        activate(f)
        s.cache_callback = mutate
        with pytest.raises(ValueError):
            b.cancel_owned(ID)
        assert len(s.submits) == 1
        assert not b._cancellations
    else:
        with pytest.raises(ValueError):
            b.submit_fixed_limit_v2(request())
        assert not s.submits and not b._submissions
    assert not s.cancels and not other_strategy.submits and not other_strategy.cancels
    assert not other_factory.calls
    assert not b._building_v2


@pytest.mark.parametrize('change', ['parent', 'nested', 'factory', 'strategy', 'scope', 'native_account'])
def test_fix_memoized_submit_and_closed_cancel_replay_check_original_context(change: str) -> None:
    b, f, s = setup()
    submitted = b.submit_fixed_limit_v2(request())
    assert b.submit_fixed_limit_v2(request()) is submitted
    activate(f)
    canceled = b.cancel_owned(ID)
    f.order.is_closed = True
    assert b.cancel_owned(ID) is canceled
    if change == 'parent':
        b._bindings = replace(b._bindings)
    elif change == 'nested':
        object.__setattr__(b._bindings, 'fixed_limit_v2', replace(b._bindings.fixed_limit_v2))
    elif change == 'factory':
        b._factory = Factory()
    elif change == 'strategy':
        b._strategy = Strategy(f)
    elif change == 'scope':
        object.__setattr__(b.scope, 'account_id', 'OTHER_ACCOUNT')
    else:
        b.native_account_id = 'OTHER-NATIVE-ACCOUNT'
    with pytest.raises(ValueError):
        b.submit_fixed_limit_v2(request())
    with pytest.raises(ValueError):
        b.cancel_owned(ID)
    assert len(s.submits) == len(s.cancels) == 1


def test_fix_failed_build_keeps_original_parent_pin_and_cleans_only_build_guard() -> None:
    b, f, s = setup()
    original = b._bindings
    def fail() -> None:
        raise RuntimeError('untrusted')
    f.callback = fail
    with pytest.raises(ValueError, match='NATIVE_ORDER_BUILD_FAILED'):
        b.submit_fixed_limit_v2(request())
    f.callback = None
    b._bindings = replace(original)
    with pytest.raises(ValueError):
        b.submit_fixed_limit_v2(request())
    assert not s.submits and not b._submissions and not b._building_v2
    b._bindings = original
    assert b.submit_fixed_limit_v2(request()).state == 'SUBMITTING'
    assert len(s.submits) == 1


@pytest.mark.parametrize('target,field,value', [
    ('account', 'account_id', ' '), ('account', 'account_id', 'a' * 257),
    ('account', 'account_id', 'BAD\nACCOUNT'), ('account', 'account_id', ' BAD'),
    ('account', 'environment', FixTextSubclass('DEMO')),
    ('account', 'environment', 'LIVE'), ('product', 'product', FixTextSubclass('LINEAR')),
    ('product', 'product', 'OTHER'), ('product', 'account', None),
])
def test_fix_independent_scope_reuses_existing_scalar_domains(
    target: str, field: str, value: object,
) -> None:
    b, f, s = setup()
    r = request()
    product = type(r.product_scope)(type(r.scope)('FIXTURE_ACCOUNT', 'DEMO'), 'LINEAR')
    obj = product.account if target == 'account' else product
    object.__setattr__(obj, field, value)
    with pytest.raises(ValueError):
        b.submit_fixed_limit_v2(replace(r, product_scope=product))
    assert not f.calls and not s.submits


@pytest.mark.parametrize('where', ['request', 'bridge'])
def test_fix_post_registration_scope_scalar_subclass_cannot_enter_detached_identity(where: str) -> None:
    b, _, s = setup()
    r = request()
    b.register_fixed_limit_v2(r)
    account = r.scope if where == 'request' else b.scope
    object.__setattr__(account, 'account_id', FixEqualAccountText('FOREIGN_ACCOUNT'))
    with pytest.raises(ValueError):
        b.submit_fixed_limit_v2(r)
    assert not s.submits and not b._submissions


def test_fix_existing_long_base_string_account_domain_is_preserved() -> None:
    b, _, s = setup()
    r = request()
    account = type(r.scope)('A' * 256, 'DEMO')
    product = type(r.product_scope)(account, 'LINEAR')
    env = replace(r.envelope, scope=replace(r.envelope.scope, product_scope=product))
    b.scope = account
    valid = replace(r, product_scope=product, envelope=env)
    assert b.submit_fixed_limit_v2(valid).state == 'SUBMITTING'
    assert len(s.submits) == 1


def test_fix_failed_scope_or_cross_version_registration_does_not_pin_invalid_context() -> None:
    b, _, _ = setup()
    r = request()
    wrong = type(r.scope)('OTHER_ACCOUNT', 'DEMO')
    b.scope = wrong
    with pytest.raises(ValueError):
        b.register_fixed_limit_v2(r)
    b.scope = r.scope
    b._bindings = replace(b._bindings)
    b.register_fixed_limit_v2(r)
    assert b.submit_fixed_limit_v2(request()).state == 'SUBMITTING'


@pytest.mark.parametrize('change', ['parent', 'factory', 'strategy', 'native_account'])
def test_fix_cancel_rechecks_context_after_active_account_string_conversion(change: str) -> None:
    b, f, s = setup()
    b.submit_fixed_limit_v2(request())
    activate(f)
    parent = b._bindings
    other_factory, other_strategy = Factory(), Strategy(f)
    class AccountLabel:
        def __str__(self) -> str:
            if change == 'parent':
                b._bindings = replace(parent)
            elif change == 'factory':
                b._factory = other_factory
            elif change == 'strategy':
                b._strategy = other_strategy
            else:
                b.native_account_id = 'OTHER-NATIVE-ACCOUNT'
            return 'BYBIT-UNIFIED'
    f.order.account_id = AccountLabel()
    with pytest.raises(ValueError):
        b.cancel_owned(ID)
    assert not s.cancels and not other_strategy.cancels and not b._cancellations
    assert len(s.submits) == 1


@pytest.mark.parametrize('change', ['subclass', 'incomplete', 'noncallable'])
def test_fix_parent_binding_exact_complete_callable_shape_required_before_mapping(change: str) -> None:
    b, f, s = setup()
    if change == 'subclass':
        class ParentSubclass(n.NativeBindings):
            pass
        parent: Any = ParentSubclass(str, str, str, str, str, b._bindings.fixed_limit_v2)
        b._bindings = parent
    elif change == 'incomplete':
        object.__delattr__(b._bindings, 'quantity')
    else:
        object.__setattr__(b._bindings, 'quantity', 0)
    with pytest.raises(ValueError):
        b.register_fixed_limit_v2(request())
    assert not b._requests and not f.calls and not s.submits


@pytest.mark.parametrize('field', ['scope_account', 'scope_environment', 'native_account'])
def test_fix_bridge_context_uses_base_string_metadata_before_initial_mapping(field: str) -> None:
    b, f, s = setup()
    if field == 'scope_account':
        object.__setattr__(b.scope, 'account_id', FixTextSubclass('FIXTURE_ACCOUNT'))
    elif field == 'scope_environment':
        object.__setattr__(b.scope, 'environment', FixTextSubclass('DEMO'))
    else:
        b.native_account_id = FixTextSubclass('BYBIT-UNIFIED')
    with pytest.raises(ValueError):
        b.register_fixed_limit_v2(request())
    assert not b._requests and not f.calls and not s.submits
