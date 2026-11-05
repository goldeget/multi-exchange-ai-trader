"""Original authenticated page issuance into the real runtime; synthetic HTTP only."""
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import test_risk
import test_runtime
from test_bybit_native import FakeFactory, FakeStrategy
from test_risk import qualified_inputs
from test_runtime import selection

from multi_exchange_trader.execution.account_binding import (
    BybitDemoAccountSession,
    _HistoryReceiptSlot,
)
from multi_exchange_trader.execution.bybit_native import NativeBindings, NativeBybitBridge
from multi_exchange_trader.execution.history import (
    ExecutionRecord,
    HistorySnapshot,
    HistoryWindow,
    OrderHistoryRecord,
)
from multi_exchange_trader.execution.history_receipts import (
    AuthenticatedHistoryReceipt,
    HistoryReceiptView,
)
from multi_exchange_trader.execution.history_store import HistoryStore
from multi_exchange_trader.execution.journal import CommandJournal, CommandKind, ExecutionScope
from multi_exchange_trader.execution.modes import AutomaticMode
from multi_exchange_trader.execution.ownership import AccountWorkerLock
from multi_exchange_trader.execution.protection import ProtectionPolicy, ProtectionScope
from multi_exchange_trader.execution.risk import RiskScope, assess_entry
from multi_exchange_trader.execution.runtime import NativeExecutionRuntime
from multi_exchange_trader.execution.state_store import RuntimeStateStore
from multi_exchange_trader.execution.venue_readback import BybitVenueReadback

SCOPE = ProtectionScope("12345", "DEMO")
OBSERVED = datetime.now(UTC)
STAMP = 0


class _WallClock:
    """Actual collector UTC with a test-only finite expiry offset."""

    def __init__(self) -> None:
        self.offset = timedelta(0)

    def __getitem__(self, index: int) -> datetime:
        assert index == 0
        return datetime.now(UTC) + self.offset

    def __setitem__(self, index: int, value: datetime) -> None:
        assert index == 0
        self.offset = value - datetime.now(UTC)


