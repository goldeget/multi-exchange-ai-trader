"""Exactly eight real-wheel contracts; only root's isolated Linux job may execute.

Native objects/adapter payloads prove local structure, never venue acceptance.
The last two cases use true J admission, the default worker, B1 and coordinator.
"""
from __future__ import annotations

import base64
import inspect
from collections.abc import Callable
from dataclasses import fields, replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest
from test_bybit_native_linux import (
    TS_INIT,
    adapter_boundary,
    isolated_native_runtime,  # noqa: F401 - mandatory imported autouse namespace prerequisite
    module,
)
from test_native_monitor_http import fee_row, instrument_row
from test_native_restart_linux import actual_worker, change_document, no_effects, start
from test_reservations_v2 import bound, claim, fixture_values, register, snapshot
from test_runtime import SCOPE

from multi_exchange_trader.execution import bybit_native as n
from multi_exchange_trader.execution import linear_risk_evidence_v2 as e
from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.history_store import HistoryStore
from multi_exchange_trader.execution.journal import CommandJournal, CommandState, ExecutionScope
from multi_exchange_trader.execution.native_recovery import OwnedCacheExpectation
from multi_exchange_trader.execution.native_restart_codec import (
    capture_native_cache,
    prepare_native_cache,
)
from multi_exchange_trader.execution.native_restart_store import (
    NativeRestartError,
    NativeRestartIdentity,
    application_digest,
)
from multi_exchange_trader.execution.protection import OwnedOrder
from multi_exchange_trader.execution.risk_v2_codec import encode_entry_intent_v2
from multi_exchange_trader.execution.state_store import RuntimeStateStore


def _request(side: str = 'BUY', suffix: int = 1) -> n.LinearFixedLimitNativeOrderRequestV2:
    envelope = fixture_values('SDK', margin_only=True, symbol='BTCUSDT')[0].envelope
    envelope = replace(envelope, side=cast(Literal['BUY', 'SELL'], side))
    owned = OwnedOrder('ec_' + f'{suffix:032x}', 'SDK_POSITION', 'BTCUSDT-LINEAR.BYBIT',
        cast(Any, side), 'ENTRY', envelope.base_quantity, False, 0, envelope.policy_revision)
    stop, take = (Decimal('90.00'), Decimal('120.000')) if side == 'BUY' else (Decimal('120.000'), Decimal('90.00'))
    return n.LinearFixedLimitNativeOrderRequestV2(envelope.scope.product_scope, owned, envelope,
        n.AttachedProtection(stop, take, 'MarkPrice'), 0)


def _harness(callback: Callable[[Any], None] | None = None) -> Any:
    clock = module('common.component').TestClock()
    clock.set_time(TS_INIT)
    trader = module('model.identifiers').TraderId('TRADER-001')
    bus = module('common.component').MessageBus(trader_id=trader, clock=clock)
    cache = module('cache.cache').Cache()
    portfolio = module('portfolio.portfolio').Portfolio(msgbus=bus, cache=cache, clock=clock)
    strategy = module('trading.strategy').Strategy()
    strategy.register(trader_id=trader, portfolio=portfolio, msgbus=bus, cache=cache, clock=clock)
    result = SimpleNamespace(clock=clock, trader=trader, msgbus=bus, cache=cache, strategy=strategy, commands=[])
    def receive(command: Any) -> None:
        result.commands.append(command)
        if callback is not None:
            callback(result)
    bus.register(endpoint='RiskEngine.execute', handler=receive)
    return result


def _bridge(h: Any, request: n.LinearFixedLimitNativeOrderRequestV2) -> n.NativeBybitBridge:
    h.bridge = n.NativeBybitBridge(scope=request.scope, native_account_id='BYBIT-UNIFIED',
        factory=h.strategy.order_factory, strategy=h.strategy,
        bindings=n.load_native_bindings(include_fixed_limit_v2=True))
    return cast(n.NativeBybitBridge, h.bridge)


