"""Worker-local lifetime only: real fixture ledgers, fake transport/native owners."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_account_binding import payload, session
from test_bybit_native import FakeFactory, FakeStrategy
from test_risk import NOW
from test_worker import fill, harness, quarantines, stage

from multi_exchange_trader.execution.account_binding import (
    AccountBindingError,
    AuthenticatedAccountBinding,
)
from multi_exchange_trader.execution.bybit_native import NativeBindings, NativeBybitBridge
from multi_exchange_trader.execution.native_node import NativeChannel, NativeNodeAssembly
from multi_exchange_trader.execution.protection import ProtectionScope
from multi_exchange_trader.execution.runtime import RuntimeBlocked
from multi_exchange_trader.execution.worker import ExecutionWorker, TrustedPlanRegistry

EXPECTATIONS = {"expected_uid": "12345", "expected_parent_uid": "56789", "expected_is_master": False}


async def authenticated_worker(tmp_path: Path, *, metadata: dict[str, Any] | None = None,
    clock: Callable[[], datetime] = lambda: NOW, monotonic: Callable[[], float] = lambda: 10.0,
) -> Any:
    h = harness(tmp_path)
    await h.session.aclose()
    owner = session(payload() if metadata is None else metadata, clock=clock, monotonic=monotonic,
                    server_ms=int(NOW.timestamp() * 1000))
    binding = await owner.authenticate(**EXPECTATIONS)
    scope = ProtectionScope("12345", "DEMO")
    assert owner.require_binding(binding, scope=scope).scope == scope
    strategy = FakeStrategy()
    bridge = NativeBybitBridge(scope=scope, native_account_id="BYBIT-UNIFIED",
        factory=FakeFactory(), strategy=strategy, bindings=NativeBindings(str, str, str, str, str))
    assembly = NativeNodeAssembly(SimpleNamespace(), strategy, object(), bridge,
                                  NativeChannel(scope, "BYBIT-UNIFIED"))

    def no_entry(_plan: Any, _now: datetime, _reservations: Any) -> Any:
        raise RuntimeBlocked("TEST_NO_APPROVED_ENTRY_RISK")

    h.worker = ExecutionWorker(directory=tmp_path, scope=scope, session=owner, binding=binding,
        symbols=("BTCUSDT",), trader_id="WORKER-001", policy_book=h.book,
        registry=TrustedPlanRegistry(()), entry_risk=no_entry, clock=clock,
        max_age=timedelta(seconds=10), assembly_factory=lambda _sink: assembly)
    h.assembly, h.session, h.binding = assembly, owner, binding
    await h.worker.start_passive()
    return h


async def finish(h: Any) -> None:
    try:
        if not h.worker._stopped:
            await h.worker.stop()
    finally:
        await h.session.aclose()


def refuse_without_suspending(operation: Coroutine[Any, Any, Any]) -> None:
    """Exercise synchronous reentry up to refusal, without awaiting in the section."""
    try:
        with pytest.raises(RuntimeBlocked, match="PUBLICATION"):
            operation.send(None)
    finally:
        operation.close()


def gate_authentication(h: Any, monkeypatch: pytest.MonkeyPatch) -> tuple[asyncio.Event, asyncio.Event]:
    entered, release = asyncio.Event(), asyncio.Event()
    original = h.session._client.read

    async def read(path: str, *args: Any, **kwargs: Any) -> Any:
        if path == "/v5/market/time":
            entered.set()
            await release.wait()
        return await original(path, *args, **kwargs)

    monkeypatch.setattr(h.session._client, "read", read)
    return entered, release


async def test_normal_section_is_local_and_keeps_actual_ledgers(tmp_path: Path) -> None:
    h = await authenticated_worker(tmp_path)
    try:
        with h.worker._entry_publication_section_v2() as approval:
            assert approval is None
            assert h.worker.runtime.journal.list_commands() == ()
            assert h.worker._lock.acquired and not h.assembly.strategy.commands
        with h.worker._entry_publication_section_v2():
            pass
        with pytest.raises(RuntimeBlocked, match="QUALIFICATION"):
            h.worker.runtime.start()
    finally:
        await finish(h)


async def test_inner_caught_section_and_refresh_taint_outer_before_mutation(tmp_path: Path) -> None:
    h = await authenticated_worker(tmp_path)
    try:
        binding = h.worker.binding
        with pytest.raises(RuntimeBlocked, match="PUBLICATION"), h.worker._entry_publication_section_v2():
            frame = h.worker._entry_publication_frame_v2
            with pytest.raises(RuntimeBlocked, match="PUBLICATION"), h.worker._entry_publication_section_v2():
                pytest.fail("nested section must not yield")
            assert h.worker._entry_publication_frame_v2 is frame
        with pytest.raises(RuntimeBlocked, match="PUBLICATION"), h.worker._entry_publication_section_v2():
            refuse_without_suspending(h.worker._refresh_binding_v2(**EXPECTATIONS))
            assert h.worker.binding is binding and h.session._binding is binding
        assert h.worker._operations == 0 and h.worker._entry_publication_frame_v2 is None
        with h.worker._entry_publication_section_v2():
            pass
    finally:
        await finish(h)


async def test_inner_caught_stop_is_sticky_and_outer_cannot_commit(tmp_path: Path) -> None:
    h = await authenticated_worker(tmp_path)
    try:
        with pytest.raises(RuntimeBlocked, match="PUBLICATION"), h.worker._entry_publication_section_v2():
            refuse_without_suspending(h.worker.stop())
            assert h.worker._stopping and h.worker._lock.acquired
            assert h.worker.runtime.journal.list_commands() == ()
        with pytest.raises(RuntimeBlocked, match="WORKER_STOPPING"), h.worker._entry_publication_section_v2():
            pass
    finally:
        await finish(h)


async def test_same_session_equal_refresh_retires_without_renewing_old_frame(tmp_path: Path) -> None:
    h = await authenticated_worker(tmp_path)
    try:
        owner, old = h.session, h.worker.binding
        generation = h.worker._entry_publication_generation_v2
        await h.worker._refresh_binding_v2(**EXPECTATIONS)
        assert h.worker.session is owner and h.worker.binding is owner._binding
        assert h.worker.binding is not old
        assert h.worker._entry_publication_generation_v2 is not generation
        assert h.worker._operations == 0 and h.worker._entry_binding_refresh_v2 is None
        with pytest.raises(ValueError, match="BINDING_REQUIRED"):
            owner.require_binding(old, scope=h.worker.scope)
        with h.worker._entry_publication_section_v2():
            pass
    finally:
        await finish(h)


@pytest.mark.parametrize("duplicate", [False, True])
async def test_actual_authentication_await_blocks_sections_and_duplicate_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, duplicate: bool,
) -> None:
    h = await authenticated_worker(tmp_path)
    entered, release = gate_authentication(h, monkeypatch)
    old, generation = h.worker.binding, h.worker._entry_publication_generation_v2
    task = asyncio.create_task(h.worker._refresh_binding_v2(**EXPECTATIONS))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert h.worker._operations == 1 and h.worker._entry_binding_refresh_v2 is not None
        assert h.worker._entry_publication_generation_v2 is not generation
        assert h.worker.binding is old and h.session._binding is None
        with pytest.raises(RuntimeBlocked, match="REFRESH"), h.worker._entry_publication_section_v2():
            pass
        if duplicate:
            with pytest.raises(RuntimeBlocked, match="REFRESH"):
                await h.worker._refresh_binding_v2(**EXPECTATIONS)
        assert h.worker._operations == 1
        release.set()
        if duplicate:
            with pytest.raises(RuntimeBlocked, match="PUBLICATION"):
                await task
            assert h.worker.binding is old
        else:
            assert await task is None
            assert h.worker.binding is h.session._binding and h.worker.binding is not old
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await finish(h)


@pytest.mark.parametrize("mutation", ["binding", "session", "assembly", "runtime", "journal", "store",
                                    "history", "lease", "node", "channel", "phase", "generation"])
async def test_await_owner_or_phase_change_never_installs_stale_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    h = await authenticated_worker(tmp_path)
    entered, release = gate_authentication(h, monkeypatch)
    fields = ("binding", "session", "_assembly", "_runtime", "_journal", "_store", "_history_store", "_lock")
    original = {name: getattr(h.worker, name) for name in fields}
    node, channel = h.assembly.node, h.assembly.channel
    task = asyncio.create_task(h.worker._refresh_binding_v2(**EXPECTATIONS))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if mutation == "binding":
            h.worker.binding = object.__new__(AuthenticatedAccountBinding)
        elif mutation == "session":
            h.worker.session = object()
        elif mutation in ("assembly", "runtime", "journal", "store", "history"):
            name = {"history": "_history_store"}.get(mutation, "_" + mutation)
            setattr(h.worker, name, object())
        elif mutation == "lease":
            h.worker._lock.release()
            h.worker._lock.acquire()
        elif mutation == "node":
            h.assembly.node = object()
        elif mutation == "channel":
            h.assembly.channel = object()
        elif mutation == "phase":
            h.worker._entry_binding_refresh_v2 = object()
        else:
            h.worker._retire_entry_publications_v2()
        expected_binding = h.worker.binding
        release.set()
        with pytest.raises(RuntimeBlocked):
            await task
        assert h.worker.binding is expected_binding
        assert h.worker.binding is not h.session._binding
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        for name, value in original.items():
            setattr(h.worker, name, value)
        h.assembly.node, h.assembly.channel = node, channel
        # Fixture disposal only: a replaced lease cannot be blessed for normal stop.
        h.worker._close_stores()
        h.worker._stopped = True
        await h.session.aclose()


async def test_stop_during_auth_wait_retains_resources_and_rejects_late_assignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await authenticated_worker(tmp_path)
    entered, release = gate_authentication(h, monkeypatch)
    old = h.worker.binding
    task = asyncio.create_task(h.worker._refresh_binding_v2(**EXPECTATIONS))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(RuntimeBlocked, match="INFLIGHT_OPERATION"):
            await h.worker.stop()
        assert h.worker._stopping and not h.worker._stopped and h.worker._lock.acquired
        assert h.worker.runtime.journal.list_commands() == ()
        release.set()
        with pytest.raises(RuntimeBlocked):
            await task
        assert h.worker.binding is old and h.worker._operations == 0
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await finish(h)


async def test_cancel_and_original_auth_error_do_not_install_and_can_refresh_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await authenticated_worker(tmp_path)
    entered, release = gate_authentication(h, monkeypatch)
    original_binding = h.worker.binding
    task = asyncio.create_task(h.worker._refresh_binding_v2(**EXPECTATIONS))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert h.worker.binding is original_binding and h.worker._operations == 0
        assert h.worker._entry_binding_refresh_v2 is None
        release.set()
        seen: list[BaseException] = []
        authenticate = h.session.authenticate

        async def record_error(**kwargs: Any) -> Any:
            try:
                return await authenticate(**kwargs)
            except BaseException as error:
                seen.append(error)
                raise

        monkeypatch.setattr(h.session, "authenticate", record_error)
        with pytest.raises(ValueError) as caught:
            await h.worker._refresh_binding_v2(**{**EXPECTATIONS, "expected_parent_uid": "999"})
        assert caught.value is seen[0] and h.worker.binding is original_binding
        assert h.worker._entry_binding_refresh_v2 is None and h.worker._operations == 0
        await h.worker._refresh_binding_v2(**EXPECTATIONS)
        with h.worker._entry_publication_section_v2():
            pass
    finally:
        release.set()
        await finish(h)


async def test_queued_fill_still_arrives_and_callback_failure_taints_outer(tmp_path: Path) -> None:
    h = harness(tmp_path)
    await h.worker.start_passive()
    try:
        record = await stage(h)
        h.worker.runtime.journal.claim("entry")
        with h.worker._entry_publication_section_v2():
            h.worker.post_native(h.assembly.channel, fill(record.client_order_id))
            assert quarantines(h.worker) == ()
        await asyncio.sleep(0)
        assert len(quarantines(h.worker)) == 1 and h.worker.callback_failures == 0
        with pytest.raises(RuntimeBlocked, match="PUBLICATION"), h.worker._entry_publication_section_v2():
            h.worker._consume_native(h.assembly.channel, fill("unknown"), 1)
        assert h.worker.callback_failures == 1 and h.worker._lock.acquired
    finally:
        await finish(h)


@pytest.mark.parametrize("in_progress", [True, False])
async def test_refresh_respects_existing_monitoring_admission(tmp_path: Path, in_progress: bool) -> None:
    h = await authenticated_worker(tmp_path)
    try:
        old = h.worker.binding
        h.worker._monitoring = SimpleNamespace(in_progress=in_progress)
        with pytest.raises(RuntimeBlocked, match="MONITORING"):
            await h.worker._refresh_binding_v2(**EXPECTATIONS)
        assert h.worker.binding is old and h.session._binding is old and h.worker._operations == 0
    finally:
        h.worker._monitoring = None
        await finish(h)


async def test_cleanup_retirement_does_not_skip_independent_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await authenticated_worker(tmp_path)
    trace: list[str] = []
    marker = RuntimeError("fixture history close failure")
    for name, owner in (("history", h.worker._history_store), ("store", h.worker._store),
                        ("journal", h.worker._journal)):
        original = owner.close

        def close(name: str = name, original: Any = original) -> None:
            trace.append(name)
            original()
            if name == "history":
                raise marker

        monkeypatch.setattr(owner, "close", close)
    try:
        with pytest.raises(RuntimeBlocked, match="PUBLICATION"), h.worker._entry_publication_section_v2():
            with pytest.raises(RuntimeError) as caught:
                h.worker._close_stores()
            assert caught.value is marker
        assert trace == ["history", "store", "journal"] and not h.worker._lock.acquired
    finally:
        h.worker._stopped = True
        await h.session.aclose()


async def test_caught_guard_reentry_cannot_issue_section_before_yield(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await authenticated_worker(tmp_path)
    original_guard = h.worker.runtime._guard
    first = True

    def guard() -> None:
        nonlocal first
        if first:
            first = False
            refuse_without_suspending(h.worker._refresh_binding_v2(**EXPECTATIONS))
        original_guard()

    monkeypatch.setattr(h.worker.runtime, "_guard", guard)
    try:
        with pytest.raises(RuntimeBlocked, match="PUBLICATION"), h.worker._entry_publication_section_v2():
            pytest.fail("tainted validation must not yield a section")
        with h.worker._entry_publication_section_v2():
            assert h.worker.runtime.journal.list_commands() == ()
    finally:
        await finish(h)


async def test_start_failure_retires_and_releases_lease_without_closing_session(tmp_path: Path) -> None:
    h = harness(tmp_path)
    generation = h.worker._entry_publication_generation_v2
    marker = RuntimeError("fixture assembly failure")

    def fail(_sink: Any) -> Any:
        raise marker

    h.worker._factory = fail
    try:
        with pytest.raises(RuntimeError) as caught:
            await h.worker.start_passive()
        assert caught.value is marker and h.worker._stopped
        assert h.worker._entry_publication_generation_v2 is not generation
        assert h.worker._lock is not None and not h.worker._lock.acquired
        assert not h.session._closed and not (tmp_path / "execution.sqlite3").exists()
    finally:
        await h.session.aclose()


async def test_connect_and_original_task_finally_retire_local_attempts(tmp_path: Path) -> None:
    h = harness(tmp_path)
    h.core.permit_fake_monitoring()
    await h.worker.start_passive()
    finished = asyncio.Event()

    async def run() -> None:
        await finished.wait()

    async def shutdown() -> None:
        finished.set()

    h.assembly.node = SimpleNamespace(run_async=run, stop_async=shutdown)
    try:
        generation = h.worker._entry_publication_generation_v2
        await h.worker.connect_monitoring()
        assert h.worker._entry_publication_generation_v2 is not generation
        generation = h.worker._entry_publication_generation_v2
        finished.set()
        await h.worker._node_task
        assert h.worker._entry_publication_generation_v2 is not generation
        assert h.worker.runtime.status.entries_paused and h.worker._lock.acquired
        assert not h.assembly.strategy.commands
    finally:
        finished.set()
        await finish(h)


async def test_refresh_rejects_foreign_account_binding(tmp_path: Path) -> None:
    metadata = payload()
    h = await authenticated_worker(tmp_path, metadata=metadata)
    old = h.worker.binding
    try:
        metadata["userID"] = 67890
        with pytest.raises(AccountBindingError, match="SCOPE_MISMATCH"):
            await h.worker._refresh_binding_v2(**{**EXPECTATIONS, "expected_uid": "67890"})
        assert h.worker.scope.account_id == "12345" and h.worker.binding is old
        assert h.session._binding is not old and h.worker._operations == 0
        assert h.worker._entry_binding_refresh_v2 is None
        assert h.worker.runtime.journal.list_commands() == () and not h.assembly.strategy.commands
    finally:
        await finish(h)


async def test_refresh_rejects_binding_expired_during_owner_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    wall, mono = [NOW], [10.0]
    h = await authenticated_worker(tmp_path, clock=lambda: wall[0], monotonic=lambda: mono[0])
    old = h.worker.binding
    original_guard = h.worker.runtime._guard
    advanced = False

    def guard() -> None:
        nonlocal advanced
        original_guard()
        if h.session._binding is not None and h.session._binding is not old and not advanced:
            wall[0] += timedelta(seconds=11)
            mono[0] += 11
            advanced = True

    monkeypatch.setattr(h.worker.runtime, "_guard", guard)
    try:
        with pytest.raises(AccountBindingError, match="BINDING_STALE"):
            await h.worker._refresh_binding_v2(**EXPECTATIONS)
        assert advanced and h.worker.binding is old and h.worker._operations == 0
        assert h.worker._entry_binding_refresh_v2 is None
        assert h.worker.runtime.journal.list_commands() == () and not h.assembly.strategy.commands
    finally:
        await finish(h)


@pytest.mark.parametrize("mutation", ["assembly", "scope", "phase", "stopping", "caught_refresh", "caught_stop"])
async def test_final_binding_clock_callback_cannot_replace_owner_or_phase(
    tmp_path: Path, mutation: str,
) -> None:
    holder: list[Any] = []
    changed = False

    def clock() -> datetime:
        nonlocal changed
        if holder:
            current = holder[0]
            if (current.session._binding is not None and current.session._binding is not current.binding
                    and not changed):
                changed = True
                if mutation == "assembly":
                    current.worker._assembly = replace(current.assembly)
                elif mutation == "scope":
                    current.worker.scope = ProtectionScope("12345", "DEMO")
                elif mutation == "phase":
                    current.worker._entry_binding_refresh_v2 = object()
                elif mutation == "stopping":
                    current.worker._stopping = True
                else:
                    operation = (current.worker._refresh_binding_v2(**EXPECTATIONS)
                                 if mutation == "caught_refresh" else current.worker.stop())
                    try:
                        with pytest.raises(RuntimeBlocked, match="REFRESH|INFLIGHT_OPERATION"):
                            operation.send(None)
                    finally:
                        operation.close()
        return NOW

    h = await authenticated_worker(tmp_path, clock=clock)
    holder.append(h)
    old, scope = h.worker.binding, h.worker.scope
    try:
        with pytest.raises(RuntimeBlocked):
            await h.worker._refresh_binding_v2(**EXPECTATIONS)
        assert changed and h.worker.binding is old and h.session._binding is not old
        assert h.worker._operations == 0 and h.worker._lock.acquired
        assert h.worker.runtime.journal.list_commands() == () and not h.assembly.strategy.commands
    finally:
        h.worker._assembly, h.worker.scope = h.assembly, scope
        await finish(h)


@pytest.mark.parametrize("mutation", [
    "credentials-key", "credentials-secret", "session-client", "credential-client", "http-client",
    "closed", "identity-uid", "identity-copy", "scope-uid", "scope-environment", "pair-reference",
    "credential-type", "client-environment", "client-base-url", "stopping-type",
])
async def test_final_original_binding_callback_cannot_install_changed_owner_values(
    tmp_path: Path, mutation: str,
) -> None:
    import copy

    holder: list[Any] = []
    changed = False
    equality_calls: list[str] = []

    class UnsupportedKey(str):
        def __eq__(self, other: object) -> bool:
            equality_calls.append(type(other).__name__)
            return True

    class UnsupportedFlag:
        def __bool__(self) -> bool:
            equality_calls.append("unsupported flag truthiness")
            return False

    def clock() -> datetime:
        nonlocal changed
        if holder:
            h = holder[0]
            if h.session._binding is not None and h.session._binding is not h.worker.binding and not changed:
                changed = True
                if mutation == "credentials-key":
                    h.session._api_key = h.session._client._api_key = "CHANGED-KEY"
                elif mutation == "credentials-secret":
                    h.session._api_secret = h.session._client._api_secret = "CHANGED-SECRET"
                elif mutation == "session-client":
                    h.session._client = copy.copy(h.session._client)
                elif mutation == "credential-client":
                    h.session._credential_client = copy.copy(h.session._client)
                elif mutation == "http-client":
                    h.session._client._client = copy.copy(h.session._client._client)
                elif mutation == "closed":
                    h.session._closed = True
                elif mutation == "identity-uid":
                    object.__setattr__(h.session._identity, "uid", "99999")
                elif mutation == "identity-copy":
                    h.session._identity = replace(h.session._identity)
                elif mutation == "scope-uid":
                    object.__setattr__(h.worker.scope, "account_id", "99999")
                elif mutation == "scope-environment":
                    object.__setattr__(h.worker.scope, "environment", "LIVE")
                elif mutation == "pair-reference":
                    h.session._credential_pair = tuple(list(h.session._credential_pair))
                elif mutation == "client-environment":
                    h.session._client.environment = "testnet"
                elif mutation == "client-base-url":
                    h.session._client.base_url = "https://api-testnet.bybit.com"
                elif mutation == "stopping-type":
                    h.worker._stopping = UnsupportedFlag()
                else:
                    h.session._api_key = h.session._client._api_key = UnsupportedKey("fake-binding-api-key")
        return NOW

    h = await authenticated_worker(tmp_path, clock=clock)
    owner, client, scope, old = h.session, h.session._client, h.worker.scope, h.worker.binding
    pair, credential_client, http_client = owner._credential_pair, owner._credential_client, client._client
    environment, base_url = client.environment, client.base_url
    holder.append(h)
    try:
        with pytest.raises(RuntimeBlocked, match="OWNER_CHANGED"):
            await h.worker._refresh_binding_v2(**EXPECTATIONS)
        assert changed and h.worker.binding is old and owner._binding is not old
        assert h.worker._operations == 0 and h.worker._entry_binding_refresh_v2 is None
        assert h.worker._lock.acquired and not h.assembly.strategy.commands and equality_calls == []
        assert h.worker.runtime.journal.list_commands() == ()
    finally:
        holder.clear()
        owner._client, owner._credential_client, owner._credential_pair = client, credential_client, pair
        client._client = http_client
        client.environment, client.base_url = environment, base_url
        owner._api_key, owner._api_secret = pair
        client._api_key, client._api_secret = pair
        owner._closed = False
        h.worker._stopping = False
        object.__setattr__(scope, "account_id", "12345")
        object.__setattr__(scope, "environment", "DEMO")
        if owner._identity is not None:
            object.__setattr__(owner._identity, "uid", "12345")
        await finish(h)


@pytest.mark.parametrize("boundary", ["final-clock", "owner-guard", "await"])
async def test_final_original_binding_allows_equal_plain_strings_without_late_file_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    holder: list[Any] = []
    changed = False
    after_final_clock = False
    late_guards: list[str] = []

    def same_values(h: Any) -> None:
        nonlocal changed
        key, secret = h.session._credential_pair
        h.session._api_key = h.session._client._api_key = key.encode().decode()
        h.session._api_secret = h.session._client._api_secret = secret.encode().decode()
        h.session._client.environment = h.session._client.environment.encode().decode()
        h.session._client.base_url = h.session._client.base_url.encode().decode()
        changed = True

    def clock() -> datetime:
        nonlocal after_final_clock
        if holder:
            h = holder[0]
            if h.session._binding is not None and h.session._binding is not h.worker.binding:
                after_final_clock = True
                if boundary == "final-clock" and not changed:
                    same_values(h)
        return NOW

    h = await authenticated_worker(tmp_path, clock=clock)
    original_guard = h.worker.runtime._guard

    def guard() -> None:
        if after_final_clock:
            late_guards.append("original-file-guard")
        original_guard()
        if (boundary == "owner-guard" and h.session._binding is not None
                and h.session._binding is not h.worker.binding and not changed):
            same_values(h)

    monkeypatch.setattr(h.worker.runtime, "_guard", guard)
    holder.append(h)
    old = h.worker.binding
    task: Any = None
    release: Any = None
    try:
        if boundary == "await":
            entered, release = gate_authentication(h, monkeypatch)
            task = asyncio.create_task(h.worker._refresh_binding_v2(**EXPECTATIONS))
            await asyncio.wait_for(entered.wait(), 5)
            same_values(h)
            release.set()
            await task
        else:
            await h.worker._refresh_binding_v2(**EXPECTATIONS)
        assert changed and h.worker.binding is h.session._binding and h.worker.binding is not old
        assert late_guards == [] and h.worker._operations == 0 and h.worker._entry_binding_refresh_v2 is None
        holder.clear()
        assert h.session.require_binding(h.worker.binding, scope=h.worker.scope).uid == "12345"
        assert not h.assembly.strategy.commands and h.worker.runtime.journal.list_commands() == ()
    finally:
        holder.clear()
        if release is not None:
            release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await finish(h)


@pytest.mark.parametrize("mutation", ["client-pair", "credentials-pair", "scope-uid"])
async def test_owner_guard_cannot_readopt_coherent_changed_binding_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    import copy

    h = await authenticated_worker(tmp_path)
    owner, client, pair, scope, old = h.session, h.session._client, h.session._credential_pair, h.worker.scope, h.worker.binding
    original_guard = h.worker.runtime._guard
    changed = False

    def guard() -> None:
        nonlocal changed
        original_guard()
        if owner._binding is not None and owner._binding is not old and not changed:
            changed = True
            if mutation == "client-pair":
                owner._client = owner._credential_client = copy.copy(client)
                owner._credential_pair = tuple(list(pair))
            elif mutation == "credentials-pair":
                owner._api_key = client._api_key = "CHANGED-KEY"
                owner._api_secret = client._api_secret = "CHANGED-SECRET"
                owner._credential_pair = ("CHANGED-KEY", "CHANGED-SECRET")
            else:
                object.__setattr__(scope, "account_id", "67890")
                object.__setattr__(owner._identity, "uid", "67890")

    monkeypatch.setattr(h.worker.runtime, "_guard", guard)
    try:
        with pytest.raises(RuntimeBlocked, match="OWNER_CHANGED"):
            await h.worker._refresh_binding_v2(**EXPECTATIONS)
        assert changed and h.worker.binding is old and h.worker._operations == 0
        assert h.worker._entry_binding_refresh_v2 is None and not h.assembly.strategy.commands
        assert h.worker.runtime.journal.list_commands() == ()
    finally:
        owner._client = owner._credential_client = client
        owner._credential_pair = pair
        owner._api_key, owner._api_secret = pair
        client._api_key, client._api_secret = pair
        object.__setattr__(scope, "account_id", "12345")
        if owner._identity is not None:
            object.__setattr__(owner._identity, "uid", "12345")
        await finish(h)


@pytest.mark.parametrize("mutation", ["client-pair", "pair", "http-client", "scope-uid"])
async def test_authenticate_await_cannot_readopt_changed_original_binding_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    import copy

    metadata = payload()
    h = await authenticated_worker(tmp_path, metadata=metadata)
    owner, client, pair, scope, old = h.session, h.session._client, h.session._credential_pair, h.worker.scope, h.worker.binding
    http_client = client._client
    entered, release = gate_authentication(h, monkeypatch)
    expectations = dict(EXPECTATIONS)
    if mutation == "scope-uid":
        metadata["userID"] = 67890
        expectations["expected_uid"] = "67890"
    task = asyncio.create_task(h.worker._refresh_binding_v2(**expectations))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if mutation == "client-pair":
            owner._client = owner._credential_client = copy.copy(client)
            owner._credential_pair = tuple(list(pair))
        elif mutation == "pair":
            owner._credential_pair = tuple(list(pair))
        elif mutation == "http-client":
            client._client = copy.copy(http_client)
        else:
            object.__setattr__(scope, "account_id", "67890")
        release.set()
        with pytest.raises(RuntimeBlocked, match="OWNER_CHANGED"):
            await task
        assert h.worker.binding is old and h.worker._operations == 0
        assert h.worker._entry_binding_refresh_v2 is None and not h.assembly.strategy.commands
        assert h.worker.runtime.journal.list_commands() == ()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        owner._client = owner._credential_client = client
        owner._credential_pair = pair
        client._client = http_client
        object.__setattr__(scope, "account_id", "12345")
        await finish(h)
