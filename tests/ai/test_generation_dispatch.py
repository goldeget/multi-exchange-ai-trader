"""Fake-only paid dispatch boundary, durable crash handling, and hostile responses."""

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from threading import Event

import httpx
import pytest

from multi_exchange_trader.ai import generation
from multi_exchange_trader.ai.budget import BudgetBook
from multi_exchange_trader.ai.dispatch import AIDispatcher, DispatchResult
from multi_exchange_trader.ai.generation import (
    GenerationResult,
    OpenRouterGenerationClient,
    quote_from_metadata,
    validate_http_status,
)
from multi_exchange_trader.ai.openrouter_metadata import (
    OpenRouterModelMetadata,
    OpenRouterModelResult,
)
from multi_exchange_trader.execution.modes import (
    Candidate,
    PolicyReference,
    PublicSnapshot,
    SelectionLimits,
    SelectionScope,
    StrategyReference,
    build_all_in_one_request,
)

NOW = datetime(2026, 11, 2, tzinfo=UTC)
KEY = "sk-or-v1-FAKE_ONLY_NEVER_A_REAL_KEY"
MODEL = "test/fake-model"


def selection_request():
    strategy = StrategyReference("trend", "v1")
    scope = SelectionScope("private-account", "DEMO", ("BTCUSDT",), (strategy,),
                           ("BTCUSDT",), (strategy,))
    policy = PolicyReference("private-account", "DEMO", "policy-v1")
    snapshot = PublicSnapshot("snapshot-1", NOW, NOW + timedelta(seconds=30))
    candidate = Candidate("candidate-1", "private-account", "DEMO", "BTCUSDT", strategy,
                          "private-signal", "snapshot-1", "policy-v1", "private-plan", True,
                          NOW, NOW + timedelta(seconds=30))
    return build_all_in_one_request(
        request_id="request-1", scope=scope, policy=policy, snapshot=snapshot,
        candidates=(candidate,), limits=SelectionLimits(4, 4096, 120, 80, 60, 60, 60, 60),
        now=NOW, expires_at=NOW + timedelta(seconds=30),
    )


def quote():
    result = OpenRouterModelResult(MODEL, "CATALOGUE_FOUND", None, NOW,
                                  OpenRouterModelMetadata(MODEL, "Fake model", 4096,
                                                          (("prompt", "0.000001"),
                                                           ("completion", "0.000002"))))
    return quote_from_metadata(result, now=NOW)


def output(**changes):
    value = {"schema_version": 1, "request_id": "request-1", "snapshot_id": "snapshot-1",
             "policy_revision": "policy-v1", "decision": "SELECT", "candidate_id": "candidate-1",
             "reason": "allowed candidate"}
    value.update(changes)
    return json.dumps(value)


def completion(**changes):
    value = {"model": MODEL, "usage": {"cost": 0.0012},
             "choices": [{"finish_reason": "stop", "message": {"role": "assistant",
                                                                  "content": output()}}]}
    value.update(changes)
    return value


def setup_dispatch(tmp_path, handler=None, *, configure=True, clock=lambda: NOW):
    calls = []

    def receive(request):
        calls.append(request)
        return handler(request) if handler else httpx.Response(200, json=completion())

    client = OpenRouterGenerationClient(KEY, MODEL, transport=httpx.MockTransport(receive))
    budget = BudgetBook(tmp_path / "ai-budget.sqlite3")
    if configure:
        budget.configure(0, "1")
    dispatcher = AIDispatcher(tmp_path / "ai-dispatch.sqlite3", budget, client, clock=clock)
    return dispatcher, budget, calls


def dispatch(dispatcher, **changes):
    arguments = {"maximum_cost_usd": "0.01", "quote": quote(), "enabled": True}
    arguments.update(changes)
    return dispatcher.dispatch(selection_request(), **arguments)


class StatusSubclass(int):
    pass


def test_dispatch_result_preserves_positional_arguments_and_validates_status():
    selection = object()
    legacy = DispatchResult("request-1", "SETTLED", None, "settled", "0.0012", selection)
    assert legacy.selection is selection and legacy.http_status is None
    for valid in (None, 100, 200, 599):
        assert DispatchResult("r", "UNCERTAIN", http_status=valid).http_status is valid
    for invalid in (True, False, 99, 600, "402", 402.0, Decimal(402), StatusSubclass(402)):
        with pytest.raises(ValueError, match="^Invalid HTTP status$"):
            DispatchResult("r", "UNCERTAIN", http_status=invalid)