async def _payload(h: Any) -> dict[str, Any]:
    receiver = adapter_boundary(h)
    await module('adapters.bybit.execution').BybitExecutionClient._submit_order(receiver, h.commands[-1])
    assert len(receiver._http_client.calls) == 1
    payload = cast(dict[str, Any], receiver._http_client.calls[0])
    signature = inspect.signature(module('core.nautilus_pyo3').BybitHttpClient.submit_order)
    signature.bind(None, **payload)
    return payload


def _assert_payload(payload: dict[str, Any], request: n.LinearFixedLimitNativeOrderRequestV2) -> None:
    native = module('core.nautilus_pyo3')
    for actual, expected in ((payload['order_type'], native.OrderType.LIMIT),
                             (payload['time_in_force'], native.TimeInForce.IOC)):
        assert type(actual) is type(expected) and actual == expected
    assert str(payload['client_order_id']) == request.owned.client_order_id
    assert str(payload['instrument_id']) == request.owned.instrument_id
    assert str(payload['account_id']) == 'BYBIT-UNIFIED'
    assert Decimal(str(payload['quantity'])).as_tuple() == request.owned.quantity.as_tuple()
    assert Decimal(str(payload['price'])).as_tuple() == request.limit_price.as_tuple()
    assert payload['product_type'] == native.BybitProductType.LINEAR
    assert payload['order_side'] == getattr(native.OrderSide, request.owned.side)
    assert payload['position_idx'] == native.BybitPositionIdx.from_str(0)
    assert payload['post_only'] is payload['reduce_only'] is payload['is_quote_quantity'] is False
    assert payload['trigger_price'] is None
    assert payload.get('bbo_side_type') is None and payload.get('bbo_level') is None


def _instrument() -> Any:
    from multi_exchange_trader.execution.native_monitor_http import convert_instrument
    native = convert_instrument(cast(Callable[[], dict[str, Any]], instrument_row)(),
        cast(Callable[[], dict[str, Any]], fee_row)(), 'BTCUSDT', 1000)
    return module('model.instruments').instruments_from_pyo3([native])[0]


def _accepted(order: Any, cache: Any) -> None:
    events, ids, uuid = module('model.events.order'), module('model.identifiers'), module('core.uuid').UUID4
    original = {'trader_id': order.trader_id, 'strategy_id': order.strategy_id,
        'instrument_id': order.instrument_id, 'client_order_id': order.client_order_id,
        'account_id': ids.AccountId('BYBIT-UNIFIED'), 'ts_event': order.ts_init + 1, 'ts_init': order.ts_init + 1}
    order.apply(events.OrderSubmitted(**original, event_id=uuid()))
    order.apply(events.OrderAccepted(**{**original, 'ts_event': order.ts_init + 2, 'ts_init': order.ts_init + 2},
        venue_order_id=ids.VenueOrderId('native-venue'), event_id=uuid()))
    cache.update_order(order)


def _fill(cache: Any, order: Any, quantity: str, price: str = '100.00') -> Any:
    instrument = cache.instrument(order.instrument_id)
    ids, enums, obj = module('model.identifiers'), module('model.enums'), module('model.objects')
    fill = module('model.events.order').OrderFilled(trader_id=order.trader_id,
        strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=order.client_order_id,
        account_id=order.account_id, venue_order_id=order.venue_order_id, trade_id=ids.TradeId('original-fill'),
        position_id=ids.PositionId(str(order.instrument_id) + '-' + str(order.strategy_id)),
        order_side=order.side, order_type=order.order_type, last_qty=obj.Quantity.from_str(quantity),
        last_px=obj.Price.from_str(price), currency=instrument.quote_currency,
        commission=obj.Money(0.01, instrument.quote_currency), liquidity_side=enums.LiquiditySide.TAKER,
        event_id=module('core.uuid').UUID4(), ts_event=order.ts_init + 3, ts_init=order.ts_init + 3)
    order.apply(fill)
    position = module('model.position').Position(instrument, fill)
    cache.update_order(order)
    cache.add_position(position, enums.OmsType.NETTING)
    cache.add_position_id(position.id, order.instrument_id.venue, order.client_order_id, order.strategy_id)
    cache.build_index()
    assert cache.check_integrity() is True
    return position


