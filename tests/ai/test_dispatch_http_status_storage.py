"""Handcrafted temporary storage fixtures; no operator database or provider access."""

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest

from multi_exchange_trader.ai.budget import BudgetBook
from multi_exchange_trader.ai.dispatch import AIDispatcher, DispatchResult
from multi_exchange_trader.ai.generation import OpenRouterGenerationClient

LEGACY_CREATE = """CREATE TABLE ai_dispatches (
    request_id TEXT PRIMARY KEY, binding_hash TEXT NOT NULL,
    maximum TEXT NOT NULL, status TEXT NOT NULL, failure TEXT,
    exact_cost TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
LEGACY_METADATA = [
    (0, "request_id", "TEXT", 0, None, 1, 0),
    (1, "binding_hash", "TEXT", 1, None, 0, 0),
    (2, "maximum", "TEXT", 1, None, 0, 0),
    (3, "status", "TEXT", 1, None, 0, 0),
    (4, "failure", "TEXT", 0, None, 0, 0),
    (5, "exact_cost", "TEXT", 0, None, 0, 0),
    (6, "created_at", "TEXT", 1, None, 0, 0),
    (7, "updated_at", "TEXT", 1, None, 0, 0),
]
V2_METADATA = [*LEGACY_METADATA, (8, "http_status", "INTEGER", 0, None, 0, 0)]
ROWS = [
    ("legacy-uncertain", "binding-u", "0.01000000", "UNCERTAIN", "HTTP_ERROR", None,
     "2026-11-02T00:00:00+00:00", "2026-11-02T00:00:00+00:00"),
    ("legacy-charged", "binding-c", "0.01000000", "CHARGED", "HTTP_ERROR", "0.0012",
     "2026-11-02T00:00:00+00:00", "2026-11-02T00:00:00+00:00"),
]


def local_dependencies(tmp_path):
    def forbidden(_):
        pytest.fail("Storage operations must never call generation")

    client = OpenRouterGenerationClient("sk-or-v1-FAKE_ONLY_NEVER_A_REAL_KEY", "test/fake-model",
                                        transport=httpx.MockTransport(forbidden))
    budget = BudgetBook(tmp_path / "budget.sqlite3")
    return budget, client


def legacy_store(path, version=1, *, create=LEGACY_CREATE, rows=ROWS):
    with sqlite3.connect(path) as db:
        db.execute(create)
        if rows:
            db.executemany("INSERT INTO ai_dispatches VALUES (?,?,?,?,?,?,?,?)", rows)
        db.execute(f"PRAGMA user_version={version}")


def assert_layout(path, *, version=2, metadata=V2_METADATA, rows=ROWS):
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == version
        assert db.execute("PRAGMA table_xinfo(ai_dispatches)").fetchall() == metadata
        actual = db.execute("SELECT * FROM ai_dispatches ORDER BY request_id DESC").fetchall()
    assert [row[:8] for row in actual] == rows
    if version == 2:
        assert all(row[8] is None for row in actual)


def forbid_accounting(monkeypatch, budget):
    def forbidden(*_):
        pytest.fail("Storage validation must precede budget effects")

    for name in ("reserve", "settle", "uncertain", "configure", "reconcile"):
        monkeypatch.setattr(budget, name, forbidden)


def test_v1_rows_migrate_without_inferred_http_status(tmp_path):
    path = tmp_path / "dispatch.sqlite3"
    legacy_store(path)
    budget, client = local_dependencies(tmp_path)
    budget.configure(0, "1")
    for row in ROWS:
        identifier = "dispatch_" + hashlib.sha256(row[0].encode()).hexdigest()
        budget.reserve(identifier, "0.01")
        budget.uncertain(identifier)
    dispatcher = AIDispatcher(path, budget, client)
    assert_layout(path)
    for row in ROWS:
        result = dispatcher.status(row[0])
        assert result.http_status is None and result.selection is None
        assert result.status == row[3] and result.reservation_status == "uncertain"
        if row[5] is None:
            assert dispatcher.reconcile_charge(row[0]).status == "UNCERTAIN"
        else:
            settled = dispatcher.reconcile_charge(row[0])
            assert settled.http_status is None and settled.reservation_status == "settled"
    assert budget.summary()["uncertain"] == "0.01000000"
    assert budget.summary()["used"] == "0.00120000"
    # A migrated legacy identity remains consumed; no replay or selection is reconstructed.
    assert dispatcher.reconcile_charge("legacy-charged").selection is None
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM ai_dispatches").fetchone()[0] == 2


@pytest.mark.parametrize("interrupted", [False, True])
def test_v0_empty_and_interrupted_legacy_create_are_supported(tmp_path, interrupted):
    path = tmp_path / "dispatch.sqlite3"
    if interrupted:
        legacy_store(path, version=0)
    budget, client = local_dependencies(tmp_path)
    AIDispatcher(path, budget, client)
    assert_layout(path, rows=ROWS if interrupted else [])
    AIDispatcher(path, budget, client)
    assert_layout(path, rows=ROWS if interrupted else [])


@pytest.mark.parametrize("version", [None, 0, 1])
def test_concurrent_dispatcher_open_migrates_once(tmp_path, monkeypatch, version):
    path = tmp_path / "dispatch.sqlite3"
    if version is not None:
        legacy_store(path, version)
    budget, client = local_dependencies(tmp_path)
    before = budget.path.read_bytes()
    forbid_accounting(monkeypatch, budget)
    barrier = Barrier(4)

    def open_store(_):
        barrier.wait(timeout=5)
        return AIDispatcher(path, budget, client)

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(list(pool.map(open_store, range(4)))) == 4
    assert_layout(path, rows=ROWS if version is not None else [])
    assert budget.path.read_bytes() == before


def test_migration_lock_timeout_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / "dispatch.sqlite3"
    legacy_store(path)
    budget, client = local_dependencies(tmp_path)
    forbid_accounting(monkeypatch, budget)
    connect = sqlite3.connect

    def short_timeout(target, **kwargs):
        if target == path:
            kwargs["timeout"] = 0.01
        return connect(target, **kwargs)

    with connect(path) as locked:
        locked.execute("BEGIN IMMEDIATE")
        monkeypatch.setattr(sqlite3, "connect", short_timeout)
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            AIDispatcher(path, budget, client)
    assert_layout(path, version=1, metadata=LEGACY_METADATA)


def test_migration_failure_rolls_back_schema_and_version(tmp_path, monkeypatch):
    path = tmp_path / "dispatch.sqlite3"
    legacy_store(path)
    budget, client = local_dependencies(tmp_path)
    forbid_accounting(monkeypatch, budget)
    connect = sqlite3.connect

    class RejectVersion(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.replace(" ", "").upper() == "PRAGMAUSER_VERSION=2":
                # Failure is injected only after the transactional ALTER took effect.
                assert len(super().execute("PRAGMA table_xinfo(ai_dispatches)").fetchall()) == 9
                raise sqlite3.OperationalError("injected migration failure")
            return super().execute(sql, parameters)

    def reject_version(target, **kwargs):
        if target == path:
            kwargs["factory"] = RejectVersion
        return connect(target, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", reject_version)
        with pytest.raises(sqlite3.OperationalError, match="injected migration failure"):
            AIDispatcher(path, budget, client)
    assert_layout(path, version=1, metadata=LEGACY_METADATA)
    AIDispatcher(path, budget, client)
    assert_layout(path)


@pytest.mark.parametrize("case", [
    "unknown-version", "missing-v1", "missing-v2", "renamed", "missing-column", "extra",
    "hidden", "type", "pk", "nullability", "view", "unknown-v0", "corrupt",
    "v0-already-v2", "v1-already-v2", "v2-legacy", "v0-prefix-lookalike",
    "v0-legacy-and-unknown", "v2-status-type", "v2-status-nullability", "v2-status-default",
    "v0-internal-table-leftover",
])
def test_incompatible_schema_fails_closed(tmp_path, monkeypatch, case):
    path = tmp_path / "dispatch.sqlite3"
    budget, client = local_dependencies(tmp_path)
    forbid_accounting(monkeypatch, budget)
    if case == "corrupt":
        path.write_bytes(b"synthetic corrupt SQLite fixture")
    else:
        create = LEGACY_CREATE
        version = 1
        if case == "unknown-version":
            version = 9
        elif case in {"missing-v1", "missing-v2"}:
            create, version = "CREATE TABLE unrelated (value TEXT)", int(case[-1])
        elif case == "renamed":
            create = create.replace("binding_hash", "renamed")
        elif case == "missing-column":
            create = create.replace(", updated_at TEXT NOT NULL", "")
        elif case == "extra":
            create = create[:-1] + ", extra TEXT)"
        elif case == "hidden":
            create = create[:-1] + ", hidden TEXT GENERATED ALWAYS AS (status) VIRTUAL)"
        elif case == "type":
            create = create.replace("maximum TEXT", "maximum INTEGER")
        elif case == "pk":
            create = create.replace("request_id TEXT PRIMARY KEY", "request_id TEXT")
        elif case == "nullability":
            create = create.replace("request_id TEXT PRIMARY KEY", "request_id TEXT PRIMARY KEY NOT NULL")
        elif case == "view":
            create = "CREATE VIEW ai_dispatches AS SELECT 1 AS request_id"
        elif case == "unknown-v0":
            create, version = "CREATE TABLE unrelated (value TEXT)", 0
        elif case == "v0-prefix-lookalike":
            create, version = "CREATE TABLE sqliteXunknown (value TEXT)", 0
        elif case == "v0-internal-table-leftover":
            create, version = "CREATE TABLE unrelated (id INTEGER PRIMARY KEY AUTOINCREMENT)", 0
        elif case == "v0-legacy-and-unknown":
            version = 0
        elif case in {"v0-already-v2", "v1-already-v2"}:
            version = int(case[1])
            create = create[:-1] + ", http_status INTEGER)"
        elif case == "v2-legacy":
            version = 2
        elif case.startswith("v2-status-"):
            version = 2
            declaration = {"v2-status-type": "TEXT", "v2-status-nullability": "INTEGER NOT NULL",
                           "v2-status-default": "INTEGER DEFAULT 200"}[case]
            create = create[:-1] + f", http_status {declaration})"
        legacy_store(path, version, create=create, rows=[])
        if case == "v0-legacy-and-unknown":
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE unrelated (value TEXT)")
        if case == "v0-internal-table-leftover":
            with sqlite3.connect(path) as db:
                db.execute("DROP TABLE unrelated")
    before = path.read_bytes()
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        AIDispatcher(path, budget, client)
    assert path.read_bytes() == before


@pytest.mark.parametrize("value", ["secret-invalid-status", b"blob", 402.5, 99, 600])
def test_invalid_stored_http_status_fails_before_accounting(tmp_path, monkeypatch, value):
    path = tmp_path / "dispatch.sqlite3"
    legacy_store(path)
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE ai_dispatches ADD COLUMN http_status INTEGER")
        db.execute("PRAGMA user_version=2")
    budget, client = local_dependencies(tmp_path)
    dispatcher = AIDispatcher(path, budget, client)
    forbid_accounting(monkeypatch, budget)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE ai_dispatches SET http_status=?", (value,))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        AIDispatcher(path, budget, client)
    assert path.read_bytes() == before
    for request_id in ("legacy-uncertain", "legacy-charged"):
        with pytest.raises(ValueError):
            dispatcher.status(request_id)
        with pytest.raises(ValueError):
            dispatcher.reconcile_charge(request_id)


@pytest.mark.parametrize("value", [None, 100, 599])
def test_valid_stored_status_bounds_and_null_are_read_without_generation(tmp_path, value):
    path = tmp_path / "dispatch.sqlite3"
    legacy_store(path)
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE ai_dispatches ADD COLUMN http_status INTEGER")
        db.execute("PRAGMA user_version=2")
        db.execute("UPDATE ai_dispatches SET http_status=?", (value,))
    budget, client = local_dependencies(tmp_path)
    dispatcher = AIDispatcher(path, budget, client)
    assert dispatcher.status("legacy-uncertain").http_status == value
    assert dispatcher.reconcile_charge("legacy-uncertain").http_status == value


@pytest.mark.parametrize("invalid", [True, False, 99, 600, "402", 402.0])
def test_record_rejects_non_exact_runtime_status_before_sql(tmp_path, invalid):
    path = tmp_path / "dispatch.sqlite3"
    legacy_store(path)
    budget, client = local_dependencies(tmp_path)
    dispatcher = AIDispatcher(path, budget, client)
    with pytest.raises(ValueError, match="^Invalid HTTP status$"):
        dispatcher._record("legacy-uncertain", "UNCERTAIN", "HTTP_ERROR", http_status=invalid)
    with dispatcher._connect() as db, pytest.raises(ValueError, match="^Invalid HTTP status$"):
        dispatcher._record_locked(db, "legacy-uncertain", "UNCERTAIN", "HTTP_ERROR",
                                  http_status=invalid)
    assert dispatcher.status("legacy-uncertain").http_status is None


def test_record_none_preserves_previously_observed_status(tmp_path):
    path = tmp_path / "dispatch.sqlite3"
    legacy_store(path)
    budget, client = local_dependencies(tmp_path)
    dispatcher = AIDispatcher(path, budget, client)
    dispatcher._record("legacy-uncertain", "UNCERTAIN", "HTTP_ERROR", http_status=429)
    dispatcher._record("legacy-uncertain", "UNCERTAIN", "HTTP_ERROR")
    assert dispatcher.status("legacy-uncertain").http_status == 429
    assert "429" in repr(DispatchResult("r", "UNCERTAIN", http_status=429))