def assert_durable_status(dispatcher, expected):
    current = dispatcher.status("request-1")
    assert current.http_status == expected and current.selection is None
    restarted = AIDispatcher(dispatcher.path, dispatcher.budget, dispatcher.client, clock=lambda: NOW)
    assert restarted.status("request-1").http_status == expected
    with sqlite3.connect(dispatcher.path) as db:
        assert db.execute("SELECT http_status FROM ai_dispatches").fetchone()[0] == expected


@pytest.mark.parametrize("value", [None, 100, 200, 599])
def test_generation_http_status_scalar_contract(value):
    assert validate_http_status(value) is value
    assert GenerationResult(None, http_status=value).http_status is value


@pytest.mark.parametrize("value", [True, False, 99, 600, "402", 402.0,
                                   Decimal(402), StatusSubclass(402)])
def test_generation_http_status_rejects_invalid_scalars(value):
    for construct in (validate_http_status, lambda status: GenerationResult(None, http_status=status)):
        with pytest.raises(ValueError) as caught:
            construct(value)
        assert str(caught.value) == "Invalid HTTP status"


def test_generation_result_preserves_positional_arguments():
    legacy = GenerationResult(None, "0.0012", "output")
    current = GenerationResult(None, "0.0012", "output", http_status=200)
    assert legacy.http_status is None
    assert current.output == legacy.output == "output"
    assert current.exact_cost_usd == legacy.exact_cost_usd == "0.0012"
    assert "output" not in repr(current)


def generate_fake(handler):
    calls = []

    def receive(request):
        calls.append(request)
        return handler(request)

    client = OpenRouterGenerationClient(KEY, MODEL, transport=httpx.MockTransport(receive))
    result = client.generate(selection_request(), quote=quote(), now=NOW)
    assert len(calls) == 1
    assert KEY not in repr(result)
    return result


@pytest.mark.parametrize("status", [200, 401, 402, 403, 404, 429, 503])
def test_generation_status_from_actual_headers(status):
    response = completion() if status == 200 else {"error": {"code": 402},
                                                  "usage": {"cost": 0.0012}}
    result = generate_fake(lambda _: httpx.Response(status, json=response))
    assert result.http_status == status
    assert result.failure == (None if status == 200 else "HTTP_ERROR")
    assert result.exact_cost_usd == "0.0012"
    assert (result.output is not None) == (status == 200)


@pytest.mark.parametrize(("case", "status", "failure", "cost"), [
    ("error-envelope", 200, "INVALID_RESPONSE", "0.0012"),
    ("redirect", 302, "REDIRECT_REJECTED", None),
    ("malformed", 402, "INVALID_RESPONSE", None),
    ("duplicate", 404, "INVALID_RESPONSE", None),
    ("non-utf8", 429, "INVALID_RESPONSE", None),
    ("reflected-key", 403, "INVALID_RESPONSE", "0.0012"),
    ("encoding", 503, "INVALID_RESPONSE", None),
    ("invalid-length", 402, "RESPONSE_TOO_LARGE", None),
    ("oversized-length", 404, "RESPONSE_TOO_LARGE", None),
    ("overflow", 429, "RESPONSE_TOO_LARGE", None),
    ("deadline", 503, "TIMEOUT", None),
    ("empty-deadline", 402, "TIMEOUT", None),
    ("read-timeout", 404, "TIMEOUT", None),
    ("network-error", 429, "NETWORK_ERROR", None),
    ("arbitrary-error", 503, "TRANSPORT_ERROR", None),
    ("context-exit", 402, "TRANSPORT_ERROR", None),
])
def test_generation_status_survives_post_header_failure(monkeypatch, case, status, failure, cost):
    clock = [0.0]
    monkeypatch.setattr(generation, "monotonic", lambda: clock[0])

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            if case == "deadline":
                clock[0] = 11.0
            if case == "empty-deadline":
                clock[0] = 11.0
                return
            if case == "read-timeout":
                raise httpx.ReadTimeout(KEY)
            if case == "network-error":
                raise httpx.ReadError(KEY)
            if case == "arbitrary-error":
                raise RuntimeError(KEY)
            if case == "overflow":
                yield b"x" * 40000
                yield b"y" * 40000
            else:
                yield json.dumps(completion()).encode()

        def close(self):
            if case == "context-exit":
                raise RuntimeError(KEY)

    def receive(_):
        if case == "error-envelope":
            return httpx.Response(status, json={"error": {"code": 402},
                                                "usage": {"cost": 0.0012}})
        if case == "redirect":
            return httpx.Response(status, headers={"location": "https://fake.invalid"})
        if case in ("malformed", "duplicate", "non-utf8"):
            raw = {"malformed": b"{", "duplicate": b'{"usage":{},"usage":{}}',
                   "non-utf8": b"\xff"}[case]
            return httpx.Response(status, content=raw)
        if case == "reflected-key":
            return httpx.Response(status, json={"error": {"message": KEY},
                                                "usage": {"cost": 0.0012}})
        headers = {"encoding": {"content-encoding": "unsupported"},
                   "invalid-length": {"content-length": "bad"},
                   "oversized-length": {"content-length": "65537"}}.get(case, {})
        return httpx.Response(status, headers=headers, stream=Body())

    result = generate_fake(receive)
    assert result.http_status == status
    assert result.failure == failure
    assert result.exact_cost_usd == cost
    assert result.output is None


