"""Separate SPOT-PAPER-v2 identity over the existing immutable PAPER ledger machinery."""

from datetime import timedelta
from typing import Any, ClassVar

from .contract import SPECS
from .spot_contract import TICK_SOURCE, VERSION, validate_spot_event
from .spot_sandbox import replay
from .store import _LOCK, PaperStore, clock, identifier, parse_time


class SpotPaperStore(PaperStore):
    metadata: ClassVar[dict[str, Any]] = {"schema": 1, "assumptions": VERSION, "engine": "1.231.0", "product": "SPOT"}

    def admission(self, event_count: int, state: dict[str, Any], payload: dict[str, Any]) -> None:
        super().admission(event_count, state, payload)
        open_count = sum(order["status"] in {"ACCEPTED", "PARTIALLY_FILLED"}
                         for order in state.get("orders", []))
        added = int(payload.get("kind") == "order" and payload.get("order_type") == "LIMIT")
        if payload.get("kind") != "cancel" and event_count + 1 + open_count + added > 500:
            raise ValueError("SPOT event limit reserves cancellation capacity")

    def rebuild(self, scenario: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows or rows[0]["payload"] != {"kind": "create"}:
            raise ValueError("SPOT seed reconciliation")
        created = parse_time(rows[0]["received_at"])
        last_time = created - timedelta(seconds=2)
        mark_times = {symbol: last_time for symbol in SPECS}
        sequence = 0
        events = []
        for row in rows[1:]:
            event = dict(row["payload"])
            received = parse_time(row["received_at"])
            symbol = event.get("instrument")
            if symbol not in SPECS:
                raise ValueError("unsupported SPOT instrument")
            if event.get("kind") == "tick":
                if (set(event) != {"kind", "instrument", "price", "sequence", "timestamp", "source"}
                        or event["source"] != TICK_SOURCE or type(event["sequence"]) is not int
                        or event["sequence"] != sequence + 1):
                    raise ValueError("SPOT tick fields/sequence/source")
                stamp = parse_time(event["timestamp"])
                if stamp <= last_time or stamp > received or received - stamp > timedelta(seconds=120):
                    raise ValueError("SPOT tick time future/stale/reversed")
                sequence = event.pop("sequence")
                event.pop("source")
                event.pop("timestamp")
                last_time = stamp
                mark_times[symbol] = stamp
            elif event.get("kind") != "cancel" and received - mark_times[symbol] > timedelta(seconds=120):
                raise ValueError("stale SPOT mark; add a new virtual tick")
            validate_spot_event(event)
            events.append(event)
        state = replay(events)
        state.update(scenario_id=scenario, created_at=created.isoformat(), revision=len(rows),
                     latest_sequence=sequence, last_tick_at=last_time.isoformat(),
                     mark_times={symbol: stamp.isoformat() for symbol, stamp in mark_times.items()},
                     tick_source=TICK_SOURCE, synthetic=True)
        return state

    def preview_command(self, scenario: str, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        identifier(scenario)
        validate_spot_event(payload)
        if payload["kind"] != "order":
            raise ValueError("SPOT order review required")
        with _LOCK, self.db() as db:
            db.execute("BEGIN")
            rows, current = self.verified(db, scenario)
            self.admission(len(rows), current, payload)
            candidate = self.rebuild(scenario, rows + [{"payload": payload, "received_at": clock().isoformat()}])
            return current, candidate
