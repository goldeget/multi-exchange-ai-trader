"""Offline B1 restoration with true J admissions, temporary stores and fake A bindings."""
from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from test_bybit_fixed_limit import Init, Kind, Limit, Tif, Updated
from test_reservations_v2 import bound, claim, fixture_values, register
from test_risk_v2_codec import NOW
from test_runtime import queue as legacy_queue
from test_runtime import setup as setup  # noqa: PLC0414 - existing real-store fixture export
from test_runtime_credit import live as live  # noqa: PLC0414 - existing offline history fixture

from multi_exchange_trader.execution import codec
from multi_exchange_trader.execution import linear_risk_evidence_v2 as e
from multi_exchange_trader.execution import protection as p
from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.account_binding import BybitDemoAccountSession
from multi_exchange_trader.execution.bybit_native import (
    FixedLimitBindingsV2,
    LinearFixedLimitNativeOrderRequestV2,
    NativeBindings,
    NativeBybitBridge,
)
from multi_exchange_trader.execution.history import (
    ExecutionRecord,
    HistorySnapshot,
    HistoryWindow,
    OrderHistoryRecord,
)
from multi_exchange_trader.execution.history_store import HistoryStore
from multi_exchange_trader.execution.journal import CommandJournal, CommandState, ExecutionScope
from multi_exchange_trader.execution.ownership import AccountWorkerLock
from multi_exchange_trader.execution.risk_v2_codec import encode_entry_intent_v2
from multi_exchange_trader.execution.runtime import NativeExecutionRuntime, RuntimeBlocked
from multi_exchange_trader.execution.state_store import RuntimeStateStore


def values(key: str, position: str, *, side: str = 'BUY', native: bool = True) -> Any:
    intent, policy, account, marks = fixture_values(key, margin_only=True)
    scope = replace(intent.envelope.scope, product_scope=replace(
        intent.envelope.scope.product_scope, account=p.ProtectionScope('B1_FIXTURE_ACCOUNT', 'DEMO')))
    policy = replace(policy, scope=scope)
    ph = c.risk_policy_sha256_v2(policy)
    envelope = replace(intent.envelope, scope=scope, policy_sha256=ph)
    prep = intent.assessment.preparation
    sizing = replace(prep.sizing, scope=scope, envelope=envelope, policy_sha256=ph)
    minimum = replace(prep.minimum_amount, scope=scope, envelope_sha256=c.envelope_sha256_v2(envelope))
    valuation = replace(prep.valuation, scope=scope)
    binding = replace(prep.binding, scope=scope, policy_sha256=ph,
        envelope_sha256=c.envelope_sha256_v2(envelope), sizing_sha256=c.sizing_sha256_v2(sizing),
        valuation_sha256=c.valuation_sha256_v2(valuation),
        minimum_assessment_sha256=e.minimum_assessment_sha256_v2(minimum))
    prep = replace(prep, sizing=sizing, minimum_amount=minimum, valuation=valuation, binding=binding)
    bh = e.binding_sha256_v2(binding)
    original_margin = intent.assessment.margin
    assert original_margin is not None
    margin = replace(original_margin, binding_sha256=bh,
        reference=replace(original_margin.reference, input_sha256=bh))
    intent = replace(intent, envelope=envelope,
        assessment=replace(intent.assessment, preparation=prep, margin=margin))
    account = replace(account, account=scope.product_scope.account)
    protection = replace(intent.protection_policy, trigger_source='MarkPrice' if native else 'MARK_PRICE')
    if side == 'SELL':
        envelope = replace(intent.envelope, side='SELL')
        prep = intent.assessment.preparation
        sizing = replace(prep.sizing, envelope=envelope, stop_price=Decimal('110.00'))
        minimum = replace(prep.minimum_amount, envelope_sha256=c.envelope_sha256_v2(envelope))
        binding = replace(prep.binding, envelope_sha256=c.envelope_sha256_v2(envelope),
            sizing_sha256=c.sizing_sha256_v2(sizing),
            minimum_assessment_sha256=e.minimum_assessment_sha256_v2(minimum))
        prep = replace(prep, sizing=sizing, minimum_amount=minimum, binding=binding)
        bh = e.binding_sha256_v2(binding)
        original_margin = intent.assessment.margin
        assert original_margin is not None
        margin = replace(original_margin, binding_sha256=bh,
            reference=replace(original_margin.reference, input_sha256=bh))
        assessment = replace(intent.assessment, preparation=prep, margin=margin)
        protection = replace(protection, stop_price=Decimal('110.00'), take_profit_price=Decimal('80.000'))
        intent = replace(intent, envelope=envelope, assessment=assessment, protection_policy=protection)
    else:
        intent = replace(intent, protection_policy=protection)
    intent = replace(intent, position_id=position)
    # The accepted codec, rather than a side flag or fabricated CommandRecord, checks this graph.
    encode_entry_intent_v2(intent)
    return intent, policy, account, marks


class NoEffects:
    def __init__(self) -> None:
        self.calls: list[str] = []
    def market(self, **kwargs: Any) -> Any:
        self.calls.append('market')
        raise AssertionError('native factory forbidden during restore')
    def limit(self, **kwargs: Any) -> Any:
        self.calls.append('limit')
        raise AssertionError('native factory forbidden during restore')
    def submit_order(self, order: object, *, params: dict[str, object]) -> None:
        self.calls.append('submit')
        raise AssertionError('native submit forbidden during restore')
    def cancel_order(self, order: object, *, params: dict[str, object]) -> None:
        self.calls.append('cancel')
        raise AssertionError('native cancel forbidden during restore')


