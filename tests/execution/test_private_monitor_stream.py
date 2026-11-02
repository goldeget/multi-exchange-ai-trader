"""Owned protocol behavior; only the generic socket I/O boundary is replaced."""

import asyncio
import importlib
import json

import pytest


class Wire:
    def __init__(self, handler):
        self.handler = handler
        self.sent = []
        self.closed = False
        self.reconnecting = False
        self.bad_sub = False

    def is_active(self):
        return not self.closed

    def is_closed(self):
        return self.closed

    def is_reconnecting(self):
        return self.reconnecting

    def is_disconnecting(self):
        return False

    def emit(self, value):
        self.handler(json.dumps(value).encode())

    async def send_text(self, data):
        packet = json.loads(data)
        self.sent.append(packet)
        op = packet["op"]
        if op == "ping":
            self.emit(
                {
                    "op": "pong",
                    "req_id": packet["req_id"],
                    "args": ["1793500000000"],
                    "conn_id": "connection-1",
                }
            )
            return
        self.emit(
            {
                "op": op,
                "success": not (op == "subscribe" and self.bad_sub),
                "req_id": packet["req_id"],
                "conn_id": "connection-1",
                **({"ret_msg": "pong"} if op == "ping" else {}),
            }
        )

    async def send_pong(self, data):
        assert len(data) <= 125

    async def disconnect(self):
        self.closed = True


def module():
    # RED is an assertion about the absent implementation, not an import error.
    spec = importlib.util.find_spec("multi_exchange_trader.execution.private_monitor_stream")
    assert spec is not None, "owned private protocol implementation is required"
    return importlib.import_module(spec.name)


def test_nonfinite_json_exponent_is_rejected_even_inside_otherwise_valid_frame():
    with pytest.raises(ValueError):
        module().decode_private_frame(b'{"topic":"position.linear","data":[{"unused":1e999}]}')


async def connected(monkeypatch, dispatch, *, bad_sub=False):
    mod = module()
    wires = []

    async def dial(**kwargs):
        assert kwargs["config"].reconnect_max_attempts == 0
        assert kwargs["config"].heartbeat is None
        wire = Wire(kwargs["handler"])
        wire.bad_sub = bad_sub
        wires.append(wire)
        return wire

    monkeypatch.setattr(mod, "_dial", dial)
    stream = mod.OwnedPrivateMonitorStream(
        api_key="synthetic-key",
        api_secret="synthetic-secret",
        guard=lambda: None,
        dispatch=dispatch,
    )
    try:
        await stream.connect()
    except BaseException:
        await stream.close()
        raise
    return stream, wires[0]


async def test_ready_requires_ack_pong_and_barrier_waits_for_dispatch(monkeypatch):
    admitted, release = asyncio.Event(), asyncio.Event()

    async def dispatch(topic, rows, provenance):
        assert topic == "execution.linear"
        assert provenance.conn_id == "connection-1"
        admitted.set()
        await release.wait()

    stream, wire = await connected(monkeypatch, dispatch)
    try:
        assert [p["op"] for p in wire.sent] == ["auth", "subscribe", "ping"]
        assert wire.sent[1]["args"] == ["order.linear", "execution.linear", "position.linear"]
        wire.emit({"topic": "execution.linear", "data": [{"execId": "original"}]})
        await admitted.wait()
        barrier = asyncio.create_task(stream.barrier())
        await asyncio.sleep(0)
        assert not barrier.done()
        release.set()
        stamp = await barrier
        assert stamp.epoch == 1 and stamp.received == stamp.delivered == 4
        stream.require_ready()
    finally:
        await stream.close()


async def test_negative_subscribe_cannot_be_swallowed_into_readiness(monkeypatch):
    async def dispatch(*args):
        pytest.fail("no topic delivery before handshake")

    with pytest.raises(ValueError, match="MONITORING_"):
        await connected(monkeypatch, dispatch, bad_sub=True)


