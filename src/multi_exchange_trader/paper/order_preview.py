"""Durable review tokens bound to one synthetic ledger command and one confirmation key."""

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from .contract import FEE, VERSION, money, validate_event
from .store import PaperStore, canonical, digest, identifier, parse_time


class OrderReviews:
    def __init__(self, path: str | Path, paper: PaperStore,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.path, self.paper = Path(path), paper
        self.clock = clock or (lambda: datetime.now(UTC))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS order_reviews (
                    id TEXT PRIMARY KEY, scenario TEXT NOT NULL, payload TEXT NOT NULL,
                    confirmation_key TEXT, result TEXT, UNIQUE(scenario,confirmation_key));
            """)
            db.commit()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        finally:
            db.close()

    def preview(self, body: object) -> dict[str, Any]:
        fields = {"scenario_id", "product", "order_type", "config_revision", "command"}
        if not isinstance(body, dict) or set(body) != fields:
            raise ValueError("PAPER review fields")
        if (body["product"] != "SYNTHETIC_PERPETUAL" or body["order_type"] != "MARKET"
                or body["config_revision"] != VERSION):
            raise ValueError("PAPER product/order/config not qualified")
        scenario, command = body["scenario_id"], body["command"]
        identifier(scenario)
        if not isinstance(command, dict) or command.get("kind") not in {"enter", "close", "protect"}:
            raise ValueError("PAPER review command")
        validate_event(command)
        current, candidate = self.paper.preview_command(scenario, command)
        token = uuid4().hex
        review = {
            **body, "preview_id": token, "revision": current["revision"],
            "expires_at": (self.clock() + timedelta(seconds=60)).isoformat(),
            "source": "SYNTHETIC", "execution": "PAPER",
            "costs": {"currency": "USDT", "fee_rate": format(FEE, "f"),
                      "estimated_fee": money(Decimal(candidate["fees"]) - Decimal(current["fees"])),
                      "available_balance_after": candidate["available_balance"],
                      "reserved_margin_after": candidate["reserved_margin"]},
        }
        review["contract_hash"] = digest(review)
        with self.connect() as db:
            db.execute("INSERT INTO order_reviews VALUES (?,?,?,NULL,NULL)",
                       (token, scenario, canonical(review)))
            db.commit()
        return review

    def confirm(self, preview_id: str, idempotency_key: str) -> dict[str, Any]:
        identifier(preview_id)
        identifier(idempotency_key)
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM order_reviews WHERE id=?", (preview_id,)).fetchone()
                if row is None:
                    raise ValueError("PAPER review not found")
                if row["confirmation_key"] not in {None, idempotency_key}:
                    raise ValueError("PAPER review idempotency conflict")
                review = json.loads(row["payload"])
                contract_hash = review.pop("contract_hash")
                if contract_hash != digest(review):
                    raise ValueError("PAPER review storage reconciliation")
                if row["confirmation_key"] is None:
                    if self.clock() >= parse_time(review["expires_at"]):
                        raise ValueError("PAPER review expired")
                    db.execute("UPDATE order_reviews SET confirmation_key=? WHERE id=?",
                               (idempotency_key, preview_id))
                # Bind the stable ledger key before applying. A crash or lost response
                # recovers through PaperStore's immutable idempotency entry.
                db.commit()
        except sqlite3.IntegrityError:
            raise ValueError("PAPER review idempotency conflict") from None
        command_id = "review-" + preview_id
        result = self.paper.command(
            review["scenario_id"], command_id, review["command"],
            expected_revision=review["revision"], expires_at=parse_time(review["expires_at"]),
        )
        response = {"preview_id": preview_id, "command_id": command_id,
                    "idempotency_key": idempotency_key, "status": "confirmed", "state": result}
        with self.connect() as db:
            db.execute("UPDATE order_reviews SET result=? WHERE id=?", (canonical(response), preview_id))
            db.commit()
        return response

    def unresolved_count(self) -> int:
        with self.connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM order_reviews WHERE confirmation_key IS NOT NULL AND result IS NULL").fetchone()[0])