@pytest.fixture
async def core(tmp_path: Path) -> AsyncIterator[Any]:
    account = values('FIXTURE', 'POSITION')[0].envelope.scope.product_scope.account
    scope = ExecutionScope(account.account_id, account.environment)
    lock = AccountWorkerLock(tmp_path, account_id=scope.account_id, environment=scope.environment)
    lock.acquire()
    journal = CommandJournal(tmp_path / 'execution.sqlite3', scope)
    bound(journal)
    store = RuntimeStateStore(tmp_path / 'runtime-state.sqlite3', scope)
    history = HistoryStore(tmp_path / 'history.sqlite3', account)
    def no_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError('offline fixture attempted network')
    session = BybitDemoAccountSession(api_key='fixture-key', api_secret='fixture-secret',
        transport=httpx.MockTransport(no_network), clock=lambda: NOW)
    effects = NoEffects()
    bridge = NativeBybitBridge(scope=account, native_account_id='BYBIT-UNIFIED',
        factory=effects, strategy=effects, bindings=NativeBindings(str, str, str, str, str,
            FixedLimitBindingsV2(Limit, Init, Updated, Kind.LIMIT, Tif.IOC)))
    def no_risk(*args: Any) -> Any:
        raise AssertionError('restoration attempted fresh risk')
    kwargs = {'journal': journal, 'store': store, 'worker_lock': lock, 'lock_directory': tmp_path,
        'bridge': bridge, 'entry_risk': no_risk, 'clock': lambda: NOW + timedelta(days=1),
        'max_evidence_age': timedelta(seconds=10), 'receipt_session': session, 'history_store': history}
    result = SimpleNamespace(journal=journal, store=store, history=history, session=session,
        effects=effects, bridge=bridge, lock=lock, kwargs=kwargs)
    try:
        yield result
    finally:
        store.close()
        history.close()
        journal.close()
        lock.release()
        await session.aclose()


def mapped(core: Any, record: Any, intent: Any, **owner_changes: Any) -> None:
    owned = p.OwnedOrder(record.client_order_id, intent.position_id,
        intent.envelope.instrument_pin.native_instrument_id, intent.envelope.side, 'ENTRY',
        intent.envelope.base_quantity, False, intent.protection_policy.generation,
        intent.protection_policy.revision)
    owned = replace(owned, **owner_changes)
    state = p.register_order(p.ProtectionProjection(core.bridge.scope, owned.position_id,
        owned.instrument_id, owned.side), owned)
    wire = codec.encode(owned)
    key = 'native:' + hashlib.sha256(wire.encode()).hexdigest()
    core.store.apply_position_event(key, json.loads(wire), owned.position_id, 0,
        json.loads(codec.encode(NativeExecutionRuntime._summary(state))))