@pytest.mark.parametrize(("error", "failure"), [(httpx.ReadTimeout, "TIMEOUT"),
                                                (httpx.ConnectError, "NETWORK_ERROR"),
                                                (RuntimeError, "TRANSPORT_ERROR")])
def test_generation_status_is_none_before_headers(error, failure):
    def receive(_):
        raise error(KEY)

    result = generate_fake(receive)
    assert result.http_status is None
    assert result.failure == failure
    assert result.exact_cost_usd is None and result.output is None


def test_transport_status_does_not_come_from_error_code():
    result = generate_fake(lambda _: httpx.Response(404, json={"error": {"code": 402}}))
    assert result.http_status == 404
    assert result.failure == "HTTP_ERROR"
    assert result.exact_cost_usd is None and result.output is None


@pytest.mark.parametrize("status", [True, False, "402", 402.0, 99, 600])
@pytest.mark.parametrize(("usage", "cost"), [({"cost": 0.0012}, "0.0012"),
                                           ({}, None), ({"cost": "0.0012"}, None)])
def test_invalid_header_status_does_not_erase_cost(tmp_path, status, usage, cost):
    def receive(_):
        response = httpx.Response(402, json={"error": {"code": 404, "message": "fake prose"},
                                            "usage": usage})
        response.status_code = status
        return response

    result = generate_fake(receive)
    assert result.http_status is None
    assert result.failure == "HTTP_ERROR"
    assert result.exact_cost_usd == cost and result.output is None
    assert "fake prose" not in repr(result)
    dispatcher, budget, calls = setup_dispatch(tmp_path, receive)
    dispatched = dispatch(dispatcher)
    assert dispatched.failure == "HTTP_ERROR" and len(calls) == 1
    assert dispatched.selection is None and dispatched.exact_cost_usd == cost
    assert dispatched.status == ("SETTLED" if cost is not None else "UNCERTAIN")
    assert budget.summary()["uncertain"] == ("0.00000000" if cost else "0.01000000")
    assert budget.summary()["used"] == ("0.00120000" if cost else "0.00000000")