def _durable_values() -> Any:
    # Rebuild every linked scope/hash; never flip one side in an accepted assessment.
    intent, policy, account, marks = fixture_values('V2_ENTRY', margin_only=True, symbol='BTCUSDT')
    scope = replace(intent.envelope.scope, product_scope=replace(intent.envelope.scope.product_scope, account=SCOPE))
    policy = replace(policy, scope=scope)
    ph = c.risk_policy_sha256_v2(policy)
    envelope = replace(intent.envelope, scope=scope, policy_sha256=ph)
    prep = intent.assessment.preparation
    sizing = replace(prep.sizing, scope=scope, envelope=envelope, policy_sha256=ph)
    minimum = replace(prep.minimum_amount, scope=scope, envelope_sha256=c.envelope_sha256_v2(envelope))
    valuation = replace(prep.valuation, scope=scope)
    binding = replace(prep.binding, scope=scope, policy_sha256=ph,
        envelope_sha256=c.envelope_sha256_v2(envelope), sizing_sha256=c.sizing_sha256_v2(sizing),
        valuation_sha256=c.valuation_sha256_v2(valuation), minimum_assessment_sha256=e.minimum_assessment_sha256_v2(minimum))
    prep = replace(prep, sizing=sizing, minimum_amount=minimum, valuation=valuation, binding=binding)
    bh = e.binding_sha256_v2(binding)
    margin = intent.assessment.margin
    assert margin is not None
    margin = replace(margin, binding_sha256=bh, reference=replace(margin.reference, input_sha256=bh))
    intent = replace(intent, envelope=envelope, assessment=replace(intent.assessment, preparation=prep, margin=margin),
        protection_policy=replace(intent.protection_policy, trigger_source='MarkPrice'))
    account = replace(account, account=SCOPE)
    encode_entry_intent_v2(intent)
    return intent, policy, account, marks


def _hold(h: Any, intent: Any) -> tuple[Any, ...]:
    journal = h.worker.runtime.journal
    record = journal.get(intent.command_key)
    current = snapshot(journal, intent)
    return (record.client_order_id, record.payload_json, record.payload_hash,
            repr(record.reservation_v2), current.held_quantity_sha256, current.journal_revision)


async def _stop(h: Any) -> None:
    try:
        await h.worker.stop()
    finally:
        await h.session.aclose()