def logical(core: Any) -> Any:
    result = []
    for db in (core.journal._db, core.store._db, core.history._db):
        result.append(tuple((name, tuple(tuple(row) for row in db.execute('SELECT * FROM "' + name + '" ORDER BY rowid')))
            for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")))
    return tuple(result)


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('hook', [False, True])
def test_complete_batch_refuses_late_native_unsupported_before_hook_or_any_recovery_write(
    core: Any, reverse: bool, hook: bool,
) -> None:
    first = values('Z_FIRST' if reverse else 'A_FIRST', 'FIRST_POSITION')
    record = register(core.journal, first)
    mapped(core, record, first[0])
    claimed = claim(core.journal, first[0])
    assert claimed is not None and claimed.state is CommandState.SENDING
    late = values('A_LATE' if reverse else 'Z_LATE', 'LATE_POSITION', native=False)
    register(core.journal, late)
    before = logical(core)
    calls: list[str] = []
    kwargs = dict(core.kwargs)
    if hook:
        kwargs['_pre_recovery'] = lambda resolver: calls.append('hook')
    with pytest.raises(RuntimeBlocked):
        NativeExecutionRuntime(**kwargs)
    assert not calls, 'native hook ran before complete batch validation'
    assert logical(core) == before, 'data refusal changed durable commands/audit/meta/projection revisions'
    assert not core.effects.calls


@pytest.mark.parametrize('reverse', [False, True])
def test_two_true_j_pending_entries_cannot_own_one_absent_position(core: Any, reverse: bool) -> None:
    for key in (('Z', 'A') if reverse else ('A', 'Z')):
        register(core.journal, values(key, 'DUPLICATE_POSITION'))
    before = logical(core)
    with pytest.raises(RuntimeBlocked):
        NativeExecutionRuntime(**core.kwargs)
    assert logical(core) == before
    assert core.store.get_position('DUPLICATE_POSITION') is None
    assert not core.effects.calls


@pytest.mark.parametrize('side', ['BUY', 'SELL'])
def test_plan_expired_pending_orphan_maps_exact_original_intent_and_never_dispatches(
    core: Any, monkeypatch: pytest.MonkeyPatch, side: str,
) -> None:
    data = values('ENTRY', 'POSITION', side=side)
    first = register(core.journal, data)
    before_reservation = first.reservation_v2
    runtime = NativeExecutionRuntime(**core.kwargs)
    request = runtime.native_owned_requests()[0].request
    assert type(request) is LinearFixedLimitNativeOrderRequestV2
    assert request.owned.client_order_id == first.client_order_id
    assert request.owned.position_id == 'POSITION'
    assert request.owned.side == side and request.owned.quantity.as_tuple() == Decimal('1.00').as_tuple()
    assert request.limit_price.as_tuple() == Decimal('100.00').as_tuple()
    assert request.position_idx == 0 and request.attached.trigger_source == 'MarkPrice'
    assert runtime._policy(first) == data[0].protection_policy
    restored = runtime.journal.get('ENTRY')
    assert restored is not None and restored.payload_json == first.payload_json
    assert restored.reservation_v2 == before_reservation
    forbidden: list[str] = []
    def qualify() -> None:
        forbidden.append('qualification')
    monkeypatch.setattr(runtime, '_require_qualification', qualify)
    before = logical(core)
    with pytest.raises(RuntimeBlocked, match='V2_FINANCIAL_DISPATCH_UNAVAILABLE'):
        runtime.dispatch('ENTRY')
    with pytest.raises(RuntimeBlocked, match='V2_FINANCIAL_DISPATCH_UNAVAILABLE'):
        runtime._plan(first)
    with pytest.raises(RuntimeBlocked, match='V2_FINANCIAL_DISPATCH_UNAVAILABLE'):
        runtime._leverage_plan('ENTRY', 'CONTROL')
    assert not forbidden and logical(core) == before and not core.effects.calls
    runtime.shutdown()


@pytest.mark.parametrize('state', ['SENDING', 'UNKNOWN', 'ACKNOWLEDGED', 'REJECTED', 'TERMINAL'])
def test_mapped_v2_historical_states_keep_original_hold_and_never_resend(core: Any, state: str) -> None:
    data = values('ENTRY', 'POSITION')
    first = register(core.journal, data)
    mapped(core, first, data[0])
    claim(core.journal, data[0])
    if state == 'UNKNOWN':
        core.journal.mark_unknown('ENTRY', 'fixture-unknown')
    elif state in ('ACKNOWLEDGED', 'TERMINAL'):
        core.journal.acknowledge('ENTRY', 'fixture-ack')
    elif state == 'REJECTED':
        core.journal.reject('ENTRY', 'fixture-reject')
    if state == 'TERMINAL':
        core.journal.release_reservation('ENTRY', confirmed_terminal=True, terminal_evidence='fixture-terminal')
    original = core.journal.get('ENTRY')
    meta = tuple(core.journal._db.execute('SELECT * FROM reservation_meta_v2').fetchone())
    runtime = NativeExecutionRuntime(**core.kwargs)
    restored = core.journal.get('ENTRY')
    assert restored.state.value == ('UNKNOWN' if state == 'SENDING' else original.state.value)
    assert restored.payload_json == original.payload_json and restored.client_order_id == original.client_order_id
    assert restored.reservation_v2 == original.reservation_v2
    assert restored.terminal_confirmed is original.terminal_confirmed
    assert tuple(core.journal._db.execute('SELECT * FROM reservation_meta_v2').fetchone()) == meta
    before = logical(core)
    assert runtime.dispatch('ENTRY') == restored
    assert logical(core) == before and not core.effects.calls
    runtime.shutdown()


def test_complete_startup_trace_registers_entire_batch_before_any_recovery_write(
    core: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = values('A', 'MAPPED')
    row = register(core.journal, first)
    mapped(core, row, first[0])
    claim(core.journal, first[0])
    register(core.journal, values('B', 'ORPHAN'))
    trace: list[str] = []
    listing, mapping = core.journal.list_commands, core.bridge.register_fixed_limit_v2
    recover, store_recover = core.journal.recover_sending, core.store.recover
    def list_all(*args: Any, **kwargs: Any) -> Any:
        trace.append('list')
        return listing(*args, **kwargs)
    def register_mapping(request: Any) -> None:
        trace.append('mapping:' + request.owned.position_id)
        mapping(request)
    def recover_journal(**kwargs: Any) -> Any:
        trace.append('recover-journal')
        return recover(**kwargs)
    def recover_store(**kwargs: Any) -> Any:
        trace.append('recover-store')
        return store_recover(**kwargs)
    def hook(resolver: Any) -> None:
        trace.append('hook')
        expectations = resolver(())
        assert len(expectations) == 1 and expectations[0].request.owned.position_id == 'MAPPED'
    monkeypatch.setattr(core.journal, 'list_commands', list_all)
    monkeypatch.setattr(core.bridge, 'register_fixed_limit_v2', register_mapping)
    monkeypatch.setattr(core.journal, 'recover_sending', recover_journal)
    monkeypatch.setattr(core.store, 'recover', recover_store)
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail('restore attempted new admission/claim/release/risk authority')
    for name in ('claim', 'claim_entry_v2', 'bind_owner_epoch_v2', 'reservation_snapshot_v2',
                 'release_reservation'):
        monkeypatch.setattr(core.journal, name, forbidden)
    runtime = NativeExecutionRuntime(**core.kwargs, _pre_recovery=hook)
    assert trace[0] == 'list'
    assert trace.index('hook') < trace.index('mapping:MAPPED') < trace.index('recover-journal')
    assert trace.index('mapping:ORPHAN') < trace.index('recover-journal') < trace.index('recover-store')
    assert core.store.get_position('ORPHAN') is not None
    assert not core.effects.calls
    runtime.shutdown()


@pytest.mark.parametrize('change', ['journal', 'mapping', 'history', 'lease'])
def test_hook_change_aborts_before_runtime_recovery_or_registration(
    core: Any, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    data = values('ENTRY', 'POSITION')
    record = register(core.journal, data)
    mapped(core, record, data[0])
    claim(core.journal, data[0])
    registrations: list[str] = []
    monkeypatch.setattr(core.bridge, 'register_fixed_limit_v2', lambda request: registrations.append('mapping'))
    after_hook: list[Any] = []
    def hook(resolver: Any) -> None:
        resolver(())
        if change == 'journal':
            core.journal.mark_unknown('ENTRY', 'fixture-hook-change')
        elif change == 'mapping':
            position = core.store.get_position('POSITION')
            core.store.save_position('POSITION', json.loads(position.payload_json), position.revision)
        elif change == 'history':
            core.history.ingest(history_snapshot(core, record, orders=False, executions=False))
        else:
            core.lock.release()
        after_hook.append(logical(core))
    with pytest.raises(RuntimeBlocked):
        NativeExecutionRuntime(**core.kwargs, _pre_recovery=hook)
    assert logical(core) == after_hook[0]
    assert not registrations and not core.effects.calls


def test_missing_fixed_binding_refuses_mapping_registration_before_recovery_writes(core: Any) -> None:
    data = values('ENTRY', 'POSITION')
    record = register(core.journal, data)
    mapped(core, record, data[0])
    claim(core.journal, data[0])
    core.bridge._bindings = replace(core.bridge._bindings, fixed_limit_v2=None)
    before = logical(core)
    with pytest.raises(ValueError):
        NativeExecutionRuntime(**core.kwargs)
    assert logical(core) == before and not core.effects.calls


def history_snapshot(core: Any, record: Any, *, orders: bool = True, executions: bool = True,
                     side: str = 'Buy', client: str | None = None, venue: str = 'VENUE_1',
                     snapshot_id: str = 'HISTORY_1') -> HistorySnapshot:
    ms = int(NOW.timestamp()) * 1000
    client = record.client_order_id if client is None else client
    fill = ExecutionRecord('EXECUTION_1', venue, client, 'XAAUSDT', side, 'Trade', ms,
        Decimal('0.4'), Decimal(100), Decimal(40), Decimal('0.01'), 'USDT', Decimal('0.00025'), Decimal(0))
    order = OrderHistoryRecord(venue, record.client_order_id, 'XAAUSDT', side, 0,
        'PartiallyFilled', Decimal('1.00'), Decimal('0.4'), Decimal('0.6'), False, ms, ms)
    return HistorySnapshot(core.bridge.scope, 'XAAUSDT', snapshot_id, HistoryWindow(ms-1000, ms+1000),
        NOW + timedelta(seconds=2), NOW + timedelta(seconds=2),
        (fill,) if executions else (), (order,) if orders else (), (), False)


def test_new_v2_orphan_requires_actual_history_dependency_before_hook_or_any_write(core: Any) -> None:
    register(core.journal, values('ENTRY', 'POSITION'))
    before = logical(core)
    calls: list[str] = []
    kwargs = {**core.kwargs, 'receipt_session': None, 'history_store': None,
              '_pre_recovery': lambda resolver: calls.append('hook')}
    with pytest.raises(RuntimeBlocked, match='V2_ORPHAN_HISTORY_REQUIRED'):
        NativeExecutionRuntime(**kwargs)
    assert not calls and logical(core) == before and not core.effects.calls


@pytest.mark.parametrize('orders,executions', [(True, False), (False, True), (True, True)])
def test_relevant_order_or_execution_history_blocks_pending_orphan_before_omission(
    core: Any, orders: bool, executions: bool,
) -> None:
    record = register(core.journal, values('ENTRY', 'POSITION'))
    core.history.ingest(history_snapshot(core, record, orders=orders, executions=executions))
    before = logical(core)
    with pytest.raises(RuntimeBlocked, match='DURABLE_IMMUTABLE_MAPPING_REQUIRED'):
        NativeExecutionRuntime(**core.kwargs)
    assert logical(core) == before and not core.effects.calls


def test_recursive_both_reader_closure_learns_venue_then_exposes_foreign_execution(
    core: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = register(core.journal, values('ENTRY', 'POSITION'))
    core.history.ingest(history_snapshot(core, record, executions=False, snapshot_id='ORDER_OBSERVATION'))
    core.history.ingest(history_snapshot(core, record, orders=False, client='FOREIGN_CLIENT',
                                        snapshot_id='EXECUTION_OBSERVATION'))
    execution_reader = core.history.execution_identity_evidence
    calls: list[tuple[str, ...]] = []
    def executions(**kwargs: Any) -> Any:
        calls.append(kwargs['venue_order_ids'])
        return execution_reader(**kwargs)
    monkeypatch.setattr(core.history, 'execution_identity_evidence', executions)
    before = logical(core)
    with pytest.raises(RuntimeBlocked):
        NativeExecutionRuntime(**core.kwargs)
    assert () in calls and ('VENUE_1',) in calls
    assert logical(core) == before


@pytest.mark.parametrize('client', ['', 'FOREIGN_CLIENT'])
def test_native_seed_restricts_orphan_history_before_any_registration_or_recovery(
    core: Any, client: str,
) -> None:
    record = register(core.journal, values('ENTRY', 'POSITION'))
    core.history.ingest(history_snapshot(core, record, orders=False, client=client))
    before = logical(core)
    def hook(resolver: Any) -> None:
        resolver(((record.client_order_id, 'VENUE_1'),))
    with pytest.raises(RuntimeBlocked):
        NativeExecutionRuntime(**core.kwargs, _pre_recovery=hook)
    assert logical(core) == before and not core.effects.calls


def test_mapped_partial_execution_is_identity_history_not_whole_quantity_or_price_proof(core: Any) -> None:
    data = values('ENTRY', 'POSITION')
    record = register(core.journal, data)
    mapped(core, record, data[0])
    snap = history_snapshot(core, record)
    fill = replace(snap.executions[0], quantity=Decimal('0.1'), price=Decimal(111), value=Decimal('11.1'))
    core.history.ingest(replace(snap, executions=(fill,)))
    runtime = NativeExecutionRuntime(**core.kwargs)
    before = logical(core)
    owners = runtime.native_owned_requests()
    assert owners[0].venue_order_ids == frozenset({'VENUE_1'})
    assert owners[0].request.owned.quantity.as_tuple() == Decimal('1.00').as_tuple()
    assert owners[0].request.limit_price == Decimal(100)
    assert runtime.projection('POSITION').owned_quantity == 0
    assert logical(core) == before and not core.effects.calls
    runtime.shutdown()


def retained_credit(core: Any, record: Any, intent: Any, *, changed: Any = None) -> Any:
    from multi_exchange_trader.execution.execution_credit import (
        ExecutionObservationReference,
        execution_guard,
    )
    from multi_exchange_trader.execution.history_recovery import (
        HistoryExecutionCredit,
        HistoryExecutionEvidence,
        HistoryOrderEvidence,
    )
    from multi_exchange_trader.execution.runtime import _ReplayContext
    snap = history_snapshot(core, record)
    key = core.history.ingest(snap).observation_key
    execution = core.history.execution_evidence(key, 'EXECUTION_1')
    order = core.history.order_evidence(client_order_id=record.client_order_id, venue_order_ids=())[0]
    source = ExecutionObservationReference(snap.scope, 'FIXTURE_RETAINED_BINDING', key,
        snap.snapshot_id, snap.symbol, snap.window.start_ms, snap.window.end_ms,
        snap.observed_at, snap.server_observed_at)
    owned = codec.decode(core.store.list_position_events(intent.position_id)[0].payload_json)
    assert type(owned) is p.OwnedOrder
    credit = HistoryExecutionCredit(HistoryExecutionEvidence(source, execution.record, execution.variant_hash),
        HistoryOrderEvidence(source, order.record, order.variant_hash), owned, record.command_key, record.payload_hash)
    if changed is not None:
        credit = changed(credit)
    state = p.ProtectionProjection(snap.scope, intent.position_id,
        intent.envelope.instrument_pin.native_instrument_id, intent.envelope.side)
    identities: dict[str, tuple[object, ...]] = {}
    context = _ReplayContext()
    state = NativeExecutionRuntime._reduce_event(state, owned, identities, context)
    state = NativeExecutionRuntime._reduce_event(state, credit, identities, context)
    summary = NativeExecutionRuntime._summary(state, identities, context.credit, context.history)
    guard = execution_guard(snap.scope, owned.instrument_id, 'EXECUTION_1')
    current = core.store.get_position(intent.position_id)
    core.store.apply_position_event(guard, json.loads(codec.encode(credit)), intent.position_id,
        current.revision, json.loads(codec.encode(summary)))
    return credit


def test_retained_history_credit_replays_original_v2_owner_and_hold(core: Any) -> None:
    data = values('ENTRY', 'POSITION')
    record = register(core.journal, data)
    mapped(core, record, data[0])
    claim(core.journal, data[0])
    retained_credit(core, record, data[0])
    before = core.journal.get('ENTRY')
    runtime = NativeExecutionRuntime(**core.kwargs)
    assert runtime.projection('POSITION').owned_quantity == Decimal('0.4')
    assert runtime._policy(before).stop_price.as_tuple() == Decimal('90.00').as_tuple()
    restored = runtime.journal.get('ENTRY')
    assert restored is not None and restored.reservation_v2 == before.reservation_v2
    assert not core.effects.calls
    runtime.shutdown()


def test_history_owner_scale_only_mutation_refuses_before_startup_writes(core: Any) -> None:
    from multi_exchange_trader.execution.execution_credit import execution_guard
    from multi_exchange_trader.execution.runtime import _ReplayContext
    data = values('ENTRY', 'POSITION')
    record = register(core.journal, data)
    mapped(core, record, data[0])
    claim(core.journal, data[0])
    credit = retained_credit(core, record, data[0])
    runtime = NativeExecutionRuntime(**core.kwargs)
    assert runtime.projection('POSITION').owned_quantity == Decimal('0.4')
    runtime.shutdown()
    # Negative isolated corruption keeps aggregate replay internally coherent so
    # rejection must come from immutable original owner representation.
    changed = replace(credit, owned_order=replace(credit.owned_order, quantity=Decimal('1.0')))
    owned = codec.decode(core.store.list_position_events('POSITION')[0].payload_json)
    assert type(owned) is p.OwnedOrder
    state = p.ProtectionProjection(core.bridge.scope, 'POSITION', owned.instrument_id, owned.side)
    identities: dict[str, tuple[object, ...]] = {}
    context = _ReplayContext()
    state = NativeExecutionRuntime._reduce_event(state, owned, identities, context)
    state = NativeExecutionRuntime._reduce_event(state, changed, identities, context)
    summary = NativeExecutionRuntime._summary(state, identities, context.credit, context.history)
    guard = execution_guard(core.bridge.scope, owned.instrument_id, 'EXECUTION_1')
    core.store._db.execute('UPDATE runtime_inbox SET event_payload=? WHERE event_id=?',
                          (codec.encode(changed), guard))
    core.store._db.execute('UPDATE runtime_positions SET payload=? WHERE position_id=?',
                          (codec.encode(summary), 'POSITION'))
    before = logical(core)
    with pytest.raises(RuntimeBlocked, match='DURABLE_IMMUTABLE_MAPPING_REQUIRED'):
        runtime._prior_credit(guard)
    with pytest.raises(RuntimeBlocked, match='DURABLE_IMMUTABLE_MAPPING_REQUIRED'):
        NativeExecutionRuntime(**core.kwargs)
    assert logical(core) == before and not core.effects.calls


@pytest.mark.parametrize('changes', [
    {'client_order_id': 'ec_' + 'f'*32}, {'position_id': 'OTHER'},
    {'instrument_id': 'OTHER-LINEAR.BYBIT'}, {'side': 'SELL'}, {'role': 'EXIT'},
    {'quantity': Decimal('1.0')}, {'reduce_only': True}, {'generation': 1},
    {'policy_revision': 'OTHER'}, {'trigger_price': Decimal(90)}, {'trigger_source': 'MarkPrice'},
])
def test_every_original_v2_owned_field_is_checked_before_startup_mutation(core: Any, changes: Any) -> None:
    data = values('ENTRY', 'POSITION')
    record = register(core.journal, data)
    mapped(core, record, data[0], **changes)
    claim(core.journal, data[0])
    before = logical(core)
    with pytest.raises(ValueError):
        NativeExecutionRuntime(**core.kwargs)
    assert logical(core) == before and not core.effects.calls


@pytest.mark.parametrize('field,value', [('generation', True), ('generation', 2**63),
    ('quantity', 1), ('reduce_only', 0), ('position_id', 1), ('trigger_price', False)])
def test_v2_owned_leaf_types_fail_before_comparison_methods(core: Any, field: str, value: Any) -> None:
    data = values('ENTRY', 'POSITION')
    record = register(core.journal, data)
    mapped(core, record, data[0])
    runtime = NativeExecutionRuntime(**core.kwargs)
    state = runtime.projection('POSITION')
    owner = replace(state.orders[0])
    object.__setattr__(owner, field, value)
    forged = replace(state)
    object.__setattr__(forged, 'orders', (owner,))
    before = logical(core)
    with pytest.raises(RuntimeBlocked):
        runtime._request_for_state(record, forged)
    assert logical(core) == before
    runtime.shutdown()


def test_maximum_supported_generation_and_original_protection_representation_survive(core: Any) -> None:
    data = values('ENTRY', 'POSITION', side='SELL')
    intent = replace(data[0], protection_policy=replace(data[0].protection_policy, generation=2**63-1))
    data = (intent, *data[1:])
    record = register(core.journal, data)
    runtime = NativeExecutionRuntime(**core.kwargs)
    request = runtime.native_owned_requests()[0].request
    assert type(request) is LinearFixedLimitNativeOrderRequestV2
    assert request.owned.generation == 2**63-1
    assert request.attached.stop_loss.as_tuple() == Decimal('110.00').as_tuple()
    assert request.attached.take_profit.as_tuple() == Decimal('80.000').as_tuple()
    assert core.journal.get('ENTRY').payload_json == record.payload_json
    runtime.shutdown()


@pytest.mark.parametrize('frame', [
    {'schema': 'FUTURE_FRAME', 'record_type': 'DurableEntryIntentV2'},
    {'wire_version': 99, 'fields': {}},
    {'wire_version': 99, 'content_sha256': 'a'*64},
    {'fields': {}, 'content_sha256': 'a'*64},
])
def test_reserved_outer_family_never_uses_legacy_decode_or_qualification(
    core: Any, monkeypatch: pytest.MonkeyPatch, frame: Any,
) -> None:
    from multi_exchange_trader.execution.journal import CommandKind
    runtime = NativeExecutionRuntime(**core.kwargs)
    core.journal.register('FUTURE', CommandKind.ENTRY, frame)
    calls: list[str] = []
    monkeypatch.setattr(runtime, '_require_qualification', lambda: calls.append('qualification'))
    monkeypatch.setattr(runtime, '_request', lambda record: calls.append('request'))
    before = logical(core)
    with pytest.raises(RuntimeBlocked, match='V2_FINANCIAL_DISPATCH_UNAVAILABLE'):
        runtime.dispatch('FUTURE')
    runtime.shutdown()
    def no_decode(*args: Any, **kwargs: Any) -> Any:
        pytest.fail('reserved frame reached legacy codec')
    monkeypatch.setattr(codec, 'decode', no_decode)
    with pytest.raises(RuntimeBlocked, match='DURABLE_V2_RESERVATION_REQUIRED'):
        NativeExecutionRuntime(**core.kwargs)
    assert not calls and logical(core) == before


@pytest.mark.parametrize('corruption', ['future', 'duplicate', 'late_terminal'])
def test_true_j_complete_list_refuses_corrupt_linked_rows_before_hook(
    core: Any, corruption: str,
) -> None:
    data = values('A_GOOD', 'GOOD')
    good = register(core.journal, data)
    mapped(core, good, data[0])
    claim(core.journal, data[0])
    bad_data = values('Z_BAD', 'BAD')
    bad = register(core.journal, bad_data)
    raw = json.loads(bad.payload_json)
    if corruption == 'late_terminal':
        claim(core.journal, bad_data[0])
        core.journal.acknowledge('Z_BAD', 'fixture-ack')
        core.journal.release_reservation('Z_BAD', confirmed_terminal=True, terminal_evidence='fixture-terminal')
    if corruption in ('future', 'late_terminal'):
        raw['wire_version'] = 99
        wire = json.dumps(raw, sort_keys=True, separators=(',', ':'))
    else:
        wire = bad.payload_json.replace('"wire_version":2', '"wire_version":2,"wire_version":2')
        assert wire != bad.payload_json
    core.journal._db.execute('UPDATE commands SET payload=?,payload_hash=? WHERE command_key=?',
        (wire, hashlib.sha256(wire.encode()).hexdigest(), 'Z_BAD'))
    before = logical(core)
    calls: list[str] = []
    with pytest.raises(ValueError):
        NativeExecutionRuntime(**core.kwargs, _pre_recovery=lambda resolver: calls.append('hook'))
    assert not calls and logical(core) == before


async def test_v2_leverage_refuses_before_qualification_or_control_even_with_open_spy(core: Any, monkeypatch: Any) -> None:
    register(core.journal, values('ENTRY', 'POSITION'))
    runtime = NativeExecutionRuntime(**core.kwargs)
    calls: list[str] = []
    monkeypatch.setattr(runtime, '_require_qualification', lambda: calls.append('qualification'))
    before = logical(core)
    with pytest.raises(RuntimeBlocked, match='V2_FINANCIAL_DISPATCH_UNAVAILABLE'):
        await runtime.apply_leverage('CONTROL', 'ENTRY', control=cast(Any, None), adapter=cast(Any, None))
    assert not calls and logical(core) == before and not core.effects.calls
    runtime.shutdown()


def test_legacy_coordinator_three_fields_schema_version_metadata_and_market_path_stay_legacy(
    setup: Any,
) -> None:
    from multi_exchange_trader.execution.journal import CommandKind
    worker, journal, _store, strategy, kwargs, module = setup
    first = legacy_queue(worker)
    raw = json.loads(first.payload_json)
    payload = {'position_id': 'COORDINATOR_POSITION', 'plan': raw['plan'],
        'protection_policy': raw['protection_policy'], 'schema_version': 2,
        'archive_note': 'ordinary text RISK_V2_DURABLE_INTENT remains legacy'}
    record = journal.register('COORDINATOR', CommandKind.ENTRY, payload)
    plan, policy = worker._plan(record), worker._policy(record)
    owner = p.OwnedOrder(record.client_order_id, payload['position_id'],
        plan.scope.symbol + '-LINEAR.BYBIT', 'BUY', 'ENTRY', plan.quantity, False,
        policy.generation, policy.revision)
    worker._register(p.ProtectionProjection(worker.bridge.scope, owner.position_id,
        owner.instrument_id, owner.side), owner)
    worker.shutdown()
    restored = module.NativeExecutionRuntime(**kwargs)
    requests = restored.native_owned_requests()
    assert len(requests) == 2
    assert all(type(item.request).__name__ == 'NativeOrderRequest' for item in requests)
    assert all(item.request.order_type == 'MARKET' for item in requests)
    assert restored._entry_version(record) == 1
    assert restored._policy(record) == policy and not strategy.commands
    restored.shutdown()


def test_v2_entry_and_legitimate_legacy_exit_share_one_position_without_duplicate_entry_conflict(core: Any) -> None:
    from multi_exchange_trader.execution.journal import CommandKind
    from multi_exchange_trader.execution.runtime import _ReplayContext
    data = values('ENTRY', 'POSITION')
    record = register(core.journal, data)
    mapped(core, record, data[0])
    claim(core.journal, data[0])
    credit = retained_credit(core, record, data[0])
    exit_record = core.journal.register('EXIT', CommandKind.EXIT, {'position_id': 'POSITION',
        'quantity': '0.4', 'reference_price': '100', 'policy_revision': 'POLICY_V1'})
    owned = credit.owned_order
    exiting = p.OwnedOrder(exit_record.client_order_id, 'POSITION', owned.instrument_id,
        'SELL', 'EXIT', Decimal('0.4'), True, 0, 'POLICY_V1')
    context = _ReplayContext()
    identities: dict[str, tuple[object, ...]] = {}
    state = p.ProtectionProjection(core.bridge.scope, 'POSITION', owned.instrument_id, 'BUY')
    for event in (owned, credit, exiting):
        state = NativeExecutionRuntime._reduce_event(state, event, identities, context)
    wire = codec.encode(exiting)
    current = core.store.get_position('POSITION')
    core.store.apply_position_event('native:' + hashlib.sha256(wire.encode()).hexdigest(), json.loads(wire),
        'POSITION', current.revision, json.loads(codec.encode(
            NativeExecutionRuntime._summary(state, identities, context.credit, context.history))))
    runtime = NativeExecutionRuntime(**core.kwargs)
    requests = runtime.native_owned_requests()
    assert len(requests) == 2
    assert {item.request.owned.position_id for item in requests} == {'POSITION'}
    exiting_request = next(item.request for item in requests if item.request.owned.role == 'EXIT')
    assert exiting_request.order_type == 'MARKET' and exiting_request.owned.reduce_only is True
    assert runtime.projection('POSITION').owned_quantity == Decimal('0.4')
    assert not core.effects.calls
    runtime.shutdown()


def test_v2_management_readback_keeps_original_policy_and_records_blocked_authority(core: Any) -> None:
    from multi_exchange_trader.execution.venue_readback import VenueSnapshot
    data = values('ENTRY', 'POSITION')
    record = register(core.journal, data)
    runtime = NativeExecutionRuntime(**core.kwargs)
    before = core.journal.get('ENTRY'), core.journal.audit_events()
    snapshot = VenueSnapshot(core.bridge.scope, 'XAAUSDT', 'OFFLINE_CYCLE',
        NOW + timedelta(days=1), None, False, (), (), ('FIXTURE_READBACK_INCOMPLETE',))
    assessment = runtime.consume_readback('POSITION', snapshot)
    assert assessment.entries_blocked is True
    assert 'AUTHENTICATED_ACCOUNT_BINDING_REQUIRED' in assessment.reasons
    assert runtime._policy(record) == data[0].protection_policy
    assert (core.journal.get('ENTRY'), core.journal.audit_events()) == before
    assert not core.effects.calls
    runtime.shutdown()


class EqualityMustNotRun(str):
    def __eq__(self, other: object) -> bool:
        raise AssertionError('malformed leaf equality ran before exact type gate')
    def __ne__(self, other: object) -> bool:
        raise AssertionError('malformed leaf equality ran before exact type gate')


def test_projection_and_historical_owner_client_leaf_types_are_gated_before_equality(core: Any) -> None:
    data = values('ENTRY', 'POSITION')
    record = register(core.journal, data)
    mapped(core, record, data[0])
    claim(core.journal, data[0])
    credit = retained_credit(core, record, data[0])
    runtime = NativeExecutionRuntime(**core.kwargs)
    state = runtime.projection('POSITION')
    bad = replace(state.orders[0])
    object.__setattr__(bad, 'client_order_id', EqualityMustNotRun(record.client_order_id))
    forged = replace(state)
    object.__setattr__(forged, 'orders', (bad,))
    with pytest.raises(RuntimeBlocked):
        runtime._request_for_state(record, forged)
    changed_credit = replace(credit)
    object.__setattr__(changed_credit, 'owned_order', bad)
    with pytest.raises(RuntimeBlocked):
        runtime._verify_history_command(changed_credit, state)
    runtime.shutdown()


def wider_quantity_values(key: str, position: str, digits: int) -> Any:
    data = values(key, position)
    intent = data[0]
    quantity = Decimal('1.' + '0'*(digits-1))
    envelope = replace(intent.envelope, base_quantity=quantity)
    prep = intent.assessment.preparation
    sizing = replace(prep.sizing, envelope=envelope)
    minimum = replace(prep.minimum_amount, envelope_sha256=c.envelope_sha256_v2(envelope))
    binding = replace(prep.binding, envelope_sha256=c.envelope_sha256_v2(envelope),
        sizing_sha256=c.sizing_sha256_v2(sizing),
        minimum_assessment_sha256=e.minimum_assessment_sha256_v2(minimum))
    prep = replace(prep, sizing=sizing, minimum_amount=minimum, binding=binding)
    bh = e.binding_sha256_v2(binding)
    margin0 = intent.assessment.margin
    assert margin0 is not None
    margin = replace(margin0, quantity=quantity, binding_sha256=bh,
        reference=replace(margin0.reference, input_sha256=bh))
    assessment = replace(intent.assessment, preparation=prep, margin=margin)
    intent = replace(intent, envelope=envelope, assessment=assessment)
    encode_entry_intent_v2(intent)
    return (intent, *data[1:])


def test_inert_codec_valid_quantity_width_is_not_narrowed_or_coerced_for_native_mapping(core: Any) -> None:
    data = wider_quantity_values('ENTRY', 'POSITION', 257)
    record = register(core.journal, data)
    assert len(data[0].envelope.base_quantity.as_tuple().digits) == 257
    before = logical(core)
    with pytest.raises(RuntimeBlocked, match='DURABLE_NATIVE_V2_MAPPING_UNSUPPORTED'):
        NativeExecutionRuntime(**core.kwargs)
    assert core.journal.get('ENTRY').payload_json == record.payload_json
    assert logical(core) == before and not core.effects.calls


@pytest.mark.parametrize('header', ['user_version', 'application_id'])
def test_hook_metadata_header_change_is_not_permission_to_mutate_application_state(core: Any, header: str) -> None:
    data = values('ENTRY', 'POSITION')
    row = register(core.journal, data)
    mapped(core, row, data[0])
    claim(core.journal, data[0])
    after: list[Any] = []
    def hook(resolver: Any) -> None:
        resolver(())
        core.store._db.execute('PRAGMA ' + header + '=99')
        after.append(logical(core))
    with pytest.raises(RuntimeBlocked, match='DURABLE_RESTORE_CHANGED'):
        NativeExecutionRuntime(**core.kwargs, _pre_recovery=hook)
    assert logical(core) == after[0] and not core.effects.calls


def test_unlinked_entry_without_known_legacy_shape_refuses_with_fixed_version_error(core: Any) -> None:
    from multi_exchange_trader.execution.journal import CommandKind
    core.journal.register('MALFORMED', CommandKind.ENTRY, {'schema_version': 2, 'archive_note': 'plain metadata'})
    before = logical(core)
    with pytest.raises(RuntimeBlocked, match='INVALID_DURABLE_ENTRY_VERSION'):
        NativeExecutionRuntime(**core.kwargs)
    assert logical(core) == before


@pytest.mark.parametrize('retained', ['mapped', 'empty'])
def test_pending_orphan_cannot_overwrite_any_retained_position_before_hook(core: Any, retained: str) -> None:
    data = values('A', 'POSITION')
    if retained == 'mapped':
        original = register(core.journal, data)
        mapped(core, original, data[0])
    else:
        empty = p.ProtectionProjection(core.bridge.scope, 'POSITION', 'XAAUSDT-LINEAR.BYBIT', 'BUY')
        core.store.save_position('POSITION', json.loads(codec.encode(NativeExecutionRuntime._summary(empty))), 0)
    register(core.journal, values('B', 'POSITION'))
    before = logical(core)
    calls: list[str] = []
    with pytest.raises(RuntimeBlocked):
        NativeExecutionRuntime(**core.kwargs, _pre_recovery=lambda resolver: calls.append('hook'))
    assert not calls and logical(core) == before


def test_orphan_commit_rechecks_expected_absence_after_recovery_callback(core: Any, monkeypatch: Any) -> None:
    register(core.journal, values('ENTRY', 'POSITION'))
    recover = core.store.recover
    inserted: list[Any] = []
    def collision(**kwargs: Any) -> None:
        recover(**kwargs)
        empty = p.ProtectionProjection(core.bridge.scope, 'POSITION', 'XAAUSDT-LINEAR.BYBIT', 'BUY')
        inserted.append(core.store.save_position('POSITION',
            json.loads(codec.encode(NativeExecutionRuntime._summary(empty))), 0))
    monkeypatch.setattr(core.store, 'recover', collision)
    with pytest.raises(RuntimeBlocked, match='DURABLE_IMMUTABLE_MAPPING_REQUIRED'):
        NativeExecutionRuntime(**core.kwargs)
    assert core.store.get_position('POSITION') == inserted[0]
    assert not core.store.list_position_events('POSITION') and not core.effects.calls


@pytest.mark.parametrize('changes', [{'quantity': Decimal(2), 'leaves_quantity': Decimal('1.6')},
    {'side': 'Sell'}, {'position_idx': 1}, {'reduce_only': True}])
def test_mapped_v2_history_still_requires_original_whole_order_identity(core: Any, changes: Any) -> None:
    data = values('ENTRY', 'POSITION')
    record = register(core.journal, data)
    mapped(core, record, data[0])
    claim(core.journal, data[0])
    snap = history_snapshot(core, record, executions=False)
    core.history.ingest(replace(snap, orders=(replace(snap.orders[0], **changes),)))
    before = logical(core)
    with pytest.raises(RuntimeBlocked):
        NativeExecutionRuntime(**core.kwargs)
    assert logical(core) == before and not core.effects.calls


def test_mapped_v2_duplicate_or_multiple_venue_history_is_not_a_single_owner(core: Any) -> None:
    data = values('ENTRY', 'POSITION')
    record = register(core.journal, data)
    mapped(core, record, data[0])
    for venue in ('VENUE_1', 'VENUE_2'):
        core.history.ingest(history_snapshot(core, record, executions=False, venue=venue, snapshot_id=venue))
    before = logical(core)
    with pytest.raises(RuntimeBlocked):
        NativeExecutionRuntime(**core.kwargs)
    assert logical(core) == before and not core.effects.calls


def test_mapped_v2_without_history_reader_rejects_nontext_retained_native_venue_before_recovery(core: Any) -> None:
    data = values('ENTRY', 'POSITION')
    record = register(core.journal, data)
    mapped(core, record, data[0])
    claim(core.journal, data[0])
    owner = codec.decode(core.store.list_position_events('POSITION')[0].payload_json)
    assert type(owner) is p.OwnedOrder
    state = p.register_order(p.ProtectionProjection(core.bridge.scope, 'POSITION', owner.instrument_id, owner.side), owner)
    # Negative isolated retained status; this is not native execution/channel evidence.
    bad = ('ACCEPTED', record.client_order_id, 123, None, None, None)
    wire = codec.encode(bad)
    current = core.store.get_position('POSITION')
    core.store.apply_position_event('native:' + hashlib.sha256(wire.encode()).hexdigest(),
        json.loads(wire), 'POSITION', current.revision,
        json.loads(codec.encode(NativeExecutionRuntime._summary(state))))
    kwargs = {**core.kwargs, 'receipt_session': None, 'history_store': None}
    before = logical(core)
    with pytest.raises(RuntimeBlocked, match='DURABLE_IMMUTABLE_MAPPING_REQUIRED'):
        NativeExecutionRuntime(**kwargs)
    assert logical(core) == before and not core.effects.calls



def test_legacy_conflicted_reopen_keeps_observation_but_native_resolver_and_hook_refuse(live: Any) -> None:
    from test_runtime_history_recovery import observed, owned, recover
    record = owned(live)
    snapshot = observed(record)
    assert recover(live, snapshot).status == 'CREDITED'
    changed = replace(snapshot, snapshot_id='LEGACY_CONTRADICTION', orders=(),
        executions=(replace(snapshot.executions[0], fee=Decimal('0.02')),))
    assert recover(live, changed).status == 'CONFLICT'
    live.runtime.shutdown()
    before_hold = live.journal.get(record.command_key)
    reopened = NativeExecutionRuntime(**live.kwargs)
    assert reopened.projection('position-1').owned_quantity == Decimal('0.4')
    assert reopened.projection('position-1').conflicts
    assert live.journal.get(record.command_key) == replace(before_hold, state=CommandState.UNKNOWN)
    with pytest.raises(RuntimeBlocked, match='DURABLE_IMMUTABLE_MAPPING_REQUIRED'):
        reopened.native_owned_requests()
    reopened.shutdown()
    before = logical(live)
    def strict_hook(resolver: Any) -> None:
        resolver(())
    with pytest.raises(RuntimeBlocked, match='DURABLE_IMMUTABLE_MAPPING_REQUIRED'):
        NativeExecutionRuntime(**live.kwargs, _pre_recovery=strict_hook)
    assert logical(live) == before and not live.strategy.commands


def test_mapped_v2_without_reader_keeps_valid_two_hundred_character_historical_venue(core: Any) -> None:
    data = values('ENTRY', 'POSITION')
    record = register(core.journal, data)
    mapped(core, record, data[0])
    owner = codec.decode(core.store.list_position_events('POSITION')[0].payload_json)
    assert type(owner) is p.OwnedOrder
    state = p.register_order(p.ProtectionProjection(core.bridge.scope, 'POSITION', owner.instrument_id, owner.side), owner)
    venue = 'V' * 200
    status = ('ACCEPTED', record.client_order_id, venue, None, None, None)
    wire = codec.encode(status)
    current = core.store.get_position('POSITION')
    core.store.apply_position_event('native:' + hashlib.sha256(wire.encode()).hexdigest(),
        json.loads(wire), 'POSITION', current.revision,
        json.loads(codec.encode(NativeExecutionRuntime._summary(state))))
    kwargs = {**core.kwargs, 'receipt_session': None, 'history_store': None}
    runtime = NativeExecutionRuntime(**kwargs)
    assert runtime.native_owned_requests()[0].venue_order_ids == frozenset({venue})
    assert not core.effects.calls
    runtime.shutdown()


def test_reader_callback_cannot_lose_original_lease_before_native_hook(core: Any, monkeypatch: Any) -> None:
    data = values('ENTRY', 'POSITION')
    record = register(core.journal, data)
    mapped(core, record, data[0])
    reader = core.history.order_evidence
    def lose_lease(**kwargs: Any) -> Any:
        result = reader(**kwargs)
        core.lock.release()
        return result
    monkeypatch.setattr(core.history, 'order_evidence', lose_lease)
    calls: list[str] = []
    before = logical(core)
    with pytest.raises(RuntimeBlocked):
        NativeExecutionRuntime(**core.kwargs, _pre_recovery=lambda resolver: calls.append('hook'))
    assert not calls and logical(core) == before
