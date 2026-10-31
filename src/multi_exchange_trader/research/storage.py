"""Single-DB atomic research/session journal over pure, isolated native replay."""

import copy
import json
import re
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..paper.sandbox import sandbox_replay
from .automation import TERMINAL, advance, default_auto_config, flatten, validate_config
from .data import canonical, checksum, load_dataset, save_dataset, synthetic_dataset
from .sandbox import sandbox_evaluate

__all__ = ["ResearchStore", "default_auto_config"]

LOCK = threading.RLock()
META = {
    "schema": 1,
    "engine": "1.231.0",
    "automation": "SYNTHETIC_AUTO_V1",
    "event_limit": 500,
    "scenario_limit": 16,
}


def now() -> datetime:
    return datetime.now(UTC)


def identifier(value: Any) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value):
        raise ValueError("Phase4 identifier")


class ResearchStore:
    def __init__(
        self, path: Path, data_root: Path, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.path, self.data_root = Path(path), Path(data_root)
        self.clock = clock or now
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.verified: set[str] = set()
        for symbol in ("BTC-PAPER", "ETH-PAPER"):
            save_dataset(self.data_root, synthetic_dataset(symbol))
        new = not self.path.exists()
        with LOCK, self.db() as db:
            try:
                if new:
                    db.executescript("""
                    PRAGMA journal_mode=WAL;
                    CREATE TABLE metadata(value TEXT NOT NULL);
                    CREATE TABLE runs(id TEXT PRIMARY KEY,snapshot TEXT NOT NULL);
                    CREATE TABLE journal(run TEXT NOT NULL,seq INTEGER NOT NULL,operation TEXT NOT NULL,snapshot TEXT NOT NULL,previous TEXT NOT NULL,hash TEXT NOT NULL,PRIMARY KEY(run,seq));
                    CREATE TABLE commands(run TEXT NOT NULL,key TEXT NOT NULL,payload TEXT NOT NULL,response TEXT NOT NULL,PRIMARY KEY(run,key));
                    CREATE TABLE reports(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
                    CREATE TABLE report_commands(key TEXT PRIMARY KEY,dataset TEXT NOT NULL,report TEXT NOT NULL);
                    PRAGMA user_version=1;
                    """)
                    db.execute("INSERT INTO metadata VALUES (?)", (canonical(META),))
                    for table in ("journal", "commands", "reports", "report_commands"):
                        for action in ("UPDATE", "DELETE"):
                            db.execute(
                                f"CREATE TRIGGER immutable_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'append-only Phase4 audit'); END"
                            )
                    db.commit()
                if (
                    db.execute("PRAGMA user_version").fetchone()[0] != 1
                    or db.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                ):
                    raise ValueError("Phase4 schema/storage corruption")
                meta = db.execute("SELECT value FROM metadata").fetchone()
                if not meta or meta[0] != canonical(META):
                    raise ValueError("Phase4 metadata mismatch")
                db.execute("BEGIN IMMEDIATE")
                for row in db.execute("SELECT id FROM runs").fetchall():
                    state = self.verify(db, row["id"], native=True)
                    if state["status"] == "RUNNING":
                        state.update(
                            status="PAUSED_RECOVERY",
                            reason="process_restart_requires_explicit_resume",
                        )
                        self.record(db, state, "recovery-paused")
                for row in db.execute("SELECT * FROM reports"):
                    report = json.loads(row["payload"])
                    self.check_report(report, row["id"])
                db.commit()
            except sqlite3.Error:
                db.rollback()
                raise ValueError("Phase4 storage unavailable") from None

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
        finally:
            db.close()

    def dataset(self, key: str) -> dict[str, Any]:
        return load_dataset(self.data_root, key)

    def datasets(self) -> list[dict[str, Any]]:
        results = []
        for p in sorted(self.data_root.glob("*.json")):
            d = self.dataset(p.stem)
            results.append(
                {k: v for k, v in d.items() if k not in ("bars", "raw_body")}
                | {
                    "bar_count": len(d["bars"]),
                    "quality": "PASS_CLOSED_CONTIGUOUS_CHECKSUM",
                    "sample_warning": "짧은 기능 검증 표본 · 수익성/실전 검증 아님",
                    "portfolio_scope": "independent_single_instrument",
                    "shared_portfolio_status": "UNRUN",
                }
            )
        return results

    def record(self, db: sqlite3.Connection, state: dict[str, Any], operation: str) -> None:
        raw = canonical(state)
        previous = db.execute(
            "SELECT seq,hash FROM journal WHERE run=? ORDER BY seq DESC LIMIT 1", (state["run_id"],)
        ).fetchone()
        seq = previous["seq"] + 1 if previous else 1
        prior = previous["hash"] if previous else "0" * 64
        chain = checksum([prior, operation, raw])
        db.execute(
            "INSERT INTO journal VALUES (?,?,?,?,?,?)",
            (state["run_id"], seq, operation, raw, prior, chain),
        )
        db.execute(
            "INSERT INTO runs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET snapshot=excluded.snapshot",
            (state["run_id"], raw),
        )
        self.verified.add(checksum(state))

    def verify(self, db: sqlite3.Connection, run: str, *, native: bool = False) -> dict[str, Any]:
        row = db.execute("SELECT snapshot FROM runs WHERE id=?", (run,)).fetchone()
        if row is None:
            raise ValueError("Phase4 run not found")
        previous = "0" * 64
        last = None
        for index, event in enumerate(
            db.execute("SELECT * FROM journal WHERE run=? ORDER BY seq", (run,)), 1
        ):
            if (
                event["seq"] != index
                or event["previous"] != previous
                or event["hash"] != checksum([previous, event["operation"], event["snapshot"]])
            ):
                raise ValueError("Phase4 journal reconciliation")
            previous = event["hash"]
            last = event["snapshot"]
        if last != row["snapshot"]:
            raise ValueError("Phase4 snapshot reconciliation")
        state: dict[str, Any] = json.loads(row["snapshot"])
        if state["run_id"] != run or len(state["native_events"]) + 1 > 500:
            raise ValueError("Phase4 run/event identity")
        validate_config(state["config"], self.dataset(state["dataset_id"]))
        digest = checksum(state)
        if native or digest not in self.verified:
            if sandbox_replay(state["native_events"]) != state["native"]:
                raise ValueError("Phase4 native reconciliation")
            self.verified.add(digest)
        return state

    def state(self, run: str) -> dict[str, Any]:
        identifier(run)
        with LOCK, self.db() as db:
            try:
                return self.verify(db, run)
            except (sqlite3.Error, KeyError, TypeError):
                raise ValueError("Phase4 state failed closed") from None

    def runs(self) -> list[dict[str, Any]]:
        with LOCK, self.db() as db:
            return [
                self.verify(db, row["id"])
                for row in db.execute("SELECT id FROM runs ORDER BY id").fetchall()
            ]

    def prior(
        self, db: sqlite3.Connection, run: str, key: str, payload: str
    ) -> dict[str, Any] | None:
        old = db.execute("SELECT * FROM commands WHERE run=? AND key=?", (run, key)).fetchone()
        if not old:
            return None
        if old["payload"] != payload:
            raise ValueError("Phase4 idempotency conflict")
        action = json.loads(payload)["action"]
        operation = action + ":" + checksum([key, payload])
        if not db.execute(
            "SELECT 1 FROM journal WHERE run=? AND operation=? AND snapshot=?",
            (run, operation, old["response"]),
        ).fetchone():
            raise ValueError("Phase4 command response reconciliation")
        response: dict[str, Any] = json.loads(old["response"])
        return response

    def create(self, run: str, key: str, config: dict[str, Any]) -> dict[str, Any]:
        identifier(run)
        identifier(key)
        validate_config(config, self.dataset(config.get("dataset_id", "")))
        payload = canonical({"action": "create", "config": config})
        with LOCK, self.db() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                prior = self.prior(db, run, key, payload)
                if prior is not None:
                    self.verify(db, run)
                    return prior
                if db.execute("SELECT id FROM runs WHERE id=?", (run,)).fetchone():
                    raise ValueError("Phase4 run exists; no reset")
                if db.execute("SELECT count(*) FROM runs").fetchone()[0] >= 16:
                    raise ValueError("Phase4 session limit16")
                state = {
                    "run_id": run,
                    "dataset_id": config["dataset_id"],
                    "synthetic": True,
                    "mode": "PAPER_AUTO_SYNTHETIC",
                    "config": config,
                    "status": "PAUSED",
                    "reason": "created_requires_explicit_start",
                    "cursor": 0,
                    "native_events": [],
                    "native": sandbox_replay([]),
                    "high_water": "10000.00000000",
                    "last_closed_net": "0.00000000",
                    "loss_streak": 0,
                    "processed_closed_trades": 0,
                    "exposure": "0",
                    "last_virtual_time_ms": None,
                    "next_due_at": now().isoformat(),
                    "created_at": now().isoformat(),
                    "account_scope": "independent_synthetic_account",
                    "funding_entries": 1,
                }
                self.record(db, state, "create:" + checksum([key, payload]))
                db.execute(
                    "INSERT INTO commands VALUES (?,?,?,?)", (run, key, payload, canonical(state))
                )
                db.commit()
                return state
            except sqlite3.Error:
                db.rollback()
                raise ValueError("Phase4 storage transaction failed") from None

    def command(self, run: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        identifier(run)
        identifier(key)
        if set(payload) != {"action"} or payload["action"] not in ("resume", "pause", "stop"):
            raise ValueError("Phase4 fixed action required")
        raw = canonical(payload)
        with LOCK, self.db() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                state = self.verify(db, run)
                prior = self.prior(db, run, key, raw)
                if prior is not None:
                    return prior
                action = payload["action"]
                if state["status"] in TERMINAL:
                    if action != "stop":
                        raise ValueError("Phase4 terminal run cannot resume")
                elif action == "stop":
                    flatten(state)
                    state.update(status="STOPPED", reason="operator_stop")
                elif action == "pause":
                    state.update(status="PAUSED", reason="operator_pause")
                elif state["status"] != "RUNNING":
                    state.update(
                        status="RUNNING", reason="explicit_resume", next_due_at=now().isoformat()
                    )
                self.record(db, state, action + ":" + checksum([key, raw]))
                db.execute(
                    "INSERT INTO commands VALUES (?,?,?,?)", (run, key, raw, canonical(state))
                )
                db.commit()
                return state
            except sqlite3.Error:
                db.rollback()
                raise ValueError("Phase4 storage transaction failed") from None

    def step(self, run: str, expected_cursor: int, *, now: datetime) -> dict[str, Any]:
        identifier(run)
        with LOCK, self.db() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                state = self.verify(db, run)
                if (
                    state["status"] != "RUNNING"
                    or state["cursor"] != expected_cursor
                    or now < datetime.fromisoformat(state["next_due_at"])
                ):
                    return state
                candidate = advance(copy.deepcopy(state), self.dataset(state["dataset_id"]))
                candidate["next_due_at"] = (
                    max(now, self.clock()) + timedelta(seconds=1)
                ).isoformat()
                self.record(db, candidate, "step:" + str(expected_cursor))
                db.commit()
                return candidate
            except sqlite3.Error:
                db.rollback()
                raise ValueError("Phase4 storage transaction failed") from None

    def pause_after_failure(self, run: str) -> None:
        """No retry/order: preserve committed native state and distinguish system failure."""
        identifier(run)
        with LOCK, self.db() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                state = self.verify(db, run)
                if state["status"] in TERMINAL:
                    return
                state.update(status="PAUSED_ERROR", reason="native_or_storage_failure")
                self.record(db, state, "native-failure-paused")
                db.commit()
            except sqlite3.Error:
                db.rollback()
                raise ValueError("Phase4 storage transaction failed") from None

    def audit(self, run: str) -> dict[str, Any]:
        identifier(run)
        with LOCK, self.db() as db:
            state = self.verify(db, run)
            return {
                "run_id": run,
                "funding_entries": 1,
                "native_fills": state["native"]["fills"],
                "native_event_count": len(state["native_events"]),
                "journal": [
                    {
                        "sequence": r["seq"],
                        "operation": r["operation"],
                        "previous": r["previous"],
                        "hash": r["hash"],
                        "snapshot_digest": checksum(json.loads(r["snapshot"])),
                    }
                    for r in db.execute("SELECT * FROM journal WHERE run=? ORDER BY seq", (run,))
                ],
                "command_digests": [
                    checksum(json.loads(r["payload"]))
                    for r in db.execute(
                        "SELECT payload FROM commands WHERE run=? ORDER BY key", (run,)
                    )
                ],
                "scope": "No raw idempotency keys in public audit summary; digests summarize actual command payloads.",
            }

    def check_report(self, report: dict[str, Any], key: str) -> None:
        if (
            report.get("reproducibility_hash") != key
            or checksum({k: v for k, v in report.items() if k != "reproducibility_hash"}) != key
        ):
            raise ValueError("Phase4 report checksum")
        self.dataset(report["dataset_id"])

    def evaluate(self, key: str, dataset_id: str) -> dict[str, Any]:
        identifier(key)
        dataset = self.dataset(dataset_id)
        with LOCK, self.db() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                old = db.execute("SELECT * FROM report_commands WHERE key=?", (key,)).fetchone()
                if old:
                    if old["dataset"] != dataset_id:
                        raise ValueError("Phase4 idempotency conflict")
                    result: dict[str, Any] = json.loads(
                        db.execute(
                            "SELECT payload FROM reports WHERE id=?", (old["report"],)
                        ).fetchone()[0]
                    )
                    self.check_report(result, old["report"])
                    return result
                if db.execute("SELECT count(*) FROM reports").fetchone()[0] >= 32:
                    raise ValueError("Phase4 report limit")
                report = sandbox_evaluate(dataset)
                rid = report["reproducibility_hash"]
                self.check_report(report, rid)
                db.execute("INSERT OR IGNORE INTO reports VALUES (?,?)", (rid, canonical(report)))
                db.execute("INSERT INTO report_commands VALUES (?,?,?)", (key, dataset_id, rid))
                db.commit()
                return report
            except sqlite3.Error:
                db.rollback()
                raise ValueError("Phase4 storage transaction failed") from None

    def reports(self) -> list[dict[str, Any]]:
        with LOCK, self.db() as db:
            results = []
            for row in db.execute("SELECT * FROM reports ORDER BY rowid DESC"):
                report = json.loads(row["payload"])
                self.check_report(report, row["id"])
                results.append(
                    {
                        "report_id": row["id"],
                        "dataset_id": report["dataset_id"],
                        "symbol": report["symbol"],
                        "market": report["market"],
                        "synthetic": report["synthetic"],
                        "performance_status": report["performance_status"],
                    }
                )
            return results

    def report(self, key: str) -> dict[str, Any]:
        if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
            raise ValueError("Phase4 report ID")
        with LOCK, self.db() as db:
            row = db.execute("SELECT payload FROM reports WHERE id=?", (key,)).fetchone()
            if row is None:
                raise ValueError("Phase4 report not found")
            result: dict[str, Any] = json.loads(row["payload"])
            self.check_report(result, key)
            return result