@pytest.fixture
async def attached_core(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    clock = _WallClock()
    rows: list[dict[str, object]] = []
    requests: list[httpx.Request] = []

    async def send(_client: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET" and request.url.host == "api-demo.bybit.com"
        path = request.url.path
        if path == "/v5/market/time":
            result: dict[str, object] = {}
        elif path == "/v5/user/query-api":
            result = {"apiKey": "ATTACHED-TEST-KEY", "secret": "", "userID": 12345,
                "parentUid": "56789", "isMaster": False, "readOnly": 0,
                "permissions": {"ContractTrade": ["Order", "Position"], "Wallet": [],
                    "Derivatives": ["DerivativesTrade"], "Spot": []}}
        elif path == "/v5/position/list":
            result = {"category": "linear", "nextPageCursor": "", "list": [{"symbol": "BTCUSDT",
                "positionIdx": 0, "side": "Buy", "size": "1", "leverage": "1.8",
                "stopLoss": "90", "takeProfit": "120", "liqPrice": "80"}]}
        else:
            assert path == "/v5/order/realtime"
            second = "cursor" in request.url.params
            result = {"category": "linear", "nextPageCursor": "" if second else "next-child",
                "list": rows[1:] if second else rows[:1]}
        return httpx.Response(200, content=json.dumps({"retCode": 0, "result": result,
            "time": int(clock[0].timestamp() * 1000)}).encode(), request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    owner = BybitDemoAccountSession(api_key="ATTACHED-TEST-KEY", api_secret="ATTACHED-TEST-SECRET",
        clock=lambda: clock[0])
    binding = await owner.authenticate(expected_uid="12345", expected_parent_uid="56789", expected_is_master=False)
    lock = AccountWorkerLock(tmp_path, account_id="12345", environment="DEMO")
    lock.acquire()
    journal = CommandJournal(tmp_path / "execution.sqlite3", ExecutionScope("12345", "DEMO"))
    store = RuntimeStateStore(tmp_path / "runtime-state.sqlite3", ExecutionScope("12345", "DEMO"))
    history = HistoryStore(tmp_path / "history.sqlite3", SCOPE)
    strategy = FakeStrategy()
    bridge = NativeBybitBridge(scope=SCOPE, native_account_id="BYBIT-UNIFIED", factory=FakeFactory(),
        strategy=strategy, bindings=NativeBindings(str, str, str, str, str))
    runtime = NativeExecutionRuntime(journal=journal, store=store, worker_lock=lock, lock_directory=tmp_path,
        bridge=bridge, entry_risk=lambda *_args: assess_entry(**data), clock=lambda: clock[0],
        max_evidence_age=timedelta(seconds=10), receipt_session=owner, history_store=history)
    observed = datetime.now(UTC)
    observed = observed.replace(microsecond=(observed.microsecond // 1000) * 1000)
    monkeypatch.setitem(globals(), "OBSERVED", observed)
    monkeypatch.setitem(globals(), "STAMP", int((observed - timedelta(seconds=2)).timestamp() * 1000))
    # The original client records datetime.now(UTC), rather than the Session clock.
    # Shift only synthetic risk/selection inputs; keep genuine capture clocks and guards.
    monkeypatch.setattr(test_risk, "NOW", observed)
    monkeypatch.setattr(test_runtime, "NOW", observed)
    data = qualified_inputs()
    risk_scope = RiskScope("12345", "DEMO", "BTCUSDT", "strategy-1")
    for key in ("policy", "market", "instrument", "account", "tiers", "reservations", "liquidation", "leverage_readback"):
        data[key] = replace(data[key], scope=risk_scope)
    request = selection()
    request = replace(request, scope=replace(request.scope, account_id="12345"),
        policy=replace(request.policy, account_id="12345"),
        candidates=tuple(replace(c, account_id="12345") for c in request.candidates))
    record = runtime.queue_entry("entry", "position-1", plan_id="plan-1", plan=assess_entry(**data),
        protection_policy=ProtectionPolicy("policy-1", 0, Decimal(90), Decimal(120), "MarkPrice"),
        mode=AutomaticMode.FULL_AUTO, selection_request=request)
    journal.claim("entry")
    execution = ExecutionRecord("fill-1", "parent-venue", record.client_order_id, "BTCUSDT", "Buy", "Trade",
        STAMP, Decimal(1), Decimal(100), Decimal(100), Decimal("0.01"), "USDT", Decimal("0.0001"), Decimal(0))
    order = OrderHistoryRecord("parent-venue", record.client_order_id, "BTCUSDT", "Buy", 0, "Filled",
        Decimal(1), Decimal(1), Decimal(0), False, STAMP, STAMP)
    snap = HistorySnapshot(SCOPE, "BTCUSDT", "fill-source", HistoryWindow(STAMP - 1000, STAMP + 1000),
        OBSERVED, OBSERVED, (execution,), (order,), (), False)
    receipt = object.__new__(AuthenticatedHistoryReceipt)
    owner._history_receipts[receipt] = _HistoryReceiptSlot(binding, SCOPE, owner._history_generation,
        OBSERVED, owner._mono(), "synthetic-fill-seed", HistoryReceiptView(snap, "synthetic-fill-seed"))
    key = history.ingest(snap).observation_key
    assert runtime.recover_history_execution(receipt=receipt, binding=binding, observation_key=key,
        execution_id="fill-1").status == "CREDITED"
    for role, trigger, direction in (("StopLoss", "90", 2), ("TakeProfit", "120", 1)):
        rows.append({"orderId": "child-" + role, "orderLinkId": "", "parentOrderLinkId": record.client_order_id,
            "symbol": "BTCUSDT", "positionIdx": 0, "side": "Sell", "qty": "1", "leavesQty": "1",
            "cumExecQty": "0", "reduceOnly": True, "closeOnTrigger": True, "orderStatus": "Untriggered",
            "stopOrderType": role, "createType": "CreateBy" + role, "orderType": "Market", "tpslMode": "Full",
            "triggerPrice": trigger, "triggerBy": "MarkPrice", "triggerDirection": direction,
            "createdTime": str(STAMP), "updatedTime": str(STAMP)})
    adapter = BybitVenueReadback(client=owner.read_client(binding, scope=SCOPE), scope=SCOPE,
        clock=lambda: clock[0], max_evidence_age=timedelta(seconds=10))
    core = SimpleNamespace(runtime=runtime, owner=owner, binding=binding, adapter=adapter,
        rows=rows, clock=clock, requests=requests, journal=journal, strategy=strategy, store=store, history=history, lock=lock, bridge=bridge, directory=tmp_path, monkeypatch=monkeypatch)
    try:
        yield core
    finally:
        core.runtime.shutdown()
        history.close()
        store.close()
        journal.close()
        lock.release()
        await owner.aclose()


async def test_authenticated_attached_children_allow_protection_and_owned_exit(attached_core: Any) -> None:
    core = attached_core
    before_orders = core.runtime.projection("position-1").orders
    assessment = await core.runtime.poll_readback(core.adapter, "position-1")
    assert assessment.status == "OPEN_PROTECTED"
    assert assessment.unprotected_quantity == 0
    assert assessment.entries_blocked is True  # Observation never grants financial entry authority.
    assert core.runtime.projection("position-1").orders == before_orders
    exit_record = core.runtime.queue_exit("cleanup", "position-1", Decimal(1), Decimal(100))
    assert exit_record.kind.value == "EXIT"
    assert not core.strategy.commands  # Nothing was sent or inserted into the native cache.
    assert len([r for r in core.requests if r.url.path == "/v5/order/realtime"]) == 2


@pytest.mark.parametrize("change", [
    {"parentOrderLinkId": "foreign-parent"}, {"symbol": "ETHUSDT"}, {"positionIdx": 1},
    {"side": "Buy"}, {"qty": "2"}, {"leavesQty": "0.9"}, {"cumExecQty": "0.1"},
    {"reduceOnly": False}, {"closeOnTrigger": False}, {"triggerPrice": "91"},
    {"triggerBy": "LastPrice"}, {"triggerDirection": 1}, {"orderStatus": "New"},
    {"tpslMode": "Partial"}, {"updatedTime": str(STAMP + 1)}, {"createType": "CreateByUser"},
])
async def test_ambiguous_changed_or_foreign_child_never_allows_cleanup(attached_core: Any, change: Any) -> None:
    core = attached_core
    core.rows[0].update(change)
    assessment = await core.runtime.poll_readback(core.adapter, "position-1")
    assert assessment.status == "ATTENTION"
    with pytest.raises(ValueError):
        core.runtime.queue_exit("cleanup", "position-1", Decimal(1), Decimal(100))
    assert core.journal.get("cleanup") is None and not core.strategy.commands


@pytest.mark.parametrize("shape", [
    "missing", "missing_stop", "empty", "position_missing", "position_zero", "position_conflict",
    "duplicate_kind", "duplicate_venue", "parent_field",
])
async def test_incomplete_or_ambiguous_children_preserve_only_proven_cleanup(attached_core: Any, shape: str) -> None:
    core = attached_core
    if shape == "missing":
        core.rows.pop()
    elif shape == "missing_stop":
        core.rows.pop(0)
    elif shape == "empty":
        core.rows.clear()
    elif shape.startswith("position_"):
        original_send = httpx.AsyncClient.send

        async def position_fields(client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
            response = await original_send(client, request, **kwargs)
            if request.url.path == "/v5/position/list":
                envelope = json.loads(response.content)
                position = envelope["result"]["list"][0]
                if shape == "position_missing":
                    position.pop("stopLoss")
                    position.pop("takeProfit")
                elif shape == "position_zero":
                    position.update({"stopLoss": "0", "takeProfit": "0"})
                else:
                    position.update({"stopLoss": "91", "takeProfit": "121"})
                return httpx.Response(200, content=json.dumps(envelope).encode(), request=request)
            return response

        core.monkeypatch.setattr(httpx.AsyncClient, "send", position_fields)
    elif shape == "duplicate_kind":
        core.rows[1].update({"stopOrderType": "StopLoss", "createType": "CreateByStopLoss", "triggerPrice": "90", "triggerDirection": 2})
    elif shape == "duplicate_venue":
        core.rows[1]["orderId"] = core.rows[0]["orderId"]
    else:
        core.rows[0]["parentOrderLinkId"] = ""
    assessment = await core.runtime.poll_readback(core.adapter, "position-1")
    assert assessment.status == "ATTENTION" and assessment.entries_blocked is True
    if shape in ("missing", "missing_stop", "empty", "position_missing", "position_zero", "position_conflict"):
        receipt, target = core.runtime._attached_readbacks_v1["position-1"]
        view = core.owner.require_attached_protection_v1(receipt, target=target)
        assert view.protection_coverage == "INCOMPLETE"
        assert {child.venue_order_id for child in view.children} == {row["orderId"] for row in core.rows}
        assert core.runtime.queue_exit("cleanup", "position-1", Decimal(1), Decimal(100)).kind.value == "EXIT"
    else:
        with pytest.raises(ValueError):
            core.runtime.queue_exit("cleanup", "position-1", Decimal(1), Decimal(100))
        assert core.journal.get("cleanup") is None
    assert not core.strategy.commands


async def test_stale_authenticated_receipt_cannot_authorize_later_cleanup(attached_core: Any) -> None:
    core = attached_core
    assert (await core.runtime.poll_readback(core.adapter, "position-1")).status == "OPEN_PROTECTED"
    core.clock[0] += timedelta(seconds=11)
    with pytest.raises(ValueError):
        core.runtime.queue_exit("cleanup", "position-1", Decimal(1), Decimal(100))


async def test_source_replacement_revokes_original_cleanup_receipt(attached_core: Any) -> None:
    from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget
    core = attached_core
    assert (await core.runtime.poll_readback(core.adapter, "position-1")).status == "OPEN_PROTECTED"
    await core.owner._capture_linear_orders_v1(core.binding, scope=SCOPE,
        budget=MonitoringReadBudget(), owner_guard=lambda: None)
    with pytest.raises(ValueError):
        core.runtime.queue_exit("cleanup", "position-1", Decimal(1), Decimal(100))


async def test_foreign_uid_or_forged_handle_cannot_reuse_issued_view(attached_core: Any) -> None:
    core = attached_core
    assert (await core.runtime.poll_readback(core.adapter, "position-1")).status == "OPEN_PROTECTED"
    receipt, target = core.runtime._attached_readbacks_v1["position-1"]
    with pytest.raises(ValueError):
        core.owner.require_attached_protection_v1(receipt, target=replace(target, scope=ProtectionScope("67890", "DEMO")))
    with pytest.raises(ValueError):
        core.owner.require_attached_protection_v1(object.__new__(type(receipt)), target=target)


async def test_restart_restores_ownership_but_not_authenticated_child_receipt(attached_core: Any) -> None:
    core = attached_core
    assert (await core.runtime.poll_readback(core.adapter, "position-1")).status == "OPEN_PROTECTED"
    core.runtime.shutdown()
    core.runtime = NativeExecutionRuntime(journal=core.journal, store=core.store, worker_lock=core.lock,
        lock_directory=core.directory, bridge=core.bridge, entry_risk=lambda *_args: assess_entry(**qualified_inputs()),
        clock=lambda: core.clock[0], max_evidence_age=timedelta(seconds=10), receipt_session=core.owner,
        history_store=core.history)
    assert core.runtime.projection("position-1").owned_quantity == Decimal(1)
    with pytest.raises(ValueError):
        core.runtime.queue_exit("cleanup", "position-1", Decimal(1), Decimal(100))
    assert (await core.runtime.poll_readback(core.adapter, "position-1")).status == "OPEN_PROTECTED"


async def test_authenticated_incomplete_protection_persists_entry_admission_gate(attached_core: Any) -> None:
    core = attached_core
    assert (await core.runtime.poll_readback(core.adapter, "position-1")).status == "OPEN_PROTECTED"
    plan_data = qualified_inputs()
    risk_scope = RiskScope("12345", "DEMO", "BTCUSDT", "strategy-1")
    for key in ("policy", "market", "instrument", "account", "tiers", "reservations",
                "liquidation", "leverage_readback"):
        plan_data[key] = replace(plan_data[key], scope=risk_scope)
    plan = assess_entry(**plan_data)
    selection_request = selection()
    selection_request = replace(selection_request,
        scope=replace(selection_request.scope, account_id="12345"),
        policy=replace(selection_request.policy, account_id="12345"),
        candidates=tuple(replace(candidate, account_id="12345")
            for candidate in selection_request.candidates))
    protection_policy = ProtectionPolicy("policy-1", 0, Decimal(90), Decimal(120), "MarkPrice")

    def assert_entry_blocked(command_key: str, position_id: str) -> None:
        with pytest.raises(ValueError, match="PROTECTION_RECOVERY_REQUIRED"):
            core.runtime.queue_entry(command_key, position_id, plan_id="plan-1", plan=plan,
                protection_policy=protection_policy, mode=AutomaticMode.FULL_AUTO,
                selection_request=selection_request)

    owned_orders = core.runtime.projection("position-1").orders
    original_rows = list(core.rows)
    core.rows.pop()

    incomplete = await core.runtime.poll_readback(core.adapter, "position-1")
    assert incomplete.status == "ATTENTION" and incomplete.entries_blocked is True
    assert core.runtime.projection("position-1").orders == owned_orders
    projection = core.journal.get_projection("protection-recovery-admission-v1")
    assert projection is not None
    state = json.loads(projection.payload_json)
    assert set(state["active"]) == {"position-1"}
    assert state["active"]["position-1"]["reason_code"] == (
        "AUTHENTICATED_PROTECTION_COVERAGE_INCOMPLETE")

    assert_entry_blocked("blocked-entry", "position-2")

    core.runtime.shutdown()
    await core.owner.aclose()
    new_owner = BybitDemoAccountSession(api_key="ATTACHED-TEST-KEY", api_secret="ATTACHED-TEST-SECRET",
        clock=lambda: core.clock[0])
    core.owner = new_owner
    new_binding = await new_owner.authenticate(expected_uid="12345", expected_parent_uid="56789",
        expected_is_master=False)
    core.binding = new_binding
    core.adapter = BybitVenueReadback(client=new_owner.read_client(new_binding, scope=SCOPE), scope=SCOPE,
        clock=lambda: core.clock[0], max_evidence_age=timedelta(seconds=10))
    core.runtime = NativeExecutionRuntime(journal=core.journal, store=core.store, worker_lock=core.lock,
        lock_directory=core.directory, bridge=core.bridge,
        entry_risk=lambda *_args: assess_entry(**qualified_inputs()), clock=lambda: core.clock[0],
        max_evidence_age=timedelta(seconds=10), receipt_session=new_owner, history_store=core.history)
    assert_entry_blocked("blocked-after-restart", "position-3")

    core.rows[:] = original_rows
    core.clock[0] += timedelta(seconds=1)
    complete = await core.runtime.poll_readback(core.adapter, "position-1")
    assert complete.status == "OPEN_PROTECTED" and complete.unprotected_quantity == 0
    assert core.runtime._recovery_required_v1() is False
    cleared = core.journal.get_projection("protection-recovery-admission-v1")
    assert cleared is not None and json.loads(cleared.payload_json)["active"] == {}


async def test_authenticated_incomplete_protection_replay_is_idempotent(attached_core: Any) -> None:
    core = attached_core
    assert (await core.runtime.poll_readback(core.adapter, "position-1")).status == "OPEN_PROTECTED"
    core.rows.pop()
    incomplete = await core.runtime.poll_readback(core.adapter, "position-1")
    assert incomplete.status == "ATTENTION" and incomplete.entries_blocked is True
    projection = core.journal.get_projection("protection-recovery-admission-v1")
    assert projection is not None
    revision = projection.revision

    receipt, target = core.runtime._attached_readbacks_v1["position-1"]
    snapshot = core.owner.require_attached_protection_v1(receipt, target=target).snapshot
    operation = core.runtime._attached_read_operations_v1["position-1"]
    replay = core.runtime.consume_readback("position-1", snapshot, attached_receipt=receipt,
        attached_target=target, _attached_operation=operation)
    assert replay.status == "ATTENTION" and replay.entries_blocked is True
    replayed = core.journal.get_projection("protection-recovery-admission-v1")
    assert replayed is not None and replayed.revision == revision


async def test_recovery_gate_blocks_pending_entry_dispatch(attached_core: Any) -> None:
    core = attached_core
    assert (await core.runtime.poll_readback(core.adapter, "position-1")).status == "OPEN_PROTECTED"
    old_receipt, old_target = core.runtime._attached_readbacks_v1["position-1"]
    old_snapshot = core.owner.require_attached_protection_v1(old_receipt, target=old_target).snapshot
    old_operation = core.runtime._attached_read_operations_v1["position-1"]
    source_entry = core.journal.get("entry")
    assert source_entry is not None
    pending_payload = json.loads(source_entry.payload_json)
    pending_payload["position_id"] = "pending-position"
    pending = core.journal.register("pending-entry", CommandKind.ENTRY, pending_payload)
    assert pending.state.value == "PENDING"

    core.rows.pop()
    incomplete = await core.runtime.poll_readback(core.adapter, "position-1")
    assert incomplete.status == "ATTENTION" and incomplete.entries_blocked is True
    with pytest.raises(ValueError, match="PROTECTION_RECOVERY_REQUIRED"):
        core.runtime.dispatch("pending-entry")
    core.runtime.consume_readback("position-1", old_snapshot, attached_receipt=old_receipt,
        attached_target=old_target, _attached_operation=old_operation)
    retained = core.journal.get_projection("protection-recovery-admission-v1")
    assert retained is not None and "position-1" in json.loads(retained.payload_json)["active"]


@pytest.mark.parametrize("capacity", ["active-count", "projection-size"])
async def test_recovery_capacity_overflow_latch_survives_restart(attached_core: Any,
        monkeypatch: pytest.MonkeyPatch, capacity: str) -> None:
    core = attached_core
    assert (await core.runtime.poll_readback(core.adapter, "position-1")).status == "OPEN_PROTECTED"
    receipt, target = core.runtime._attached_readbacks_v1["position-1"]
    snapshot = core.owner.require_attached_protection_v1(receipt, target=target).snapshot
    original_rows = list(core.rows)
    observed_at = snapshot.observed_at.isoformat()
    server_observed_at = snapshot.server_observed_at.isoformat()

    def active_item(index: int, policy_revision: str = "p") -> dict[str, object]:
        return {
            "entry_client_order_id": "ec_" + f"{index:032x}",
            "policy_revision": policy_revision,
            "policy_generation": 0,
            "target_sha256": "0" * 64,
            "observed_at": observed_at,
            "server_observed_at": server_observed_at,
            "reason_code": "AUTHENTICATED_PROTECTION_COVERAGE_INCOMPLETE",
        }

    active: dict[str, dict[str, object]] = {}
    if capacity == "active-count":
        active = {f"capacity-position-{index}": active_item(index) for index in range(256)}
    else:
        target_item = {
            "entry_client_order_id": target.parent_client_order_id,
            "policy_revision": target.policy_revision,
            "policy_generation": target.policy_generation,
            "target_sha256": target.projection_sha256,
            "observed_at": observed_at,
            "server_observed_at": server_observed_at,
            "reason_code": "AUTHENTICATED_PROTECTION_COVERAGE_INCOMPLETE",
        }
        def projection_size(records: dict[str, dict[str, object]]) -> int:
            payload = {"schema": "PROTECTION_RECOVERY_ADMISSION_V1", "active": records}
            return len(json.dumps(payload, ensure_ascii=True, sort_keys=True,
                separators=(",", ":")).encode())

        target_projection = dict(active)
        target_projection[target.position_id] = target_item
        target_delta = projection_size(target_projection) - projection_size(active)
        for index in range(1, 256):
            active = {f"capacity-{item}": active_item(item) for item in range(index)}
            needed_padding = 65536 - projection_size(active) - target_delta + 1
            if 0 <= needed_padding <= 255 * len(active):
                remaining = needed_padding
                for item in active.values():
                    extra = min(remaining, 255)
                    item["policy_revision"] = "p" * (1 + extra)
                    remaining -= extra
                    if remaining == 0:
                        break
                break
        target_projection = dict(active)
        target_projection[target.position_id] = target_item
        assert (len(active) < 256 and projection_size(active) <= 65536
                < projection_size(target_projection))

    monkeypatch.setattr(core.runtime, "_protection_recovery_projection_v1",
        lambda: (0, active))
    core.rows.pop()
    incomplete = await core.runtime.poll_readback(core.adapter, "position-1")
    assert incomplete.status == "ATTENTION"
    overflow = core.journal.get_projection("protection-recovery-overflow-v1")
    assert overflow is not None
    overflow_payload = json.loads(overflow.payload_json)
    expected_reason = ("PROTECTION_RECOVERY_ACTIVE_COUNT_LIMIT" if capacity == "active-count"
                       else "PROTECTION_RECOVERY_PROJECTION_SIZE_LIMIT")
    assert overflow_payload["reason_code"] == expected_reason
    assert core.journal.get_projection("protection-recovery-admission-v1") is None

    core.runtime.shutdown()
    core.runtime = NativeExecutionRuntime(journal=core.journal, store=core.store,
        worker_lock=core.lock, lock_directory=core.directory, bridge=core.bridge,
        entry_risk=lambda *_args: assess_entry(**qualified_inputs()), clock=lambda: core.clock[0],
        max_evidence_age=timedelta(seconds=10), receipt_session=core.owner,
        history_store=core.history)
    assert core.runtime._recovery_required_v1() is True
    with pytest.raises(ValueError, match="PROTECTION_RECOVERY_REQUIRED"):
        core.runtime._require_protection_recovery_clear_v1()

    core.rows[:] = original_rows
    core.clock[0] += timedelta(seconds=1)
    complete = await core.runtime.poll_readback(core.adapter, "position-1")
    assert complete.status == "ATTENTION" and complete.entries_blocked is True
    with pytest.raises(ValueError, match="PROTECTION_RECOVERY_REQUIRED"):
        core.runtime._require_protection_recovery_clear_v1()


@pytest.mark.parametrize("mutate", ["children", "snapshot"])
async def test_final_budget_callback_cannot_mutate_a_retained_view(attached_core: Any, monkeypatch: pytest.MonkeyPatch, mutate: str) -> None:
    core = attached_core
    assert (await core.runtime.poll_readback(core.adapter, "position-1")).status == "OPEN_PROTECTED"
    receipt, target = core.runtime._attached_readbacks_v1["position-1"]
    issuer = core.owner._attached_protection_current_v1
    original = issuer.budget.check
    called = False

    def change_after_check() -> None:
        nonlocal called
        original()
        if not called:
            called = True
            if mutate == "children":
                object.__setattr__(issuer.view, "children", ())
            else:
                object.__setattr__(issuer.view.snapshot, "orders", ())

    monkeypatch.setattr(issuer.budget, "check", change_after_check)
    with pytest.raises(ValueError):
        core.owner.require_attached_protection_v1(receipt, target=target)
    assert called
    with pytest.raises(ValueError):
        core.runtime.queue_exit("cleanup", "position-1", Decimal(1), Decimal(100))


async def test_unknown_row_symbol_cannot_be_ignored_as_foreign_scope(attached_core: Any) -> None:
    core = attached_core
    core.rows.append({"orderId": "unknown-scope", "symbol": None})
    assert (await core.runtime.poll_readback(core.adapter, "position-1")).status == "ATTENTION"
    assert core.journal.get("cleanup") is None


@pytest.mark.parametrize("consumer", ["consume", "exit"])
async def test_final_session_callback_cannot_hide_runtime_clock_expiry(attached_core: Any, monkeypatch: pytest.MonkeyPatch, consumer: str) -> None:
    core = attached_core
    assert (await core.runtime.poll_readback(core.adapter, "position-1")).status == "OPEN_PROTECTED"
    receipt, target = core.runtime._attached_readbacks_v1["position-1"]
    issuer = core.owner._attached_protection_current_v1
    original_check = issuer.budget.check
    original_binding = core.owner.require_observation_binding
    armed = False
    moved = False

    def arm_after_final_budget() -> None:
        nonlocal armed
        original_check()
        armed = True

    def session_callback(binding: Any, *, scope: ProtectionScope) -> Any:
        nonlocal moved
        result = original_binding(binding, scope=scope)
        if armed and not moved:
            moved = True
            # Session keeps its own fresh clock; only the actual consumer clock expires.
            core.runtime._clock = lambda: OBSERVED + timedelta(seconds=11)
        return result

    monkeypatch.setattr(issuer.budget, "check", arm_after_final_budget)
    monkeypatch.setattr(core.owner, "require_observation_binding", session_callback)
    if consumer == "consume":
        result = core.runtime.consume_readback("position-1", issuer.view.snapshot,
            attached_receipt=receipt, attached_target=target,
            _attached_operation=core.runtime._attached_read_operations_v1["position-1"])
        assert result.status == "ATTENTION" and result.entries_blocked is True
    else:
        with pytest.raises(ValueError):
            core.runtime.queue_exit("cleanup", "position-1", Decimal(1), Decimal(100))
    assert moved and core.journal.get("cleanup") is None and not core.strategy.commands


async def test_final_runtime_clock_cannot_drop_an_original_foreign_page(attached_core: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    core = attached_core
    core.rows.insert(0, {"orderId": "known-foreign", "symbol": "ETHUSDT"})
    assert (await core.runtime.poll_readback(core.adapter, "position-1")).status == "OPEN_PROTECTED"
    issuer = core.owner._attached_protection_current_v1
    assert len(issuer.order_sources) == 2
    original_require = core.owner.require_attached_protection_v1
    dropped = False

    def clock_drops_original_page() -> Any:
        nonlocal dropped
        dropped = True
        issuer.order_sources = issuer.order_sources[1:]
        issuer.order_slot.pages = issuer.order_slot.pages[1:]
        return core.clock[0]

    def arm_after_opaque_require(receipt: Any, *, target: Any) -> Any:
        result = original_require(receipt, target=target)
        core.runtime._clock = clock_drops_original_page
        return result

    monkeypatch.setattr(core.owner, "require_attached_protection_v1", arm_after_opaque_require)
    with pytest.raises(ValueError):
        core.runtime.queue_exit("cleanup", "position-1", Decimal(1), Decimal(100))
    assert dropped and core.journal.get("cleanup") is None and not core.strategy.commands


async def test_child_creation_cannot_postdate_its_original_order_page(attached_core: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    core = attached_core
    core.rows[1]["createdTime"] = str(STAMP + 1000)
    core.rows[1]["updatedTime"] = str(STAMP + 1000)
    original_send = httpx.AsyncClient.send

    async def earlier_child_page(client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        response = await original_send(client, request, **kwargs)
        if request.url.path == "/v5/order/realtime" and "cursor" in request.url.params:
            envelope = json.loads(response.content)
            # Fresh and after the actual fill, but before this child's claimed creation.
            envelope["time"] = STAMP + 500
            return httpx.Response(200, content=json.dumps(envelope).encode(), request=request)
        return response

    monkeypatch.setattr(httpx.AsyncClient, "send", earlier_child_page)
    assert (await core.runtime.poll_readback(core.adapter, "position-1")).status == "ATTENTION"
    with pytest.raises(ValueError):
        core.runtime.queue_exit("cleanup", "position-1", Decimal(1), Decimal(100))
    assert core.journal.get("cleanup") is None and not core.strategy.commands
