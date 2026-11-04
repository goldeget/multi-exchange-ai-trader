"""Stage quiescent AI ledger pairs without changing originals or spending settings.

This offline tool does not publish into a running application. The operator must
stop all users of the canonical pair and later publish both staged files together.
No environment variable, key store, provider client, prompt or generation is used.
The manifest is required for a complete staging result; it contains hashes, never
request text. Application callers must still use the same canonical BudgetBook
and dispatch path: this tool does not install that runtime integration.
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from multi_exchange_trader.ai.budget import BudgetBook, actual_amount, amount
from multi_exchange_trader.ai.dispatch import AIDispatcher

if TYPE_CHECKING:
    from multi_exchange_trader.ai.generation import OpenRouterGenerationClient

Cell = str | int | None
Row = tuple[Cell, ...]
Pair = tuple[str, Path, Path]
_BUDGET_COLUMNS = ("request_id", "period", "maximum", "actual", "status", "updated_at")
_DISPATCH_STATUSES = {"CLAIMED", "DISPATCHING", "CHARGED", "SETTLED", "UNCERTAIN", "BLOCKED"}
_MAX_BYTES = 64 * 1024 * 1024


class AIHistoryImportError(ValueError):
    """Only fixed local codes, never SQLite row values, paths or exception text."""


class _NoGeneration:
    def generate(self, *_args: object, **_kwargs: object) -> None:
        raise AIHistoryImportError("OFFLINE_GENERATION_FORBIDDEN")


def _hash_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _pin(path: Path) -> str:
    if not path.is_file() or path.stat().st_size > _MAX_BYTES:
        raise AIHistoryImportError("SQLITE_INPUT_REQUIRED")
    for suffix in ("-wal", "-journal"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise AIHistoryImportError("QUIESCENT_SQLITE_REQUIRED")
    with path.open("rb") as stream:
        if stream.read(16) != b"SQLite format 3\x00":
            raise AIHistoryImportError("SQLITE_INPUT_REQUIRED")
    return _hash_file(path)


def _readonly(path: Path) -> sqlite3.Connection:
    # Nonempty WAL/journal is rejected before every open; immutable prevents
    # SQLite from creating SHM/journals next to any original input.
    _pin(path)
    db = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    db.execute("PRAGMA query_only=ON")
    return db


def _objects(db: sqlite3.Connection) -> set[tuple[str, str]]:
    return set(db.execute("SELECT type,name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"))


def _backup(source: Path, target: Path, *, budget: bool) -> None:
    with closing(_readonly(source)) as original, closing(sqlite3.connect(target)) as copied:
        if original.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise AIHistoryImportError("INVALID_LEDGER")
        expected = {("table", "ai_settings"), ("table", "reservations")} if budget else {
            ("table", "ai_dispatches")}
        if _objects(original) != expected:
            raise AIHistoryImportError("INVALID_LEDGER")
        if budget and (original.execute("PRAGMA user_version").fetchone()[0] != 0
                or tuple(row[1] for row in original.execute("PRAGMA table_info(reservations)"))
                != _BUDGET_COLUMNS
                or tuple(row[1] for row in original.execute("PRAGMA table_info(ai_settings)"))
                != ("id", "revision", "monthly_limit")
                or original.execute("SELECT count(*) FROM ai_settings WHERE id=1").fetchone()[0] != 1
                or original.execute("SELECT count(*) FROM ai_settings").fetchone()[0] != 1):
            raise AIHistoryImportError("INVALID_LEDGER")
        original.backup(copied)
        copied.execute("PRAGMA journal_mode=DELETE")


def _copy_pair(budget: Path, dispatch: Path, root: Path, *, allow_missing_dispatch: bool) -> tuple[Path, Path]:
    root.mkdir()
    copy_budget, copy_dispatch = root / "budget.sqlite3", root / "dispatch.sqlite3"
    _backup(budget, copy_budget, budget=True)
    if dispatch.exists():
        _backup(dispatch, copy_dispatch, budget=False)
    elif not allow_missing_dispatch:
        raise AIHistoryImportError("SQLITE_INPUT_REQUIRED")
    # These original constructors only inspect/migrate copies. No real generation
    # client is constructed; dispatcher construction itself never calls a provider.
    book = BudgetBook(copy_budget)
    AIDispatcher(copy_dispatch, book, cast("OpenRouterGenerationClient", _NoGeneration()))
    for path in (copy_budget, copy_dispatch):
        with closing(sqlite3.connect(path)) as db:
            db.execute("PRAGMA journal_mode=DELETE")
    return copy_budget, copy_dispatch


def _read_rows(path: Path, table: str) -> list[Row]:
    if table not in {"reservations", "ai_dispatches"}:
        raise AIHistoryImportError("INVALID_LEDGER")
    with closing(_readonly(path)) as db:
        result = db.execute('SELECT * FROM "' + table + '" ORDER BY request_id').fetchall()
    if len(result) > 100_000 or any(type(cell) not in {str, int, type(None)} for row in result for cell in row):
        raise AIHistoryImportError("INVALID_LEDGER")
    return cast(list[Row], result)


def _row_hash(table: str, row: Row) -> str:
    return hashlib.sha256(json.dumps([table, list(row)], ensure_ascii=True,
        separators=(",", ":")).encode("ascii")).hexdigest()


def _check_links(budgets: dict[str, Row], dispatches: dict[str, Row]) -> None:
    for identifier, row in dispatches.items():
        maximum, status, cost = row[2], row[3], row[5]
        if status not in _DISPATCH_STATUSES or amount(maximum) <= 0:
            raise AIHistoryImportError("INVALID_LEDGER")
        if cost is not None:
            actual_amount(cost)
        if status in {"BLOCKED", "CLAIMED"}:
            continue  # A crash/blocked admission may legitimately have no reservation.
        reservation = budgets.get("dispatch_" + hashlib.sha256(identifier.encode()).hexdigest())
        if reservation is None or amount(reservation[2]) != amount(maximum):
            raise AIHistoryImportError("CROSS_LEDGER_MISMATCH")
        if status == "SETTLED" and (cost is None or reservation[4] != "settled"
                or actual_amount(reservation[3]) != actual_amount(cost)):
            raise AIHistoryImportError("CROSS_LEDGER_MISMATCH")
        if status == "CHARGED" and (cost is None or reservation[4] == "settled"
                and actual_amount(reservation[3]) != actual_amount(cost)):
            raise AIHistoryImportError("CROSS_LEDGER_MISMATCH")
        if status in {"UNCERTAIN", "DISPATCHING"} and reservation[4] not in {"uncertain", "reserved"}:
            raise AIHistoryImportError("CROSS_LEDGER_MISMATCH")


def _merge_rows(target_budget: Path, target_dispatch: Path,
                copies: Sequence[tuple[str, Path, Path]]) -> list[dict[str, object]]:
    merged: dict[str, dict[str, Row]] = {"reservations": {}, "ai_dispatches": {}}
    provenance: dict[tuple[str, str], set[str]] = {}
    for system, budget, dispatch in copies:
        for table, path in (("reservations", budget), ("ai_dispatches", dispatch)):
            for row in _read_rows(path, table):
                identifier = row[0]
                if type(identifier) is not str or not identifier:
                    raise AIHistoryImportError("INVALID_LEDGER")
                prior = merged[table].get(identifier)
                if prior is not None and prior != row:
                    raise AIHistoryImportError("ROW_IDENTITY_CONFLICT")
                merged[table][identifier] = row
                provenance.setdefault((table, _row_hash(table, row)), set()).add(system)
    _check_links(merged["reservations"], merged["ai_dispatches"])
    # Both private working copies participate in one DELETE-journal transaction.
    # No new table or column is added to either existing storage contract.
    with closing(sqlite3.connect(target_budget)) as db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("ATTACH DATABASE ? AS dispatch", (str(target_dispatch),))
        db.execute("PRAGMA dispatch.synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        try:
            for table, prefix in (("reservations", "main"), ("ai_dispatches", "dispatch")):
                width = 6 if table == "reservations" else 10
                for row in merged[table].values():
                    db.execute(f"INSERT OR IGNORE INTO {prefix}.{table} VALUES ({','.join('?' for _ in range(width))})", row)
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return [{"table": table, "row_sha256": digest, "systems": sorted(systems)}
            for (table, digest), systems in sorted(provenance.items())]


def _fingerprint(inputs: list[dict[str, object]]) -> str:
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _unchanged(pins: dict[Path, str | None]) -> None:
    if any(path.exists() if digest is None else _pin(path) != digest for path, digest in pins.items()):
        raise AIHistoryImportError("INPUT_CHANGED")


def _existing(manifest: Path, budget: Path, dispatch: Path, fingerprint: str) -> dict[str, Any]:
    if not all(path.is_file() for path in (manifest, budget, dispatch)) or manifest.stat().st_size > 4_000_000:
        raise AIHistoryImportError("OUTPUT_ALREADY_EXISTS")
    value = json.loads(manifest.read_text(encoding="utf-8"))
    if (value.get("schema_version") != 1 or value.get("input_fingerprint") != fingerprint
            or value.get("outputs") != {"budget_sha256": _pin(budget), "dispatch_sha256": _pin(dispatch)}):
        raise AIHistoryImportError("OUTPUT_ALREADY_EXISTS")
    summary = value.get("summary")
    if (type(summary) is not dict or set(summary) != {"state", "reservations", "dispatches", "used",
            "reserved", "uncertain", "manifest", "runtime_integration_installed"}
            or summary.get("state") != "STAGED_OFFLINE" or summary.get("manifest") != str(manifest)
            or summary.get("runtime_integration_installed") is not False):
        raise AIHistoryImportError("OUTPUT_ALREADY_EXISTS")
    for key in ("used", "reserved", "uncertain"):
        if type(summary[key]) is not str or not re.fullmatch(r"(?:0|[1-9][0-9]{0,63})\.[0-9]{8}", summary[key]):
            raise AIHistoryImportError("OUTPUT_ALREADY_EXISTS")
    for key, path, table in (("reservations", budget, "reservations"), ("dispatches", dispatch, "ai_dispatches")):
        if type(summary[key]) is not int or summary[key] != len(_read_rows(path, table)):
            raise AIHistoryImportError("OUTPUT_ALREADY_EXISTS")
    return cast(dict[str, Any], summary)


def import_ai_history(*, canonical_budget: Path, canonical_dispatch: Path,
                      sources: Sequence[Pair], staging_budget: Path,
                      staging_dispatch: Path) -> dict[str, Any]:
    """Preserve original identities/settings and stage only; never start/retry AI."""
    pairs = [("canonical", Path(canonical_budget).resolve(), Path(canonical_dispatch).resolve())]
    tags = {"canonical"}
    for system, budget, dispatch in sources:
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", system) or system in tags
                or "sk-" in system.lower() or "bearer" in system.lower()):
            raise AIHistoryImportError("SOURCE_SYSTEM_REQUIRED")
        tags.add(system)
        pairs.append((system, Path(budget).resolve(), Path(dispatch).resolve()))
    budget, dispatch = Path(staging_budget).resolve(), Path(staging_dispatch).resolve()
    manifest = budget.with_name(budget.name + ".import.json")
    if not sources or budget == dispatch or budget.parent != dispatch.parent:
        raise AIHistoryImportError("DISTINCT_STAGING_PAIR_REQUIRED")
    input_paths = {path for _, first, second in pairs for path in (first, second)}
    if any(path in input_paths for path in (budget, dispatch, manifest)):
        raise AIHistoryImportError("DISTINCT_STAGING_PAIR_REQUIRED")
    pins: dict[Path, str | None] = {}
    inputs: list[dict[str, object]] = []
    for system, first, second in sorted(pairs):
        budget_hash = pins.setdefault(first, _pin(first))
        dispatch_hash = None if system == "canonical" and not second.exists() else _pin(second)
        pins[second] = dispatch_hash
        inputs.append({"system": system, "budget_sha256": budget_hash, "dispatch_sha256": dispatch_hash})
    fingerprint = _fingerprint(inputs)
    if any(path.exists() for path in (budget, dispatch, manifest)):
        summary = _existing(manifest, budget, dispatch, fingerprint)
        _unchanged(pins)
        return summary
    budget.parent.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        with tempfile.TemporaryDirectory(prefix=".ai-history-import-", dir=budget.parent) as temporary:
            root = Path(temporary).resolve()
            assert root.is_relative_to(budget.parent)
            copies = [(tag, *_copy_pair(first, second, root / str(index),
                allow_missing_dispatch=tag == "canonical")) for index, (tag, first, second) in enumerate(pairs)]
            _, work_budget, work_dispatch = copies[0]
            provenance = _merge_rows(work_budget, work_dispatch, copies)
            book = BudgetBook(work_budget)
            AIDispatcher(work_dispatch, book, cast("OpenRouterGenerationClient", _NoGeneration()))
            totals = book.summary()
            summary = {"state": "STAGED_OFFLINE", "reservations": len(_read_rows(work_budget, "reservations")),
                "dispatches": len(_read_rows(work_dispatch, "ai_dispatches")),
                "used": totals["used"], "reserved": totals["reserved"], "uncertain": totals["uncertain"],
                "manifest": str(manifest), "runtime_integration_installed": False}
            _unchanged(pins)
            # Exclusive new files only. A complete manifest is written last; the
            # operator must never publish a partial/missing-manifest pair.
            for source, target in ((work_budget, budget), (work_dispatch, dispatch)):
                with source.open("rb") as original, target.open("xb") as output:
                    created.append(target)
                    while chunk := original.read(65_536):
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            _unchanged(pins)
            content = {"schema_version": 1, "input_fingerprint": fingerprint, "inputs": inputs,
                "outputs": {"budget_sha256": _pin(budget), "dispatch_sha256": _pin(dispatch)},
                "row_provenance": provenance, "summary": summary}
            with manifest.open("x", encoding="utf-8") as output:
                created.append(manifest)
                json.dump(content, output, ensure_ascii=True, sort_keys=True, indent=2)
                output.flush()
                os.fsync(output.fileno())
            return summary
    except BaseException:
        for path in reversed(created):
            path.unlink()
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline AI history staging; no application publish or provider calls")
    parser.add_argument("--canonical-budget", type=Path, required=True)
    parser.add_argument("--canonical-dispatch", type=Path, required=True)
    parser.add_argument("--source", nargs=3, action="append", required=True, metavar=("SYSTEM", "BUDGET", "DISPATCH"))
    parser.add_argument("--staging-budget", type=Path, required=True)
    parser.add_argument("--staging-dispatch", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = import_ai_history(canonical_budget=args.canonical_budget,
            canonical_dispatch=args.canonical_dispatch,
            sources=[(tag, Path(first), Path(second)) for tag, first, second in args.source],
            staging_budget=args.staging_budget, staging_dispatch=args.staging_dispatch)
    except Exception:  # noqa: BLE001 - database/path/row diagnostics may contain private data.
        print('{"error":"AI_HISTORY_IMPORT_REJECTED"}', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
