"""One owned, non-reconnecting private socket and an observed-ingress barrier.

The SDK schedules its Python handler from Rust. Our watermark begins at that
handler's first action, not at an unknowable Rust/venue receive boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .monitoring_models import MonitoringError, StreamStamp

_TOPICS = ("order.linear", "execution.linear", "position.linear")
_MAX_FRAME = 1024 * 1024


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise MonitoringError("MONITORING_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _nonfinite(value: str) -> None:
    raise MonitoringError("MONITORING_NONFINITE_JSON")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise MonitoringError("MONITORING_NONFINITE_JSON")
    return result


def decode_private_frame(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes or not 0 < len(raw) <= _MAX_FRAME:
        raise MonitoringError("MONITORING_FRAME_SIZE")
    try:
        value = json.loads(
            raw, object_pairs_hook=_pairs, parse_constant=_nonfinite, parse_float=_finite_float
        )
    except (ValueError, UnicodeError, RecursionError):
        raise MonitoringError("MONITORING_FRAME_INVALID") from None
    if type(value) is not dict:
        raise MonitoringError("MONITORING_FRAME_INVALID")
    return value


@dataclass(frozen=True)
class PrivateProvenance:
    epoch: int
    conn_id: str
    ingress: int
    received_ns: int


async def _dial(**kwargs: Any) -> Any:
    from nautilus_trader.core import nautilus_pyo3

    return await nautilus_pyo3.WebSocketClient.connect(**kwargs)


class OwnedPrivateMonitorStream:
    """Private raw transport; dispatch completion MUST include native and app ACKs."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        guard: Callable[[], None],
        dispatch: Callable[
            [str, list[dict[str, Any]], PrivateProvenance], Awaitable[asyncio.Future[Any] | None]
        ],
    ) -> None:
        self._key, self._secret = api_key, api_secret
        self._guard, self._dispatch = guard, dispatch
        self._loop = asyncio.get_running_loop()
        self._lock = threading.Lock()
        self._epoch = 0
        self._retired = False
        self._fault: str | None = None
        self._socket: Any = None
        self._received = self._delivered = self._entries = 0
        self._completed: set[int] = set()
        self._weights: dict[int, int] = {}
        self._queue: asyncio.Queue[tuple[int, bytes, int]] = asyncio.Queue(maxsize=1024)
        self._tasks: set[asyncio.Task[None]] = set()
        self._changed = asyncio.Event()
        self._dispatch_done = asyncio.Event()
        self._dispatch_done.set()
        self._conn_id = ""
        self._pending: dict[str, Any] | None = None
        self._pending_sent = False
        self._phase = "NEW"
        self._authenticated = self._subscribed = False
        self._last_pong: float | None = None
        self._ping_number = 0
        self._early: list[tuple[int, dict[str, Any], int]] = []
        self._dispatch_claim: tuple[str, list[dict[str, Any]], PrivateProvenance] | None = None

    def __repr__(self) -> str:
        return "OwnedPrivateMonitorStream(credentials=redacted)"

    @property
    def fault(self) -> str | None:
        return self._fault

    def stamp(self) -> StreamStamp:
        with self._lock:
            return StreamStamp(self._epoch, self._conn_id, self._received, self._delivered)

    def retire(self, reason: str = "MONITORING_RETIRED") -> None:
        # This is also called by the Rust reconnection callback, off-loop.
        with self._lock:
            self._retired = True
            self._fault = self._fault or reason
        self._loop.call_soon_threadsafe(self._changed.set)

    def _require_live(self) -> None:
        if self._retired or self._fault:
            raise MonitoringError(self._fault or "MONITORING_RETIRED")
        try:
            self._guard()
            socket = self._socket
            if socket is not None and (
                not socket.is_active()
                or socket.is_reconnecting()
                or socket.is_disconnecting()
                or socket.is_closed()
            ):
                raise MonitoringError("MONITORING_SOCKET_MODE")
        except BaseException:  # noqa: BLE001 - retire before containing provider/consumer faults.
            self.retire("MONITORING_OWNER_OR_SOCKET_CHANGED")
            raise MonitoringError(self._fault or "MONITORING_OWNER_OR_SOCKET_CHANGED") from None

    def require_ready(self) -> None:
        self._require_live()
        if (
            self._socket is None
            or not self._authenticated
            or not self._subscribed
            or self._last_pong is None
        ):
            raise MonitoringError("MONITORING_HANDSHAKE_PENDING")
        age = time.monotonic() - self._last_pong
        if not 0 <= age <= 30:
            self.retire("MONITORING_PONG_EXPIRED")
            raise MonitoringError("MONITORING_PONG_EXPIRED")

    def _receive(self, epoch: int, raw: bytes) -> None:
        # First application action: assign ingress before scheduling anything.
        with self._lock:
            if self._retired or epoch != self._epoch:
                return
            self._received += 1
            sequence = self._received
        try:
            packet = decode_private_frame(raw)
            rows = packet.get("data") if "topic" in packet else None
            if "topic" in packet and (type(rows) is not list or not 0 < len(rows) <= 200):
                raise MonitoringError("MONITORING_TOPIC_ROW_LIMIT")
            weight = len(rows) if isinstance(rows, list) else 1
        except (ValueError, TypeError):
            self.retire("MONITORING_INGRESS_INVALID")
            return
        with self._lock:
            if self._retired:
                return
            self._entries += weight
            self._weights[sequence] = weight
            overflow = self._entries > 1024
        if overflow or type(raw) is not bytes or len(raw) > _MAX_FRAME:
            self.retire("MONITORING_INGRESS_LIMIT")
            return
        self._loop.call_soon_threadsafe(self._enqueue, epoch, sequence, raw, time.time_ns())

    def _enqueue(self, epoch: int, sequence: int, raw: bytes, received_ns: int) -> None:
        if self._retired or epoch != self._epoch:
            return
        try:
            self._queue.put_nowait((sequence, raw, received_ns))
        except asyncio.QueueFull:
            self.retire("MONITORING_INGRESS_LIMIT")

    def _complete(self, sequence: int) -> None:
        with self._lock:
            self._completed.add(sequence)
            self._entries -= self._weights.pop(sequence)
            while self._delivered + 1 in self._completed:
                self._delivered += 1
                self._completed.remove(self._delivered)
        self._changed.set()

    def _task(self, coroutine: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)

    async def connect(self, *, timeout: float = 10) -> None:
        from nautilus_trader.core import nautilus_pyo3

        if self._epoch or self._retired or not 0 < timeout <= 10:
            raise MonitoringError("MONITORING_NEW_STREAM_REQUIRED")
        self._guard()
        self._epoch = 1
        epoch = self._epoch
        config = nautilus_pyo3.WebSocketConfig(
            url="wss://stream-demo.bybit.com/v5/private",
            headers=[],
            heartbeat=None,
            reconnect_max_attempts=0,
            reconnect_timeout_ms=10000,
            idle_timeout_ms=30000,
        )
        try:
            async with asyncio.timeout(timeout):
                self._socket = await _dial(
                    loop_=self._loop,
                    config=config,
                    handler=lambda raw: self._receive(epoch, raw),
                    ping_handler=lambda raw: self._control_ping(epoch, raw),
                    post_reconnection=lambda: self.retire("MONITORING_UNEXPECTED_RECONNECT"),
                )
                self._require_live()
                self._task(self._consume())
                self._task(self._watchdog())
                expires = int(time.time() * 1000) + 10000
                signature = hmac.new(
                    self._secret.encode(), f"GET/realtime{expires}".encode(), hashlib.sha256
                ).hexdigest()
                self._phase = "AUTH_PENDING"
                self._pending = {
                    "op": "auth",
                    "req_id": f"auth-{epoch}",
                    "args": [self._key, expires, signature],
                }
                await self._send(self._pending)
                await self._wait(lambda: self._authenticated)
                self._phase = "SUB_PENDING"
                self._pending = {"op": "subscribe", "req_id": f"sub-{epoch}", "args": list(_TOPICS)}
                await self._send(self._pending)
                await self._wait(lambda: self._subscribed)
                await self._ping()
                await self._wait(lambda: self._last_pong is not None)
                self.require_ready()
                await self.barrier()
                self._task(self._heartbeat())
        except BaseException:
            self.retire("MONITORING_CONNECT_FAILED")
            raise

    async def _send(self, packet: dict[str, Any]) -> None:
        self._require_live()
        op = packet.get("op")
        allowed = (
            self._pending is not None
            and not self._pending_sent
            and packet == self._pending
            and (
                op == "auth"
                and self._phase == "AUTH_PENDING"
                or op == "subscribe"
                and self._phase == "SUB_PENDING"
                or op == "ping"
                and self._phase == "LIVE"
            )
        )
        if not allowed:
            self.retire("MONITORING_OUTGOING_DENIED")
            raise MonitoringError("MONITORING_OUTGOING_DENIED")
        self._pending_sent = True
        await self._socket.send_text(json.dumps(packet, separators=(",", ":")).encode())
        self._require_live()

    async def _ping(self) -> None:
        if self._pending is not None:
            self.retire("MONITORING_PENDING_PING")
            raise MonitoringError("MONITORING_PENDING_PING")
        self._ping_number += 1
        self._pending = {"op": "ping", "req_id": f"ping-{self._epoch}-{self._ping_number}"}
        await self._send(self._pending)

    def _control_ping(self, epoch: int, raw: bytes) -> None:
        if self._retired or epoch != self._epoch:
            return
        if type(raw) is not bytes or len(raw) > 125:
            self.retire("MONITORING_CONTROL_FRAME_INVALID")
            return
        self._loop.call_soon_threadsafe(self._task, self._control_pong(raw))

    async def _control_pong(self, raw: bytes) -> None:
        try:
            self._require_live()
            await self._socket.send_pong(raw)
        except BaseException:  # noqa: BLE001 - retire before containing provider/consumer faults.
            self.retire("MONITORING_CONTROL_PONG_FAILED")

    async def _wait(self, predicate: Callable[[], bool]) -> None:
        while True:
            self._changed.clear()
            self._require_live()
            if predicate():
                return
            await self._changed.wait()

    async def barrier(self) -> StreamStamp:
        self.require_ready()
        # Explicit owner-loop FIFO fence accounts for callbacks already scheduled
        # into this loop. It makes no claim about not-yet-invoked Rust callbacks.
        fence: asyncio.Future[None] = self._loop.create_future()
        self._loop.call_soon(fence.set_result, None)
        await fence
        while True:
            self._changed.clear()
            self.require_ready()
            stamp = self.stamp()
            if stamp.received == stamp.delivered:
                return stamp
            await self._changed.wait()

    async def _consume(self) -> None:
        try:
            while True:
                sequence, raw, received_ns = await self._queue.get()
                self._require_live()
                packet = decode_private_frame(raw)
                if "topic" in packet:
                    if not self._subscribed or self._last_pong is None:
                        self._early.append((sequence, packet, received_ns))
                        continue
                    await self._topic(sequence, packet, received_ns)
                else:
                    self._ack(packet)
                    self._complete(sequence)
                    if self._subscribed and self._last_pong is not None:
                        early, self._early = self._early, []
                        for item in early:
                            await self._topic(*item)
        except asyncio.CancelledError:
            raise
        except BaseException:  # noqa: BLE001 - retire before containing provider/consumer faults.
            self.retire("MONITORING_INGRESS_FAILED")

    def _ack(self, packet: dict[str, Any]) -> None:
        pending = self._pending
        if pending is None:
            raise MonitoringError("MONITORING_UNEXPECTED_ACK")
        op = packet.get("op")
        expected = pending["op"]
        if (
            op != ("pong" if expected == "ping" else expected)
            or expected != "ping"
            and packet.get("success") is not True
            or expected == "ping"
            and packet.get("success", True) is not True
            or "req_id" in packet
            and packet["req_id"] != pending["req_id"]
            or type(packet.get("conn_id")) is not str
            or not packet["conn_id"]
            or self._conn_id
            and packet["conn_id"] != self._conn_id
        ):
            raise MonitoringError("MONITORING_ACK_INVALID")
        if expected == "auth":
            if self._authenticated or self._phase != "AUTH_PENDING":
                raise MonitoringError("MONITORING_DUPLICATE_ACK")
            self._conn_id = packet["conn_id"]
            self._authenticated = True
        elif expected == "subscribe":
            topics = packet.get("successTopics")
            if (
                self._subscribed
                or self._phase != "SUB_PENDING"
                or topics is not None
                and (
                    type(topics) is not list
                    or len(topics) != len(_TOPICS)
                    or set(topics) != set(_TOPICS)
                )
                or packet.get("failTopics", []) != []
            ):
                raise MonitoringError("MONITORING_SUBSCRIPTION_ACK_INVALID")
            self._subscribed = True
            self._phase = "LIVE"
        elif expected == "ping":
            args = packet.get("args")
            if (
                packet.get("req_id") != pending["req_id"]
                or type(args) is not list
                or len(args) != 1
                or type(args[0]) is not str
                or not args[0].isdigit()
                or not 1 <= len(args[0]) <= 20
            ):
                raise MonitoringError("MONITORING_PONG_INVALID")
            self._last_pong = time.monotonic()
        self._pending = None
        self._pending_sent = False
        self._changed.set()

    async def _topic(self, sequence: int, packet: dict[str, Any], received_ns: int) -> None:
        self.require_ready()
        rows, topic = packet.get("data"), packet["topic"]
        if (
            topic not in _TOPICS
            or type(rows) is not list
            or not 0 < len(rows) <= 200
            or any(type(row) is not dict for row in rows)
            or "conn_id" in packet
            and packet["conn_id"] != self._conn_id
        ):
            raise MonitoringError("MONITORING_TOPIC_INVALID")
        provenance = PrivateProvenance(self._epoch, self._conn_id, sequence, received_ns)
        self._dispatch_claim = topic, rows, provenance
        self._dispatch_done.clear()
        try:
            completion = await self._dispatch(topic, rows, provenance)
            if not self._retired:
                self._require_live()
            if completion is None:
                self._complete(sequence)
            else:
                self._task(self._finish_pending(sequence, completion))
        finally:
            self._dispatch_claim = None
            self._dispatch_done.set()

    async def drain_accepted(self) -> StreamStamp:
        """Settle already dispatched callbacks without authorizing new ingress.

        Unlike barrier(), this intentionally works after retirement and can
        return an unequal watermark: unprocessed or failed rows remain visible.
        """
        async with asyncio.timeout(5):
            await self._dispatch_done.wait()
            fence: asyncio.Future[None] = self._loop.create_future()
            self._loop.call_soon(fence.set_result, None)
            await fence
        return self.stamp()

    def _claim_dispatch(
        self, topic: str, rows: list[dict[str, Any]], provenance: PrivateProvenance
    ) -> None:
        self._require_live()
        claim = self._dispatch_claim
        if claim is None or claim[0] != topic or claim[1] is not rows or claim[2] is not provenance:
            self.retire("MONITORING_ORIGINAL_CALLBACK_REQUIRED")
            raise MonitoringError("MONITORING_ORIGINAL_CALLBACK_REQUIRED")
        self._dispatch_claim = None

    async def _finish_pending(self, sequence: int, completion: asyncio.Future[Any]) -> None:
        try:
            await completion
            if not self._retired:
                self._require_live()
            self._complete(sequence)
        except asyncio.CancelledError:
            if not self._retired:
                self.retire("MONITORING_PENDING_DELIVERY_CANCELLED")
        except BaseException:  # noqa: BLE001 - retire before containing provider/consumer faults.
            self.retire("MONITORING_PENDING_DELIVERY_FAILED")

    async def _heartbeat(self) -> None:
        try:
            while True:
                await asyncio.sleep(20)
                self.require_ready()
                await self._ping()
        except asyncio.CancelledError:
            raise
        except BaseException:  # noqa: BLE001 - retire before containing provider/consumer faults.
            self.retire("MONITORING_HEARTBEAT_FAILED")

    async def _watchdog(self) -> None:
        try:
            while True:
                await asyncio.sleep(1)
                self._require_live()
                if self._last_pong is not None:
                    self.require_ready()
        except asyncio.CancelledError:
            raise
        except BaseException:  # noqa: BLE001 - retire before containing provider/consumer faults.
            self.retire("MONITORING_WATCHDOG_FAILED")

    async def close(self) -> None:
        self.retire()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        async with asyncio.timeout(10):
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if self._socket is not None:
                await self._socket.disconnect()
                if not self._socket.is_closed():
                    raise MonitoringError("MONITORING_SOCKET_NOT_CLOSED")
        self._tasks.clear()
