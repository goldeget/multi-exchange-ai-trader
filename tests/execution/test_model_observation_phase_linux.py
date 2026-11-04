"""Real native owner/stop/seal/restore/REST comparisons with isolated synthetic I/O."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from test_bybit_native_linux import isolated_native_runtime  # noqa: F401
from test_fresh_monitoring_linux import fresh_case

from multi_exchange_trader.execution.model_observation_phase_v2 import ModelObservationPhaseV2
from multi_exchange_trader.execution.monitoring_resume import MonitoringResume
from multi_exchange_trader.execution.observation_instrument_v1 import (
    SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
)
from multi_exchange_trader.execution.runtime import RuntimeBlocked
from multi_exchange_trader.execution.worker import ExecutionWorker


async def finish(h: Any, phase: ModelObservationPhaseV2) -> None:
    # The fixture must always retain the actual replacement, including failed startup.
    h.worker = phase.worker
    await phase.stop_current()
    await phase.close_sources()


async def test_original_fresh_stop_seal_restore_and_auth_resume_enters_only_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with fresh_case(tmp_path, monkeypatch, metadata_profile=SDK1231_OBSERVATION_UNKNOWN_FEES_V1) as h:
        original, session = h.worker, h.session
        phase = ModelObservationPhaseV2(original)
        try:
            events = [event async for event in phase.enter(asyncio.Event())]
            current = phase.worker
            assert events == ["OLD_STOPPED", "NEW_STARTED", "MODEL_OBSERVING"]
            assert current is not original and original._stopped and not original._lock.acquired
            assert current.session is session and not session._closed
            assert current._lock is not None and current._entry_model_owner_v2 is not None
            assert current._assembly is not None and phase._sealed is not None
            assert current.scope is original.scope and current._lock.acquired
            assert current.native_restart_status.generation_id == phase._sealed.generation_id
            assert type(current._monitoring) is MonitoringResume
            assert current._entry_model_owner_v2._assembler.session is session
            assert current._entry_model_owner_v2._assembler.journal is current.runtime.journal
            assert current.runtime.journal.list_commands() == ()
            status = current.monitoring_status
            assert status.phase == "MONITORING" and status.reconciliation_current
            assert not any((status.trading_enabled, status.credit_authorized,
                status.production_supported, status.venue_event_complete))
            assert len(h.io.wires) == 2 and h.io.wires[0].is_closed() and not h.io.wires[1].is_closed()
            assert current._assembly.channel is not original._assembly.channel
            with pytest.raises(RuntimeBlocked, match="NATIVE_WORKER_QUALIFICATION_REQUIRED"):
                current.runtime.start()
        finally:
            await finish(h, phase)


@pytest.mark.parametrize("failure", ["raises", "lease-still-live"])
async def test_old_stop_failure_or_live_lease_never_creates_a_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    async with fresh_case(tmp_path, monkeypatch, metadata_profile=SDK1231_OBSERVATION_UNKNOWN_FEES_V1) as h:
        original = h.worker
        stop_original = original.stop
        phase = ModelObservationPhaseV2(original)
        attempts = 0

        async def bad_stop() -> None:
            nonlocal attempts
            attempts += 1
            if failure == "raises":
                raise RuntimeBlocked("TEST_OLD_PRODUCER_NOT_QUIESCED")
            original._stopped = True  # A flag alone cannot substitute for released actual lease.

        monkeypatch.setattr(original, "stop", bad_stop)
        try:
            with pytest.raises(RuntimeBlocked):
                _events = [event async for event in phase.enter(asyncio.Event())]
            assert phase.worker is original and not phase._old_stop_confirmed and original._lock.acquired
            with pytest.raises(RuntimeBlocked, match="CLEANUP_UNCONFIRMED"):
                await phase.stop_current()
            assert attempts == 1 and not h.session._closed
            assert phase._marks is None and phase._catalog is None
        finally:
            # Explicit test teardown, never an automatic production retry.
            monkeypatch.setattr(original, "stop", stop_original)
            original._stopped = False
            await stop_original()
            h.worker = original


async def test_another_valid_sealed_generation_is_rejected_under_the_new_uid_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with fresh_case(tmp_path, monkeypatch, metadata_profile=SDK1231_OBSERVATION_UNKNOWN_FEES_V1) as h:
        phase = ModelObservationPhaseV2(h.worker)
        try:
            stream = phase.enter(asyncio.Event())
            assert await anext(stream) == "OLD_STOPPED"
            restart = phase._origin_restart
            assert restart is not None and phase._sealed is not None
            previous = restart.store._read()
            # A valid competing publication, not a malformed digest shortcut.
            document = restart.store._document(restart.identity, phase._sealed.payload, "sealed")
            assert document["generation_id"] != phase._sealed.generation_id
            restart.store._publish(document, previous)
            with pytest.raises(RuntimeBlocked, match="SEALED_GENERATION_CHANGED"):
                await anext(stream)
            assert phase.worker is not phase._origin
            assert phase.worker._assembly is None  # Refused before native restore/construction.
            assert phase.worker._entry_model_owner_v2 is None
        finally:
            await finish(h, phase)


async def test_new_worker_is_retained_before_its_startup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with fresh_case(tmp_path, monkeypatch, metadata_profile=SDK1231_OBSERVATION_UNKNOWN_FEES_V1) as h:
        original = h.worker
        phase = ModelObservationPhaseV2(original)
        start_original = ExecutionWorker.start_passive

        async def start_then_fail(worker: ExecutionWorker) -> None:
            await start_original(worker)
            if worker is not original:
                raise RuntimeBlocked("TEST_NEW_STARTUP_FAILED_AFTER_LEASE")

        monkeypatch.setattr(ExecutionWorker, "start_passive", start_then_fail)
        try:
            with pytest.raises(RuntimeBlocked, match="TEST_NEW_STARTUP_FAILED_AFTER_LEASE"):
                _events = [event async for event in phase.enter(asyncio.Event())]
            assert phase.worker is not original and original._stopped
            assert phase.worker._lock is not None and phase.worker._lock.acquired
            assert not h.session._closed
        finally:
            await finish(h, phase)


@pytest.mark.parametrize("stage", ["PRE", "POST"])
async def test_original_pre_or_post_private_auth_failure_never_reports_model_observing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    async with fresh_case(tmp_path, monkeypatch, metadata_profile=SDK1231_OBSERVATION_UNKNOWN_FEES_V1) as h:
        phase = ModelObservationPhaseV2(h.worker)
        events: list[str] = []

        async def reject(path: str, _params: Any, response: Any) -> None:
            if path == "/v5/position/list" and ((stage == "PRE" and len(h.io.wires) == 1)
                    or (stage == "POST" and len(h.io.wires) >= 2)):
                response.update(retCode=10003, result={})

        try:
            with pytest.raises((ValueError, RuntimeError)):
                async for event in phase.enter(asyncio.Event()):
                    events.append(event)
                    if event == "NEW_STARTED":
                        h.io.on_http = reject
            assert "MODEL_OBSERVING" not in events
            assert phase.worker is not phase._origin
            assert not phase.worker.monitoring_status.trading_enabled
        finally:
            h.io.on_http = None
            await finish(h, phase)


@pytest.mark.parametrize("boundary", ["OLD_STOPPED", "NEW_STARTED"])
async def test_stop_signal_at_a_handoff_boundary_starts_no_later_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    async with fresh_case(tmp_path, monkeypatch, metadata_profile=SDK1231_OBSERVATION_UNKNOWN_FEES_V1) as h:
        phase = ModelObservationPhaseV2(h.worker)
        stop = asyncio.Event()
        events: list[str] = []
        try:
            async for event in phase.enter(stop):
                events.append(event)
                if event == boundary:
                    stop.set()
            assert boundary in events and "MODEL_OBSERVING" not in events
            assert len(h.io.wires) == 1 and h.io.wires[0].is_closed()
        finally:
            await finish(h, phase)


@pytest.mark.parametrize("stage", ["PRE", "POST"])
async def test_signal_during_original_auth_blocks_later_admission_and_positive_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    async with fresh_case(tmp_path, monkeypatch, metadata_profile=SDK1231_OBSERVATION_UNKNOWN_FEES_V1) as h:
        phase = ModelObservationPhaseV2(h.worker)
        stop = asyncio.Event()
        events: list[str] = []
        consumed: list[str] = []
        original_consume = h.session.consume_monitoring_receipt
        signalled = False

        def consume(receipt: Any, *, binding: Any, target: Any, phase: str) -> None:
            consumed.append(phase)
            original_consume(receipt, binding=binding, target=target, phase=phase)

        async def signal(path: str, _params: Any, _response: Any) -> None:
            nonlocal signalled
            if path == "/v5/position/list" and ((stage == "PRE" and len(h.io.wires) == 1)
                    or (stage == "POST" and len(h.io.wires) >= 2)):
                signalled = True
                stop.set()  # Deterministic in-flight REST hook, before original guard/receipt consumption.
                await asyncio.sleep(0)

        try:
            async for event in phase.enter(stop):
                events.append(event)
                if event == "NEW_STARTED":
                    h.io.on_http = signal
                    monkeypatch.setattr(h.session, "consume_monitoring_receipt", consume)
            assert signalled and stop.is_set() and "MODEL_OBSERVING" not in events
            assert "POST" not in consumed
            if stage == "PRE":
                assert consumed == [] and len(h.io.wires) == 1
            else:
                assert consumed == ["PRE"] and len(h.io.wires) == 2
        finally:
            h.io.on_http = None
            await finish(h, phase)
