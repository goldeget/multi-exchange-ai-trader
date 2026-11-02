"""Hermetic enrollment qualification at the HTTP transport I/O boundary, never venue proof."""

import asyncio
import copy
import hashlib
import hmac
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from multi_exchange_trader import account_enrollment as enrollment
from multi_exchange_trader import account_profiles as profiles

NOW = datetime(2026, 11, 3, 1, tzinfo=UTC)
KEY, SECRET = "synthetic-enrollment-key", "synthetic-enrollment-secret"


def metadata() -> dict[str, Any]:
    return {"apiKey": KEY, "secret": "", "userID": 12345, "parentUid": "0",
            "isMaster": False, "readOnly": 0,
            "permissions": {"ContractTrade": ["Order", "Position"], "Spot": ["SpotTrade"],
                            "Wallet": [], "Derivatives": ["DerivativesTrade"]}}


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    store = profiles.AccountProfileStore(tmp_path / "private" / "profile.json")
    store.register_uid("12345", expected_revision=None)
    state: dict[str, Any] = {"now": NOW, "mono": 10.0, "metadata": metadata(),
                             "stamp": int(NOW.timestamp() * 1000), "requests": []}

    async def send(_transport: Any, request: httpx.Request) -> httpx.Response:
        # This is the lowest real transport boundary, not an injected qualifying transport.
        state["requests"].append(request)
        assert request.method == "GET" and request.url.host == "api-demo.bybit.com"
        assert request.url.path in {"/v5/market/time", "/v5/user/query-api"}
        assert not request.url.query
        if request.url.path == "/v5/market/time":
            assert "X-BAPI-API-KEY" not in request.headers
        else:
            assert request.headers["X-BAPI-API-KEY"] == KEY
            signed = request.headers["X-BAPI-TIMESTAMP"] + KEY + "10000"
            assert request.headers["X-BAPI-RECV-WINDOW"] == "10000"
            assert request.headers["X-BAPI-SIGN"] == hmac.new(
                SECRET.encode(), signed.encode(), hashlib.sha256).hexdigest()
        if state.get("cancel"):
            raise asyncio.CancelledError
        if state.get("advance"):
            state["mono"] += 31
            state["now"] += timedelta(seconds=31)
        result = state["metadata"] if request.url.path == "/v5/user/query-api" else {}
        return httpx.Response(200, json={"retCode": 0, "result": result, "time": state["stamp"]})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", send)

    def owner(**kwargs: Any) -> Any:
        return enrollment.BybitDemoEnrollmentSession(store=store, snapshot=store.load(),
            api_key=KEY, api_secret=SECRET, clock=lambda: state["now"],
            monotonic=lambda: state["mono"], **kwargs)

    return store, state, owner


def adopt(owner: Any, candidate: Any) -> Any:
    return owner.adopt(candidate, policy="uid-anchored-adoption-v1",
                       adopt_observed_relationship=True)


async def test_explicit_adoption_persists_provenance_without_runtime_authority(setup: Any) -> None:
    store, state, create = setup
    owner = create()
    try:
        candidate = await owner.observe()
        assert store.load().state == "PENDING"
        assert len(state["requests"]) == 2
        assert not any(hasattr(owner, name) for name in (
            "authenticate", "_binding", "_identity", "_attested", "configure_native_execution"))
        assert "12345" not in repr(candidate) and KEY not in repr(owner)
        result = adopt(owner, candidate)
        assert (result.revision, result.state, result.basis) == (
            2, "COMPLETE", "PROVIDER_OBSERVED_OPERATOR_ADOPTED")
        expected = store.load().resolve_expectations(
            supported_policies=frozenset({"uid-anchored-adoption-v1"}))
        assert (expected._uid, expected._parent_uid, expected._is_master) == ("12345", "0", False)
        assert expected.policy == "uid-anchored-adoption-v1"
        raw = store.path.read_text()
        assert KEY not in raw and SECRET not in raw and "SpotTrade" not in raw
        with pytest.raises(enrollment.AccountEnrollmentError):
            adopt(owner, candidate)
    finally:
        await owner.aclose()