def test_explicit_single_post_public_only_and_exact_settlement(tmp_path):
    dispatcher, budget, calls = setup_dispatch(tmp_path)
    assert calls == []
    assert dispatcher.status("request-1") is None
    assert dispatch(dispatcher, enabled=False).failure == "GENERATION_DISABLED"
    assert calls == []
    result = dispatch(dispatcher)
    assert result.http_status == 200
    assert_durable_status(dispatcher, 200)
    assert result.status == "SETTLED" and result.failure is None
    assert result.selection.candidate.plan_id == "private-plan"
    assert result.exact_cost_usd == "0.0012"
    assert budget.summary()["used"] == "0.00120000"
    assert budget.summary()["reserved"] == "0.00000000"
    request = calls[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.method == "POST" and request.headers["authorization"] == "Bearer " + KEY
    raw = request.content.decode()
    for secret in (KEY, "private-account", "private-plan", "private-signal", "DEMO"):
        assert secret not in raw
    payload = json.loads(raw)
    assert payload["stream"] is False and payload["max_tokens"] == 512
    assert payload["provider"] == {"allow_fallbacks": False, "require_parameters": True,
                                    "max_price": {"prompt": 1, "completion": 2, "request": 0}}
    assert dispatch(dispatcher).failure == "ALREADY_DISPATCHED"
    status = dispatcher.status("request-1")
    assert status.selection is None and status.reservation_status == "settled"
    assert len(calls) == 1
    restarted = AIDispatcher(dispatcher.path, budget, dispatcher.client, clock=lambda: NOW)
    assert dispatch(restarted).failure == "ALREADY_DISPATCHED"
    assert restarted.status("request-1").selection is None
    assert len(calls) == 1
    with sqlite3.connect(dispatcher.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        persisted = repr(db.execute("SELECT * FROM ai_dispatches").fetchall())
    for secret in (KEY, "private-account", "private-plan", "allowed candidate"):
        assert secret not in persisted


def test_zero_default_budget_and_missing_stale_or_small_quote_never_call(tmp_path):
    dispatcher, budget, calls = setup_dispatch(tmp_path, configure=False)
    assert dispatch(dispatcher, quote=None).failure == "PRICE_QUOTE_REQUIRED"
    assert dispatch(dispatcher, maximum_cost_usd="0.00000001").failure == "INSUFFICIENT_MAXIMUM"
    stale = replace(quote(), retrieved_at=NOW - timedelta(minutes=6),
                    expires_at=NOW - timedelta(minutes=1))
    assert dispatch(dispatcher, quote=stale).failure == "INVALID_DISPATCH_INPUT"
    assert dispatch(dispatcher).failure == "BUDGET_BLOCKED"
    assert budget.summary()["monthly_limit"] == "0.00000000" and calls == []


def test_quote_full_context_and_byok_allowance_ignore_decimal_context():
    with localcontext() as context:
        context.prec = 2
        priced = quote()
    assert priced.maximum_cost_usd == "0.00537600"
    assert Decimal(priced.maximum_cost_usd) > Decimal("0.004096")


@pytest.mark.parametrize("changes", [
    {"model": "other/model"},
    {"choices": []},
    {"choices": [{"finish_reason": "length", "message": {"role": "assistant",
                                                             "content": output()}}]},
    {"choices": [{"finish_reason": "stop", "message": {"role": "assistant",
                                                           "content": output(leverage=100)}}]},
    {"choices": [{"finish_reason": "stop", "message": {"role": "assistant",
                                                           "content": output(candidate_id="invented")}}]},
    {"choices": [{"finish_reason": "stop", "message": {"role": "assistant",
                                                           "content": output(reason=KEY)}}]},
])
def test_exact_charge_settles_even_when_model_or_selection_is_rejected(tmp_path, changes):
    dispatcher, budget, calls = setup_dispatch(
        tmp_path, lambda _: httpx.Response(200, json=completion(**changes)))
    result = dispatch(dispatcher)
    assert result.status == "SETTLED" and result.failure is not None
    assert result.selection is None and result.exact_cost_usd == "0.0012"
    assert result.http_status == 200
    assert_durable_status(dispatcher, 200)
    assert budget.summary()["used"] == "0.00120000"
    assert len(calls) == 1 and KEY not in repr(result)


@pytest.mark.parametrize("usage", [{}, {"cost": None}, {"cost": -1}, {"cost": "0.001"},
                                    {"cost": True}])
def test_unknown_cost_retains_full_maximum_and_no_selection(tmp_path, usage):
    dispatcher, budget, calls = setup_dispatch(
        tmp_path, lambda _: httpx.Response(200, json=completion(usage=usage)))
    result = dispatch(dispatcher)
    assert result.status == "UNCERTAIN" and result.selection is None
    assert result.http_status == 200 and result.exact_cost_usd is None
    assert_durable_status(dispatcher, 200)
    assert budget.summary()["uncertain"] == "0.01000000"
    assert dispatch(dispatcher).failure == "ALREADY_DISPATCHED" and len(calls) == 1


@pytest.mark.parametrize("status", [302, 401, 402, 404, 429, 503])
def test_errors_never_follow_redirect_or_retry_and_never_infer_free(tmp_path, status):
    private_values = (KEY, "fake provider prose", "fake billing detail", "flagged input",
                      "candidate reason", "provider-response-id")
    dispatcher, budget, calls = setup_dispatch(
        tmp_path, lambda _: httpx.Response(status, headers={"location": "https://evil.invalid"},
                                          json={"error": {"code": 499, "message": private_values},
                                                "billing": "fake billing detail",
                                                "id": "provider-response-id"}))
    result = dispatch(dispatcher)
    assert result.status == "UNCERTAIN" and KEY not in repr(result)
    assert result.http_status == status and result.exact_cost_usd is None
    assert_durable_status(dispatcher, status)
    assert budget.summary()["uncertain"] == "0.01000000"
    assert dispatch(dispatcher).failure == "ALREADY_DISPATCHED" and len(calls) == 1
    with sqlite3.connect(dispatcher.path) as db:
        persisted = repr(db.execute("SELECT * FROM ai_dispatches").fetchall())
    for private in private_values:
        assert private not in repr(result) and private not in persisted


@pytest.mark.parametrize("legacy_state", ["UNCERTAIN", "CHARGED"])
def test_migrated_consumed_dispatch_never_infers_status_or_replays(tmp_path, legacy_state):
    dispatcher, budget, calls = setup_dispatch(tmp_path)
    assert dispatch(dispatcher).http_status == 200
    with sqlite3.connect(dispatcher.path) as db:
        db.execute("ALTER TABLE ai_dispatches DROP COLUMN http_status")
        db.execute("UPDATE ai_dispatches SET status=?", (legacy_state,))
        db.execute("PRAGMA user_version=1")
    restarted = AIDispatcher(dispatcher.path, budget, dispatcher.client, clock=lambda: NOW)
    assert restarted.status("request-1").http_status is None
    duplicate = dispatch(restarted)
    assert duplicate.failure == "ALREADY_DISPATCHED" and duplicate.http_status is None
    reconciled = restarted.reconcile_charge("request-1")
    assert reconciled.http_status is None and reconciled.selection is None
    assert len(calls) == 1


def test_timeout_exception_secrets_are_not_exposed_and_no_retry(tmp_path):
    def timeout(_):
        raise httpx.ReadTimeout(KEY)

    dispatcher, budget, calls = setup_dispatch(tmp_path, timeout)
    result = dispatch(dispatcher)
    assert result.failure == "TIMEOUT" and KEY not in repr(result)
    assert result.http_status is None
    assert_durable_status(dispatcher, None)
    assert budget.summary()["uncertain"] == "0.01000000"
    assert dispatch(dispatcher).failure == "ALREADY_DISPATCHED" and len(calls) == 1


def test_crash_during_paid_call_preserves_liability_across_restart(tmp_path):
    class ProcessCrash(BaseException):
        pass

    def crash(_):
        raise ProcessCrash

    dispatcher, budget, calls = setup_dispatch(tmp_path, crash)
    with pytest.raises(ProcessCrash):
        dispatch(dispatcher)
    restarted = AIDispatcher(dispatcher.path, BudgetBook(budget.path), dispatcher.client,
                             clock=lambda: NOW)
    assert restarted.status("request-1").status == "UNCERTAIN"
    assert restarted.status("request-1").http_status is None
    assert dispatch(restarted).failure == "ALREADY_DISPATCHED"
    assert budget.summary()["uncertain"] == "0.01000000" and len(calls) == 1


def test_existing_reservation_is_never_permission(tmp_path):
    dispatcher, budget, calls = setup_dispatch(tmp_path)
    identifier = "dispatch_" + hashlib.sha256(b"request-1").hexdigest()
    budget.reserve(identifier, "0.01")
    assert dispatch(dispatcher).failure == "EXISTING_RESERVATION"
    assert calls == []


def test_parallel_workers_send_at_most_one_request(tmp_path):
    dispatcher, budget, calls = setup_dispatch(tmp_path)
    other = AIDispatcher(dispatcher.path, budget, dispatcher.client, clock=lambda: NOW)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: dispatch(other if i % 2 else dispatcher), range(8)))
    assert len(calls) == 1
    assert sum(result.selection is not None for result in results) == 1


