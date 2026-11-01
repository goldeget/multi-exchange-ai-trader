"""Local recommendation cards. This file does not talk to an exchange."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .rules import Plan, plan_id


def _object(raw: str) -> dict[str, Any]:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise TypeError("추천 저장이 손상되었습니다")
    return cast(dict[str, Any], parsed)


def _commands(raw: str | None) -> list[dict[str, Any]] | None:
    if raw is None:
        return None
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise TypeError("추천 저장이 손상되었습니다")
    commands: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise TypeError("추천 저장이 손상되었습니다")
        commands.append(cast(dict[str, Any], item))
    return commands


class RecommendationBook:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS cards (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    commands TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            db.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    def _row(self, db: sqlite3.Connection, card_id: str) -> dict[str, Any] | None:
        row = db.execute(
            "SELECT id, status, payload, commands, updated_at FROM cards WHERE id=?",
            (card_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "status": str(row["status"]),
            "payload": _object(str(row["payload"])),
            "commands": _commands(None if row["commands"] is None else str(row["commands"])),
            "updated_at": str(row["updated_at"]),
        }

    def get(self, card_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            return self._row(db, card_id)

    def upsert(self, plan: Plan) -> dict[str, Any]:
        card_id = plan_id(plan)
        payload = json.dumps(plan.payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        stamp = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as db:
            existing = self._row(db, card_id)
            if existing is not None:
                return existing
            db.execute(
                "INSERT INTO cards VALUES (?,?,?,?,?)",
                (card_id, "pending", payload, None, stamp),
            )
            db.commit()
            stored = self._row(db, card_id)
        if stored is None:
            raise ValueError("추천 저장을 확인하지 못했습니다")
        return stored

    def expire_if_needed(self, card_id: str, now: datetime) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            current = self._row(db, card_id)
            if current is None:
                raise ValueError("추천을 찾지 못했습니다")
            expires = datetime.fromisoformat(str(current["payload"]["expires_at"]))
            if current["status"] == "pending" and now >= expires:
                db.execute(
                    "UPDATE cards SET status=?, updated_at=? WHERE id=? AND status='pending'",
                    ("expired", datetime.now(UTC).isoformat(), card_id),
                )
                db.commit()
                current = self._row(db, card_id)
        if current is None:
            raise ValueError("추천을 찾지 못했습니다")
        return current

    def begin(self, card_id: str, commands: list[dict[str, Any]]) -> dict[str, Any]:
        raw = json.dumps(commands, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        with self._lock, self._connect() as db:
            updated = db.execute(
                """
                UPDATE cards SET status='approving', commands=?, updated_at=?
                WHERE id=? AND status='pending'
                """,
                (raw, datetime.now(UTC).isoformat(), card_id),
            )
            if updated.rowcount != 1:
                current = self._row(db, card_id)
                db.commit()
                if current is None:
                    raise ValueError("추천을 찾지 못했습니다")
                return current
            db.commit()
            current = self._row(db, card_id)
        if current is None:
            raise ValueError("추천을 찾지 못했습니다")
        return current

    def mark(self, card_id: str, status: str) -> dict[str, Any]:
        if status not in ("pending", "rejected", "expired", "approved"):
            raise ValueError("추천 상태를 확인하지 못했습니다")
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE cards SET status=?, updated_at=? WHERE id=?",
                (status, datetime.now(UTC).isoformat(), card_id),
            )
            db.commit()
            current = self._row(db, card_id)
        if current is None:
            raise ValueError("추천을 찾지 못했습니다")
        return current

    def remember_flag(self, card_id: str, name: str) -> dict[str, Any]:
        if name not in ("demo_round_trip", "demo_close_failed"):
            raise ValueError("추천 상태를 확인하지 못했습니다")
        with self._lock, self._connect() as db:
            current = self._row(db, card_id)
            if current is None:
                raise ValueError("추천을 찾지 못했습니다")
            payload = dict(current["payload"])
            payload[name] = True
            raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            db.execute(
                "UPDATE cards SET payload=?, updated_at=? WHERE id=?",
                (raw, datetime.now(UTC).isoformat(), card_id),
            )
            db.commit()
            stored = self._row(db, card_id)
        if stored is None:
            raise ValueError("추천을 찾지 못했습니다")
        return stored
