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
from test_bybit_native_linux import module
from test_native_monitor_http import fee_row, instrument_row, wallet_row
from test_risk import inputs
from test_runtime import selection

from multi_exchange_trader.execution.account_binding import BybitDemoAccountSession
from multi_exchange_trader.execution.journal import CommandState
from multi_exchange_trader.execution.modes import AutomaticMode
from multi_exchange_trader.execution.native_restart_store import decode_document
from multi_exchange_trader.execution.protection import ProtectionPolicy, ProtectionScope
from multi_exchange_trader.execution.risk import assess_entry
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


async def make_worker(directory, io, previous=None, *, binding_age=30):
    directory.mkdir(parents=True, exist_ok=True)
    locks = directory.parent / "leases"
    locks.mkdir(mode=0o700, exist_ok=True)
    session = BybitDemoAccountSession(api_key=KEY, api_secret=SECRET,
        max_age=timedelta(seconds=binding_age))
    binding = await session.authenticate(expected_uid="12345", expected_parent_uid="0", expected_is_master=False)
    book = RuntimePolicyBook(directory / "runtime-policy.sqlite3")
    if previous is None:
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
        draft = book.read()["policy"]
        for key, value in asdict(data["policy"]).items():
            if key in draft:
                draft[key] = str(value) if isinstance(value, Decimal) else value
        draft.update(capital_usdt="1000", mode="FULL_AUTO", environment="DEMO",
            allowed_symbols=["BTCUSDT"], selected_symbols=["BTCUSDT"],
            allowed_strategies=["strategy-1"], selected_strategies=["strategy-1"])
        book.save(0, draft)
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
        plan, request, registry = previous.plan, previous.request, previous.registry
    worker = ExecutionWorker(directory=directory, scope=SCOPE, session=session, binding=binding,
        symbols=("BTCUSDT",), trader_id="WORKER-001", policy_book=book, registry=registry,
        entry_risk=lambda _plan, _now, _reservations: plan, clock=lambda: datetime.now(UTC),
        max_age=timedelta(seconds=10), lock_directory=locks)
    assert worker._factory is None and worker._decoder is None
    return SimpleNamespace(worker=worker, session=session, book=book, request=request,
        registry=registry, plan=plan, io=io, assembly=None)


async def seed_baseline(h, *, filled=False):
    from multi_exchange_trader.execution.native_monitor_http import convert_instrument

    await h.worker.start_passive()
    h.assembly = h.worker._assembly
    record = await h.worker.stage(command_key="entry", position_id="position-1",
        mode=AutomaticMode.FULL_AUTO, request=h.request)
    assert record.state is CommandState.PENDING
    request = h.worker.runtime._request(record)
    ids, enums, objects = module("model.identifiers"), module("model.enums"), module("model.objects")
    instrument = module("model.instruments").instruments_from_pyo3([
        convert_instrument(h.io.instrument, h.io.fee, "BTCUSDT", milliseconds())])[0]
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


@asynccontextmanager
async def restored_case(tmp_path, monkeypatch, *, filled=False, binding_age=30):
    """Own every session, native task, fixture gate and temporary store in finally."""
    io, owners = IO(monkeypatch), []
    loop = asyncio.get_running_loop()
    previous_handler, errors = loop.get_exception_handler(), []
    loop.set_exception_handler(lambda _loop, context: errors.append(context))
    initial_tasks = asyncio.all_tasks()
    try:
        source = await make_worker(tmp_path, io)
        owners.append(source)
        await seed_baseline(source, filled=filled)
        h = await make_worker(tmp_path, io, source, binding_age=binding_age)
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
            except (RuntimeError, ValueError, TimeoutError) as error:
                cleanup_errors.append(type(error).__name__)
                # Failure tests may intentionally leave an observed delivery gap.
                # Quiesce the genuine socket/node; never change stamps or publish a seal.
                assembly = h.worker._assembly
                if assembly is not None:
                    client = assembly.execution_client
                    if client._monitor_stream is not None:
                        await client._monitor_stream.close()
                    if client._monitor_reports is not None:
                        client._monitor_reports.retire()
                    await assembly.node.stop_async()
                    if h.worker._node_task is not None:
                        await asyncio.wait_for(asyncio.shield(h.worker._node_task), 12)
                        await assembly.settle_owned_tasks()
                    assert not assembly.node.is_running()
                    assembly.require_quiesced()
                    assembly.require_monitoring_tasks_quiesced()
                    # Test-owned temporary handles only, after all producers ended.
                    assembly.close_event_channel()
                    assembly.dispose_local()
                    for store in (h.worker._history_store, h.worker._store, h.worker._journal):
                        if store is not None:
                            store.close()
                    if h.worker._lock.acquired:
                        h.worker._lock.release()
            finally:
                await h.session.aclose()
        await asyncio.sleep(0)
        loop.set_exception_handler(previous_handler)
        assert not errors, [(item.get("message"), type(item.get("exception")).__name__) for item in errors]
        assert all(wire.is_closed() for wire in io.wires)
        extra = [task for task in asyncio.all_tasks() - initial_tasks if not task.done()]
        assert not extra, [task.get_coro().__qualname__ for task in extra]
        if cleanup_errors:
            assert getattr(io, "allow_retained_cleanup", False), cleanup_errors


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
