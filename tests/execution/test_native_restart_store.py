"""Only temporary synthetic SQLite ledgers; never operator state."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from multi_exchange_trader.execution.protection import ProtectionScope


def api() -> Any:
    return importlib.import_module("multi_exchange_trader.execution.native_restart_store")


def identity() -> Any:
    return api().NativeRestartIdentity(ProtectionScope("fixture", "DEMO"), "BYBIT-UNIFIED",
        "TRADER-001", "Strategy-000", ("BTCUSDT-LINEAR.BYBIT",))


def payload() -> dict[str, object]:
    return {"schema": 1, "encoding": "msgpack-base64", "instruments": [],
        "orders": [], "positions": []}


def test_seal_roundtrip_consume_and_no_reuse(tmp_path: Path) -> None:
    store = api().NativeRestartStore(tmp_path)
    assert store.load(identity()) is None
    generation = store.seal(identity(), payload())
    assert store.load(identity()) == generation
    store.mark_active(identity(), generation)
    for action in (lambda: store.load(identity()),
                   lambda: store.mark_active(identity(), generation),
                   lambda: store.mark_active(identity(), None)):
        with pytest.raises(api().NativeRestartError, match="^NATIVE_RESTART_BLOCKED$"):
            action()
    assert store.seal(identity(), payload()).generation_id != generation.generation_id


@pytest.mark.parametrize("ledger", ["execution.sqlite3", "runtime-state.sqlite3", "history.sqlite3"])
@pytest.mark.parametrize("table", ["commands", "reservations", "audit", "inbox", "history"])
def test_digest_binds_all_logical_rows_not_wal_layout(
    tmp_path: Path, ledger: str, table: str,
) -> None:
    with sqlite3.connect(tmp_path / ledger) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(f"CREATE TABLE {table} (id INTEGER, value BLOB)")
        connection.execute(f"INSERT INTO {table} VALUES (1, ?)", (b"fixture",))
        connection.commit()
        before = api().application_digest(tmp_path)
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert api().application_digest(tmp_path) == before
        connection.execute(f"UPDATE {table} SET value = ?", (b"changed",))
        connection.commit()
        assert api().application_digest(tmp_path) != before


def test_identity_digest_and_replacement_cas(tmp_path: Path) -> None:
    store = api().NativeRestartStore(tmp_path)
    first = store.seal(identity(), payload())
    with pytest.raises(api().NativeRestartError):
        store.load(replace(identity(), trader_id="OTHER-001"))
    with sqlite3.connect(tmp_path / "history.sqlite3") as connection:
        connection.execute("CREATE TABLE history (value TEXT)")
    with pytest.raises(api().NativeRestartError):
        store.load(identity())
    with pytest.raises(api().NativeRestartError):
        store.mark_active(identity(), first)


@pytest.mark.parametrize("raw", [b"{", b'{"schema":1,"schema":1}', b'{"x":NaN}',
    b" " * (16 * 1024 * 1024 + 1)])
def test_malformed_duplicate_nonfinite_oversized_rejected(tmp_path: Path, raw: bytes) -> None:
    (tmp_path / "native-restart.json").write_bytes(raw)
    with pytest.raises(api().NativeRestartError, match="^NATIVE_RESTART_BLOCKED$"):
        api().NativeRestartStore(tmp_path).load(identity())


def test_replacement_and_publication_failure_leave_no_valid_new_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = api().NativeRestartStore(tmp_path)
    store.mark_active(identity(), None)
    def fail(*args: object) -> None:
        raise OSError("SECRET")
    monkeypatch.setattr(api().os, "replace", fail)
    with pytest.raises(api().NativeRestartError, match="^NATIVE_RESTART_BLOCKED$"):
        store.seal(identity(), payload())
    with pytest.raises(api().NativeRestartError):
        store.load(identity())
    assert not list(tmp_path.glob("*.tmp"))


def test_stale_generation_and_unknown_fields_rejected(tmp_path: Path) -> None:
    store = api().NativeRestartStore(tmp_path)
    old = store.seal(identity(), payload())
    store.mark_active(identity(), old)
    store.seal(identity(), payload())
    with pytest.raises(api().NativeRestartError):
        store.mark_active(identity(), old)
    path = tmp_path / "native-restart.json"
    document = json.loads(path.read_bytes())
    document["unexpected"] = "SECRET"
    path.write_text(json.dumps(document))
    with pytest.raises(api().NativeRestartError):
        store.load(identity())


def test_hardlinks_rejected_for_generation_and_databases(tmp_path: Path) -> None:
    first, other = tmp_path / "native-restart.json", tmp_path / "copy"
    first.write_text("{}")
    os.link(first, other)
    with pytest.raises(api().NativeRestartError):
        api().NativeRestartStore(tmp_path).load(identity())
    first.unlink()
    with sqlite3.connect(tmp_path / "execution.sqlite3") as connection:
        connection.execute("CREATE TABLE commands (id INTEGER)")
    os.link(tmp_path / "execution.sqlite3", tmp_path / "linked-db")
    with pytest.raises(api().NativeRestartError):
        api().application_digest(tmp_path)


@pytest.mark.parametrize("key", ["instruments", "orders", "positions", "events"])
def test_bounded_native_inventory_before_publication(tmp_path: Path, key: str) -> None:
    value = payload()
    if key == "events":
        value["orders"] = [{"events": ["invalid"] * 100_001}]
    else:
        value[key] = [{}] * 2_001
    with pytest.raises(api().NativeRestartError):
        api().NativeRestartStore(tmp_path).seal(identity(), value)
    assert not (tmp_path / "native-restart.json").exists()


def test_failed_replace_after_write_invalidates_new_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = api().NativeRestartStore(tmp_path)
    store.mark_active(identity(), None)
    original = api().os.replace
    def fail_after_replace(source: Any, destination: Any) -> None:
        original(source, destination)
        raise OSError("SECRET")
    monkeypatch.setattr(api().os, "replace", fail_after_replace)
    with pytest.raises(api().NativeRestartError):
        store.seal(identity(), payload())
    assert store.load(identity()) is None


def test_schema_and_types_are_bound_even_with_reserved_table_names(tmp_path: Path) -> None:
    with sqlite3.connect(tmp_path / "execution.sqlite3") as connection:
        connection.execute("CREATE TABLE schema (value)")
        connection.execute("INSERT INTO schema VALUES (1)")
        connection.commit()
        first = api().application_digest(tmp_path)
        connection.execute("UPDATE schema SET value = '1'")
        connection.commit()
        second = api().application_digest(tmp_path)
        assert first != second
        connection.execute("CREATE INDEX fixture_idx ON schema (value)")
        connection.commit()
        assert api().application_digest(tmp_path) != second


def test_traversal_and_linked_ancestor_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(api().NativeRestartError):
        api().application_digest(tmp_path / "child" / "..")
    original = Path.lstat
    def linked(path: Path) -> Any:
        value = original(path)
        if path == tmp_path:
            from types import SimpleNamespace
            return SimpleNamespace(st_mode=value.st_mode, st_nlink=value.st_nlink,
                                   st_file_attributes=0x400)
        return value
    monkeypatch.setattr(Path, "lstat", linked)
    with pytest.raises(api().NativeRestartError):
        api().NativeRestartStore(tmp_path).load(identity())


@pytest.mark.parametrize("inventory", ["orders", "positions"])
@pytest.mark.parametrize("defect", ["missing", "unknown", "empty-events", "wrong-events",
    "wrong-event", "empty-event", "invalid-base64", "wrong-state", "empty-state"])
def test_exact_row_schema_rejected_by_seal_load_and_pure_validator(
    tmp_path: Path, inventory: str, defect: str,
) -> None:
    row: dict[str, object] = {"events": ["eA=="], "state": {"type": "fixture"}}
    if inventory == "orders":
        row.update(position_id=None, client_id=None)
    if defect == "missing":
        row.pop("state")
    elif defect == "unknown":
        row["unexpected"] = "fixture"
    elif defect == "empty-events":
        row["events"] = []
    elif defect == "wrong-events":
        row["events"] = "eA=="
    elif defect == "wrong-event":
        row["events"] = [12]
    elif defect == "empty-event":
        row["events"] = [""]
    elif defect == "invalid-base64":
        row["events"] = ["???"]
    elif defect == "wrong-state":
        row["state"] = []
    elif defect == "empty-state":
        row["state"] = {}
    invalid = payload()
    invalid[inventory] = [row]
    store = api().NativeRestartStore(tmp_path)
    # Build a valid envelope first, then recompute its hash around the malformed
    # native row. Refusal must be schema-based rather than corruption-based.
    store.seal(identity(), payload())
    document = json.loads((tmp_path / "native-restart.json").read_bytes())
    document["payload"] = invalid
    document.pop("content_hash")
    document["content_hash"] = hashlib.sha256(api().canonical_bytes(document)).hexdigest()
    (tmp_path / "native-restart.json").write_bytes(api().canonical_bytes(document))
    with pytest.raises(api().NativeRestartError, match="^NATIVE_RESTART_BLOCKED$"):
        store.load(identity())
    with pytest.raises(api().NativeRestartError, match="^NATIVE_RESTART_BLOCKED$"):
        api().validate_generation_document(document)
    (tmp_path / "native-restart.json").unlink()
    with pytest.raises(api().NativeRestartError, match="^NATIVE_RESTART_BLOCKED$"):
        store.seal(identity(), invalid)
    assert not (tmp_path / "native-restart.json").exists()


@pytest.mark.parametrize("field,value", [("client_id", 1), ("client_id", ""),
    ("position_id", []), ("position_id", ""), ("position_id", "x" * 257)])
def test_row_links_are_nullable_bounded_text(tmp_path: Path, field: str, value: object) -> None:
    row = {"events": ["eA=="], "state": {"type": "fixture"}, "position_id": None, "client_id": None}
    row[field] = value
    invalid = payload()
    invalid["orders"] = [row]
    with pytest.raises(api().NativeRestartError):
        api().NativeRestartStore(tmp_path).seal(identity(), invalid)


@pytest.mark.parametrize("value", [1, {}, "", "???"])
def test_instrument_rows_require_nonempty_encoded_blob(tmp_path: Path, value: object) -> None:
    invalid = payload()
    invalid["instruments"] = [value]
    with pytest.raises(api().NativeRestartError):
        api().NativeRestartStore(tmp_path).seal(identity(), invalid)