@pytest.mark.parametrize("change", [
    {"userID": 54321}, {"userID": True}, {"apiKey": "wrong"}, {"secret": SECRET},
    {"parentUid": 0}, {"parentUid": "12345"}, {"parentUid": "01"},
    {"isMaster": 0}, {"readOnly": False}, {"permissions": {"Unknown": []}},
])
async def test_metadata_never_becomes_its_own_expected_uid(setup: Any, change: Any) -> None:
    store, state, create = setup
    state["metadata"].update(change)
    owner = create()
    try:
        with pytest.raises(enrollment.AccountEnrollmentError, match="^ENROLLMENT_OBSERVATION_FAILED$"):
            await owner.observe()
        assert store.load().state == "PENDING"
    finally:
        await owner.aclose()


@pytest.mark.parametrize("kind", ["copy", "forged", "dictionary", "cross-session", "new-attempt",
                                  "closed", "host", "pair", "client", "transport", "target",
                                  "snapshot", "store"])
async def test_candidate_exact_owner_and_target_not_copied_data(setup: Any, kind: str) -> None:
    store, _, create = setup
    owner, other = create(), create()
    try:
        candidate = await owner.observe()
        consumer = owner
        if kind == "copy":
            try:
                candidate = copy.copy(candidate)
            except (TypeError, enrollment.AccountEnrollmentError):
                return
        elif kind == "forged":
            candidate = object.__new__(enrollment.EnrollmentCandidate)
        elif kind == "dictionary":
            candidate = {"profile_id": store.load().profile_id}
        elif kind == "cross-session":
            consumer = other
        elif kind == "new-attempt":
            await owner.observe()
        elif kind == "closed":
            await owner.aclose()
        elif kind == "host":
            owner._client.base_url = "https://api.bybit.com"
        elif kind == "pair":
            owner._client._api_key = "changed"
        elif kind == "client":
            owner._client._client = httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200)))
        elif kind == "transport":
            owner._client._client._transport = httpx.MockTransport(lambda request: httpx.Response(200))
        elif kind == "target":
            store.path = store.path.with_name("other.json")
        elif kind == "snapshot":
            owner._snapshot = profiles.AccountProfileSnapshot(1, store.load().profile_id,
                dict(store.load()._profile) | {"uid": "777"})
        elif kind == "store":
            owner._store = profiles.AccountProfileStore(store.path)
        with pytest.raises(enrollment.AccountEnrollmentError):
            adopt(consumer, candidate)
    finally:
        await owner.aclose()
        await other.aclose()


@pytest.mark.parametrize("change", ["wall-expired", "mono-expired", "wall-backwards", "mono-backwards",
                                    "nan", "server-future", "server-stale", "slow"])
async def test_request_start_bounded_three_clock_freshness(setup: Any, change: str) -> None:
    store, state, create = setup
    owner = create()
    try:
        if change in {"server-future", "server-stale", "slow"}:
            state["stamp"] += 1000 if change == "server-future" else -31000
            state["advance"] = change == "slow"
            with pytest.raises(enrollment.AccountEnrollmentError):
                await owner.observe()
        else:
            candidate = await owner.observe()
            if change == "wall-expired":
                state["now"] += timedelta(seconds=31)
            elif change == "mono-expired":
                state["mono"] += 31
            elif change == "wall-backwards":
                state["now"] -= timedelta(microseconds=1)
            elif change == "mono-backwards":
                state["mono"] -= 1
            else:
                state["mono"] = float("nan")
            with pytest.raises(enrollment.AccountEnrollmentError):
                adopt(owner, candidate)
        assert store.load().state == "PENDING"
    finally:
        await owner.aclose()


