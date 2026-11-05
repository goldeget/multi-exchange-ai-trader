"""Synthetic venue inputs and the two network I/O seams for Linux qualification.

No factory, decoder, clock, issuer, native lifecycle or readiness replacement.
The venue ledger is independent of the native cache after baseline construction.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
from fixtures.runtime_policy_v1 import load_v1
from test_bybit_native_linux import module
from test_native_monitor_http import fee_row, instrument_row, wallet_row
from test_risk import inputs
from test_runtime import selection

from multi_exchange_trader.execution.account_binding import BybitDemoAccountSession
from multi_exchange_trader.execution.journal import CommandState
from multi_exchange_trader.execution.modes import AutomaticMode
from multi_exchange_trader.execution.monitoring_models import MonitoringError
from multi_exchange_trader.execution.observation_instrument_v1 import (
    ACTUAL_ACCOUNT_FEE_V1,
    SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
    convert_observation_instrument_v1,
)
from multi_exchange_trader.execution.monitoring_resume import MonitoringResume
from multi_exchange_trader.execution.native_restart_store import decode_document
from multi_exchange_trader.execution.protection import ProtectionPolicy, ProtectionScope
from multi_exchange_trader.execution.risk import assess_entry
from multi_exchange_trader.execution.runtime import RuntimeBlocked
from multi_exchange_trader.execution.runtime_policy import RuntimePolicyBook
from multi_exchange_trader.execution.worker import (
    ExecutionWorker,
    RegisteredPlan,
    TrustedPlanRegistry,
)

SCOPE = ProtectionScope("12345", "DEMO")
KEY, SECRET = "synthetic-key", "synthetic-secret"


def milliseconds():
    return time.time_ns() // 1_000_000


async def eventually(predicate, *, timeout=12):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.001)


class Gate:
    """Hold a real network operation; never hold a fabricated lifecycle method."""

    def __init__(self):
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def wait(self):
        self.entered.set()
        await self.release.wait()


class VenueLedger:
    def __init__(self):
        self.order = None
        self.executions = []
        self.position = {"symbol": "BTCUSDT", "positionIdx": 0, "side": "", "size": "0"}

    def own(self, order):
        created = order.ts_init // 1_000_000
        self.order = {
            "orderId": str(order.venue_order_id), "orderLinkId": str(order.client_order_id),
            "symbol": "BTCUSDT", "side": "Buy", "positionIdx": 0, "reduceOnly": False,
            "orderStatus": "New", "qty": str(order.quantity.as_decimal()),
            "cumExecQty": "0", "leavesQty": str(order.quantity.as_decimal()),
            "createdTime": str(created), "updatedTime": str(created),
            "orderType": "Market" if order.order_type.name == "MARKET" else "Limit",
            "timeInForce": "IOC" if order.order_type.name == "MARKET" else "GTC",
            "price": "0" if order.order_type.name == "MARKET" else str(order.price.as_decimal()),
            "stopOrderType": "", "triggerPrice": "", "triggerDirection": 0,
            "closeOnTrigger": False, "takeProfit": "", "stopLoss": "", "createType": "CreateByUser",
        }

    async def execution(self, execution_id, quantity="1", *, apply=True):
        # All source timestamps are real integer milliseconds, never a patched native clock.
        await eventually(lambda: milliseconds() * 1_000_000 > self.initialized_ns)
        row = {
            "category": "linear", "symbol": "BTCUSDT", "side": "Buy",
            "orderLinkId": self.order["orderLinkId"], "orderId": self.order["orderId"],
            "execId": execution_id, "execType": "Trade", "execQty": quantity,
            "execPrice": "100.00", "execFee": str(Decimal(quantity) * Decimal("0.055")),
            "feeCurrency": "USDT", "isMaker": False, "execTime": str(milliseconds()),
            "execValue": str(Decimal(quantity) * 100), "feeRate": "0.00055", "closedSize": "0",
            "seq": len(self.executions) + 1, "orderQty": self.order["qty"],
            "orderType": self.order["orderType"], "orderPrice": self.order["price"],
            "leavesQty": str(Decimal(self.order["leavesQty"]) - Decimal(quantity)),
            "stopOrderType": "", "createType": "CreateByUser", "extraFees": "",
        }
        if apply:
            self.apply(row)
        return row

    def apply(self, row):
        self.executions.append(copy.deepcopy(row))
        total = sum((Decimal(e["execQty"]) for e in self.executions), Decimal(0))
        self.order.update(cumExecQty=str(total), leavesQty=str(Decimal(self.order["qty"]) - total),
            orderStatus="Filled" if total == Decimal(self.order["qty"]) else "PartiallyFilled",
            updatedTime=row["execTime"])
        self.position.update(side="Buy", size=str(total))

    def page(self, path):
        if path == "/v5/position/list":
            rows = [self.position]
        elif path == "/v5/order/realtime":
            rows = [self.order] if self.order and self.order["orderStatus"] in ("New", "PartiallyFilled") else []
        elif path == "/v5/order/history":
            rows = [self.order] if self.order and self.order["orderStatus"] not in ("New", "PartiallyFilled") else []
        elif path == "/v5/execution/list":
            rows = self.executions
        else:
            raise AssertionError("unexpected ledger endpoint")
        return copy.deepcopy({"category": "linear", "list": rows, "nextPageCursor": ""})


class Wire:
    def __init__(self, io, callbacks):
        self.io, self.callbacks = io, callbacks
        self.mode = "active"
        self.sent = []  # Operation names only; never retain generated auth packets.
        self.connection = f"synthetic-{len(io.wires) + 1}"
        self.disconnect_gate = None

    def is_active(self):
        return self.mode == "active"

    def is_reconnecting(self):
        return self.mode == "reconnecting"

    def is_disconnecting(self):
        return self.mode == "disconnecting"

    def is_closed(self):
        return self.mode == "closed"

    def emit(self, topic, rows):
        self.raw({"topic": topic, "data": rows, "conn_id": self.connection})

    def raw(self, packet):
        self.callbacks["handler"](json.dumps(packet, separators=(",", ":")).encode())

    async def send_text(self, raw):
        packet = json.loads(raw)
        op = packet["op"]
        assert op in ("auth", "subscribe", "ping")
        self.sent.append(op)
        if op == "auth":
            key, expires, signature = packet["args"]
            assert key == KEY
            assert hmac.compare_digest(signature, hmac.new(SECRET.encode(),
                f"GET/realtime{expires}".encode(), hashlib.sha256).hexdigest())
        if op == "subscribe":
            assert packet["args"] == ["order.linear", "execution.linear", "position.linear"]
        ack = {"op": "pong" if op == "ping" else op, "req_id": packet["req_id"],
            "conn_id": self.connection}
        if op == "ping":
            ack["args"] = [str(milliseconds())]
        else:
            ack["success"] = True
        if self.io.on_send is not None:
            await self.io.on_send(self, packet, ack)
        if ack:
            self.raw(ack)

    async def send_pong(self, raw):
        assert type(raw) is bytes and len(raw) <= 125

    async def disconnect(self):
        if self.disconnect_gate is not None:
            await self.disconnect_gate.wait()
        self.mode = "closed"


class IO:
    def __init__(self, monkeypatch):
        from multi_exchange_trader.execution import private_monitor_stream

        self.ledger, self.sends, self.wires = VenueLedger(), [], []
        self.on_http, self.on_send = None, None
        self.instrument, self.fee, self.wallet = instrument_row(), fee_row(), wallet_row()
        self.gates = []

        async def send(client, request, **kwargs):
            return await self.send(request, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "send", send)
        monkeypatch.setattr(private_monitor_stream, "_dial", self.dial)

    def gate(self):
        gate = Gate()
        self.gates.append(gate)
        return gate

    async def dial(self, **callbacks):
        config = callbacks["config"]
        assert config.url == "wss://stream-demo.bybit.com/v5/private"
        assert config.reconnect_max_attempts == 0 and config.heartbeat is None
        assert config.reconnect_timeout_ms == 10000 and config.idle_timeout_ms == 30000
        wire = Wire(self, callbacks)
        self.wires.append(wire)
        return wire

    async def send(self, request, **kwargs):
        assert request.method == "GET" and request.url.host == "api-demo.bybit.com"
        assert kwargs.get("stream") is True
        path, params = request.url.path, dict(request.url.params)
        allowed = {
            "/v5/market/time": set(), "/v5/user/query-api": set(),
            "/v5/market/instruments-info": {"category", "symbol", "limit", "cursor"},
            "/v5/account/fee-rate": {"category", "symbol"},
            "/v5/account/wallet-balance": {"accountType", "coin"},
            "/v5/position/list": {"category", "symbol", "limit", "cursor"},
            "/v5/order/realtime": {"category", "symbol", "limit", "cursor", "openOnly"},
            "/v5/order/history": {"category", "symbol", "limit", "cursor", "startTime", "endTime"},
            "/v5/execution/list": {"category", "symbol", "limit", "cursor", "startTime", "endTime"},
        }
        assert path in allowed and set(params) <= allowed[path]
        if "category" in params:
            assert params["category"] == "linear" and params.get("symbol") == "BTCUSDT"
        if path == "/v5/order/realtime":
            assert params["openOnly"] == "0" and "orderFilter" not in params
        signed = path not in ("/v5/market/time", "/v5/market/instruments-info")
        if signed:
            headers = request.headers
            assert headers["X-BAPI-API-KEY"] == KEY
            message = headers["X-BAPI-TIMESTAMP"] + KEY + headers["X-BAPI-RECV-WINDOW"]
            message += request.url.query.decode()
            assert hmac.compare_digest(headers["X-BAPI-SIGN"],
                hmac.new(SECRET.encode(), message.encode(), hashlib.sha256).hexdigest())
        else:
            assert "X-BAPI-SIGN" not in request.headers
        self.sends.append((path, params))
        if path == "/v5/user/query-api":
            result = {"apiKey": KEY, "secret": "", "userID": 12345, "isMaster": False,
                "parentUid": "0", "readOnly": 1, "permissions": {"Wallet": []}}
        elif path == "/v5/market/time":
            result = {}
        elif path == "/v5/market/instruments-info":
            result = {"category": "linear", "list": [copy.deepcopy(self.instrument)], "nextPageCursor": ""}
        elif path == "/v5/account/fee-rate":
            result = {"list": [copy.deepcopy(self.fee)]}
        elif path == "/v5/account/wallet-balance":
            result = {"list": [copy.deepcopy(self.wallet)]}
        else:
            result = self.ledger.page(path)
        response = {"retCode": 0, "result": result}
        status, headers = 200, {}
        if self.on_http is not None:
            override = await self.on_http(path, params, response)
            if override is not None:
                status, headers = override
        response.setdefault("time", milliseconds())
        return httpx.Response(status, json=response, headers=headers, request=request)


async def make_worker(
    directory,
    io,
    previous=None,
    *,
    binding_age=30,
    metadata_profile=ACTUAL_ACCOUNT_FEE_V1,
):
    directory.mkdir(parents=True, exist_ok=True)
    locks = directory.parent / "leases"
    locks.mkdir(mode=0o700, exist_ok=True)
    session = BybitDemoAccountSession(api_key=KEY, api_secret=SECRET,
        max_age=timedelta(seconds=binding_age))
    binding = await session.authenticate(expected_uid="12345", expected_parent_uid="0", expected_is_master=False)
    policy_path = directory / "runtime-policy.sqlite3"
    if previous is None:
        historical = load_v1()(policy_path)
        now = datetime.now(UTC)
        data = inputs()
        for name in ("policy", "market", "instrument", "account", "tiers", "reservations"):
            value = data[name]
            values = {"scope": replace(value.scope, account_id="12345")}
            if name != "policy":
                values["observed_at"] = now
            if name == "account":
                values["loss_day"] = now.date()
            if name == "market":
                values.update(next_funding_at=now + timedelta(hours=1), costs_valid_until=now + timedelta(minutes=1))
            data[name] = replace(value, **values)
        data["now"] = now
        draft = historical.read()["policy"]
        for key, value in asdict(data["policy"]).items():
            if key in draft:
                draft[key] = str(value) if isinstance(value, Decimal) else value
        draft.update(capital_usdt="1000", mode="FULL_AUTO", environment="DEMO",
            allowed_symbols=["BTCUSDT"], selected_symbols=["BTCUSDT"],
            allowed_strategies=["strategy-1"], selected_strategies=["strategy-1"])
        historical.save(0, draft)
        book = RuntimePolicyBook(policy_path)
        assert book.read()["schema_version"] == 1
        assert book.read()["revision"] == 1 and book.read()["policy"] == draft
        data["policy"] = book.risk_policy(data["policy"].scope)
        plan = assess_entry(**data)
        request = selection()
        expiry = now + timedelta(seconds=10)
        request = replace(request, scope=replace(request.scope, account_id="12345"),
            policy=replace(request.policy, account_id="12345", revision="1"),
            snapshot=replace(request.snapshot, observed_at=now, expires_at=expiry),
            candidates=tuple(replace(c, account_id="12345", policy_revision="1", issued_at=now,
                expires_at=expiry) for c in request.candidates), issued_at=now, expires_at=expiry)
        protection = ProtectionPolicy("1", 0, Decimal(90), Decimal(120), "MarkPrice")
        registry = TrustedPlanRegistry((RegisteredPlan("plan-1", request.candidates[0].strategy, plan, protection),))
    else:
        book = RuntimePolicyBook(policy_path)
        plan, request, registry = previous.plan, previous.request, previous.registry
    worker = ExecutionWorker(directory=directory, scope=SCOPE, session=session, binding=binding,
        symbols=("BTCUSDT",), trader_id="WORKER-001", policy_book=book, registry=registry,
        entry_risk=lambda _plan, _now, _reservations: plan, clock=lambda: datetime.now(UTC),
        max_age=timedelta(seconds=10), lock_directory=locks,
        metadata_profile=metadata_profile)
    assert worker._factory is None and worker._decoder is None
    return SimpleNamespace(worker=worker, session=session, book=book, request=request,
        registry=registry, plan=plan, io=io, assembly=None)


async def seed_baseline(h, *, filled=False):
    await h.worker.start_passive()
    h.assembly = h.worker._assembly
    record = await h.worker.stage(command_key="entry", position_id="position-1",
        mode=AutomaticMode.FULL_AUTO, request=h.request)
    assert record.state is CommandState.PENDING
    request = h.worker.runtime._request(record)
    ids, enums, objects = module("model.identifiers"), module("model.enums"), module("model.objects")
    if h.worker.metadata_profile == SDK1231_OBSERVATION_UNKNOWN_FEES_V1:
        native_instrument = convert_observation_instrument_v1(
            h.io.instrument, "BTCUSDT", milliseconds()
        )
    else:
        from multi_exchange_trader.execution.native_monitor_http import convert_instrument

        native_instrument = convert_instrument(
            h.io.instrument, h.io.fee, "BTCUSDT", milliseconds()
        )
    instrument = module("model.instruments").instruments_from_pyo3([native_instrument])[0]
    order = h.assembly.strategy.order_factory.market(instrument_id=instrument.id,
        order_side=enums.OrderSide.BUY, quantity=objects.Quantity.from_str(str(request.owned.quantity)),
        client_order_id=ids.ClientOrderId(record.client_order_id), reduce_only=False)
    events, uuid = module("model.events.order"), module("core.uuid").UUID4
    original = {"trader_id": order.trader_id, "strategy_id": order.strategy_id,
        "instrument_id": order.instrument_id, "client_order_id": order.client_order_id,
        "account_id": ids.AccountId(h.assembly.channel.native_account_id)}
    order.apply(events.OrderSubmitted(**original, event_id=uuid(), ts_event=order.ts_init + 1, ts_init=order.ts_init + 1))
    order.apply(events.OrderAccepted(**original, event_id=uuid(), ts_event=order.ts_init + 2,
        ts_init=order.ts_init + 2, venue_order_id=ids.VenueOrderId("synthetic-venue")))
    cache = h.assembly.node.cache
    cache.add_instrument(instrument)
    cache.add_order(order, client_id=ids.ClientId("BYBIT"))
    h.io.ledger.initialized_ns = order.ts_init
    h.io.ledger.own(order)
    if filled:
        row = await h.io.ledger.execution("historical-original", str(order.quantity.as_decimal()))
        stamp = int(row["execTime"]) * 1_000_000
        fill = events.OrderFilled(**original, venue_order_id=order.venue_order_id,
            trade_id=ids.TradeId(row["execId"]), position_id=ids.PositionId(f"{instrument.id}-{order.strategy_id}"),
            order_side=order.side, order_type=order.order_type,
            last_qty=objects.Quantity.from_str(format(order.quantity.as_decimal(), ".3f")),
            last_px=objects.Price.from_str("100.00"), currency=instrument.quote_currency,
            commission=objects.Money(Decimal(row["execFee"]), instrument.quote_currency),
            liquidity_side=enums.LiquiditySide.TAKER, event_id=uuid(), ts_event=stamp, ts_init=stamp)
        order.apply(fill)
        position = module("model.position").Position(instrument, fill)
        cache.update_order(order)
        cache.add_position(position, enums.OmsType.NETTING)
        cache.add_position_id(position.id, instrument.id.venue, order.client_order_id, order.strategy_id)
    cache.build_index()
    assert cache.check_integrity()
    h.worker.runtime.journal.claim("entry")
    assert h.worker.runtime.journal.get("entry").state is CommandState.SENDING
    assert not h.assembly.bridge._submissions
    h.order_id = order.client_order_id
    h.original_events = [event.to_dict(event) for event in order.events]
    await h.worker.stop()
    assert decode_document((h.worker.directory / "native-restart.json").read_bytes())["state"] == "sealed"


def cleanup_contract(owner, reason):
    """Capture a specific owner's original resources before the deliberate fault."""
    if reason not in ("gap", "lease"):
        raise ValueError("unsupported fixture cleanup contract")
    worker = owner.worker if hasattr(owner, "worker") else None
    runtime = worker._runtime if worker is not None else None
    lock = worker._lock if worker is not None else None
    return SimpleNamespace(owner=owner, reason=reason, runtime=runtime, lock=lock,
        assembly=worker._assembly if worker is not None else None,
        stores=(worker._history_store, worker._store, worker._journal) if worker is not None else None,
        path=lock.path.resolve() if lock is not None else None,
        scope=worker.scope if worker is not None else None,
        lease=runtime._lease if runtime is not None else None)