async def _lineage(directory: Path, state: str) -> Any:
    source = actual_worker(directory)
    await start(source)
    values = _durable_values()
    intent = values[0]
    bound(source.worker.runtime.journal)
    record = register(source.worker.runtime.journal, values)
    assert record.reservation_v2 is not None and source.worker._factory is None
    assert source.worker.runtime.native_owned_requests() == ()  # Real pending orphan omission.
    await _stop(source)
    h = actual_worker(directory, source)
    await start(h)
    requests = h.worker.runtime.native_owned_requests()
    assert len(requests) == 1 and type(requests[0].request) is n.LinearFixedLimitNativeOrderRequestV2
    request = requests[0].request
    assert request.owned.client_order_id == record.client_order_id
    if state != 'PENDING_MISSING':
        claimed = claim(h.worker.runtime.journal, intent)
        assert claimed is not None and claimed.state is CommandState.SENDING
    if state in ('UNKNOWN', 'UNKNOWN_MISSING'):
        h.worker.runtime.journal.mark_unknown(intent.command_key, 'FIXTURE_AMBIGUOUS_NATIVE_STATE')
    h.intent, h.original_request, h.held = intent, request, _hold(h, intent)
    h.factory = h.assembly.strategy.order_factory
    assert h.assembly.bridge._factory is h.factory
    if state not in ('MISSING', 'PENDING_MISSING', 'UNKNOWN_MISSING'):
        cache, ids, enums, obj = h.assembly.node.cache, module('model.identifiers'), module('model.enums'), module('model.objects')
        instrument = _instrument()
        cache.add_instrument(instrument)
        order = h.factory.limit(instrument_id=instrument.id, order_side=enums.OrderSide.BUY,
            quantity=obj.Quantity.from_str(format(request.owned.quantity, 'f')),
            client_order_id=ids.ClientOrderId(record.client_order_id), reduce_only=False,
            price=obj.Price.from_str(format(request.limit_price, 'f')), time_in_force=enums.TimeInForce.IOC)
        cache.add_order(order, client_id=ids.ClientId('BYBIT'))
        _accepted(order, cache)
        if state in ('PARTIAL', 'FULL', 'CANCELED', 'EXPIRED'):
            _fill(cache, order, '1.000' if state == 'FULL' else '0.400')
        if state in ('CANCELED', 'EXPIRED'):
            event_type = getattr(module('model.events.order'), 'OrderCanceled' if state == 'CANCELED' else 'OrderExpired')
            order.apply(event_type(trader_id=order.trader_id, strategy_id=order.strategy_id,
                instrument_id=order.instrument_id, client_order_id=order.client_order_id,
                account_id=order.account_id, venue_order_id=order.venue_order_id,
                event_id=module('core.uuid').UUID4(), ts_event=order.ts_init + 4, ts_init=order.ts_init + 4))
            cache.update_order(order)
        cache.build_index()
        assert cache.check_integrity() is True
        h.order = order
    # Writer assertions begin after fixture-only real factory/event/cache seeding.
    no_effects(h)
    assert not h.assembly.bridge._cancellations and _hold(h, intent) == h.held
    return h


async def _missing_acknowledged_and_rejected_refuse(directory: Path) -> None:
    for state in ('ACKNOWLEDGED', 'REJECTED'):
        h = await _lineage(directory / state, 'MISSING')
        journal = h.worker.runtime.journal
        transition = journal.acknowledge if state == 'ACKNOWLEDGED' else journal.reject
        transition(h.intent.command_key, 'FIXTURE_NATIVE_LABEL_WITHOUT_OBJECT')
        before, held = application_digest(h.worker.directory), _hold(h, h.intent)
        marker = h.worker.directory / 'native-restart.json'
        native_before = marker.read_bytes()
        try:
            with pytest.raises(NativeRestartError):
                h.worker._restart.seal(h.worker.runtime.native_owned_requests)
            assert application_digest(h.worker.directory) == before and marker.read_bytes() == native_before
            assert _hold(h, h.intent) == held and h.assembly.node.cache.orders() == []
            no_effects(h)
        finally:
            with pytest.raises(NativeRestartError):
                await _stop(h)