def test_exact_overrun_is_liability_and_blocks_selected_candidate(tmp_path):
    dispatcher, budget, calls = setup_dispatch(
        tmp_path, lambda _: httpx.Response(200, json=completion(usage={"cost": 150.125})))
    result = dispatch(dispatcher)
    assert result.failure == "BUDGET_OVERRUN_HOLD" and result.selection is None
    assert result.http_status == 200
    assert_durable_status(dispatcher, 200)
    assert budget.summary()["overrun_liability"] == "150.12500000" and len(calls) == 1


def test_charge_persists_before_settlement_failure_then_local_reconcile(tmp_path):
    dispatcher, budget, calls = setup_dispatch(
        tmp_path, lambda _: httpx.Response(429, json={"error": {"code": 402},
                                                    "usage": {"cost": 0.0012}}))
    with budget.connect() as db:
        db.execute("""CREATE TRIGGER reject_settlement BEFORE UPDATE OF actual ON reservations
                      BEGIN SELECT RAISE(ABORT, 'fake storage failure'); END""")
        db.commit()
    with pytest.raises(sqlite3.DatabaseError):
        dispatch(dispatcher)
    status = dispatcher.status("request-1")
    assert status.status == "CHARGED" and status.exact_cost_usd == "0.0012"
    assert status.http_status == 429
    assert_durable_status(dispatcher, 429)
    assert budget.summary()["uncertain"] == "0.01000000"
    with budget.connect() as db:
        db.execute("DROP TRIGGER reject_settlement")
        db.commit()
    restarted = AIDispatcher(dispatcher.path, BudgetBook(budget.path), dispatcher.client,
                             clock=lambda: NOW)
    reconciled = restarted.reconcile_charge("request-1")
    assert reconciled.reservation_status == "settled" and reconciled.selection is None
    assert reconciled.http_status == 429 and reconciled.exact_cost_usd == "0.0012"
    assert_durable_status(restarted, 429)
    assert dispatch(dispatcher).failure == "ALREADY_DISPATCHED" and len(calls) == 1


