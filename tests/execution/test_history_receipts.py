"""Synthetic private-core lifecycle checks; never actual authenticated GET evidence.

The public injected-transport route must reject. Other fixtures seed a private
binding and replace the exact client's read method, outside the process trust
boundary, so no production test issuer or authorization switch is introduced.
"""
import asyncio
import copy
import pickle
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from multi_exchange_trader.execution.account_binding import (
    AuthenticatedAccountBinding,
    BybitAccountIdentity,
    BybitDemoAccountSession,
)
from multi_exchange_trader.execution.codec import encode
from multi_exchange_trader.execution.protection import ProtectionScope

NOW = datetime(2026, 11, 2, 1, tzinfo=UTC)
MS = int(NOW.timestamp() * 1000)
SCOPE = ProtectionScope("12345", "DEMO")


@dataclass
class SyntheticCore:
    owner: Any
    binding: Any
    wall: list[datetime]
    mono: list[float]
    server: list[int]
    started: asyncio.Event
    release: asyncio.Event
    block: list[bool]
    failure: list[bool]
    reads: list[str]


@pytest.fixture
async def synthetic_core(monkeypatch: pytest.MonkeyPatch) -> Any:
    wall, mono, server = [NOW], [10.0], [MS]
    owner = BybitDemoAccountSession(api_key="fake-receipt-key", api_secret="fake-secret",
        clock=lambda: wall[0], monotonic=lambda: mono[0], max_age=timedelta(seconds=60))
    binding = object.__new__(AuthenticatedAccountBinding)
    # Synthetic fixture only: no claim these values came from authentication.
    owner._binding = binding
    owner._identity = BybitAccountIdentity("12345", "0", False, True,
        (("Wallet", ()),), NOW, NOW, False)
    owner._issued_mono = 10.0
    core = SyntheticCore(owner, binding, wall, mono, server, asyncio.Event(),
                         asyncio.Event(), [False], [False], [])

    async def read(path: str, params: Any, *, signed: bool, deadline: float) -> Any:
        core.reads.append(path)
        if core.block[0]:
            core.started.set()
            await core.release.wait()
        if core.failure[0]:
            raise RuntimeError("fake-receipt-key fake-secret provider body")
        if path == "/v5/user/query-api":
            return {"apiKey": "fake-receipt-key", "secret": "", "userID": 12345,
                    "isMaster": False, "parentUid": "0", "readOnly": 1,
                    "permissions": {"Wallet": []}}, core.server[0]
        return {"category": "linear", "list": [], "nextPageCursor": ""}, core.server[0]

    monkeypatch.setattr(owner._client, "read", read)
    try:
        yield core
    finally:
        await owner.aclose()


async def issue(core: SyntheticCore) -> Any:
    return await core.owner.read_history_receipt(core.binding, scope=SCOPE,
        symbol="BTCUSDT", start_ms=MS - 1000, end_ms=MS)


async def test_public_receipt_rejects_injected_transport() -> None:
    reads: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        reads.append(request.url.path)
        result = {} if request.url.path == "/v5/market/time" else {
            "apiKey": "fake-receipt-key", "secret": "", "userID": 12345,
            "isMaster": False, "parentUid": "0", "readOnly": 1,
            "permissions": {"Wallet": []}}
        return httpx.Response(200, json={"retCode": 0, "time": MS, "result": result})

    owner = BybitDemoAccountSession(api_key="fake-receipt-key", api_secret="fake-secret",
        clock=lambda: NOW, monotonic=lambda: 10.0, transport=httpx.MockTransport(respond))
    try:
        binding = await owner.authenticate(expected_uid="12345", expected_parent_uid="0",
                                           expected_is_master=False)
        with pytest.raises(ValueError, match="TRANSPORT"):
            await owner.read_history_receipt(binding, scope=SCOPE, symbol="BTCUSDT",
                                             start_ms=MS - 1000, end_ms=MS)
        assert reads == ["/v5/market/time", "/v5/user/query-api"]
        assert len(owner._history_receipts) == 0
        with pytest.raises(ValueError, match="TRANSPORT"):
            owner.require_execution_binding(binding, scope=SCOPE)
    finally:
        await owner.aclose()