async def _native_orphan_and_meta_only_refuse(directory: Path) -> None:
    orphan = actual_worker(directory / 'orphan')
    await start(orphan)
    values = _durable_values()
    bound(orphan.worker.runtime.journal)
    record = register(orphan.worker.runtime.journal, values)
    instrument, intent = _instrument(), values[0]
    enums, ids, obj = module('model.enums'), module('model.identifiers'), module('model.objects')
    cache = orphan.assembly.node.cache
    cache.add_instrument(instrument)
    order = orphan.assembly.strategy.order_factory.limit(instrument_id=instrument.id,
        order_side=enums.OrderSide.BUY, quantity=obj.Quantity.from_str(format(intent.envelope.base_quantity, 'f')),
        client_order_id=ids.ClientOrderId(record.client_order_id), reduce_only=False,
        price=obj.Price.from_str(format(intent.envelope.limit_price, 'f')), time_in_force=enums.TimeInForce.IOC)
    cache.add_order(order, client_id=ids.ClientId('BYBIT'))
    cache.build_index()
    assert order.venue_order_id is None and orphan.worker.runtime.native_owned_requests() == ()
    before = application_digest(orphan.worker.directory)
    try:
        with pytest.raises(NativeRestartError):
            orphan.worker._restart.seal(orphan.worker.runtime.native_owned_requests)
        assert application_digest(orphan.worker.directory) == before
        no_effects(orphan)
    finally:
        with pytest.raises(NativeRestartError):
            await _stop(orphan)
    meta = actual_worker(directory / 'meta-only')
    journal = CommandJournal(meta.worker.directory / 'execution.sqlite3', ExecutionScope(SCOPE.account_id, SCOPE.environment))
    store = RuntimeStateStore(meta.worker.directory / 'runtime-state.sqlite3', journal.scope)
    history = HistoryStore(meta.worker.directory / 'history.sqlite3', SCOPE)
    try:
        bound(journal)
        assert journal.list_commands() == ()
    finally:
        history.close()
        store.close()
        journal.close()
    before = application_digest(meta.worker.directory)
    try:
        with pytest.raises(NativeRestartError):
            await meta.worker.start_passive()
        assert application_digest(meta.worker.directory) == before
        assert not (meta.worker.directory / 'native-restart.json').exists()
    finally:
        await _stop(meta)


async def test_actual_limit_ioc_factory() -> None:
    for index, side in enumerate(('BUY', 'SELL'), 1):
        h, request = _harness(), _request(side, index)
        bridge = _bridge(h, request)
        bridge.submit_fixed_limit_v2(request)
        assert len(h.commands) == 1 and type(h.commands[0]) is module('execution.messages').SubmitOrder
        order = h.commands[0].order
        assert type(order) is module('model.orders').LimitOrder
        assert type(order.events[0]) is module('model.events.order').OrderInitialized
        assert order.time_in_force == order.events[0].time_in_force == module('model.enums').TimeInForce.IOC
        _assert_payload(await _payload(h), request)


async def test_exact_base_quantity_and_price() -> None:
    h, request = _harness(), _request()
    bridge = _bridge(h, request)
    bridge.submit_fixed_limit_v2(request)
    order = h.commands[0].order
    assert order.quantity.as_decimal().as_tuple() == request.owned.quantity.as_tuple()
    assert order.events[0].quantity.as_decimal().as_tuple() == request.envelope.base_quantity.as_tuple()
    assert order.price.as_decimal().as_tuple() == request.limit_price.as_tuple()
    _assert_payload(await _payload(h), request)
    instrument = _instrument()
    h.cache.add_instrument(instrument)
    assert instrument.price_precision == 2 and instrument.size_precision == 3
    _accepted(order, h.cache)
    identity = NativeRestartIdentity(request.scope, 'BYBIT-UNIFIED', str(h.trader), str(h.strategy.id), (request.owned.instrument_id,))
    owner = OwnedCacheExpectation(request, 'BYBIT-UNIFIED', str(h.trader), str(h.strategy.id), 'native-venue', None)
    with pytest.raises(ValueError):
        _fill(h.cache, order, '0.400', '100.001')
        capture_native_cache(h.cache, identity=identity, owners=(owner,))
    assert order.price.as_decimal().as_tuple() == request.limit_price.as_tuple()
    with pytest.raises(ValueError):
        replace(request, envelope=replace(request.envelope, limit_price=Decimal('100.000000001')))