@pytest.mark.parametrize("budget_committed", [False, True])
def test_restart_applies_pending_overrun_before_different_request(tmp_path, monkeypatch,
                                                                 budget_committed):
    class ProcessCrash(BaseException):
        pass

    dispatcher, budget, calls = setup_dispatch(
        tmp_path, lambda _: httpx.Response(200, json=completion(usage={"cost": 150.125})))
    settle = budget.settle

    def crash(identifier, cost):
        if budget_committed:
            settle(identifier, cost)
        raise ProcessCrash

    monkeypatch.setattr(budget, "settle", crash)
    with pytest.raises(ProcessCrash):
        dispatch(dispatcher)
    assert dispatcher.status("request-1").status == "CHARGED"
    fresh_budget = BudgetBook(budget.path)
    restarted = AIDispatcher(dispatcher.path, fresh_budget, dispatcher.client, clock=lambda: NOW)
    request = replace(selection_request(), request_id="request-2")
    result = restarted.dispatch(request, maximum_cost_usd="0.01", quote=quote(), enabled=True)
    assert result.failure == "BUDGET_BLOCKED" and result.selection is None
    assert len(calls) == 1
    summary = fresh_budget.summary()
    assert summary["used"] == "150.12500000" and summary["overrun_count"] == 1
    assert summary["overrun_hold"] and summary["reserved"] == "0.00000000"
    assert restarted.status("request-1").status == "SETTLED"
    assert restarted.status("request-1").http_status == 200
    assert restarted.reconcile_charge("request-1").selection is None
    assert fresh_budget.summary()["used"] == "150.12500000"