async def test_receipt_cannot_be_constructed_copied_or_serialized(
    synthetic_core: SyntheticCore,
) -> None:
    from multi_exchange_trader.execution.history_receipts import AuthenticatedHistoryReceipt

    receipt = await issue(synthetic_core)
    for action in (AuthenticatedHistoryReceipt, lambda: copy.copy(receipt),
                   lambda: copy.deepcopy(receipt), lambda: pickle.dumps(receipt),
                   lambda: encode(receipt)):
        with pytest.raises((ValueError, TypeError)):
            action()
    assert repr(receipt) == "AuthenticatedHistoryReceipt(opaque=True)"
    assert "fake" not in repr(receipt)


async def test_foreign_handle_and_view_do_not_authorize(synthetic_core: SyntheticCore) -> None:
    from multi_exchange_trader.execution.history_receipts import AuthenticatedHistoryReceipt

    core = synthetic_core
    receipt = await issue(core)
    view = core.owner.require_history_receipt(receipt, binding=core.binding, scope=SCOPE)
    for candidate in (object.__new__(AuthenticatedHistoryReceipt), view,
                      view.snapshot, view.binding_reference, object()):
        with pytest.raises(ValueError):
            core.owner.require_history_receipt(candidate, binding=core.binding, scope=SCOPE)
    other = BybitDemoAccountSession(api_key="fake-other", api_secret="fake-other-secret",
        clock=lambda: NOW, monotonic=lambda: 10.0)
    other_binding = object.__new__(AuthenticatedAccountBinding)
    other._binding, other._identity, other._issued_mono = other_binding, core.owner._identity, 10.0
    try:
        with pytest.raises(ValueError):
            other.require_history_receipt(receipt, binding=other_binding, scope=SCOPE)
        other.discard_history_receipt(receipt)
        assert core.owner.require_history_receipt(receipt, binding=core.binding, scope=SCOPE)
        with pytest.raises((TypeError, ValueError)):
            encode(view)
    finally:
        await other.aclose()
    core.owner.discard_history_receipt(receipt)
    core.owner.discard_history_receipt(receipt)
    with pytest.raises(ValueError):
        core.owner.require_history_receipt(receipt, binding=core.binding, scope=SCOPE)


async def test_read_only_identity_does_not_gain_execution_permission(
    synthetic_core: SyntheticCore,
) -> None:
    core = synthetic_core
    assert core.owner.require_observation_binding(core.binding, scope=SCOPE).read_only
    receipt = await issue(core)
    view = core.owner.require_history_receipt(receipt, binding=core.binding, scope=SCOPE)
    assert view.snapshot.scope == SCOPE
    assert view.snapshot.symbol == "BTCUSDT"
    assert view.snapshot.window.start_ms == MS - 1000
    assert view.snapshot.window.end_ms == MS
    assert not view.snapshot.transport_injected
    with pytest.raises(ValueError, match="PERMISSIONS"):
        core.owner.require_execution_binding(core.binding, scope=SCOPE)


async def test_four_pending_and_issued_slots_are_bounded(synthetic_core: SyntheticCore) -> None:
    core = synthetic_core
    issued = [await issue(core) for _ in range(2)]
    core.block[0] = True
    pending = [asyncio.create_task(issue(core)) for _ in range(2)]
    await core.started.wait()
    await asyncio.sleep(0)
    try:
        with pytest.raises(ValueError, match="CAPACITY"):
            await issue(core)
        for receipt in issued:
            assert core.owner.require_history_receipt(receipt, binding=core.binding, scope=SCOPE)
    finally:
        core.release.set()
        completed = await asyncio.gather(*pending)
    for receipt in issued + completed:
        assert core.owner.require_history_receipt(receipt, binding=core.binding, scope=SCOPE)


