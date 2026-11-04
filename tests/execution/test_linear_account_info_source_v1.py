"""Original Session with intercepted HTTPX, never actual authentication/source authority."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from multi_exchange_trader.account.bybit_readonly import sign_v5
from multi_exchange_trader.execution.account_binding import BybitDemoAccountSession
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget
from multi_exchange_trader.execution.protection import ProtectionScope

KEY, SECRET = "M3-SESSION-OFFLINE-KEY", "M3-SESSION-OFFLINE-SECRET"
SCOPE = ProtectionScope("12345", "DEMO")
RAW = b'{"retCode":0,"result":{"opaque":"no-field-projection"}}'


def intercepted_session(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[httpx.Request]]:
    now = datetime.now(UTC)
    stamp = int(now.timestamp() * 1000)
    requests: list[httpx.Request] = []

    async def intercepted_send(_self: httpx.AsyncClient, request: httpx.Request,
                               **_kwargs: Any) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET" and request.url.host == "api-demo.bybit.com"
        assert not request.url.query
        if request.url.path == "/v5/market/time":
            return httpx.Response(200, json={"retCode": 0, "result": {}, "time": stamp},
                                  request=request)
        assert request.headers["X-BAPI-SIGN"] == sign_v5(
            SECRET, KEY, request.headers["X-BAPI-TIMESTAMP"],
            request.headers["X-BAPI-RECV-WINDOW"], "")
        if request.url.path == "/v5/user/query-api":
            return httpx.Response(200, json={"retCode": 0, "time": stamp, "result": {
                "apiKey": KEY, "secret": "", "userID": 12345, "parentUid": "56789",
                "isMaster": False, "readOnly": 0,
                "permissions": {"ContractTrade": ["Order", "Position"], "Wallet": [],
                                "Derivatives": ["DerivativesTrade"], "Spot": []}}},
                request=request)
        assert request.url.path == "/v5/account/info"
        return httpx.Response(200, content=RAW, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", intercepted_send)
    return BybitDemoAccountSession(api_key=KEY, api_secret=SECRET, clock=lambda: now), requests


async def authenticate(owner: Any) -> Any:
    return await owner.authenticate(expected_uid="12345", expected_parent_uid="56789",
                                    expected_is_master=False)


async def test_original_session_publishes_one_exact_source_and_requires_after_receipt_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.execution.bybit_linear_model_inputs_v1 import BybitModelSourceRefV1

    owner, requests = intercepted_session(monkeypatch)
    try:
        binding = await authenticate(owner)
        assert owner._transport_injected is owner._client._injected_transport is False
        assert owner.require_observation_binding(binding, scope=SCOPE).uid == "12345"
        capture = getattr(owner, "_capture_linear_account_info_v1", None)
        assert callable(capture), "M3-A original-Session source publication is absent"
        guards: list[str] = []
        budget = MonitoringReadBudget()
        source = await capture(binding, scope=SCOPE, budget=budget,
                               owner_guard=lambda: guards.append("original-guard"))
        assert type(source) is BybitModelSourceRefV1
        assert source.path == "/v5/account/info" and source.query == ()
        assert source.account_scope == SCOPE and source.source_environment == "DEMO"
        assert source.raw_body_sha256 == hashlib.sha256(RAW).hexdigest()
        assert source.envelope_time_ms is None and source.page_ordinal == source.accepted_attempt == 1
        assert budget._receipt.get() is None
        before = len(guards)
        assert owner._require_linear_account_info_v1(source, binding=binding, scope=SCOPE) is None
        assert len(guards) > before and len(requests) == 3
        for value in (source, owner):
            assert KEY not in repr(value) and SECRET not in repr(value)
            assert RAW.decode() not in repr(value)
    finally:
        await owner.aclose()


async def test_issued_require_inner_caught_reentry_taints_original_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.execution.account_binding import AccountBindingError

    owner, requests = intercepted_session(monkeypatch)
    enabled = False
    fired = False
    refused: list[str] = []
    source: Any = None
    binding: Any = None

    def owner_guard() -> None:
        nonlocal fired
        if enabled and not fired:
            fired = True
            try:
                owner._require_linear_account_info_v1(source, binding=binding, scope=SCOPE)
            except AccountBindingError as error:
                refused.append(str(error))

    try:
        binding = await authenticate(owner)
        source = await owner._capture_linear_account_info_v1(
            binding, scope=SCOPE, budget=MonitoringReadBudget(), owner_guard=owner_guard)
        enabled = True
        with pytest.raises(AccountBindingError):
            owner._require_linear_account_info_v1(source, binding=binding, scope=SCOPE)
        assert fired and refused == ["ACCOUNT_INFO_SOURCE_OVERLAP"]
        enabled = False
        with pytest.raises(AccountBindingError):
            owner._require_linear_account_info_v1(source, binding=binding, scope=SCOPE)
        assert len(requests) == 3
    finally:
        await owner.aclose()


async def source_for(owner: Any, binding: Any, guard: Any = lambda: None,
                     budget: Any = None) -> Any:
    return await owner._capture_linear_account_info_v1(binding, scope=SCOPE,
        budget=MonitoringReadBudget() if budget is None else budget, owner_guard=guard)


async def test_constructed_copied_and_foreign_refs_are_data_not_original_issuers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import copy
    from dataclasses import replace

    from multi_exchange_trader.execution.account_binding import AccountBindingError

    owner, _requests = intercepted_session(monkeypatch)
    other: Any = None
    try:
        binding = await authenticate(owner)
        source = await source_for(owner, binding)
        for copied in (copy.copy(source), replace(source)):
            with pytest.raises(AccountBindingError):
                owner._require_linear_account_info_v1(copied, binding=binding, scope=SCOPE)
        copied_owner = copy.copy(owner)
        with pytest.raises(AccountBindingError):
            copied_owner._require_linear_account_info_v1(source, binding=binding, scope=SCOPE)
        with pytest.raises(AccountBindingError): await source_for(copied_owner, binding)
        other, _ = intercepted_session(monkeypatch)
        other_binding = await authenticate(other)
        with pytest.raises(AccountBindingError):
            other._require_linear_account_info_v1(source, binding=other_binding, scope=SCOPE)
        assert owner._require_linear_account_info_v1(source, binding=binding, scope=SCOPE) is None
    finally:
        await owner.aclose()
        if other is not None: await other.aclose()


@pytest.mark.parametrize("change", ["client", "http-client", "binding", "identity", "source-id",
                                   "source-generation", "raw-and-ref", "ref-time", "response-headers"])
async def test_original_issued_preimages_and_owner_changes_cannot_revive_by_restore(
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    import copy
    from dataclasses import replace
    from datetime import timedelta

    from multi_exchange_trader.execution.account_binding import AccountBindingError

    owner, _requests = intercepted_session(monkeypatch)
    extra_http: Any = None
    undo: Any = lambda: None
    try:
        binding = await authenticate(owner)
        source = await source_for(owner, binding)
        slot = owner._linear_info_current
        assert slot is not None and slot.capture is not None
        if change == "client":
            original = owner._client
            owner._client = copy.copy(original)
            undo = lambda: setattr(owner, "_client", original)
        elif change == "http-client":
            original = owner._client._client
            extra_http = httpx.AsyncClient()
            owner._client._client = extra_http
            undo = lambda: setattr(owner._client, "_client", original)
        elif change == "binding":
            owner._binding = object.__new__(type(binding))
            undo = lambda: setattr(owner, "_binding", binding)
        elif change == "identity":
            original = owner._identity
            owner._identity = replace(original)
            undo = lambda: setattr(owner, "_identity", original)
        elif change in ("source-id", "source-generation"):
            attribute = "_account_info_source_id" if change == "source-id" else "_account_info_generation_text"
            original = getattr(owner._client, attribute)
            setattr(owner._client, attribute, "DIFFERENT_SOURCE")
            undo = lambda: setattr(owner._client, attribute, original)
        elif change == "ref-time":
            original = source.received_at
            object.__setattr__(source, "received_at", original + timedelta(microseconds=1))
            undo = lambda: object.__setattr__(source, "received_at", original)
        elif change == "response-headers":
            original = slot.capture.response.headers
            slot.capture.response.headers = httpx.Headers(original)
            undo = lambda: setattr(slot.capture.response, "headers", original)
        else:
            raw, digest = slot.capture.raw_body, source.raw_body_sha256
            replacement = b'{"retCode":0,"result":{"changed":"same-alias"}}'
            new_digest = hashlib.sha256(replacement).hexdigest()
            object.__setattr__(slot.capture, "raw_body", replacement)
            object.__setattr__(slot.capture, "raw_body_sha256", new_digest)
            object.__setattr__(source, "raw_body_sha256", new_digest)

            def restore() -> None:
                object.__setattr__(slot.capture, "raw_body", raw)
                object.__setattr__(slot.capture, "raw_body_sha256", digest)
                object.__setattr__(source, "raw_body_sha256", digest)
            undo = restore
        with pytest.raises(AccountBindingError):
            owner._require_linear_account_info_v1(source, binding=binding, scope=SCOPE)
        undo()
        with pytest.raises(AccountBindingError):
            owner._require_linear_account_info_v1(source, binding=binding, scope=SCOPE)
        assert owner._linear_info_current is None
    finally:
        undo()
        await owner.aclose()
        if extra_http is not None: await extra_http.aclose()


async def test_equal_republication_retires_old_source_without_changing_its_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.execution.account_binding import AccountBindingError

    owner, requests = intercepted_session(monkeypatch)
    try:
        binding = await authenticate(owner)
        first = await source_for(owner, binding)
        original_times = first.request_started_at, first.received_at, first.envelope_time_ms
        second = await source_for(owner, binding)
        assert first is not second and first.raw_body_sha256 == second.raw_body_sha256
        assert first.source_generation != second.source_generation
        with pytest.raises(AccountBindingError):
            owner._require_linear_account_info_v1(first, binding=binding, scope=SCOPE)
        assert owner._require_linear_account_info_v1(second, binding=binding, scope=SCOPE) is None
        assert original_times == (first.request_started_at, first.received_at, first.envelope_time_ms)
        assert len(requests) == 4
    finally:
        await owner.aclose()


async def test_capture_inner_caught_reentry_taints_before_another_http_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.execution.account_binding import AccountBindingError

    owner, requests = intercepted_session(monkeypatch)
    fired = False
    caught: list[str] = []
    binding: Any = None

    def guard() -> None:
        nonlocal fired
        if not fired:
            fired = True
            nested = owner._capture_linear_account_info_v1(binding, scope=SCOPE,
                budget=MonitoringReadBudget(), owner_guard=lambda: None)
            try:
                nested.send(None)
            except AccountBindingError as error:
                caught.append(str(error))
            finally:
                nested.close()

    try:
        binding = await authenticate(owner)
        with pytest.raises(AccountBindingError): await source_for(owner, binding, guard)
        assert caught == ["ACCOUNT_INFO_SOURCE_OVERLAP"] and len(requests) == 2
        assert owner._linear_info_pending is owner._linear_info_current is None
        source = await source_for(owner, binding)
        owner._require_linear_account_info_v1(source, binding=binding, scope=SCOPE)
        assert len(requests) == 3
    finally:
        await owner.aclose()


async def test_authenticate_retires_source_before_waiting_for_its_existing_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from multi_exchange_trader.execution.account_binding import AccountBindingError

    owner, requests = intercepted_session(monkeypatch)
    await owner._refresh_lock.acquire()
    owner._refresh_lock.release()
    try:
        binding = await authenticate(owner)
        source = await source_for(owner, binding)
        await owner._refresh_lock.acquire()
        task = asyncio.create_task(authenticate(owner))
        await asyncio.sleep(0)
        with pytest.raises(AccountBindingError):
            owner._require_linear_account_info_v1(source, binding=binding, scope=SCOPE)
        assert len(requests) == 3
        owner._refresh_lock.release()
        replacement = await task
        assert replacement is not binding
        with pytest.raises(AccountBindingError):
            owner._require_linear_account_info_v1(source, binding=replacement, scope=SCOPE)
    finally:
        if owner._refresh_lock.locked(): owner._refresh_lock.release()
        await owner.aclose()


async def test_inflight_overlap_retires_outer_until_actual_receive_drains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from multi_exchange_trader.execution.account_binding import AccountBindingError

    owner, requests = intercepted_session(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original_send = httpx.AsyncClient.send

    async def send(client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        if request.url.path == "/v5/account/info" and not release.is_set():
            entered.set()
            await release.wait()
        return await original_send(client, request, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    task: Any = None
    try:
        binding = await authenticate(owner)
        task = asyncio.create_task(source_for(owner, binding))
        await entered.wait()
        with pytest.raises(AccountBindingError): await source_for(owner, binding)
        pending = owner._linear_info_pending
        assert pending is not None and pending.retired
        release.set()
        with pytest.raises(AccountBindingError): await task
        assert owner._linear_info_pending is owner._linear_info_current is None
        source = await source_for(owner, binding)
        owner._require_linear_account_info_v1(source, binding=binding, scope=SCOPE)
        assert len(requests) == 4
    finally:
        release.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await owner.aclose()


async def test_guard_cancellation_withdraws_issued_source_before_propagation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from multi_exchange_trader.execution.account_binding import AccountBindingError

    owner, _ = intercepted_session(monkeypatch)
    cancel = False

    def guard() -> None:
        if cancel: raise asyncio.CancelledError

    try:
        binding = await authenticate(owner)
        source = await source_for(owner, binding, guard)
        cancel = True
        with pytest.raises(asyncio.CancelledError):
            owner._require_linear_account_info_v1(source, binding=binding, scope=SCOPE)
        cancel = False
        with pytest.raises(AccountBindingError):
            owner._require_linear_account_info_v1(source, binding=binding, scope=SCOPE)
    finally:
        await owner.aclose()


async def test_injected_session_is_still_unqualified_for_source_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.execution.account_binding import AccountBindingError

    owner, requests = intercepted_session(monkeypatch)
    injected = BybitDemoAccountSession(api_key=KEY, api_secret=SECRET,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=RAW, request=request)))
    try:
        binding = await authenticate(injected)
        with pytest.raises(AccountBindingError): await source_for(injected, binding)
        with pytest.raises(AccountBindingError, match="NETWORK_TRANSPORT_REQUIRED"):
            injected.require_observation_binding(binding, scope=SCOPE)
        assert len(requests) == 2 and injected._linear_info_current is None
    finally:
        await injected.aclose()
        await owner.aclose()


async def test_actual_binding_validation_follows_the_last_original_owner_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, _ = intercepted_session(monkeypatch)
    events: list[str] = []
    enabled = False
    original = owner.require_observation_binding

    def observe(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if enabled: events.append("ACTUAL_BINDING")
        return result

    def guard() -> None:
        if enabled: events.append("ORIGINAL_OWNER")

    monkeypatch.setattr(owner, "require_observation_binding", observe)
    try:
        binding = await authenticate(owner)
        source = await source_for(owner, binding, guard)
        enabled = True
        owner._require_linear_account_info_v1(source, binding=binding, scope=SCOPE)
        assert "ORIGINAL_OWNER" in events and events[-1] == "ACTUAL_BINDING"
    finally:
        await owner.aclose()


async def test_actual_binding_refuses_guard_expiry_without_renewing_source_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import timedelta

    from multi_exchange_trader.execution.account_binding import AccountBindingError

    owner, requests = intercepted_session(monkeypatch)
    original_clock = owner._clock
    expire = False
    fired = False

    def guard() -> None:
        nonlocal fired
        if expire:
            fired = True
            owner._clock = lambda: original_clock() + timedelta(seconds=31)

    try:
        binding = await authenticate(owner)
        source = await source_for(owner, binding, guard)
        original_times = source.request_started_at, source.received_at
        expire = True
        with pytest.raises(AccountBindingError):
            owner._require_linear_account_info_v1(source, binding=binding, scope=SCOPE)
        assert fired and (source.request_started_at, source.received_at) == original_times
        owner._clock = original_clock
        expire = False
        with pytest.raises(AccountBindingError):
            owner._require_linear_account_info_v1(source, binding=binding, scope=SCOPE)
        assert len(requests) == 3
    finally:
        owner._clock = original_clock
        await owner.aclose()


@pytest.mark.parametrize("boundary", ["body", "cleanup"])
async def test_cancellation_and_pending_cleanup_never_publish_or_allow_overlap(
    monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    import asyncio
    from collections.abc import AsyncIterator

    from multi_exchange_trader.execution.account_binding import AccountBindingError

    owner, _ = intercepted_session(monkeypatch)
    body_entered, close_entered = asyncio.Event(), asyncio.Event()
    release_body, release_close = asyncio.Event(), asyncio.Event()
    sends = 0
    task: Any = None

    class BlockingBody(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield RAW
            body_entered.set()
            if boundary == "body": await release_body.wait()

        async def aclose(self) -> None:
            close_entered.set()
            await release_close.wait()

    async def send(_self: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        nonlocal sends
        sends += 1
        return httpx.Response(200, stream=BlockingBody(), request=request) if sends == 1 else (
            httpx.Response(200, content=RAW, request=request))

    try:
        binding = await authenticate(owner)
        monkeypatch.setattr(httpx.AsyncClient, "send", send)
        task = asyncio.create_task(source_for(owner, binding))
        await body_entered.wait()
        if boundary == "body":
            task.cancel()
            await close_entered.wait()
            assert owner._linear_info_pending is not None and owner._linear_info_current is None
            with pytest.raises(AccountBindingError, match="ACCOUNT_INFO_SOURCE_OVERLAP"):
                await source_for(owner, binding)
            assert sends == 1 and owner._linear_info_pending is not None
            release_close.set()
        else:
            await close_entered.wait()
            task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert owner._linear_info_pending is owner._linear_info_current is None
        assert owner._client._account_info_pending is None and sends == 1
        following = await source_for(owner, binding)
        owner._require_linear_account_info_v1(following, binding=binding, scope=SCOPE)
        assert sends == 2
    finally:
        release_body.set()
        release_close.set()
        if task is not None and not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
        await owner.aclose()


async def test_freshness_is_rechecked_after_actual_response_cleanup_before_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import timedelta

    from multi_exchange_trader.execution.account_binding import AccountBindingError

    owner, requests = intercepted_session(monkeypatch)
    original_clock = owner._clock
    fired = False

    def guard() -> None:
        nonlocal fired
        if len(requests) == 3 and owner._client._account_info_pending is None:
            fired = True
            owner._clock = lambda: original_clock() + timedelta(seconds=31)

    try:
        binding = await authenticate(owner)
        with pytest.raises(AccountBindingError): await source_for(owner, binding, guard)
        assert fired and owner._linear_info_current is owner._linear_info_pending is None
        assert requests[-1].url.path == "/v5/account/info"
    finally:
        owner._clock = original_clock
        await owner.aclose()


@pytest.mark.parametrize("close_owner", ["session", "client"])
async def test_original_close_retires_issued_source(
    monkeypatch: pytest.MonkeyPatch, close_owner: str,
) -> None:
    from multi_exchange_trader.execution.account_binding import AccountBindingError

    owner, requests = intercepted_session(monkeypatch)
    try:
        binding = await authenticate(owner)
        source = await source_for(owner, binding)
        await (owner.aclose() if close_owner == "session" else owner._client.aclose())
        with pytest.raises(AccountBindingError):
            owner._require_linear_account_info_v1(source, binding=binding, scope=SCOPE)
        assert len(requests) == 3
    finally:
        await owner.aclose()


@pytest.mark.parametrize("operation", ["capture", "require"])
@pytest.mark.parametrize("field", ["account_id", "environment"])
async def test_supplied_scope_closed_leaves_precede_equality_and_new_get(
    monkeypatch: pytest.MonkeyPatch, operation: str, field: str,
) -> None:
    from multi_exchange_trader.execution.account_binding import AccountBindingError

    calls: list[str] = []

    class UnsupportedLeaf:
        def __eq__(self, other: object) -> bool:
            calls.append(type(other).__name__)
            return True

        def __bool__(self) -> bool:
            raise AssertionError("Unsupported leaf must not dispatch bool")

    owner, requests = intercepted_session(monkeypatch)
    try:
        binding = await authenticate(owner)
        source = await source_for(owner, binding)
        supplied = ProtectionScope("12345", "DEMO")
        object.__setattr__(supplied, field, UnsupportedLeaf())  # Negative alias corruption only.
        with pytest.raises(AccountBindingError):
            if operation == "capture":
                await owner._capture_linear_account_info_v1(binding, scope=supplied,
                    budget=MonitoringReadBudget(), owner_guard=lambda: None)
            else:
                owner._require_linear_account_info_v1(source, binding=binding, scope=supplied)
        assert calls == [] and len(requests) == 3
        assert owner._linear_info_current is owner._linear_info_pending is None
    finally:
        await owner.aclose()


@pytest.mark.parametrize("change", ["source", "scope", "raw", "credentials", "headers",
                                   "late-supplied-scope", "identity-uid", "captured-status"])
async def test_last_genuine_binding_clock_cannot_change_issued_data_or_revive_it(
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    from multi_exchange_trader.execution.account_binding import AccountBindingError

    owner, requests = intercepted_session(monkeypatch)
    original_clock = owner._clock
    enabled = fired = False
    source: Any = None
    capture: Any = None
    supplied = ProtectionScope("12345", "DEMO")
    original_scope_id = SCOPE.account_id
    old_owner_key, old_client_key = owner._api_key, owner._client._api_key
    eq_calls: list[str] = []

    class UnsupportedLeaf:
        def __eq__(self, other: object) -> bool:
            eq_calls.append(type(other).__name__)
            return True

    def clock() -> datetime:
        nonlocal fired
        if enabled and not fired:
            fired = True
            if change == "source": object.__setattr__(source, "observation_id", "CHANGED-SAME-REF")
            elif change == "scope": object.__setattr__(source.account_scope, "account_id", "99999")
            elif change == "raw":
                replacement = b'{"retCode":0,"result":{"changed":"after-final-clock"}}'
                digest = hashlib.sha256(replacement).hexdigest()
                object.__setattr__(capture, "raw_body", replacement)
                object.__setattr__(capture, "raw_body_sha256", digest)
                object.__setattr__(source, "raw_body_sha256", digest)
            elif change == "credentials": owner._api_key = owner._client._api_key = "CHANGED-KEY"
            elif change == "headers": capture.response.headers["content-encoding"] = "gzip"
            elif change == "late-supplied-scope":
                object.__setattr__(supplied, "account_id", UnsupportedLeaf())
            elif change == "identity-uid": object.__setattr__(owner._identity, "uid", "99999")
            elif change == "captured-status":
                object.__setattr__(capture, "response_status", UnsupportedLeaf())
        stamp: object = original_clock()
        assert type(stamp) is datetime
        return stamp

    try:
        binding = await authenticate(owner)
        source = await source_for(owner, binding)
        capture = owner._linear_info_current.capture
        source_id, digest = source.observation_id, source.raw_body_sha256
        raw, raw_digest = capture.raw_body, capture.raw_body_sha256
        original_status = capture.response_status
        original_headers = list(capture.response.headers.multi_items())
        owner._clock = clock
        enabled = True
        with pytest.raises(AccountBindingError):
            owner._require_linear_account_info_v1(source, binding=binding, scope=supplied)
        assert fired and owner._linear_info_current is None and eq_calls == []
        enabled = False
        owner._clock = original_clock
        owner._api_key, owner._client._api_key = old_owner_key, old_client_key
        object.__setattr__(SCOPE, "account_id", original_scope_id)
        object.__setattr__(supplied, "account_id", "12345")
        object.__setattr__(source, "observation_id", source_id)
        object.__setattr__(source, "raw_body_sha256", digest)
        object.__setattr__(capture, "raw_body", raw)
        object.__setattr__(capture, "raw_body_sha256", raw_digest)
        object.__setattr__(capture, "response_status", original_status)
        object.__setattr__(owner._identity, "uid", original_scope_id)
        capture.response.headers.clear()
        capture.response.headers.update(original_headers)
        with pytest.raises(AccountBindingError):
            owner._require_linear_account_info_v1(source, binding=binding, scope=SCOPE)
        assert len(requests) == 3
    finally:
        enabled = False
        owner._clock = original_clock
        owner._api_key, owner._client._api_key = old_owner_key, old_client_key
        object.__setattr__(SCOPE, "account_id", original_scope_id)
        if owner._identity is not None:
            object.__setattr__(owner._identity, "uid", original_scope_id)
        await owner.aclose()


async def test_positive_final_binding_suffix_calls_no_guard_hash_serializer_or_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins
    import json

    from multi_exchange_trader.execution import bybit_linear_model_inputs_v1 as a

    owner, requests = intercepted_session(monkeypatch)
    budget = MonitoringReadBudget()
    original_clock = owner._clock
    original_hash, original_json = hashlib.sha256, json.dumps
    original_import = builtins.__import__
    original_canonical = a.bybit_source_ref_sha256_v1
    original_budget = budget.require_active_group
    tracking = after_clock = False
    late_calls: list[str] = []
    clock_calls: list[str] = []

    def note(name: str) -> None:
        if tracking and after_clock: late_calls.append(name)

    def guard() -> None: note("original-owner-guard")

    def clock() -> datetime:
        nonlocal after_clock
        if tracking:
            clock_calls.append("original-binding-clock")
            after_clock = True
        stamp: object = original_clock()
        assert type(stamp) is datetime
        return stamp

    def digest(*args: Any, **kwargs: Any) -> Any:
        note("hash")
        return original_hash(*args, **kwargs)

    def dumps(*args: Any, **kwargs: Any) -> Any:
        note("json")
        return original_json(*args, **kwargs)

    def imports(*args: Any, **kwargs: Any) -> Any:
        note("import")
        return original_import(*args, **kwargs)

    def canonical(*args: Any, **kwargs: Any) -> Any:
        note("canonical-serializer")
        return original_canonical(*args, **kwargs)

    def active(*args: Any, **kwargs: Any) -> Any:
        note("active-budget")
        return original_budget(*args, **kwargs)

    try:
        binding = await authenticate(owner)
        source = await source_for(owner, binding, guard, budget)
        owner._clock = clock
        monkeypatch.setattr(hashlib, "sha256", digest)
        monkeypatch.setattr(json, "dumps", dumps)
        monkeypatch.setattr(builtins, "__import__", imports)
        monkeypatch.setattr(a, "bybit_source_ref_sha256_v1", canonical)
        monkeypatch.setattr(budget, "require_active_group", active)
        tracking = True
        assert owner._require_linear_account_info_v1(source, binding=binding, scope=SCOPE) is None
        tracking = False
        assert clock_calls == ["original-binding-clock"] and late_calls == []
        assert len(requests) == 3 and owner._linear_info_current.source is source
    finally:
        tracking = False
        owner._clock = original_clock
        await owner.aclose()