def test_unapplied_charge_blocks_new_reservation_when_repair_still_fails(tmp_path):
    dispatcher, budget, calls = setup_dispatch(
        tmp_path, lambda _: httpx.Response(200, json=completion(usage={"cost": 150.125})))
    with budget.connect() as db:
        db.execute("""CREATE TRIGGER reject_settlement BEFORE UPDATE OF actual ON reservations
                      BEGIN SELECT RAISE(ABORT, 'fake storage failure'); END""")
        db.commit()
    with pytest.raises(sqlite3.DatabaseError):
        dispatch(dispatcher)
    restarted = AIDispatcher(dispatcher.path, BudgetBook(budget.path), dispatcher.client,
                             clock=lambda: NOW)
    result = restarted.dispatch(replace(selection_request(), request_id="request-2"),
                                maximum_cost_usd="0.01", quote=quote(), enabled=True)
    assert result.failure == "PENDING_CHARGE_RECONCILIATION" and result.selection is None
    assert len(calls) == 1 and budget.summary()["reserved"] == "0.01000000"
    assert restarted.status("request-1").status == "CHARGED"
    assert restarted.status("request-1").http_status == 200
    with budget.connect() as db:
        db.execute("DROP TRIGGER reject_settlement")
        db.commit()
    result = restarted.dispatch(replace(selection_request(), request_id="request-3"),
                                maximum_cost_usd="0.01", quote=quote(), enabled=True)
    assert result.failure == "BUDGET_BLOCKED" and result.selection is None
    assert budget.summary()["overrun_hold"] and len(calls) == 1


def test_concurrent_workers_wait_for_pending_charge_repair(tmp_path, monkeypatch):
    class ProcessCrash(BaseException):
        pass

    dispatcher, budget, calls = setup_dispatch(
        tmp_path, lambda _: httpx.Response(200, json=completion(usage={"cost": 150.125})))

    def crash(*_):
        raise ProcessCrash

    monkeypatch.setattr(budget, "settle", crash)
    with pytest.raises(ProcessCrash):
        dispatch(dispatcher)
    fresh_budget = BudgetBook(budget.path)
    other = AIDispatcher(dispatcher.path, fresh_budget, dispatcher.client, clock=lambda: NOW)
    third = AIDispatcher(dispatcher.path, BudgetBook(budget.path), dispatcher.client,
                         clock=lambda: NOW)
    entered, release, reserved = Event(), Event(), Event()
    settle = fresh_budget.settle
    reserve = third.budget.reserve

    def slow_repair(identifier, cost):
        entered.set()
        assert release.wait(5)
        return settle(identifier, cost)

    def observe_reserve(identifier, maximum):
        reserved.set()
        return reserve(identifier, maximum)

    monkeypatch.setattr(fresh_budget, "settle", slow_repair)
    monkeypatch.setattr(third.budget, "reserve", observe_reserve)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(other.dispatch,
                            replace(selection_request(), request_id="request-2"),
                            maximum_cost_usd="0.01", quote=quote(), enabled=True)
        try:
            assert entered.wait(5)
            second = pool.submit(third.dispatch,
                                 replace(selection_request(), request_id="request-3"),
                                 maximum_cost_usd="0.01", quote=quote(), enabled=True)
            assert not reserved.wait(0.3)
            assert len(calls) == 1
        finally:
            release.set()
        assert first.result().failure == "BUDGET_BLOCKED"
        assert second.result().failure == "BUDGET_BLOCKED"
    assert len(calls) == 1
    assert fresh_budget.summary()["used"] == "150.12500000"
    assert third.status("request-1").status == "SETTLED"
    assert third.status("request-1").http_status == 200


def test_dispatch_lock_timeout_fails_closed_without_reservation(tmp_path, monkeypatch):
    dispatcher, budget, calls = setup_dispatch(tmp_path)
    connect = sqlite3.connect

    def short_timeout(path, **kwargs):
        if path == dispatcher.path:
            kwargs["timeout"] = 0.01
        return connect(path, **kwargs)

    with connect(dispatcher.path) as db:
        db.execute("BEGIN IMMEDIATE")
        monkeypatch.setattr(sqlite3, "connect", short_timeout)
        result = dispatch(dispatcher)
    assert result.failure == "DISPATCH_STORAGE_UNAVAILABLE" and result.selection is None
    assert calls == [] and budget.summary()["reserved"] == "0.00000000"
    assert dispatcher.status("request-1") is None