async def test_expiry_prunes_without_evicting_live_handles(synthetic_core: SyntheticCore) -> None:
    core = synthetic_core
    old = await issue(core)
    core.wall[0] += timedelta(seconds=1)
    core.mono[0] += 1
    core.server[0] += 1000
    live = [await issue(core) for _ in range(3)]
    with pytest.raises(ValueError, match="CAPACITY"):
        await issue(core)
    core.wall[0] += timedelta(seconds=29.1)
    core.mono[0] += 29.1
    core.server[0] += 29100
    fresh = await issue(core)
    with pytest.raises(ValueError):
        core.owner.require_history_receipt(old, binding=core.binding, scope=SCOPE)
    for receipt in live + [fresh]:
        assert core.owner.require_history_receipt(receipt, binding=core.binding, scope=SCOPE)


async def test_cancelled_failed_get_frees_slot(synthetic_core: SyntheticCore) -> None:
    core = synthetic_core
    assert callable(getattr(core.owner, "read_history_receipt", None))
    core.block[0] = True
    pending = asyncio.create_task(issue(core))
    await core.started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    core.block[0] = False
    core.failure[0] = True
    with pytest.raises(ValueError) as failed:
        await issue(core)
    assert "fake" not in str(failed.value)
    assert failed.value.__context__ is None
    core.failure[0] = False
    receipts = [await issue(core) for _ in range(4)]
    assert len(receipts) == 4


@pytest.mark.parametrize("change", ["reauth", "close", "key", "secret", "host"])
async def test_reauth_close_and_key_change_revoke_inflight(
    synthetic_core: SyntheticCore, change: str,
) -> None:
    core = synthetic_core
    issued = await issue(core)
    core.block[0] = True
    pending = asyncio.create_task(issue(core))
    await core.started.wait()
    if change == "reauth":
        with pytest.raises(ValueError):
            await core.owner.authenticate(expected_uid="invalid", expected_parent_uid="0",
                                          expected_is_master=False)
    elif change == "close":
        await core.owner.aclose()
    else:
        attribute = {"key": "_api_key", "secret": "_api_secret", "host": "base_url"}[change]
        setattr(core.owner._client, attribute, "changed")
    core.release.set()
    with pytest.raises(ValueError):
        await pending
    with pytest.raises(ValueError):
        core.owner.require_history_receipt(issued, binding=core.binding, scope=SCOPE)
    assert len(core.owner._history_receipts) == 0


@pytest.mark.parametrize("change", ["wall", "mono", "nan", "server", "future"])
async def test_clock_rollback_and_stale_server_time_reject(
    synthetic_core: SyntheticCore, change: str,
) -> None:
    core = synthetic_core
    receipt = await issue(core)
    if change == "wall":
        core.wall[0] -= timedelta(milliseconds=1)
    elif change == "mono":
        core.mono[0] -= .001
    elif change == "nan":
        core.mono[0] = float("nan")
    else:
        core.server[0] += -31000 if change == "server" else 1
        with pytest.raises(ValueError):
            await issue(core)
        return
    with pytest.raises(ValueError):
        core.owner.require_history_receipt(receipt, binding=core.binding, scope=SCOPE)


async def test_binding_reference_is_stable_only_for_same_binding(
    synthetic_core: SyntheticCore,
) -> None:
    core = synthetic_core
    first, second = await issue(core), await issue(core)
    one = core.owner.require_history_receipt(first, binding=core.binding, scope=SCOPE)
    two = core.owner.require_history_receipt(second, binding=core.binding, scope=SCOPE)
    assert one.binding_reference == two.binding_reference
    assert "fake" not in one.binding_reference
    assert one.binding_reference not in {"12345", "True", "False"}
    with pytest.raises(ValueError):
        core.owner.require_history_receipt(first, binding=core.binding,
                                           scope=ProtectionScope("12345", "LIVE"))
    assert core.owner.require_history_receipt(first, binding=core.binding, scope=SCOPE)
    core.owner._identity = replace(core.owner._identity, transport_injected=True)
    with pytest.raises(ValueError, match="TRANSPORT"):
        core.owner.require_history_receipt(first, binding=core.binding, scope=SCOPE)