def classify_cleanup(contract, owner, error, evidence):
    if contract is None or contract.owner is not owner or not evidence:
        return None
    pairs = {
        "gap": ((MonitoringError, "MONITORING_DELIVERY_GAP_RETAINS_RESOURCES"),
                (RuntimeBlocked, "MONITORING_DELIVERY_GAP_RETAINS_RESOURCES")),
        "lease": ((RuntimeBlocked, "ACCOUNT_WORKER_LEASE_REQUIRED"),),
    }
    for kind, code in pairs[contract.reason]:
        if type(error) is kind and error.args == (code,):
            return (kind, code, contract.reason)
    return None


def cleanup_evidence(contract, owner):
    if contract is None or contract.owner is not owner:
        return False
    worker, runtime, lock = owner.worker, contract.runtime, contract.lock
    if (worker._runtime is not runtime or worker._lock is not lock
            or worker._assembly is not contract.assembly or worker.scope != contract.scope
            or any(current is not original for current, original in zip(
                (worker._history_store, worker._store, worker._journal), contract.stores, strict=True))
            or not lock.acquired or worker._channel_closed or worker._stopped
            or not worker.monitoring_status.resources_retained
            or decode_document((worker.directory / "native-restart.json").read_bytes())["state"] != "active"):
        return False
    # Actual readable temporary stores, not merely captured object references.
    if worker.runtime.journal.get("entry") is None or worker.runtime.store.get_position("position-1") is None:
        return False
    if contract.reason == "gap":
        stamp = owner.assembly.monitoring_stamp()
        retained = worker.monitoring_status.retained_stamp
        return (stamp.received > stamp.delivered and retained is not None
            and retained.received > retained.delivered and lock.acquisition_id == contract.lease)
    return (runtime.worker_lock is lock and not runtime._closed
        and lock.path.resolve() == contract.path == runtime._lock_path
        and lock.matches_scope(account_id=contract.scope.account_id, environment=contract.scope.environment)
        and runtime.journal.scope.account_id == contract.scope.account_id
        and runtime.journal.scope.environment == contract.scope.environment
        and runtime._lease == contract.lease and lock.acquisition_id != contract.lease)