def test_output_expired_during_generation_is_rejected_but_charge_settles(tmp_path):
    current = [NOW]

    def delayed(_):
        current[0] += timedelta(seconds=31)
        return httpx.Response(200, json=completion())

    dispatcher, budget, calls = setup_dispatch(tmp_path, delayed, clock=lambda: current[0])
    result = dispatch(dispatcher)
    assert result.failure == "INVALID_SELECTION" and result.selection is None
    assert budget.summary()["used"] == "0.00120000" and len(calls) == 1


def test_declared_or_streamed_oversized_response_stays_uncertain(tmp_path):
    dispatcher, budget, calls = setup_dispatch(
        tmp_path, lambda _: httpx.Response(200, content=b"x" * 65537))
    assert dispatch(dispatcher).failure == "RESPONSE_TOO_LARGE"
    assert budget.summary()["uncertain"] == "0.01000000" and len(calls) == 1


def test_ambiguous_json_charge_does_not_release_reservation(tmp_path):
    raw = b'{"usage":{"cost":0.001,"cost":0},"model":"test/fake-model"}'
    dispatcher, budget, calls = setup_dispatch(tmp_path, lambda _: httpx.Response(200, content=raw))
    result = dispatch(dispatcher)
    assert result.failure == "INVALID_RESPONSE" and result.exact_cost_usd is None
    assert budget.summary()["uncertain"] == "0.01000000" and len(calls) == 1


def test_exact_usage_numeric_evidence_is_not_rounded_by_json_float_or_decimal_context(tmp_path):
    value = completion()
    raw = json.dumps(value).replace('"cost": 0.0012',
                                    '"cost": 0.00120000000000000000000000000001').encode()
    dispatcher, budget, calls = setup_dispatch(tmp_path, lambda _: httpx.Response(200, content=raw))
    with localcontext() as context:
        context.prec = 2
        result = dispatch(dispatcher)
    assert result.exact_cost_usd == "0.00120000000000000000000000000001"
    assert budget.summary()["used"] == "0.00120001" and len(calls) == 1


def test_arbitrary_transport_exception_is_fixed_and_secret_free(tmp_path):
    def broken(_):
        raise RuntimeError(KEY)

    dispatcher, budget, calls = setup_dispatch(tmp_path, broken)
    result = dispatch(dispatcher)
    assert result.failure == "TRANSPORT_ERROR" and KEY not in repr(result)
    assert budget.summary()["uncertain"] == "0.01000000" and len(calls) == 1


def test_streamed_body_limit_without_length_is_enforced(tmp_path):
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield b"x" * 40000
            yield b"y" * 40000

    dispatcher, budget, calls = setup_dispatch(
        tmp_path, lambda _: httpx.Response(200, stream=Body()))
    result = dispatch(dispatcher)
    assert result.failure == "RESPONSE_TOO_LARGE"
    assert budget.summary()["uncertain"] == "0.01000000" and len(calls) == 1


def test_secret_like_request_id_cannot_enter_status_result(tmp_path):
    dispatcher, _, calls = setup_dispatch(tmp_path)
    request = replace(selection_request(), request_id=KEY)
    result = dispatcher.dispatch(request, maximum_cost_usd="0.01", quote=quote(), enabled=True)
    assert result.failure == "INVALID_DISPATCH_INPUT" and KEY not in repr(result) and calls == []


@pytest.mark.parametrize("settings", [
    {"api_key": KEY, "model_id": "test/" + KEY},
    {"api_key": KEY, "model_id": MODEL, "timeout_seconds": 10**1000},
    {"api_key": KEY, "model_id": MODEL, "timeout_seconds": float("nan")},
    {"api_key": KEY, "model_id": MODEL, "max_body_bytes": True},
])
def test_invalid_client_configuration_raises_fixed_secret_free_error(settings):
    with pytest.raises(ValueError, match="Invalid generation configuration") as caught:
        OpenRouterGenerationClient(**settings)
    assert KEY not in str(caught.value)


def test_abstention_never_falls_back_to_a_candidate(tmp_path):
    response = completion(choices=[{"finish_reason": "stop", "message": {
        "role": "assistant", "content": output(decision="ABSTAIN", candidate_id=None)}}])
    dispatcher, _, calls = setup_dispatch(tmp_path, lambda _: httpx.Response(200, json=response))
    result = dispatch(dispatcher)
    assert result.failure is None and result.selection.status == "ABSTAINED"
    assert result.selection.candidate is None and len(calls) == 1