@pytest.mark.parametrize("age", [5, 30])
async def test_original_get_start_and_session_ttl_bound_pending_receipt(
    synthetic_core: SyntheticCore, age: int,
) -> None:
    core = synthetic_core
    if age == 5:
        core.owner._max_age = timedelta(seconds=5)
    core.block[0] = True
    pending = asyncio.create_task(issue(core))
    await core.started.wait()
    core.wall[0] += timedelta(seconds=age, milliseconds=1)
    core.mono[0] += age + .001
    core.server[0] = int(core.wall[0].timestamp() * 1000)
    core.release.set()
    with pytest.raises(ValueError):
        await pending
    assert len(core.owner._history_receipts) == 0


async def test_clock_rollback_after_prior_lookup_revokes_even_above_start(
    synthetic_core: SyntheticCore,
) -> None:
    core = synthetic_core
    receipt = await issue(core)
    core.wall[0] += timedelta(seconds=10)
    core.mono[0] += 10
    assert core.owner.require_history_receipt(receipt, binding=core.binding, scope=SCOPE)
    core.mono[0] -= 1
    with pytest.raises(ValueError):
        core.owner.require_history_receipt(receipt, binding=core.binding, scope=SCOPE)
    core.mono[0] += 1
    with pytest.raises(ValueError):
        core.owner.require_history_receipt(receipt, binding=core.binding, scope=SCOPE)


async def test_successful_reauth_changes_binding_reference_and_revokes_receipts(
    synthetic_core: SyntheticCore,
) -> None:
    core = synthetic_core
    receipt = await issue(core)
    before = core.owner.require_history_receipt(receipt, binding=core.binding, scope=SCOPE)
    core.binding = await core.owner.authenticate(expected_uid="12345", expected_parent_uid="0",
                                                  expected_is_master=False)
    with pytest.raises(ValueError):
        core.owner.require_history_receipt(receipt, binding=core.binding, scope=SCOPE)
    replacement = await issue(core)
    after = core.owner.require_history_receipt(replacement, binding=core.binding, scope=SCOPE)
    assert before.binding_reference != after.binding_reference


async def test_cancelled_reauth_revokes_inflight_history(synthetic_core: SyntheticCore) -> None:
    core = synthetic_core
    core.block[0] = True
    pending = asyncio.create_task(issue(core))
    await core.started.wait()
    core.started.clear()
    auth = asyncio.create_task(core.owner.authenticate(expected_uid="12345",
        expected_parent_uid="0", expected_is_master=False))
    await core.started.wait()
    auth.cancel()
    with pytest.raises(asyncio.CancelledError):
        await auth
    core.release.set()
    with pytest.raises(ValueError):
        await pending
    assert len(core.owner._history_receipts) == 0


async def test_invalid_binding_and_scope_reject_before_history_get(
    synthetic_core: SyntheticCore,
) -> None:
    core = synthetic_core
    for binding, scope in (
        (object.__new__(AuthenticatedAccountBinding), SCOPE),
        (core.binding, ProtectionScope("12345", "LIVE")),
        (core.binding, ProtectionScope("99999", "DEMO")),
    ):
        with pytest.raises(ValueError):
            await core.owner.read_history_receipt(binding, scope=scope, symbol="BTCUSDT",
                                                  start_ms=MS - 1000, end_ms=MS)
    assert core.reads == []
    assert len(core.owner._history_receipts) == 0
