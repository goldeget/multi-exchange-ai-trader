"""Offline six-ledger recovery, Python 3.12 standard library only.

No command stops services or opens source SQLite databases. Operators must stop
ALL writers and keep source/backup/parent directories private for the operation.
The manifest detects accidental corruption; it is not a signature or encryption.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import struct
import sys
import tempfile
import time
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_FILES = 10_000
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
SQLITE_SECONDS = 10
PAPER = {
    "metadata": "value", "scenarios": "id created_at snapshot",
    "events": "scenario seq payload received_at previous hash",
    "commands": "scenario key payload seq result_hash", "fills": "scenario idx payload",
    "orders": "scenario idx payload", "settlements": "scenario seq payload",
}
SCHEMAS = {
    "paper.sqlite3": PAPER,
    "spot-paper.sqlite3": PAPER,
    "order-reviews.sqlite3": {"order_reviews": "id scenario payload confirmation_key result"},
    "ai.sqlite3": {"ai_settings": "id revision monthly_limit",
                   "reservations": "request_id period maximum actual status updated_at"},
    "recommendations.sqlite3": {"cards": "id status payload commands updated_at"},
    "research.sqlite3": {
        "metadata": "value", "runs": "id snapshot",
        "journal": "run seq operation snapshot previous hash",
        "commands": "run key payload response", "reports": "id payload",
        "report_commands": "key dataset report",
    },
}
METADATA = {
    "paper.sqlite3": {"schema": 1, "assumptions": "PAPER-v1", "engine": "1.231.0"},
    "spot-paper.sqlite3": {"schema": 1, "assumptions": "SPOT-PAPER-v2",
                           "engine": "1.231.0", "product": "SPOT"},
    "research.sqlite3": {"schema": 1, "engine": "1.231.0", "automation": "SYNTHETIC_AUTO_V1",
                         "event_limit": 500, "scenario_limit": 16},
}


class SnapshotError(ValueError):
    """Safe failure; command output never includes file contents or raw errors."""


@dataclass(frozen=True)
class Entry:
    path: str
    size: int
    sha256: str


def safe_path(raw: str | Path, *, directory: bool = True) -> Path:
    path = Path(raw)
    if ".." in path.parts:
        raise SnapshotError("Parent traversal is forbidden")
    path = path.absolute()
    for part in [*reversed(path.parents), path]:
        if part.is_symlink() or part.is_junction():
            raise SnapshotError("Symlinks and junctions are forbidden")
    if directory and not path.is_dir():
        raise SnapshotError("Existing directory required")
    return path


def target_path(raw: str | Path, source: Path) -> Path:
    path = safe_path(raw, directory=False)
    safe_path(path.parent)
    if path.exists() or path == source or source in path.parents:
        raise SnapshotError("Target must be new and outside the source")
    return path


def file_hash(path: Path) -> tuple[int, str]:
    safe_path(path, directory=False)
    initial = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
        raise SnapshotError("Only regular, non-hardlinked files are accepted")
    if initial.st_size > MAX_FILE_BYTES:
        raise SnapshotError("File size limit exceeded")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    digest, size = hashlib.sha256(), 0
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino):
            raise SnapshotError("File changed during read")
        while block := stream.read(1024 * 1024):
            size += len(block)
            if size > MAX_FILE_BYTES:
                raise SnapshotError("File size limit exceeded")
            digest.update(block)
        final = os.fstat(stream.fileno())
    current = path.stat(follow_symlinks=False)
    def identity(s: os.stat_result) -> tuple[int, ...]:
        # Windows Path.stat and fstat expose different legacy ctime meanings.
        return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns,
                s.st_ctime_ns if os.name == "posix" else 0)
    if identity(initial) != identity(final) or identity(final) != identity(current):
        raise SnapshotError("File changed during read")
    return size, digest.hexdigest()


def inventory(root: Path) -> list[Entry]:
    safe_path(root)
    files: list[Path] = []
    allowed = {name + suffix for name in SCHEMAS for suffix in ("", "-wal", "-shm")}
    with os.scandir(root) as children:
        for child in children:
            path = Path(child.path)
            if child.name == "datasets":
                safe_path(path)
                with os.scandir(path) as datasets:
                    for dataset in datasets:
                        if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}\.json", dataset.name):
                            raise SnapshotError("Unexpected dataset path")
                        files.append(Path(dataset.path))
                        if len(files) > MAX_FILES:
                            raise SnapshotError("File count limit exceeded")
            elif child.name in allowed:
                files.append(path)
            else:
                raise SnapshotError("Unexpected state entry; secrets, journals and extras refused")
            if len(files) > MAX_FILES:
                raise SnapshotError("File count limit exceeded")
    if not (root / "datasets").is_dir() or any(not (root / name).is_file() for name in SCHEMAS):
        raise SnapshotError("All six ledgers and datasets directory are required")
    result, total = [], 0
    for path in sorted(files):
        size, digest = file_hash(path)
        total += size
        if total > MAX_TOTAL_BYTES:
            raise SnapshotError("Total size limit exceeded")
        result.append(Entry(path.relative_to(root).as_posix(), size, digest))
    return result


def copy_files(source: Path, target: Path, entries: list[Entry]) -> None:
    (target / "datasets").mkdir(mode=0o700)
    for entry in entries:
        src, dst = source / entry.path, target / entry.path
        # Parents are trusted private directories; O_NOFOLLOW additionally rejects file swaps.
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(src, flags), "rb") as reader, dst.open("xb") as writer:
            os.chmod(dst, 0o600)
            copied = 0
            while block := reader.read(1024 * 1024):
                copied += len(block)
                if copied > entry.size:
                    raise SnapshotError("File changed during copy")
                writer.write(block)
            writer.flush()
            os.fsync(writer.fileno())
        if file_hash(dst) != (entry.size, entry.sha256):
            raise SnapshotError("Copy hash mismatch")


def check_wal(path: Path) -> None:
    """Reject damaged/ambiguous WAL that SQLite might silently ignore.

    Format/checksum: https://www.sqlite.org/fileformat.html#wal_file_format
    Conservative offline format: no incomplete, uncommitted or reused stale tail.
    """
    if path.stat().st_size == 0:
        return
    deadline = time.monotonic() + SQLITE_SECONDS
    with path.open("rb") as stream:
        header = stream.read(32)
        if len(header) != 32:
            raise SnapshotError("Incomplete WAL header")
        magic, version, page_size = struct.unpack(">III", header[:12])
        if (magic not in {0x377F0682, 0x377F0683} or version != 3007000
                or not 512 <= page_size <= 65536 or page_size & (page_size - 1)):
            raise SnapshotError("Unsupported WAL header")
        byte_order = "<II" if magic == 0x377F0682 else ">II"

        def checksum(data: bytes, state: tuple[int, int]) -> tuple[int, int]:
            first, second = state
            for left, right in struct.iter_unpack(byte_order, data):
                first = (first + left + second) & 0xFFFFFFFF
                second = (second + right + first) & 0xFFFFFFFF
            return first, second

        running = checksum(header[:24], (0, 0))
        if running != struct.unpack(">II", header[24:32]):
            raise SnapshotError("WAL header checksum mismatch")
        last_commit = True
        while frame := stream.read(24 + page_size):
            if time.monotonic() > deadline:
                raise SnapshotError("WAL verification time limit exceeded")
            if len(frame) != 24 + page_size or frame[8:16] != header[16:24]:
                raise SnapshotError("Incomplete or reused WAL tail requires manual review")
            page, commit = struct.unpack(">II", frame[:8])
            if page == 0:
                raise SnapshotError("Invalid WAL page")
            running = checksum(frame[24:], checksum(frame[:8], running))
            if running != struct.unpack(">II", frame[16:24]):
                raise SnapshotError("WAL frame checksum mismatch")
            last_commit = commit != 0
        if not last_commit:
            raise SnapshotError("Uncommitted WAL tail requires manual review")


def check_sqlite(root: Path, entries: list[Entry]) -> None:
    # SQLite may rebuild SHM or perform recovery. Only disposable copies are opened.
    with tempfile.TemporaryDirectory(prefix="trader-sqlite-check-") as raw:
        scratch = Path(raw)
        for entry in entries:
            if "/" not in entry.path:
                shutil.copyfile(root / entry.path, scratch / entry.path)
                if entry.path.endswith("-wal"):
                    check_wal(scratch / entry.path)
        for name, schema in SCHEMAS.items():
            deadline = time.monotonic() + SQLITE_SECONDS
            with closing(sqlite3.connect((scratch / name).as_uri() + "?mode=ro", uri=True,
                                         timeout=1)) as db:
                def expired(stop_at: float = deadline) -> int:
                    return int(time.monotonic() > stop_at)
                db.set_progress_handler(expired, 1000)
                db.execute("PRAGMA trusted_schema=OFF")
                db.execute("PRAGMA query_only=ON")
                if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise SnapshotError("SQLite integrity check failed")
                tables = {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )}
                if tables != set(schema):
                    raise SnapshotError("Unsupported SQLite schema")
                for table, columns in schema.items():
                    actual = [row[1] for row in db.execute(f'PRAGMA table_xinfo("{table}")')]
                    if actual != columns.split():
                        raise SnapshotError("Unsupported SQLite columns")
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version != (1 if name in METADATA else 0):
                    raise SnapshotError("Unsupported SQLite version")
                if name in METADATA:
                    rows = db.execute("SELECT value FROM metadata").fetchmany(2)
                    if len(rows) != 1 or json.loads(rows[0][0]) != METADATA[name]:
                        raise SnapshotError("Unsupported ledger metadata")


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotError("Duplicate manifest fields")
        result[key] = value
    return result


def verify(source: Path) -> list[Entry]:
    safe_path(source)
    if {p.name for p in source.iterdir()} != {"manifest.json", "data"}:
        raise SnapshotError("Incomplete snapshot or unexpected snapshot entry")
    path = safe_path(source / "manifest.json", directory=False)
    size, _digest = file_hash(path)
    if size > MAX_MANIFEST_BYTES:
        raise SnapshotError("Manifest size limit exceeded")
    manifest = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    if (not isinstance(manifest, dict)
            or set(manifest) != {"format_version", "created_at", "writers_stopped", "files"}
            or type(manifest["format_version"]) is not int or manifest["format_version"] != 1
            or manifest["writers_stopped"] is not True
            or not isinstance(manifest["created_at"], str)
            or not isinstance(manifest["files"], list)
            or len(manifest["files"]) > MAX_FILES):
        raise SnapshotError("Unsupported manifest")
    entries = []
    for item in manifest["files"]:
        if (not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}
                or not isinstance(item["path"], str) or type(item["size"]) is not int
                or not 0 <= item["size"] <= MAX_FILE_BYTES
                or not isinstance(item["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
            raise SnapshotError("Invalid manifest file record")
        entries.append(Entry(**item))
    # Match against independently discovered allowlisted paths before using manifest paths.
    actual = inventory(source / "data")
    if entries != actual:
        raise SnapshotError("Manifest paths, completeness or hashes do not match")
    check_sqlite(source / "data", actual)
    if inventory(source / "data") != actual:
        raise SnapshotError("Snapshot changed during verification")
    return actual


def publish(source: Path, target: Path, entries: list[Entry], *, snapshot: bool) -> None:
    # Exclusive mkdir reserves a never-existing destination; no rename/overwrite fallback.
    target.mkdir(mode=0o700)
    data = target / "data" if snapshot else target
    if snapshot:
        data.mkdir(mode=0o700)
    copy_files(source, data, entries)
    if snapshot:
        manifest = {"format_version": 1, "created_at": datetime.now(UTC).isoformat(),
                    "writers_stopped": True, "files": [asdict(e) for e in entries]}
        with (target / "manifest.json").open("x", encoding="utf-8") as stream:
            os.chmod(target / "manifest.json", 0o600)
            json.dump(manifest, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    for name in ("snapshot", "verify", "restore"):
        command = commands.add_parser(name)
        command.add_argument("--source", required=True, type=Path)
        if name != "verify":
            command.add_argument("--target", required=True, type=Path)
        if name == "snapshot":
            command.add_argument("--writers-stopped", action="store_true",
                                 help="Attest ALL writers are stopped and will remain stopped")
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        source = safe_path(args.source)
        if args.command == "verify":
            entries = verify(source)
        else:
            target = target_path(args.target, source)
            if args.command == "snapshot":
                if not args.writers_stopped:
                    raise SnapshotError("All writers must be stopped: --writers-stopped required")
                before = inventory(source)
                with tempfile.TemporaryDirectory(prefix=".trader-stage-", dir=target.parent) as raw:
                    stage = Path(raw)
                    copy_files(source, stage, before)
                    if inventory(source) != before or inventory(stage) != before:
                        raise SnapshotError("Source changed; no consistent snapshot produced")
                    check_sqlite(stage, before)
                    if inventory(source) != before:
                        raise SnapshotError("Source changed; no consistent snapshot produced")
                    publish(stage, target, before, snapshot=True)
                entries = verify(target)
            else:
                entries = verify(source)
                # Freeze the verified generation before publishing restored data.
                with tempfile.TemporaryDirectory(prefix=".trader-stage-", dir=target.parent) as raw:
                    stage = Path(raw)
                    copy_files(source / "data", stage, entries)
                    check_sqlite(stage, entries)
                    if inventory(stage) != entries:
                        raise SnapshotError("Restore copy mismatch")
                    publish(stage, target, entries, snapshot=False)
                if inventory(target) != entries:
                    raise SnapshotError("Restore copy mismatch")
        print(json.dumps({"status": "verified", "operation": args.command,
                          "files": len(entries), "bytes": sum(e.size for e in entries)}))
        return 0
    except SnapshotError as error:
        # Only our fixed diagnostics, never a raw library error or supplied path/value.
        print(f"Recovery refused: {error}", file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, RecursionError):
        # Never print input strings, database payloads, credentials or raw exception details.
        print("Recovery refused: check paths, writers stopped, schema, limits and manifest integrity.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