async def test_synthetic_injected_transport_observes_but_cannot_adopt(setup: Any) -> None:
    store, state, create = setup
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "retCode": 0, "time": state["stamp"], "result": metadata()
        if request.url.path == "/v5/user/query-api" else {}}))
    owner = create(transport=transport)
    try:
        candidate = await owner.observe()
        with pytest.raises(enrollment.AccountEnrollmentError):
            adopt(owner, candidate)
        assert store.load().state == "PENDING"
    finally:
        await owner.aclose()


@pytest.mark.parametrize("stage", ["lock", "prewrite", "ambiguous"])
async def test_expired_or_ambiguous_publication_unconditionally_retires_candidate(
    setup: Any, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    store, state, create = setup
    owner = create()
    try:
        candidate = await owner.observe()
        if stage == "lock":
            original_lock = profiles.private_lock

            @contextmanager
            def delayed(*args: Any, **kwargs: Any) -> Any:
                with original_lock(*args, **kwargs) as guard:
                    state["mono"] += 31
                    yield guard

            monkeypatch.setattr(profiles, "private_lock", delayed)
        else:
            original_publish = profiles.publish_private

            def delayed_publish(*args: Any, **kwargs: Any) -> None:
                if stage == "prewrite":
                    state["mono"] += 31
                original_publish(*args, **kwargs)
                if stage == "ambiguous":
                    raise OSError("synthetic ambiguous result")

            monkeypatch.setattr(profiles, "publish_private", delayed_publish)
        with pytest.raises((profiles.AccountProfileError, enrollment.AccountEnrollmentError)):
            adopt(owner, candidate)
        state["mono"] = 10.0
        with pytest.raises(enrollment.AccountEnrollmentError):
            adopt(owner, candidate)
        assert store.load().state == ("COMPLETE" if stage == "ambiguous" else "PENDING")
    finally:
        await owner.aclose()


async def test_cancelled_attempt_revokes_prior_candidate(setup: Any) -> None:
    _, state, create = setup
    owner = create()
    try:
        candidate = await owner.observe()
        state["cancel"] = True
        with pytest.raises(asyncio.CancelledError):
            await owner.observe()
        with pytest.raises(enrollment.AccountEnrollmentError):
            adopt(owner, candidate)
    finally:
        await owner.aclose()


async def test_intent_and_exact_policy_required_and_complete_cannot_reenroll(setup: Any) -> None:
    store, _, create = setup
    owner = create()
    try:
        candidate = await owner.observe()
        with pytest.raises(enrollment.AccountEnrollmentError):
            owner.adopt(candidate, policy="independent-v1", adopt_observed_relationship=True)
        with pytest.raises(enrollment.AccountEnrollmentError):
            adopt(owner, candidate)
        candidate = await owner.observe()
        with pytest.raises(enrollment.AccountEnrollmentError):
            owner.adopt(candidate, policy="uid-anchored-adoption-v1", adopt_observed_relationship=False)
        adopt(owner, await owner.observe())
        with pytest.raises((profiles.AccountProfileError, enrollment.AccountEnrollmentError)):
            create()
        assert store.load().revision == 2
    finally:
        await owner.aclose()


async def test_untrusted_clock_errors_are_redacted_and_candidate_retired(setup: Any) -> None:
    _, _, create = setup
    owner = create()
    try:
        candidate = await owner.observe()

        def bad_clock() -> datetime:
            raise OSError(KEY + SECRET)

        owner._clock = bad_clock
        with pytest.raises(enrollment.AccountEnrollmentError) as error:
            adopt(owner, candidate)
        assert KEY not in repr(error.value) and SECRET not in repr(error.value)
        assert error.value.__context__ is None
        owner._clock = lambda: NOW
        with pytest.raises(enrollment.AccountEnrollmentError):
            adopt(owner, candidate)
    finally:
        await owner.aclose()


@pytest.mark.parametrize("changed", [{"userID": 777}, {"parentUid": "777"}, {"isMaster": True}])
async def test_saved_adoption_new_session_requires_fresh_three_field_authentication(
    setup: Any, changed: Any,
) -> None:
    from multi_exchange_trader.execution.account_binding import (
        AccountBindingError,
        BybitDemoAccountSession,
    )
    from multi_exchange_trader.execution.protection import ProtectionScope

    store, state, create = setup
    owner = create()
    try:
        adopt(owner, await owner.observe())
    finally:
        await owner.aclose()
    # Separate immutable file read and separate credential/authentication owner.
    expected = profiles.AccountProfileStore(store.path).load().resolve_expectations(
        supported_policies=frozenset({"uid-anchored-adoption-v1"}))
    session = BybitDemoAccountSession(api_key=KEY, api_secret=SECRET,
                                     clock=lambda: NOW, monotonic=lambda: 10.0)
    try:
        async def authenticate() -> Any:
            return await session.authenticate(expected_uid=expected._uid,
                expected_parent_uid=expected._parent_uid, expected_is_master=expected._is_master)

        before = len(state["requests"])
        binding = await authenticate()
        assert len(state["requests"]) == before + 2
        identity = session.require_binding(binding, scope=ProtectionScope("12345", "DEMO"))
        assert identity.has_permission("Spot", "SpotTrade")
        assert not identity.linear_execution_permitted
        state["metadata"].update(changed)
        with pytest.raises(AccountBindingError, match="^ACCOUNT_AUTHENTICATION_FAILED$"):
            await authenticate()
        with pytest.raises(AccountBindingError):
            session.require_binding(binding, scope=ProtectionScope("12345", "DEMO"))
        assert store.load().revision == 2
    finally:
        await session.aclose()


@pytest.mark.parametrize("kind", ["absent", "pending", "complete"])
def test_environment_factory_uses_pending_profile_before_exactly_one_resolver(
    setup: Any, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    store, _, _ = setup
    calls: list[str] = []
    if kind == "absent":
        store = profiles.AccountProfileStore(store.path.with_name("absent.json"))
    elif kind == "complete":
        # Configuration-only fixture; no enrollment/runtime authority is inferred.
        from .test_store import observation, relationship

        snapshot = store.load()
        store._adopt_initial(profile_id=snapshot.profile_id, expected_uid=snapshot._uid,
            expected_revision=1, relationship=relationship(), last_observation=observation(),
            guard=lambda: None)

    def resolve(**kwargs: Any) -> Any:
        calls.append("resolve")
        raise RuntimeError("stop before credentials/network")

    monkeypatch.setattr(enrollment.CredentialResolver, "from_environment", resolve)
    with pytest.raises((enrollment.AccountEnrollmentError, RuntimeError)):
        enrollment.BybitDemoEnrollmentSession.from_environment(store=store, environ={})
    assert calls == (["resolve"] if kind == "pending" else [])


async def test_overlapping_observation_rejected_and_cancelled_operation_cannot_publish(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, state, create = setup
    entered, release = asyncio.Event(), asyncio.Event()
    original = httpx.AsyncHTTPTransport.handle_async_request

    async def blocked(transport: Any, request: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return await original(transport, request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
    owner = create()
    task = asyncio.create_task(owner.observe())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(enrollment.AccountEnrollmentError, match="OPERATION_IN_PROGRESS"):
            await owner.observe()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        candidate = await owner.observe()
        result = adopt(owner, candidate)
        assert result.revision == 2 and store.load().state == "COMPLETE"
        assert len(state["requests"]) == 2
    finally:
        release.set()
        await owner.aclose()


async def test_consume_precedes_store_publication_and_prevents_reentrant_replay(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _, create = setup
    owner = create()
    try:
        candidate = await owner.observe()
        original = store._adopt_initial

        def publication(**kwargs: Any) -> Any:
            # Rejected contention cannot retire the active owner. Keep the
            # actual guarded store and require exactly one complete publication.
            with pytest.raises(enrollment.AccountEnrollmentError):
                adopt(owner, candidate)
            return original(**kwargs)

        monkeypatch.setattr(store, "_adopt_initial", publication)
        assert adopt(owner, candidate).state == "COMPLETE"
        assert store.load().revision == 2
        with pytest.raises(enrollment.AccountEnrollmentError):
            adopt(owner, candidate)
    finally:
        await owner.aclose()


async def test_observation_cannot_overlap_an_adopting_operation(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _, create = setup
    owner = create()
    entered = asyncio.Event()
    loop = asyncio.get_running_loop()

    resume = threading.Event()
    original = store._adopt_initial

    def blocked_publication(**kwargs: Any) -> Any:
        loop.call_soon_threadsafe(entered.set)
        assert resume.wait(timeout=3)
        return original(**kwargs)

    monkeypatch.setattr(store, "_adopt_initial", blocked_publication)
    candidate = await owner.observe()
    task = asyncio.create_task(asyncio.to_thread(adopt, owner, candidate))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(enrollment.AccountEnrollmentError, match="OPERATION_IN_PROGRESS"):
            await owner.observe()
    finally:
        resume.set()
        try:
            result = await task
            assert result.state == "COMPLETE"
        finally:
            await owner.aclose()


@pytest.mark.parametrize("clock", ["wall", "monotonic"])
async def test_intermediate_guard_clock_reversal_above_original_baseline_rejects(
    setup: Any, monkeypatch: pytest.MonkeyPatch, clock: str,
) -> None:
    store, state, create = setup
    owner = create()
    try:
        candidate = await owner.observe()
        state["now"], state["mono"] = NOW + timedelta(seconds=2), 12.0
        original_lock, original_publish = profiles.private_lock, profiles.publish_private

        @contextmanager
        def advanced_lock(*args: Any, **kwargs: Any) -> Any:
            with original_lock(*args, **kwargs) as guard:
                state["now"], state["mono"] = NOW + timedelta(seconds=4), 14.0
                yield guard

        def reversed_prewrite(*args: Any, **kwargs: Any) -> None:
            if clock == "wall":
                state["now"] = NOW + timedelta(seconds=3)
            else:
                state["mono"] = 13.0
            original_publish(*args, **kwargs)

        monkeypatch.setattr(profiles, "private_lock", advanced_lock)
        monkeypatch.setattr(profiles, "publish_private", reversed_prewrite)
        with pytest.raises(profiles.AccountProfileError):
            adopt(owner, candidate)
        assert store.load().state == "PENDING"
    finally:
        await owner.aclose()


async def test_guard_clock_watermarks_never_extend_request_start_expiry(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, state, create = setup
    owner = create()
    try:
        candidate = await owner.observe()
        original_lock, original_publish = profiles.private_lock, profiles.publish_private

        @contextmanager
        def almost_expired(*args: Any, **kwargs: Any) -> Any:
            with original_lock(*args, **kwargs) as guard:
                state["now"], state["mono"] = NOW + timedelta(seconds=29), 39.0
                yield guard

        def expired_prewrite(*args: Any, **kwargs: Any) -> None:
            state["now"], state["mono"] = NOW + timedelta(seconds=31), 41.0
            original_publish(*args, **kwargs)

        monkeypatch.setattr(profiles, "private_lock", almost_expired)
        monkeypatch.setattr(profiles, "publish_private", expired_prewrite)
        with pytest.raises(profiles.AccountProfileError):
            adopt(owner, candidate)
        assert store.load().state == "PENDING"
    finally:
        await owner.aclose()


def test_thread_contention_before_retirement_allows_only_one_get_sequence(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, state, create = setup
    owner = create()
    paused, release, contender_started, contender_done = (threading.Event() for _ in range(4))
    schedule_lock = threading.Lock()
    original_retire = owner._retire
    original_send = httpx.AsyncHTTPTransport.handle_async_request
    first_thread: list[int] = []

    def paused_retirement() -> None:
        # Pause the first entry before it mutates any candidate/generation state.
        # The competing thread reaches the public operation while this is paused.
        with schedule_lock:
            first = not first_thread
            if first:
                first_thread.append(threading.get_ident())
        if first:
            paused.set()
            assert release.wait(3)
        original_retire()

    async def send(transport: Any, request: httpx.Request) -> httpx.Response:
        if threading.get_ident() == first_thread[0]:
            assert await asyncio.to_thread(contender_done.wait, 3)
        return await original_send(transport, request)

    def run(*, contender: bool = False) -> Any:
        if contender:
            contender_started.set()
        try:
            return asyncio.run(owner.observe())
        except enrollment.AccountEnrollmentError as error:
            return str(error)
        finally:
            if contender:
                contender_done.set()

    monkeypatch.setattr(owner, "_retire", paused_retirement)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", send)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            winner = pool.submit(run)
            assert paused.wait(3)
            loser = pool.submit(run, contender=True)
            assert contender_started.wait(3)
            rejected = loser.result(timeout=3)
            release.set()
            assert rejected == "ENROLLMENT_OPERATION_IN_PROGRESS"
            candidate = winner.result(timeout=6)
        assert type(candidate) is enrollment.EnrollmentCandidate
        assert [request.url.path for request in state["requests"]] == [
            "/v5/market/time", "/v5/user/query-api"]
        assert adopt(owner, candidate).state == "COMPLETE"
        assert store.load().revision == 2
    finally:
        release.set()
        asyncio.run(owner.aclose())


async def test_close_revokes_observation_without_holding_mutex_across_get(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _, create = setup
    owner = create()
    entered, release = asyncio.Event(), asyncio.Event()
    original = httpx.AsyncHTTPTransport.handle_async_request

    async def blocked(transport: Any, request: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return await original(transport, request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
    pending = asyncio.create_task(owner.observe())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.wait_for(owner.aclose(), 2)
        release.set()
        with pytest.raises(enrollment.AccountEnrollmentError):
            await asyncio.wait_for(pending, 2)
        with pytest.raises(enrollment.AccountEnrollmentError, match="CLOSED"):
            await owner.observe()
        assert store.load().state == "PENDING"
    finally:
        release.set()
        await owner.aclose()


async def test_close_revokes_adoption_without_mutex_store_lock_deadlock(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _, create = setup
    owner = create()
    loop = asyncio.get_running_loop()
    entered, release = asyncio.Event(), threading.Event()
    original = store._adopt_initial

    def blocked(**kwargs: Any) -> Any:
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3)
        return original(**kwargs)

    monkeypatch.setattr(store, "_adopt_initial", blocked)
    candidate = await owner.observe()
    pending = asyncio.create_task(asyncio.to_thread(adopt, owner, candidate))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.wait_for(owner.aclose(), 2)
        release.set()
        with pytest.raises(profiles.AccountProfileError):
            await asyncio.wait_for(pending, 2)
        with pytest.raises(enrollment.AccountEnrollmentError, match="CLOSED"):
            adopt(owner, candidate)
        assert store.load().state == "PENDING"
    finally:
        release.set()
        await owner.aclose()


def test_late_finalizer_cannot_retire_a_newer_operations_candidate(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, create = setup
    owner = create()
    entered, release = threading.Event(), threading.Event()
    schedule_lock = threading.Lock()
    original = owner._finish
    first = [True]

    def delayed(operation: object) -> None:
        with schedule_lock:
            pause = first[0]
            first[0] = False
        if pause:
            entered.set()
            assert release.wait(3)
        original(operation)

    monkeypatch.setattr(owner, "_finish", delayed)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            prior = pool.submit(asyncio.run, owner.observe())
            assert entered.wait(3)
            current = pool.submit(asyncio.run, owner.observe())
            candidate = current.result(timeout=3)
            release.set()
            prior.result(timeout=3)
        assert adopt(owner, candidate).state == "COMPLETE"
    finally:
        release.set()
        asyncio.run(owner.aclose())