@asynccontextmanager
async def restored_case(
    tmp_path,
    monkeypatch,
    *,
    filled=False,
    binding_age=30,
    metadata_profile=ACTUAL_ACCOUNT_FEE_V1,
):
    """Own every session, native task, fixture gate and temporary store in finally."""
    io, owners = IO(monkeypatch), []
    loop = asyncio.get_running_loop()
    previous_handler, errors = loop.get_exception_handler(), []
    loop.set_exception_handler(lambda _loop, context: errors.append(context))
    initial_tasks = asyncio.all_tasks()
    try:
        source = await make_worker(
            tmp_path, io, metadata_profile=metadata_profile
        )
        owners.append(source)
        await seed_baseline(source, filled=filled)
        h = await make_worker(
            tmp_path,
            io,
            source,
            binding_age=binding_age,
            metadata_profile=metadata_profile,
        )
        owners.append(h)
        await h.worker.start_passive()
        h.assembly, h.order_id = h.worker._assembly, source.order_id
        h.original_events, h.owners = source.original_events, owners
        assert h.worker.native_restart_status.status == "RESTORED_LOCAL_CACHE"
        assert h.worker.runtime.journal.get("entry").state is CommandState.UNKNOWN
        h.reservations = h.worker.runtime.journal.get("entry").reservation
        yield h
    finally:
        for gate in io.gates:
            gate.release.set()
        cleanup_errors = []
        for h in reversed(owners):
            try:
                if not h.worker._stopped:
                    await h.worker.stop()
            except Exception as error:  # noqa: BLE001 - report any failure after owned cleanup.
                contract = getattr(h, "cleanup_contract", None)
                try:
                    evidence = cleanup_evidence(contract, h)
                except Exception:  # noqa: BLE001 - preserve teardown and reject unproved evidence.
                    evidence = False
                classified = classify_cleanup(contract, h, error, evidence)
                if classified is None:
                    cleanup_errors.append((type(error).__name__, "unexpected cleanup failure"))
                else:
                    h.cleanup_classification = classified
                # Failure tests may intentionally leave an observed delivery gap.
                # Quiesce the genuine socket/node; never change stamps or publish a seal.
                worker, assembly = h.worker, h.assembly
                if assembly is not None:
                    node, client = assembly.node, assembly.execution_client
                    run_task, stop_task = worker._node_task, worker._node_stop_task
                    controller, runtime = worker._monitoring, worker._runtime
                    stores = worker._history_store, worker._store, worker._journal
                    lock, lease = worker._lock, worker._lock.acquisition_id

                    def require_fixture_owner(worker=worker, assembly=assembly, node=node,
                            client=client, runtime=runtime, controller=controller,
                            run_task=run_task, stop_task=stop_task, stores=stores, lock=lock, lease=lease):
                        assert worker._assembly is assembly and assembly.node is node
                        assert assembly.execution_client is client and worker._runtime is runtime
                        assert worker._monitoring is controller and worker._node_task is run_task
                        assert worker._node_stop_task is stop_task
                        assert all(current is original for current, original in zip(
                            (worker._history_store, worker._store, worker._journal), stores, strict=True))
                        assert worker._lock is lock and lock.acquired and lock.acquisition_id == lease

                    require_fixture_owner()
                    if run_task is not None and not run_task.done():
                        coroutine = run_task.get_coro()
                        assert coroutine.cr_code is MonitoringResume._run_node.__code__
                        assert coroutine.cr_frame.f_locals.get("self") is controller
                        assert controller.worker is worker and controller.assembly is assembly
                        assert controller.node is node
                    if client._monitor_stream is not None:
                        await client._monitor_stream.close()
                        require_fixture_owner()
                    if client._monitor_reports is not None:
                        client._monitor_reports.retire()
                    assembly.capture_engine_queues(require_fixture_owner)
                    await node.stop_async()
                    require_fixture_owner()
                    assert not node.is_running()
                    # The existing 12-second fixture bound covers both queue and run completion.
                    async with asyncio.timeout(12):
                        await assembly.settle_engine_queues(require_fixture_owner)
                        require_fixture_owner()
                        assembly.require_quiesced()
                        if run_task is not None:
                            # End only the captured startup waiter after the queue consumers.
                            if not run_task.done():
                                run_task.cancel()
                            await asyncio.shield(run_task)
                            require_fixture_owner()
                    if run_task is not None:
                        await assembly.settle_owned_tasks()
                        require_fixture_owner()
                    assert not node.is_running()
                    assembly.require_quiesced()
                    assembly.require_monitoring_tasks_quiesced()
                    # Test-owned temporary handles only, after all producers ended.
                    assembly.close_event_channel()
                    assembly.dispose_local()
                    for store in stores:
                        if store is not None:
                            store.close()
                    lock.release()
            else:
                if getattr(h, "cleanup_contract", None) is not None:
                    cleanup_errors.append(("no retained failure", "expected production retention"))
            finally:
                await h.session.aclose()
        await asyncio.sleep(0)
        loop.set_exception_handler(previous_handler)
        assert not errors, [(item.get("message"), type(item.get("exception")).__name__) for item in errors]
        assert all(wire.is_closed() for wire in io.wires)
        extra = [task for task in asyncio.all_tasks() - initial_tasks if not task.done()]
        assert not extra, [task.get_coro().__qualname__ for task in extra]
        assert not cleanup_errors, cleanup_errors