def test_ioc_and_order_kind_revalidation() -> None:
    h, request = _harness(), _request()
    bindings = n.load_native_bindings(include_fixed_limit_v2=True)
    fixed = n._fixed_limit_bindings_v2(bindings.fixed_limit_v2)
    ids, enums, objects = module('model.identifiers'), module('model.enums'), module('model.objects')
    arguments = {'instrument_id': ids.InstrumentId.from_str(request.owned.instrument_id),
        'order_side': enums.OrderSide.BUY, 'quantity': objects.Quantity.from_str('1.00'),
        'client_order_id': ids.ClientOrderId(request.owned.client_order_id), 'reduce_only': False}
    gtc = h.strategy.order_factory.limit(**arguments, price=objects.Price.from_str('100.00'), time_in_force=enums.TimeInForce.GTC)
    market = h.strategy.order_factory.market(**arguments)
    for wrong in (gtc, market, object()):
        with pytest.raises(ValueError, match='NATIVE_ORDER_CHANGED'):
            n._verify_fixed_limit_order_v2(wrong, request, fixed, h.strategy.id, building=False)
    assert not h.commands


async def test_no_bbo_or_post_only_override() -> None:
    h, request = _harness(), _request()
    assert tuple(field.name for field in fields(type(request))) == ('product_scope', 'owned', 'envelope', 'attached', 'position_idx')
    for option in ('post_only', 'bbo_side_type', 'bbo_level', 'quote_quantity'):
        with pytest.raises(TypeError):
            n.LinearFixedLimitNativeOrderRequestV2(**{**vars(request), option: True})
    _bridge(h, request).submit_fixed_limit_v2(request)
    payload = await _payload(h)
    _assert_payload(payload, request)
    assert h.commands[0].order.is_post_only is h.commands[0].order.is_quote_quantity is False


async def test_protection_and_product_binding() -> None:
    for index, side in enumerate(('BUY', 'SELL'), 1):
        h, request = _harness(), _request(side, index)
        bridge = _bridge(h, request)
        bridge.submit_fixed_limit_v2(request)
        payload = await _payload(h)
        _assert_payload(payload, request)
        attached = payload['native_tp_sl']
        assert type(attached) is module('core.nautilus_pyo3').BybitNativeTpSlParams
        assert (attached.stop_loss, attached.take_profit, attached.sl_trigger_by, attached.tp_trigger_by,
                attached.sl_order_type, attached.tp_order_type, attached.tpsl_mode) == (
            format(request.attached.stop_loss, 'f'), format(request.attached.take_profit, 'f'),
            'MarkPrice', 'MarkPrice', 'Market', 'Market', 'Full')
        assert request.product_scope.product == 'LINEAR' and request.position_idx == 0
        with pytest.raises(ValueError):
            replace(request, product_scope=replace(request.product_scope, account=replace(request.scope, environment='LIVE')))
        assert len(h.commands) == 1


def test_ambiguous_submit_keeps_identity() -> None:
    request = _request()
    def ambiguous(h: Any) -> None:
        assert h.bridge.submit_fixed_limit_v2(request).state == 'SUBMITTING'
        raise RuntimeError('synthetic lost return after native command')
    h = _harness(ambiguous)
    bridge = _bridge(h, request)
    first = bridge.submit_fixed_limit_v2(request)
    assert first.state == 'UNKNOWN' and bridge.submit_fixed_limit_v2(request) is first
    assert len(h.commands) == 1
    with pytest.raises(ValueError):
        bridge.submit_fixed_limit_v2(replace(request, envelope=replace(request.envelope, limit_price=Decimal('101.00'))))
    order = h.commands[0].order
    _accepted(order, h.cache)
    assert bridge.cached_owned_order(request.owned.client_order_id) is order
    assert len(h.commands) == 1 and not bridge._cancellations


