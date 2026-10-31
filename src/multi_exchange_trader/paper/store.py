"""Atomic local SQLite audit with deterministic native replay and fail-closed reconciliation."""

import hashlib
import json
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .contract import SPECS, VERSION, validate_event
from .sandbox import sandbox_replay as replay

_LOCK = threading.RLock()  # Native simulation clocks/logging are also serialized in this process.
ACCOUNT_FIELDS = (
    "wallet_balance",
    "reserved_margin",
    "available_balance",
    "deficit",
    "unrealized_pnl",
    "equity",
    "closed_net_pnl",
    "realized_wallet_delta",
    "realized_gross",
    "fees",
    "native_rounding_adjustment",
)
META = {"schema": 1, "assumptions": VERSION, "engine": "1.231.0"}


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def clock() -> datetime:
    return datetime.now(UTC)


def parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or "T" not in value or len(value) > 64:
        raise ValueError("UTC timestamp required")
    try:
        t = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("UTC timestamp required") from None
    if t.tzinfo is None:
        raise ValueError("UTC timestamp required")
    return t.astimezone(UTC)


def identifier(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("invalid local identifier")


class PaperStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        with _LOCK:
            new = not self.path.exists()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with self.db() as db:
                    if new:
                        db.executescript("""
                        PRAGMA journal_mode=WAL;
                        CREATE TABLE metadata(value TEXT NOT NULL);
                        CREATE TABLE scenarios(id TEXT PRIMARY KEY,created_at TEXT NOT NULL,snapshot TEXT NOT NULL);
                        CREATE TABLE events(scenario TEXT NOT NULL,seq INTEGER NOT NULL,payload TEXT NOT NULL,received_at TEXT NOT NULL,previous TEXT NOT NULL,hash TEXT NOT NULL,PRIMARY KEY(scenario,seq));
                        CREATE TABLE commands(scenario TEXT NOT NULL,key TEXT NOT NULL,payload TEXT NOT NULL,seq INTEGER NOT NULL,result_hash TEXT NOT NULL,PRIMARY KEY(scenario,key),UNIQUE(scenario,seq));
                        CREATE TABLE fills(scenario TEXT NOT NULL,idx INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(scenario,idx));
                        CREATE TABLE orders(scenario TEXT NOT NULL,idx INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(scenario,idx));
                        CREATE TABLE settlements(scenario TEXT NOT NULL,seq INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(scenario,seq));
                        PRAGMA user_version=1;
                        """)
                        db.execute("INSERT INTO metadata VALUES (?)", (canonical(META),))
                        for table in ("events", "commands", "fills", "orders", "settlements"):
                            for action in ("UPDATE", "DELETE"):
                                db.execute(
                                    f"CREATE TRIGGER immutable_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'append-only PAPER ledger'); END"
                                )
                        db.commit()
                    if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                        raise ValueError("unsupported PAPER schema")
                    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise ValueError("paper storage corruption")
                    meta = db.execute("SELECT value FROM metadata").fetchone()
                    if meta is None or meta[0] != canonical(META):
                        raise ValueError("PAPER schema/assumptions mismatch")
                    ids = [row[0] for row in db.execute("SELECT id FROM scenarios")]
                for scenario in ids:
                    self.state(scenario)
            except sqlite3.Error:
                raise ValueError("paper storage unavailable") from None

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        finally:
            db.close()

    def rebuild(self, scenario: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows or rows[0]["payload"] != {"kind": "create"}:
            raise ValueError("PAPER seed reconciliation")
        created = parse_time(rows[0]["received_at"])
        last_time = created - timedelta(seconds=2)
        sequence = 0
        mark_times = {s: last_time for s in SPECS}
        native = []
        for row in rows[1:]:
            e = dict(row["payload"])
            received = parse_time(row["received_at"])
            if e.get("kind") == "tick":
                if set(e) != {"kind", "instrument", "price", "sequence", "timestamp", "source"}:
                    raise ValueError("unsupported tick fields")
                if (
                    e["source"] != "LOCAL_SCENARIO_V1"
                    or type(e["sequence"]) is not int
                    or e["sequence"] != sequence + 1
                ):
                    raise ValueError("tick sequence/source")
                ts = parse_time(e["timestamp"])
                if ts <= last_time or ts > received or received - ts > timedelta(seconds=120):
                    raise ValueError("tick time future/stale/reversed")
                sequence = e.pop("sequence")
                e.pop("source")
                e.pop("timestamp")
                last_time = ts
                if e.get("instrument") not in mark_times:
                    raise ValueError("unsupported PAPER instrument")
                mark_times[e["instrument"]] = ts
            elif not (e.get("kind") == "protect" and e.get("sl") is None and e.get("tp") is None):
                symbol = e.get("instrument")
                if not isinstance(symbol, str) or symbol not in mark_times:
                    raise ValueError("unsupported PAPER instrument")
                if received - mark_times[symbol] > timedelta(seconds=120):
                    raise ValueError("stale local mark; add a new virtual tick")
            validate_event(e)
            native.append(e)
        state = replay(native)
        state.update(
            {
                "scenario_id": scenario,
                "created_at": created.isoformat(),
                "revision": len(rows),
                "latest_sequence": sequence,
                "last_tick_at": last_time.isoformat(),
                "mark_times": {s: t.isoformat() for s, t in mark_times.items()},
                "tick_source": "LOCAL_SCENARIO_V1",
                "synthetic": True,
            }
        )
        return state

    def verified(
        self, db: sqlite3.Connection, scenario: str
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        current = db.execute("SELECT * FROM scenarios WHERE id=?", (scenario,)).fetchone()
        if current is None:
            raise ValueError("PAPER scenario not found")
        events = []
        previous = "0" * 64
        commands = {
            r["seq"]: r for r in db.execute("SELECT * FROM commands WHERE scenario=?", (scenario,))
        }
        settlements = {
            r["seq"]: r["payload"]
            for r in db.execute("SELECT * FROM settlements WHERE scenario=?", (scenario,))
        }
        for index, row in enumerate(
            db.execute("SELECT * FROM events WHERE scenario=? ORDER BY seq", (scenario,)), 1
        ):
            cmd = commands.get(index)
            settle = settlements.get(index)
            if (
                row["seq"] != index
                or row["previous"] != previous
                or cmd is None
                or settle is None
                or cmd["payload"] != row["payload"]
            ):
                raise ValueError("PAPER audit reconciliation")
            expected = digest(
                [
                    previous,
                    row["payload"],
                    row["received_at"],
                    cmd["key"],
                    cmd["result_hash"],
                    settle,
                ]
            )
            if expected != row["hash"]:
                raise ValueError("PAPER audit hash reconciliation")
            previous = expected
            events.append(
                {"payload": json.loads(row["payload"]), "received_at": row["received_at"]}
            )
        if len(commands) != len(events) or len(settlements) != len(events):
            raise ValueError("PAPER audit count reconciliation")
        state = self.rebuild(scenario, events)
        persisted = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT payload FROM fills WHERE scenario=? ORDER BY idx", (scenario,)
            )
        ]
        if persisted != state["fills"] or current["snapshot"] != canonical(state):
            raise ValueError("PAPER snapshot/native fill reconciliation")
        history = [json.loads(settlements[n]) for n in range(1, len(events) + 1)]
        expected_orders = [change for settlement in history for change in settlement["orders"]]
        journal = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT payload FROM orders WHERE scenario=? ORDER BY idx", (scenario,)
            )
        ]
        latest_orders = {row["order"]["id"]: row["order"] for row in journal}
        if journal != expected_orders or latest_orders != {o["id"]: o for o in state["orders"]}:
            raise ValueError("PAPER native order audit reconciliation")
        if [f for settlement in history for f in settlement["fills"]] != persisted:
            raise ValueError("PAPER fill settlement reconciliation")
        if history[-1]["account"] != {k: state[k] for k in ACCOUNT_FIELDS}:
            raise ValueError("PAPER settlement reconciliation")
        return events, state

    def command(self, scenario: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        identifier(scenario)
        identifier(key)
        if not isinstance(payload, dict):
            raise TypeError("JSON command required")
        raw = canonical(payload)
        if len(raw) > 4096:
            raise ValueError("PAPER command too large")
        with _LOCK, self.db() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute("SELECT id FROM scenarios WHERE id=?", (scenario,)).fetchone()
                if existing:
                    rows, old = self.verified(db, scenario)
                    prior = db.execute(
                        "SELECT * FROM commands WHERE scenario=? AND key=?", (scenario, key)
                    ).fetchone()
                    if prior:
                        if prior["payload"] != raw:
                            raise ValueError("idempotency payload conflict")
                        result = self.rebuild(scenario, rows[: prior["seq"]])
                        if digest(result) != prior["result_hash"]:
                            raise ValueError("idempotency reconciliation")
                        db.rollback()
                        return result
                    if payload.get("kind") == "create":
                        raise ValueError("PAPER scenario exists; no reset/deposit")
                else:
                    if payload != {"kind": "create"}:
                        raise ValueError("PAPER scenario not found")
                    if db.execute("SELECT count(*) FROM scenarios").fetchone()[0] >= 16:
                        raise ValueError("PAPER scenario limit")
                    rows = []
                    old = {"fills": [], "orders": []}
                if len(rows) >= 500:
                    raise ValueError("PAPER event limit; use a separate scenario")
                received = clock().isoformat()
                candidate = rows + [{"payload": payload, "received_at": received}]
                result = self.rebuild(scenario, candidate)
                seq = len(candidate)
                # Every committed fill is a native output; old output must be an exact replay prefix.
                if result["fills"][: len(old["fills"])] != old["fills"]:
                    raise ValueError("native replay prefix reconciliation")
                old_orders = {o["id"]: o for o in old["orders"]}
                changes = [
                    {"event_seq": seq, "order": o}
                    for o in result["orders"]
                    if old_orders.get(o["id"]) != o
                ]
                settle = canonical(
                    {
                        "account": {k: result[k] for k in ACCOUNT_FIELDS},
                        "orders": changes,
                        "fills": result["fills"][len(old["fills"]) :],
                    }
                )
                result_hash = digest(result)
                prior_hash = db.execute(
                    "SELECT hash FROM events WHERE scenario=? ORDER BY seq DESC LIMIT 1",
                    (scenario,),
                ).fetchone()
                previous = prior_hash[0] if prior_hash else "0" * 64
                chain = digest([previous, raw, received, key, result_hash, settle])
                if not existing:
                    db.execute(
                        "INSERT INTO scenarios VALUES (?,?,?)",
                        (scenario, received, canonical(result)),
                    )
                else:
                    db.execute(
                        "UPDATE scenarios SET snapshot=? WHERE id=?", (canonical(result), scenario)
                    )
                db.execute(
                    "INSERT INTO events VALUES (?,?,?,?,?,?)",
                    (scenario, seq, raw, received, previous, chain),
                )
                db.execute(
                    "INSERT INTO commands VALUES (?,?,?,?,?)",
                    (scenario, key, raw, seq, result_hash),
                )
                db.execute("INSERT INTO settlements VALUES (?,?,?)", (scenario, seq, settle))
                for n, fill in enumerate(
                    result["fills"][len(old["fills"]) :], len(old["fills"]) + 1
                ):
                    db.execute("INSERT INTO fills VALUES (?,?,?)", (scenario, n, canonical(fill)))
                order_index = db.execute(
                    "SELECT count(*) FROM orders WHERE scenario=?", (scenario,)
                ).fetchone()[0]
                for n, change in enumerate(changes, order_index + 1):
                    db.execute(
                        "INSERT INTO orders VALUES (?,?,?)", (scenario, n, canonical(change))
                    )
                db.commit()
                return result
            except sqlite3.Error:
                db.rollback()
                raise ValueError("paper storage transaction failed") from None
            except Exception:
                db.rollback()
                raise

    def state(self, scenario: str) -> dict[str, Any]:
        identifier(scenario)
        with _LOCK, self.db() as db:
            try:
                db.execute("BEGIN")
                _, state = self.verified(db, scenario)
                return state
            except sqlite3.Error:
                raise ValueError("paper storage unavailable") from None

    def audit(self, scenario: str) -> dict[str, Any]:
        identifier(scenario)
        with _LOCK, self.db() as db:
            try:
                db.execute("BEGIN")
                self.verified(db, scenario)
                return {
                    "scenario_id": scenario,
                    "events": [
                        dict(r)
                        for r in db.execute(
                            "SELECT * FROM events WHERE scenario=? ORDER BY seq", (scenario,)
                        )
                    ],
                    "fills": [
                        dict(r)
                        for r in db.execute(
                            "SELECT * FROM fills WHERE scenario=? ORDER BY idx", (scenario,)
                        )
                    ],
                    "settlements": [
                        dict(r)
                        for r in db.execute(
                            "SELECT * FROM settlements WHERE scenario=? ORDER BY seq", (scenario,)
                        )
                    ],
                    "orders": [
                        dict(r)
                        for r in db.execute(
                            "SELECT * FROM orders WHERE scenario=? ORDER BY idx", (scenario,)
                        )
                    ],
                    "funding_entries": 1,
                    "append_only": True,
                    "synthetic": True,
                }
            except sqlite3.Error:
                raise ValueError("paper storage unavailable") from None

    def scenarios(self) -> list[str]:
        with _LOCK, self.db() as db:
            return [r[0] for r in db.execute("SELECT id FROM scenarios ORDER BY id")]