@pytest.mark.parametrize(
    "payload",
    [b'{"op":"ping","op":"auth"}', b'{"x":NaN}', b'{"x":1e999}', b"x" * (1024 * 1024 + 1)],
)
async def test_malformed_ingress_is_sticky_and_never_dispatched(monkeypatch, payload):
    async def dispatch(*args):
        pytest.fail("invalid frame reached delivery")

    stream, wire = await connected(monkeypatch, dispatch)
    try:
        wire.handler(payload)
        await asyncio.sleep(0)
        with pytest.raises(ValueError, match="MONITORING_"):
            await stream.barrier()
        assert stream.fault is not None
    finally:
        await stream.close()


@pytest.mark.parametrize(
    "change",
    ["absent-request", "wrong-request", "wrong-connection", "duplicate-auth", "public-pong"],
)
async def test_exact_ack_scope_and_sole_pending_without_request_id(monkeypatch, change):
    mod = module()
    wires = []

    async def dial(**kwargs):
        wire = Wire(kwargs["handler"])
        base = wire.emit

        def emit(packet):
            if change == "absent-request" and packet["op"] != "pong":
                packet.pop("req_id", None)
            elif change == "wrong-request":
                packet["req_id"] = "wrong"
            elif change == "wrong-connection" and packet["op"] == "subscribe":
                packet["conn_id"] = "other"
            elif change == "public-pong" and packet["op"] == "pong":
                packet.update(op="ping", success=True, ret_msg="pong")
            base(packet)
            if change == "duplicate-auth" and packet["op"] == "auth":
                base(packet)

        wire.emit = emit
        wires.append(wire)
        return wire

    monkeypatch.setattr(mod, "_dial", dial)

    async def dispatch(*args):
        pass

    stream = mod.OwnedPrivateMonitorStream(
        api_key="synthetic", api_secret="synthetic", guard=lambda: None, dispatch=dispatch
    )
    try:
        if change == "absent-request":
            await stream.connect()
            stream.require_ready()
        else:
            with pytest.raises(ValueError):
                await stream.connect()
            assert stream.fault
    finally:
        await stream.close()


async def test_terminal_mode_is_sticky_even_if_socket_looks_active_again(monkeypatch):
    async def dispatch(*args):
        pytest.fail("retired epoch dispatched")

    stream, wire = await connected(monkeypatch, dispatch)
    try:
        wire.reconnecting = True
        with pytest.raises(ValueError):
            stream.require_ready()
        wire.reconnecting = False
        wire.emit({"topic": "execution.linear", "data": [{}]})
        with pytest.raises(ValueError):
            stream.require_ready()
    finally:
        await stream.close()


async def test_raw_send_escape_is_denied_before_io_and_retires_epoch(monkeypatch):
    async def dispatch(*args):
        pass

    stream, wire = await connected(monkeypatch, dispatch)
    try:
        with pytest.raises(ValueError):
            await stream._send({"op": "order.create", "args": []})
        assert len(wire.sent) == 3
        with pytest.raises(ValueError):
            stream.require_ready()
    finally:
        await stream.close()


async def test_pending_terminal_ack_does_not_block_original_execution_ingress(monkeypatch):
    terminal = asyncio.get_running_loop().create_future()
    pending = asyncio.Event()

    async def dispatch(topic, rows, provenance):
        if topic == "order.linear":
            pending.set()
            return terminal
        terminal.set_result(None)

    stream, wire = await connected(monkeypatch, dispatch)
    try:
        wire.emit({"topic": "order.linear", "data": [{"orderStatus": "Filled"}]})
        await pending.wait()
        barrier = asyncio.create_task(stream.barrier())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(barrier), 0.05)
        wire.emit({"topic": "execution.linear", "data": [{"execId": "original"}]})
        stamp = await asyncio.wait_for(barrier, 1)
        assert stamp.received == stamp.delivered == 5
    finally:
        await stream.close()


async def test_pending_row_limit_counts_rows_not_only_frames(monkeypatch):
    gate = asyncio.Event()

    async def dispatch(*args):
        await gate.wait()

    stream, wire = await connected(monkeypatch, dispatch)
    try:
        for _ in range(6):
            wire.emit({"topic": "execution.linear", "data": [{}] * 200})
        assert stream.fault is not None
    finally:
        gate.set()
        await stream.close()