def assert_liabilities(h, *, candidates=0):
    state = h.worker.runtime.monitoring_state()
    assert len(state.candidates) == candidates
    assert state.credits == state.history_credits == ()
    assert h.worker.runtime.projection("position-1").owned_quantity == 0
    command = h.worker.runtime.journal.get("entry")
    assert command.state is CommandState.UNKNOWN and not command.terminal_confirmed
    assert command.reservation == h.reservations
    assert h.book.read()["desired_state"] == "STOPPED"
    assert h.assembly.node.cache.check_integrity()


async def admit(h):
    status = await h.worker.resume_restored_monitoring()
    assert status.phase == "MONITORING" and status.reconciliation_current
    assert not any((status.trading_enabled, status.credit_authorized,
        status.production_supported, status.venue_event_complete))
    assert h.worker._node_task is not None and not h.worker._node_task.done()
    assert h.assembly.node.is_running()
    assert h.assembly.execution_client.is_connected
    assert h.assembly.strategy.is_running
    assert h.assembly.node.kernel.portfolio.initialized
    assert h.worker._monitoring.budget.physical_attempts == len(h.io.sends) - 4
    assert h.worker._monitoring.budget.physical_attempts <= 200
    return status


async def deliver(h, rows):
    h.io.wires[-1].emit("execution.linear", rows)
    await h.assembly.monitoring_barrier()
    return h.assembly.node.cache.order(h.order_id)
