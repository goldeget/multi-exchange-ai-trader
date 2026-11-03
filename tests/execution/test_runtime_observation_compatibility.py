"""Blocked observation is data; strict native ownership remains a separate pass."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from test_bybit_fixed_limit import Init, Kind, Limit, Tif, Updated
from test_execution_credit import replay_credit
from test_native_quarantine import candidates, seed_credit
from test_reservations_v2 import bound, claim, register
from test_runtime import consume, queue
from test_runtime import setup as setup  # noqa: PLC0414
from test_runtime_restore_v2 import mapped as v2_mapped
from test_runtime_restore_v2 import values as v2_values

from multi_exchange_trader.execution import codec
from multi_exchange_trader.execution import linear_risk_evidence_v2 as e
from multi_exchange_trader.execution import protection as p
from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.bybit_native import FixedLimitBindingsV2
from multi_exchange_trader.execution.execution_credit import (
    CreditReplayState,
    ExecutionCorroborationConflict,
    execution_guard,
    reduce_credit_event,
)
from multi_exchange_trader.execution.execution_provenance import FillQuarantineRecord
from multi_exchange_trader.execution.journal import CommandKind


def rows(journal: Any, store: Any) -> Any:
    return tuple(tuple((name, tuple(tuple(row) for row in db.execute('SELECT * FROM "' + name + '"')))
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"))
        for db in (journal._db, store._db))


def credit_with_label(worker: Any, store: Any, candidate: Any, label: str) -> Any:
    credit = replace(replay_credit(candidate), command_key=label)
    state, replay = reduce_credit_event(worker.projection(candidate.fill.position_id), credit,
        candidates=worker.persisted_candidates(), replay=CreditReplayState())
    identities = {candidate.fill.execution_id: (worker._candidate_identity(candidate),)}
    current = store.get_position(state.position_id)
    store.apply_position_event(execution_guard(state.scope, state.instrument_id, candidate.fill.execution_id),
        json.loads(codec.encode(credit)), state.position_id, current.revision,
        json.loads(codec.encode(worker._summary(state, identities, replay))))
    return credit


def legacy_graph(fixture: Any, change: str = '') -> Any:
    worker, journal, store, _, _, _ = fixture
    record = queue(worker)
    journal.claim('entry')
    consume(worker, record.client_order_id, 'OrderFilled', reconciliation=False)
    original = candidates(store)[0].candidate
    if change in ('absent-label', 'matching-label', 'real-other-key'):
        if change == 'real-other-key':
            journal.register('other-real', CommandKind.CANCEL, {'fixture': 'unrelated-control'})
        label = {'absent-label': 'retained-missing-label', 'matching-label': record.command_key,
                 'real-other-key': 'other-real'}[change]
        prior = credit_with_label(worker, store, original, label)
    else:
        prior = seed_credit(worker, store, original)
    first = worker.projection('position-1')
    other = replace(original, fill=replace(original.fill,
        position_id='a-other', client_order_id='ec_' + 'a' * 32), venue_order_id='other-venue')
    order = replace(first.orders[0], position_id='a-other', client_order_id=other.fill.client_order_id)
    if change == 'owner':
        order = replace(order, policy_revision='changed-policy')
    elif change == 'fill':
        other = replace(other, fill=replace(other.fill, quantity=Decimal('0.40')))
    elif change == 'venue':
        other = replace(other, venue_order_id=original.venue_order_id)
    elif change == 'exposure':
        other = replace(other, fill=replace(other.fill, execution_id='foreign-effective-execution'))
    worker._register(p.ProtectionProjection(first.scope, 'a-other', order.instrument_id, 'BUY'), order)
    state = worker.projection('a-other')
    if change == 'bare':
        return record, original, prior, other, state
    quarantine = FillQuarantineRecord(other, 'AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED')
    worker._persist(quarantine, worker._reduce_event(state, quarantine, {}))
    if change in ('candidate-only', 'exposure'):
        if change == 'exposure':
            seed_credit(worker, store, other)
            exposed = worker.projection('a-other')
            assert exposed.owned_quantity > 0 and exposed.executions
        return record, original, prior, other, worker.projection('a-other')
    decision = ExecutionCorroborationConflict(worker._candidate_identity(other), first.scope,
        other.fill.instrument_id, other.fill.execution_id, other.venue_order_id,
        other.fill.client_order_id, prior.source, prior.history_variant_hash, (),
        ('VENUE_ORDER_ID_MISMATCH',), execution_guard(first.scope,
            original.fill.instrument_id, original.fill.execution_id))
    if change == 'missing-prior':
        decision = replace(decision, prior_credit_event_id=None)
    elif change == 'source':
        decision = replace(decision, source=replace(decision.source,
            observed_at=decision.source.observed_at + timedelta(seconds=1)))
    elif change == 'hash':
        decision = replace(decision, history_variant_hash='a' * 64)
    elif change == 'decision':
        decision = replace(decision, client_order_id='ec_' + 'b' * 32)
    elif change == 'reason':
        decision = replace(decision, mismatch_codes=('PRICE_MISMATCH',))
    reduced, replay = reduce_credit_event(worker.projection('a-other'), decision,
        candidates=(other,), replay=CreditReplayState())
    wire = codec.encode(decision)
    store.apply_position_event('decision:' + hashlib.sha256(wire.encode()).hexdigest(),
        json.loads(wire), 'a-other', store.get_position('a-other').revision,
        json.loads(codec.encode(worker._summary(reduced,
            {other.fill.execution_id: (worker._candidate_identity(other),)}, replay))))
    if change == 'second-stray':
        stray = replace(order, client_order_id='ec_' + 'c' * 32, position_id='z-stray')
        worker._register(p.ProtectionProjection(first.scope, stray.position_id, stray.instrument_id, 'BUY'), stray)
    elif change == 'unknown-command':
        journal._db.execute("DELETE FROM commands WHERE command_key='entry'")
    elif change in ('wrong-prior', 'invalid-prior'):
        payload = codec.encode(order) if change == 'wrong-prior' else '{}'
        store._db.execute('UPDATE runtime_inbox SET event_payload=? WHERE event_id=?',
            (payload, decision.prior_credit_event_id))
    elif change == 'missing-quarantine':
        store._db.execute('DELETE FROM runtime_inbox WHERE event_id=?',
            ('native:' + worker._candidate_identity(original),))
    elif change == 'changed-guard':
        store._db.execute('UPDATE runtime_inbox SET event_id=? WHERE event_id=?',
            ('native:wrong-original-guard', decision.prior_credit_event_id))
    elif change == 'changed-original-source':
        changed = replace(prior, source=replace(prior.source,
            observed_at=prior.source.observed_at + timedelta(seconds=1)))
        store._db.execute('UPDATE runtime_inbox SET event_payload=? WHERE event_id=?',
            (codec.encode(changed), decision.prior_credit_event_id))
    elif change == 'ambiguous-owner':
        duplicate = replace(first.orders[0], position_id='z-duplicate')
        worker._register(p.ProtectionProjection(first.scope, duplicate.position_id, duplicate.instrument_id, 'BUY'), duplicate)
    return record, original, prior, other, reduced


@pytest.mark.parametrize('malformation', ['mixed', 'duplicate', 'unbacked', 'genuine'])
def test_monitoring_keeps_the_same_blocked_view_and_candidate_context(setup: Any, malformation: str) -> None:
    worker, journal, store, strategy, _, _ = setup
    record = queue(worker)
    journal.claim(record.command_key)
    consume(worker, record.client_order_id, 'OrderFilled')
    state = worker.projection('position-1')
    hold = state.conflicts[0]
    if malformation != 'genuine':
        conflicts = {'mixed': (hold, p.ProjectionConflict('REAL_CONFLICT')),
                     'duplicate': (hold, hold), 'unbacked': (hold,)}[malformation]
        worker._persist(('READBACK', 'malformed-domain-input', ()), replace(state, conflicts=conflicts))
        if malformation == 'unbacked':
            worker._projections['position-1'][3].candidates = ()
    cache = worker._projections['position-1']
    context = cache[3]
    before_candidates = context.candidates
    before = rows(journal, store)
    first = worker.monitoring_state()
    assert first.conflicts == (() if malformation == 'genuine' else ('PROJECTION_CONFLICT',))
    assert first.candidates == before_candidates
    assert worker._projections['position-1'] is cache
    assert worker._projections['position-1'][3] is context
    assert context.candidates is before_candidates
    assert worker.monitoring_state() == first
    assert rows(journal, store) == before
    assert first.positions[0].owned_quantity == 0 and not first.credits and not first.history_credits
    assert not strategy.commands


@pytest.mark.parametrize('label', ['', 'absent-label', 'matching-label'])
def test_exact_cross_position_legacy_quarantine_is_observable_but_never_a_native_owner(
    setup: Any, monkeypatch: pytest.MonkeyPatch, label: str,
) -> None:
    worker, journal, store, strategy, kwargs, module = setup
    record, _, _, other, reduced = legacy_graph(setup, label)
    first = worker.projection('position-1')
    worker.shutdown()
    registered: list[str] = []
    original_register = kwargs['bridge'].register_owned
    def register(request: Any) -> None:
        registered.append(request.owned.client_order_id)
        original_register(request)
    monkeypatch.setattr(kwargs['bridge'], 'register_owned', register)
    restarted = module.NativeExecutionRuntime(**kwargs)
    assert registered == [record.client_order_id]
    assert other.fill.client_order_id not in registered
    assert restarted.projection('a-other') == reduced
    assert restarted.projection('position-1') == first
    assert reduced.owned_quantity == 0 and first.owned_quantity == Decimal('0.4')
    app = restarted.monitoring_state()
    assert 'CREDIT_DECISION' in app.conflicts and 'PROJECTION_CONFLICT' in app.conflicts
    assert len(app.owned) == 1 and len(app.candidates) == 2
    before = rows(journal, store)
    with pytest.raises(module.RuntimeBlocked):
        restarted.native_owned_requests()
    assert rows(journal, store) == before
    restarted.shutdown()
    before = rows(journal, store)
    registered.clear()
    with pytest.raises(module.RuntimeBlocked):
        module.NativeExecutionRuntime(**kwargs, _pre_recovery=lambda resolver: resolver(()))
    assert not registered and rows(journal, store) == before and not strategy.commands


@pytest.mark.parametrize('change', ['bare', 'candidate-only', 'missing-prior', 'wrong-prior',
    'invalid-prior', 'unknown-command', 'exposure', 'owner', 'fill', 'source', 'hash',
    'venue', 'decision', 'reason', 'second-stray', 'real-other-key', 'missing-quarantine',
    'changed-guard', 'changed-original-source', 'ambiguous-owner'])
def test_unproved_foreign_graph_cannot_reach_hook_registration_or_recovery(
    setup: Any, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    worker, journal, store, strategy, kwargs, module = setup
    legacy_graph(setup, change)
    worker.shutdown()
    before = rows(journal, store)
    calls: list[str] = []
    monkeypatch.setattr(kwargs['bridge'], 'register_owned', lambda request: calls.append('register'))
    with pytest.raises(ValueError):
        module.NativeExecutionRuntime(**kwargs, _pre_recovery=lambda resolver: calls.append('hook'))
    assert not calls and rows(journal, store) == before and not strategy.commands


@pytest.mark.parametrize('malformation', ['mixed', 'duplicate'])
def test_cold_bad_summary_still_refuses_strict_replay_before_recovery(setup: Any, malformation: str) -> None:
    worker, journal, store, strategy, kwargs, module = setup
    record = queue(worker)
    journal.claim(record.command_key)
    consume(worker, record.client_order_id, 'OrderFilled')
    state = worker.projection('position-1')
    conflicts = (state.conflicts[0], state.conflicts[0]) if malformation == 'duplicate' else (
        state.conflicts[0], p.ProjectionConflict('REAL_CONFLICT'))
    worker._persist(('READBACK', 'bad-summary', ()), replace(state, conflicts=conflicts))
    before = rows(journal, store)
    with pytest.raises(module.RuntimeBlocked, match='INVALID_DURABLE_PROJECTION_HISTORY'):
        worker.native_owned_requests()
    worker.shutdown()
    with pytest.raises(module.RuntimeBlocked, match='INVALID_DURABLE_PROJECTION_HISTORY'):
        module.NativeExecutionRuntime(**kwargs)
    assert rows(journal, store) == before and not strategy.commands


def add_known_v2(fixture: Any, owner_changes: Any = None) -> Any:
    worker, journal, store, _, _, _ = fixture
    # Accepted J refuses V2 admission beside an unreconciled scalar hold.
    # Prepare an explicitly terminal legacy order; its credited position remains.
    journal.acknowledge('entry', 'fixture-legacy-order-terminal')
    journal.release_reservation('entry', confirmed_terminal=True, terminal_evidence='fixture-terminal-order')
    intent, policy, account, marks = v2_values('V2', 'V2_POSITION')
    scope = replace(intent.envelope.scope, product_scope=replace(
        intent.envelope.scope.product_scope, account=worker.bridge.scope))
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
    margin = intent.assessment.margin
    assert margin is not None
    margin = replace(margin, binding_sha256=bh, reference=replace(margin.reference, input_sha256=bh))
    intent = replace(intent, envelope=envelope,
        assessment=replace(intent.assessment, preparation=prep, margin=margin))
    bound(journal)
    record = register(journal, (intent, policy, replace(account, account=worker.bridge.scope), marks))
    worker.bridge._bindings = replace(worker.bridge._bindings,
        fixed_limit_v2=FixedLimitBindingsV2(Limit, Init, Updated, Kind.LIMIT, Tif.IOC))
    v2_mapped(SimpleNamespace(store=store, bridge=worker.bridge), record, intent, **(owner_changes or {}))
    return record, intent


@pytest.mark.parametrize('change', ['valid', 'bare-v2', 'owner-scale', 'terminal-frame', 'bad-meta'])
def test_known_v2_remains_strict_alongside_legacy_foreign_observation(
    setup: Any, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    worker, journal, store, strategy, kwargs, module = setup
    legacy_graph(setup)
    changes = {'client_order_id': 'ec_' + 'd' * 32} if change == 'bare-v2' else (
        {'quantity': Decimal('1.0')} if change == 'owner-scale' else {})
    record, intent = add_known_v2(setup, changes)
    if change == 'terminal-frame':
        claim(journal, intent)
        journal.acknowledge(record.command_key, 'fixture-ack')
        journal.release_reservation(record.command_key, confirmed_terminal=True, terminal_evidence='fixture-terminal')
        raw = json.loads(record.payload_json)
        raw['wire_version'] = 99
        wire = json.dumps(raw, sort_keys=True, separators=(',', ':'))
        journal._db.execute('UPDATE commands SET payload=?,payload_hash=? WHERE command_key=?',
            (wire, hashlib.sha256(wire.encode()).hexdigest(), record.command_key))
    elif change == 'bad-meta':
        journal._db.execute('UPDATE reservation_meta_v2 SET schema_version=99')
    if change == 'valid':
        app = worker.monitoring_state()
        assert len(app.owned) == 2 and 'CREDIT_DECISION' in app.conflicts
        worker.shutdown()
        restarted = module.NativeExecutionRuntime(**kwargs)
        assert restarted.projection('a-other').owned_quantity == 0
        assert len(restarted.monitoring_state().owned) == 2
        with pytest.raises(module.RuntimeBlocked):
            restarted.native_owned_requests()
        restarted.shutdown()
    else:
        before = rows(journal, store)
        with pytest.raises(ValueError):
            worker.monitoring_state()
        worker.shutdown()
        calls: list[str] = []
        monkeypatch.setattr(worker.bridge, 'register_owned', lambda request: calls.append('register'))
        with pytest.raises(ValueError):
            module.NativeExecutionRuntime(**kwargs, _pre_recovery=lambda resolver: calls.append('hook'))
        assert not calls and rows(journal, store) == before
    assert not strategy.commands


def test_v2_prior_credit_cannot_explain_a_foreign_legacy_owner(setup: Any) -> None:
    worker, journal, store, strategy, kwargs, module = setup
    _, original, _, _, _ = legacy_graph(setup)
    record, _ = add_known_v2(setup)
    state = worker.projection('V2_POSITION')
    candidate = replace(original, fill=replace(original.fill, scope=state.scope,
        client_order_id=record.client_order_id, position_id=state.position_id,
        instrument_id=state.instrument_id, execution_id='V2_PRIOR_EXECUTION'), venue_order_id='V2_PRIOR_VENUE')
    quarantine = FillQuarantineRecord(candidate, 'AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED')
    worker._persist(quarantine, worker._reduce_event(state, quarantine, {}))
    prior = credit_with_label(worker, store, candidate, record.command_key)
    other = replace(candidate, fill=replace(candidate.fill, position_id='b-v2-foreign',
        client_order_id='ec_' + 'e' * 32), venue_order_id='V2_FOREIGN_VENUE')
    owner = replace(state.orders[0], position_id=other.fill.position_id, client_order_id=other.fill.client_order_id)
    worker._register(p.ProtectionProjection(state.scope, owner.position_id, owner.instrument_id, owner.side), owner)
    foreign_state = worker.projection(owner.position_id)
    quarantine = FillQuarantineRecord(other, 'AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED')
    worker._persist(quarantine, worker._reduce_event(foreign_state, quarantine, {}))
    decision = ExecutionCorroborationConflict(worker._candidate_identity(other), state.scope,
        other.fill.instrument_id, other.fill.execution_id, other.venue_order_id, other.fill.client_order_id,
        prior.source, prior.history_variant_hash, (), ('VENUE_ORDER_ID_MISMATCH',),
        execution_guard(state.scope, candidate.fill.instrument_id, candidate.fill.execution_id))
    reduced, replay = reduce_credit_event(worker.projection(owner.position_id), decision,
        candidates=(other,), replay=CreditReplayState())
    wire = codec.encode(decision)
    store.apply_position_event('decision:' + hashlib.sha256(wire.encode()).hexdigest(), json.loads(wire),
        owner.position_id, store.get_position(owner.position_id).revision,
        json.loads(codec.encode(worker._summary(reduced,
            {other.fill.execution_id: (worker._candidate_identity(other),)}, replay))))
    worker.shutdown()
    before = rows(journal, store)
    with pytest.raises(module.RuntimeBlocked):
        module.NativeExecutionRuntime(**kwargs)
    assert rows(journal, store) == before and not strategy.commands
