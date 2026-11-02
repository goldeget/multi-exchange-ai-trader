"""Durable corroboration data cannot act as a live observation capability."""

import importlib
import importlib.util
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from multi_exchange_trader.execution.protection import ProtectionScope


def api() -> Any:
    name = "multi_exchange_trader.execution.execution_credit"
    assert importlib.util.find_spec(name) is not None, "durable credit data module required"
    return importlib.import_module(name)


def reference() -> Any:
    return api().ExecutionObservationReference(ProtectionScope("fixture-account", "DEMO"),
        "binding", "a" * 64, "snapshot", "BTCUSDT", 1, 2,
        datetime(2026, 11, 2, tzinfo=UTC), datetime(2026, 11, 2, tzinfo=UTC))


def credit() -> Any:
    from test_execution_provenance import candidate, observation

    return api().CorroboratedExecutionCredit("c" * 64, candidate(),
        replace(observation(), binding_reference="binding", history_batch_id="snapshot"),
        reference(), "b" * 64, "command")


def conflict() -> Any:
    return api().ExecutionCorroborationConflict("c" * 64, reference().scope, "BTC.BYBIT",
        "exec-1", "venue-1", "client-1", reference(), "b" * 64,
        ("d" * 64,), ("PRICE_MISMATCH",), None)


def historical_order() -> Any:
    return api().HistoricalOrderEvidence(reference().scope, "a" * 64, "original-order", "BTCUSDT",
        1, 2, reference().observed_at, reference().server_observed_at, True,
        "e" * 64, "venue-1", "", "Buy", 1, True)


def test_historical_order_sources_roundtrip_and_affect_full_decision_digest() -> None:
    from multi_exchange_trader.execution import codec
    from multi_exchange_trader.execution.runtime import _record_digest

    original = replace(conflict(), client_order_id="", venue_order_id="", order_sources=(historical_order(),))
    assert codec.decode(codec.encode(original)) == original
    assert not hasattr(original.order_sources[0], "binding_reference")
    assert not hasattr(original.order_sources[0], "credit_authorized")
    changed = replace(original, order_sources=(replace(historical_order(), reduce_only=False),))
    assert api().decision_event_id(changed) != api().decision_event_id(original)
    assert _record_digest((changed,)) != _record_digest((original,))
    # The extended schema stays closed; no implicit default on codec restore.
    import json

    payload = json.loads(codec.encode(original))
    del payload["fields"]["order_sources"]
    with pytest.raises(ValueError, match="missing or extra record fields"):
        codec.decode(json.dumps(payload))


@pytest.mark.parametrize("changes", [
    {"observation_key": "bad"}, {"variant_hash": "bad"}, {"venue_order_id": ""},
    {"client_order_id": None}, {"side": "BUY"}, {"position_idx": True},
    {"position_idx": 3}, {"transport_injected": 1}, {"reduce_only": 1},
    {"server_observed_at": datetime(2026, 11, 3, tzinfo=UTC)},
])
def test_historical_order_evidence_is_bounded_typed_data(changes: Any) -> None:
    with pytest.raises(ValueError):
        replace(historical_order(), **changes)


def test_conflict_order_sources_are_unique_scoped_and_bounded() -> None:
    with pytest.raises(ValueError):
        replace(conflict(), order_sources=(historical_order(),) * 2)
    with pytest.raises(ValueError):
        replace(conflict(), order_sources=(replace(historical_order(), scope=ProtectionScope("other", "DEMO")),))
    with pytest.raises(ValueError):
        replace(conflict(), order_sources=tuple(replace(historical_order(), snapshot_id=str(index))
                                               for index in range(129)))


def test_durable_records_are_frozen_original_data_with_no_authority() -> None:
    for record in (reference(), credit(), conflict(),
                   api().ExecutionCreditResult("c" * 64, "CREDITED", ())):
        assert not hasattr(record, "credit_authorized")
        with pytest.raises(FrozenInstanceError):
            record.candidate_id = "changed"
    assert str(credit().candidate.fill.commission) == "-0.010"
    assert reference().observed_at == datetime(2026, 11, 2, tzinfo=UTC)