async def test_original_dispatch_claim_rejects_copied_provenance_before_delivery(monkeypatch):
    from dataclasses import replace

    attempted = asyncio.Event()

    async def dispatch(topic, rows, provenance):
        assert hasattr(stream, "_claim_dispatch"), "exact raw callback claim required"
        with pytest.raises(ValueError):
            stream._claim_dispatch(topic, rows, replace(provenance))
        attempted.set()

    stream, wire = await connected(monkeypatch, dispatch)
    try:
        wire.emit({"topic": "execution.linear", "data": [{}]})
        try:
            await asyncio.wait_for(attempted.wait(), 0.1)
        except TimeoutError:
            assert hasattr(stream, "_claim_dispatch"), "exact raw callback claim required"
            raise
        assert stream.fault
    finally:
        await stream.close()


async def test_pending_auth_cannot_be_sent_twice(monkeypatch):
    mod = module()
    sent, wires = asyncio.Event(), []

    async def dial(**kwargs):
        wire = Wire(kwargs["handler"])

        async def send(data):
            wire.sent.append(json.loads(data))
            sent.set()

        wire.send_text = send
        wires.append(wire)
        return wire

    async def dispatch(*args):
        pass

    monkeypatch.setattr(mod, "_dial", dial)
    stream = mod.OwnedPrivateMonitorStream(
        api_key="synthetic", api_secret="synthetic", guard=lambda: None, dispatch=dispatch
    )
    connection = asyncio.create_task(stream.connect())
    try:
        await sent.wait()
        with pytest.raises(ValueError):
            await stream._send(dict(wires[0].sent[0]))
        assert len(wires[0].sent) == 1
    finally:
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)
        await stream.close()


@pytest.mark.parametrize("unprocessed", [False, True])
async def test_retirement_drains_only_already_admitted_delivery_without_ready(
    monkeypatch, unprocessed
):
    entered, release = asyncio.Event(), asyncio.Event()

    async def dispatch(*args):
        entered.set()
        await release.wait()

    stream, wire = await connected(monkeypatch, dispatch)
    try:
        wire.emit({"topic": "execution.linear", "data": [{}]})
        await entered.wait()
        if unprocessed:
            wire.emit({"topic": "execution.linear", "data": [{}]})
        stream.retire("MONITORING_STOPPED")
        with pytest.raises(ValueError):
            stream.require_ready()
        assert hasattr(stream, "drain_accepted"), "retired accepted-delivery drain required"
        draining = asyncio.create_task(stream.drain_accepted())
        release.set()
        stamp = await asyncio.wait_for(draining, 1)
        assert stamp.delivered == 4
        assert stamp.received == (5 if unprocessed else 4)
        assert stream.fault == "MONITORING_STOPPED"
    finally:
        release.set()
        await stream.close()

async def test_completed_owned_tasks_are_retired_and_failures_retrieved(monkeypatch):
    stream, _ = await connected(monkeypatch, lambda *args: None)
    active = len(stream._tasks)
    async def completed():
        return
    async def failed():
        raise RuntimeError('private provider payload must not escape')
    try:
        for _ in range(100):
            stream._task(completed())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(stream._tasks) == active
        stream._task(failed())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert stream.fault == 'MONITORING_OWNED_TASK_FAILED'
        assert all(not task.done() for task in stream._tasks)
    finally:
        await stream.close()
    assert not stream._tasks

async def test_close_keeps_running_task_owned_until_cancel_cleanup_finishes(monkeypatch):
    stream, _ = await connected(monkeypatch, lambda *args: None)
    entered, finishing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def running():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            finishing.set()
            await release.wait()
    stream._task(running())
    await entered.wait()
    closing = asyncio.create_task(stream.close())
    await finishing.wait()
    assert not closing.done()
    assert any(not task.done() for task in stream._tasks)
    release.set()
    await closing
    assert not stream._tasks