async def test_partial_and_zero_fill_keep_reservation(tmp_path: Path) -> None:
    for state in ('ZERO', 'PARTIAL'):
        source = await _lineage(tmp_path / state, state)
        original = source.order.to_dict()
        held = source.held
        await _stop(source)
        reopened = actual_worker(source.worker.directory, source)
        await start(reopened)
        try:
            order = reopened.assembly.node.cache.order(source.order.client_order_id)
            assert order.to_dict() == original
            assert order.quantity.as_decimal() == Decimal('1.00')
            assert order.events[0].time_in_force == module('model.enums').TimeInForce.IOC
            if state == 'PARTIAL':
                assert order.events[-1].last_qty.as_decimal() == Decimal('0.400')
                assert reopened.assembly.node.cache.position_for_order(order.client_order_id).quantity.as_decimal() == Decimal('0.400')
            else:
                assert reopened.assembly.node.cache.positions() == []
            assert _hold(reopened, source.intent) == held
            assert reopened.worker.runtime.journal.get(source.intent.command_key).state is CommandState.UNKNOWN
            no_effects(reopened)
            assert reopened.worker._factory is None and not reopened.assembly.bridge._cancellations
        finally:
            await _stop(reopened)


async def test_restore_never_resubmits_or_reprices(tmp_path: Path) -> None:
    for state in ('FULL', 'CANCELED', 'EXPIRED', 'UNKNOWN', 'MISSING', 'PENDING_MISSING', 'UNKNOWN_MISSING', 'EXPIRED_BAD_SEED'):
        source = await _lineage(tmp_path / state, 'EXPIRED' if state == 'EXPIRED_BAD_SEED' else state)
        before = application_digest(source.worker.directory)
        resolver = source.worker.runtime.native_owned_requests
        owners, missing = source.worker._restart._expectations(resolver(()))
        payload = capture_native_cache(source.assembly.node.cache, identity=source.worker._restart.identity,
            owners=owners, allowed_missing_ids=missing)
        prepare_native_cache(payload, identity=source.worker._restart.identity, owners=owners, allowed_missing_ids=missing)
        assert application_digest(source.worker.directory) == before
        held, request = source.held, source.original_request
        original = None if state in ('MISSING', 'PENDING_MISSING', 'UNKNOWN_MISSING') else source.order.to_dict()
        await _stop(source)
        if state == 'EXPIRED_BAD_SEED':
            marker = source.worker.directory / 'native-restart.json'
            def wrong_seed(document: Any) -> None:
                msgpack = __import__('msgspec').msgpack
                row = document['payload']['orders'][0]
                seed = msgpack.decode(base64.b64decode(row['events'][0]))
                seed['time_in_force'] = 'GTC'
                row['events'][0] = base64.b64encode(msgpack.encode(seed)).decode('ascii')
            sealed = change_document(marker, wrong_seed)
            before = application_digest(source.worker.directory)
            refused = actual_worker(source.worker.directory, source)
            try:
                with pytest.raises(NativeRestartError):
                    await refused.worker.start_passive()
                assert application_digest(source.worker.directory) == before and marker.read_bytes() == sealed
                target = refused.worker._assembly.node.cache
                assert target.orders() == target.positions() == target.instruments() == []
            finally:
                await _stop(refused)
            continue
        reopened = actual_worker(source.worker.directory, source)
        await start(reopened)
        try:
            assert _hold(reopened, source.intent) == held
            current = reopened.worker.runtime.native_owned_requests()[0].request
            assert type(current) is n.LinearFixedLimitNativeOrderRequestV2
            assert n._fixed_limit_identity_v2(current) == n._fixed_limit_identity_v2(request)
            if state in ('MISSING', 'PENDING_MISSING', 'UNKNOWN_MISSING'):
                assert reopened.worker.native_restart_status.missing_owned_orders == 1
                assert reopened.assembly.node.cache.orders() == []
            else:
                order = reopened.assembly.node.cache.order(source.order.client_order_id)
                assert order.to_dict() == original and order.quantity.as_decimal() == Decimal('1.00')
            no_effects(reopened)
            assert reopened.worker._factory is None and not reopened.assembly.bridge._cancellations
        finally:
            await _stop(reopened)
    await _missing_acknowledged_and_rejected_refuse(tmp_path / 'missing-refusals')
    await _native_orphan_and_meta_only_refuse(tmp_path / 'orphan-refusals')