@pytest.mark.parametrize("field,value", [
    ("scope", "scope"), ("binding_reference", ""), ("binding_reference", "x" * 257),
    ("observation_key", "bad"), ("snapshot_id", ""), ("symbol", "BTC-USDT"),
    ("start_ms", True), ("end_ms", 0), ("end_ms", 7 * 86400000 + 2),
    ("observed_at", datetime(2026, 11, 2)),
    ("server_observed_at", datetime(2026, 11, 2, tzinfo=timezone(timedelta(hours=1)))),
])
def test_reference_rejects_invalid_fields(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        replace(reference(), **{field: value})


@pytest.mark.parametrize("field,value", [
    ("candidate_id", "bad"), ("candidate", None), ("observation", None),
    ("source", None), ("history_variant_hash", "bad"), ("command_key", ""),
])
def test_credit_rejects_invalid_fields(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        replace(credit(), **{field: value})


def test_credit_rejects_disconnected_source_and_mismatching_economics() -> None:
    original = credit()
    for changed in (replace(original.observation, history_batch_id="other"),
                    replace(original.observation, binding_reference="other"),
                    replace(original.observation, commission=Decimal("0.01"))):
        with pytest.raises(ValueError, match="^EXECUTION_CREDIT_INVALID$"):
            replace(original, observation=changed)


@pytest.mark.parametrize("changes", [
    {"mismatch_codes": ("raw-provider-message",)}, {"mismatch_codes": ()},
    {"mismatch_codes": ("PRICE_MISMATCH", "PRICE_MISMATCH")},
    {"history_conflict_keys": ["d" * 64]}, {"history_conflict_keys": ("bad",)},
    {"history_conflict_keys": ([],)},
    {"prior_credit_event_id": ""}, {"execution_id": ""}, {"source": None},
    {"prior_credit_event_id": "position-local-event"},
    {"scope": ProtectionScope("other", "DEMO")},
])
def test_conflict_rejects_unbounded_or_unknown_evidence(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        replace(conflict(), **changes)


def test_result_is_data_only_and_reason_allowlist_is_closed() -> None:
    assert api().ExecutionCreditResult("c" * 64, "CONFLICT", ("PRICE_MISMATCH",)).status == "CONFLICT"
    for status, codes in (("READY", ()), ("UNRESOLVED", ("provider raw secret",)),
                          ("UNRESOLVED", ["PRICE_MISMATCH"])):
        with pytest.raises(ValueError, match="^EXECUTION_CREDIT_RESULT_INVALID$"):
            api().ExecutionCreditResult("c" * 64, status, codes)


def test_prior_credit_reference_is_the_stable_account_execution_guard() -> None:
    original = conflict()
    prior = api().execution_guard(original.scope, original.instrument_id, original.execution_id)
    assert replace(original, prior_credit_event_id=prior).prior_credit_event_id == prior
    with pytest.raises(ValueError):
        replace(original, prior_credit_event_id="credit:" + "F" * 64)
    with pytest.raises(ValueError):
        replace(original, prior_credit_event_id="credit:" + "f" * 64)


def test_guard_is_structured_scoped_and_excludes_economic_representations() -> None:
    guard = api().execution_guard
    scope = ProtectionScope("a", "DEMO")
    first = guard(scope, "bc", "d")
    assert first.startswith("credit:") and len(first) == 71
    assert first != guard(scope, "b", "cd")
    assert first != guard(ProtectionScope("other", "DEMO"), "bc", "d")
    assert first != guard(ProtectionScope("a", "LIVE"), "bc", "d")
    candidate = credit().candidate
    scale = replace(candidate, fill=replace(candidate.fill, quantity=Decimal("0.00400")),
                    venue_order_id="changed")
    assert guard(candidate.fill.scope, candidate.fill.instrument_id, candidate.fill.execution_id) == guard(
        scale.fill.scope, scale.fill.instrument_id, scale.fill.execution_id)
    with pytest.raises(ValueError):
        guard(scope, "", "d")

# Synthetic durable credit tests exercise pure replay, never live receipt authority.
def replay_credit(candidate_value: Any = None) -> Any:
    from test_execution_provenance import candidate

    value = candidate() if candidate_value is None else candidate_value
    original = credit()
    fill = value.fill
    observed = replace(original.observation, scope=fill.scope,
        execution_id=fill.execution_id, client_order_id=fill.client_order_id,
        venue_order_id=value.venue_order_id, instrument_id=fill.instrument_id, side=fill.side,
        quantity=fill.quantity, price=fill.price, commission=fill.commission,
        commission_currency=fill.commission_currency, ts_event_ns=fill.ts_event_ns)
    return replace(original, candidate_id=api().candidate_identity(value), candidate=value,
                   observation=observed, source=replace(original.source, scope=fill.scope))


def replay_projection(*values: Any, quantity: str = '0.020') -> Any:
    from multi_exchange_trader.execution import protection as p

    fill = values[0].fill
    owned = p.OwnedOrder(fill.client_order_id, fill.position_id, fill.instrument_id,
                        fill.side, 'ENTRY', Decimal(quantity), False, 0, 'policy')
    return p.ProtectionProjection(fill.scope, fill.position_id, fill.instrument_id, fill.side,
        (owned,), conflicts=(p.ProjectionConflict(
            'AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED',
            client_order_id=fill.client_order_id), p.ProjectionConflict('UNRELATED')))


def test_pure_credit_resolves_individual_candidates_and_preserves_rebate() -> None:
    from test_execution_provenance import candidate

    first = candidate()
    second = replace(first, fill=replace(first.fill, execution_id='second'))
    state = replay_projection(first, second)
    replay = api().CreditReplayState()
    state, replay = api().reduce_credit_event(state, replay_credit(first),
        candidates=(first, second), replay=replay)
    assert state.owned_quantity == Decimal('0.004')
    assert len(replay.resolved_candidate_ids) == 1
    assert any(c.reason == 'AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED' for c in state.conflicts)
    assert replay.credits[0].candidate.fill.commission == Decimal('-0.010')
    state, replay = api().reduce_credit_event(state, replay_credit(second),
        candidates=(first, second), replay=replay)
    assert state.owned_quantity == Decimal('0.008')
    assert tuple(c.reason for c in state.conflicts) == ('UNRELATED',)
    assert len(replay.credits) == len(replay.resolved_candidate_ids) == 2


def test_pure_duplicate_keeps_first_observation_and_does_not_reapply() -> None:
    event = replay_credit()
    state, replay = api().reduce_credit_event(replay_projection(event.candidate), event,
        candidates=(event.candidate,), replay=api().CreditReplayState())
    fresh = replace(event, source=replace(event.source, snapshot_id='fresh',
        observed_at=event.source.observed_at + timedelta(seconds=1)),
        observation=replace(event.observation, history_batch_id='fresh'))
    assert api().reduce_credit_event(state, fresh, candidates=(event.candidate,),
                                    replay=replay) == (state, replay)
    assert replay.credits == (event,)


@pytest.mark.parametrize('change', ['price', 'venue', 'client', 'source'])
def test_changed_guard_payload_preserves_credit_and_records_sources(change: str) -> None:
    event = replay_credit()
    state, replay = api().reduce_credit_event(replay_projection(event.candidate), event,
        candidates=(event.candidate,), replay=api().CreditReplayState())
    value = event.candidate
    if change == 'price':
        value = replace(value, fill=replace(value.fill, price=Decimal(50002)))
    elif change == 'venue':
        value = replace(value, venue_order_id='different')
    elif change == 'client':
        from multi_exchange_trader.execution import protection as p
        value = replace(value, fill=replace(value.fill, client_order_id='other-client'))
        state = p.register_order(state, replace(state.orders[0], client_order_id='other-client'))
    else:
        value = replace(value, fill=replace(value.fill, native_reconciliation=True))
    reduced, replayed = api().reduce_credit_event(state, replay_credit(value),
        candidates=(event.candidate, value), replay=replay)
    assert reduced.executions == state.executions
    assert replayed.credits == (event,)
    assert replayed.resolved_candidate_ids == replay.resolved_candidate_ids
    assert len(replayed.decisions) == 1
    decision = replayed.decisions[0]
    assert decision.candidate_id == api().candidate_identity(value)
    assert decision.prior_credit_event_id == api().execution_guard(
        value.fill.scope, value.fill.instrument_id, value.fill.execution_id)
    assert decision.source == replay_credit(value).source


def test_precredit_source_variant_blocks_reduction() -> None:
    event = replay_credit()
    variant = replace(event.candidate,
                      fill=replace(event.candidate.fill, native_reconciliation=True))
    state, replay = api().reduce_credit_event(replay_projection(event.candidate), event,
        candidates=(event.candidate, variant), replay=api().CreditReplayState())
    assert state.executions == ()
    assert replay.credits == replay.resolved_candidate_ids == ()
    assert replay.decisions[0].mismatch_codes == ('NATIVE_CANDIDATE_VARIANT_CONFLICT',)


def test_quantity_refusal_does_not_resolve_or_credit_candidate() -> None:
    event = replay_credit()
    state, replay = api().reduce_credit_event(replay_projection(event.candidate, quantity='0.001'),
        event, candidates=(event.candidate,), replay=api().CreditReplayState())
    assert state.executions == ()
    assert replay.credits == replay.resolved_candidate_ids == ()
    assert replay.decisions[0].mismatch_codes == ('FILL_EXCEEDS_ORDER_INTENT',)


def test_reduce_only_refusal_does_not_resolve_candidate() -> None:
    from multi_exchange_trader.execution import protection as p

    event = replay_credit()
    candidate_value = replace(event.candidate, fill=replace(event.candidate.fill, side='SELL'))
    order = p.OwnedOrder(candidate_value.fill.client_order_id, candidate_value.fill.position_id,
        candidate_value.fill.instrument_id, 'SELL', 'EXIT', Decimal('0.004'), True, 0, 'policy')
    state = p.ProtectionProjection(order_scope := candidate_value.fill.scope,
        order.position_id, order.instrument_id, 'BUY', (order,))
    assert order_scope == event.source.scope
    reduced, replay = api().reduce_credit_event(state, replay_credit(candidate_value),
        candidates=(candidate_value,), replay=api().CreditReplayState())
    assert reduced.executions == () and not replay.resolved_candidate_ids
    assert replay.decisions[0].mismatch_codes == ('EXIT_EXCEEDS_OWNED_EXPOSURE',)


@pytest.mark.parametrize('kind', ['missing', 'id', 'payload', 'scope', 'position', 'side'])
def test_exact_persisted_candidate_and_owned_mapping_required(kind: str) -> None:
    event = replay_credit()
    state = replay_projection(event.candidate)
    values = (event.candidate,)
    if kind == 'missing':
        values = ()
    elif kind == 'id':
        event = replace(event, candidate_id='f' * 64)
    elif kind == 'payload':
        values = (replace(event.candidate,
                          fill=replace(event.candidate.fill, received_at_ns=999)),)
    elif kind == 'scope':
        state = replace(state, scope=ProtectionScope('other', 'DEMO'))
    elif kind == 'position':
        state = replace(state, position_id='other', orders=())
    else:
        state = replace(state, orders=())
    with pytest.raises(ValueError, match='EXECUTION_CREDIT_'):
        api().reduce_credit_event(state, event, candidates=values, replay=api().CreditReplayState())


def test_conflict_replay_does_not_require_original_credit_in_local_position() -> None:
    event = replay_credit()
    value = event.candidate
    fill = value.fill
    decision = api().ExecutionCorroborationConflict(event.candidate_id, fill.scope,
        fill.instrument_id, fill.execution_id, value.venue_order_id, fill.client_order_id,
        event.source, event.history_variant_hash, (), ('PRICE_MISMATCH',),
        api().execution_guard(fill.scope, fill.instrument_id, fill.execution_id))
    state, replay = api().reduce_credit_event(replay_projection(value), decision,
        candidates=(value,), replay=api().CreditReplayState())
    assert not state.executions and replay.decisions == (decision,)
    assert api().reduce_credit_event(state, decision, candidates=(value,), replay=replay) == (
        state, replay)
    state, replay = api().reduce_credit_event(state, event, candidates=(value,), replay=replay)
    assert not state.executions and not replay.resolved_candidate_ids


def test_v1_v2_candidate_and_summary_hashes_are_pinned() -> None:
    import hashlib

    from test_execution_provenance import candidate

    from multi_exchange_trader.execution import codec
    from multi_exchange_trader.execution.runtime import NativeExecutionRuntime

    value = candidate()
    state = replay_projection(value)
    identity = NativeExecutionRuntime._candidate_identity(value)
    assert identity == 'c3a76b9ceaa60bf35c4a7c74945dce338cfae33d59e2ccda1aa559604701a27b'
    for keys, expected in (
        ({}, '3b300a2905d828a4c7eb80dd0a73ae9733b91a25c0f2491c41509e4d52138963'),
        ({value.fill.execution_id: (identity,)},
         'fd1743b25822b999a984aca0aad13454f53583188acbe70dd335c116a941f17a'),
    ):
        summary = NativeExecutionRuntime._summary(state, keys)
        assert hashlib.sha256(codec.encode(summary).encode()).hexdigest() == expected


def test_v3_empty_candidates_and_complete_record_digests() -> None:
    import hashlib

    from multi_exchange_trader.execution import codec
    from multi_exchange_trader.execution.runtime import NativeExecutionRuntime

    event = replay_credit()
    state, replay = api().reduce_credit_event(replay_projection(event.candidate), event,
        candidates=(event.candidate,), replay=api().CreditReplayState())
    summary = NativeExecutionRuntime._summary(state, {}, replay)
    assert len(summary) == 17 and summary[0] == 'RUNTIME_POSITION_V3'
    assert summary[11:13] == (0, hashlib.sha256(b'').hexdigest())
    assert summary[13] == 1 and summary[15] == 0
    assert hashlib.sha256(codec.encode(summary).encode()).hexdigest() == (
        'f9497dee2b359c60d59f93f59e123f9ebf6501dfa356bd120f38ee2cb86fe1d9')
    changed = replace(replay, credits=(replace(event, source=replace(event.source,
        observed_at=event.source.observed_at + timedelta(seconds=1))),))
    assert NativeExecutionRuntime._summary(state, {}, changed)[14] != summary[14]


def test_existing_execution_identity_conflict_cannot_be_cleared_by_credit() -> None:
    from multi_exchange_trader.execution import protection as p

    event = replay_credit()
    state = replay_projection(event.candidate)
    state = replace(state, conflicts=state.conflicts + (p.ProjectionConflict(
        'EXECUTION_ID_CONFLICT', event=event.candidate.fill.to_owned_fill()),))
    reduced, replay = api().reduce_credit_event(state, event, candidates=(event.candidate,),
                                              replay=api().CreditReplayState())
    assert reduced.executions == () and not replay.resolved_candidate_ids
    assert replay.decisions[0].mismatch_codes == ('EXECUTION_ID_CONFLICT',)


def test_replay_digests_sort_complete_records() -> None:
    from multi_exchange_trader.execution.runtime import _record_digest

    one = replay_credit()
    two = replay_credit(replace(one.candidate, fill=replace(one.candidate.fill, execution_id='two')))
    assert _record_digest((one, two)) == _record_digest((two, one))
    assert _record_digest((one,)) != _record_digest((two,))


def test_conflict_must_bind_exact_candidate_execution_id_but_retains_observed_ids() -> None:
    event = replay_credit()
    fill = event.candidate.fill
    decision = api().ExecutionCorroborationConflict(event.candidate_id, fill.scope,
        fill.instrument_id, fill.execution_id, 'observed-venue', 'observed-client',
        event.source, event.history_variant_hash, (), ('CLIENT_ORDER_ID_MISMATCH',), None)
    state = replay_projection(event.candidate)
    _, replay = api().reduce_credit_event(state, decision,
        candidates=(event.candidate,), replay=api().CreditReplayState())
    assert replay.decisions == (decision,)
    with pytest.raises(ValueError, match='EXECUTION_CREDIT_'):
        api().reduce_credit_event(state, replace(decision, execution_id='unrelated'),
            candidates=(event.candidate,), replay=api().CreditReplayState())
