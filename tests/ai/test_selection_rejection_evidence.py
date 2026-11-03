"""Synthetic, local-only rejection diagnostics; never historical cause inference."""

import sqlite3
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from test_dispatch_http_status_storage import (
    LEGACY_CREATE,
    ROWS,
    V2_METADATA,
    forbid_accounting,
    legacy_store,
    local_dependencies,
)
from test_generation_dispatch import (
    KEY,
    NOW,
    completion,
    dispatch,
    output,
    quote,
    selection_request,
    setup_dispatch,
)

from multi_exchange_trader.ai import dispatch as dispatch_module
from multi_exchange_trader.ai.dispatch import AIDispatcher, DispatchResult
from multi_exchange_trader.execution.modes import SelectionRejected

CODES = (
    "INVALID_INPUT", "FUTURE_EVIDENCE", "STALE_EVIDENCE", "EXPIRED", "INVALID_TTL",
    "SCOPE_MISMATCH", "RESOURCE_LIMIT", "DUPLICATE_ID", "REVISION_MISMATCH",
    "DISABLED_CANDIDATE", "NO_CANDIDATE", "INVALID_JSON", "INVALID_PRIORITY", "AMBIGUOUS",
    "INVALID_SCHEMA", "BINDING_MISMATCH", "UNKNOWN_CANDIDATE", "UNCLASSIFIED",
)
V3_CREATE = LEGACY_CREATE[:-1] + ", http_status INTEGER, selection_rejection_code TEXT)"
V3_METADATA = [*V2_METADATA, (9, "selection_rejection_code", "TEXT", 0, None, 0, 0)]


class HostileCode:
    def __str__(self):
        raise AssertionError("Must not format untrusted code")

    def __repr__(self):
        raise AssertionError("Must not represent untrusted code")

    def __hash__(self):
        raise AssertionError("Must not hash untrusted code")


class CodeSubclass(str):
    def __hash__(self):
        raise AssertionError("Must require exact str before allowlist membership")


INVALID_CODES = [KEY, "invalid_schema", " INVALID_SCHEMA", "INVALID_SCHEMA ", "", 1, True,
                 b"INVALID_SCHEMA", HostileCode(), CodeSubclass("INVALID_SCHEMA")]


@pytest.mark.parametrize("code", INVALID_CODES, ids=[f"invalid-{i}" for i in range(10)])
def test_explicit_diagnostic_inputs_reject_safely_before_sql(tmp_path, code):
    dispatcher, _, _ = setup_dispatch(tmp_path)
    before = dispatcher.path.read_bytes()
    with pytest.raises(ValueError, match="^Invalid selection rejection code$") as error:
        DispatchResult("r", "SETTLED", selection_rejection_code=code)
    assert KEY not in str(error.value)
    with pytest.raises(ValueError, match="^Invalid selection rejection code$"):
        dispatcher._record("r", "SETTLED", None, selection_rejection_code=code)
    with (dispatcher._connect() as db,
          pytest.raises(ValueError, match="^Invalid selection rejection code$")):
        dispatcher._record_locked(db, "r", "SETTLED", None, selection_rejection_code=code)
    assert dispatcher.path.read_bytes() == before


@pytest.mark.parametrize("code", [None, *CODES])
def test_fixed_diagnostics_and_old_positional_dto_remain_compatible(code):
    marker = object()
    result = DispatchResult("r", "SETTLED", "INVALID_SELECTION", "settled", "0.1", marker, 200,
                            code)
    assert result.selection is marker and result.http_status == 200
    assert result.selection_rejection_code == code
    assert DispatchResult("r", "SETTLED").selection_rejection_code is None


@pytest.mark.parametrize("response,expected", [
    ("not-json", "INVALID_JSON"),
    (output(reason="x" * 121), "INVALID_SCHEMA"),
    (output(extra="synthetic-private-detail"), "INVALID_SCHEMA"),
    (output(request_id="wrong"), "BINDING_MISMATCH"),
    (output(candidate_id="unknown"), "UNKNOWN_CANDIDATE"),
    (" " * 4097, "RESOURCE_LIMIT"),
])
def test_real_selection_rejections_settle_and_persist_fixed_code(tmp_path, response, expected):
    envelope = completion(choices=[{"finish_reason": "stop", "message": {
        "role": "assistant", "content": response}}])
    dispatcher, budget, calls = setup_dispatch(
        tmp_path, lambda _: httpx.Response(200, json=envelope))
    result = dispatch(dispatcher)
    assert result.failure == "INVALID_SELECTION" and result.selection is None
    assert result.selection_rejection_code == expected and result.http_status == 200
    assert result.exact_cost_usd == "0.0012" and budget.summary()["used"] == "0.00120000"
    fresh = AIDispatcher(dispatcher.path, budget, dispatcher.client, clock=lambda: NOW)
    for readback in (fresh.status("request-1"), fresh.reconcile_charge("request-1")):
        assert readback.selection_rejection_code == expected and readback.selection is None
        assert readback.http_status == 200 and readback.reservation_status == "settled"
    assert dispatch(fresh).failure == "ALREADY_DISPATCHED" and len(calls) == 1
    with sqlite3.connect(dispatcher.path) as db:
        stored = repr(db.execute("SELECT * FROM ai_dispatches").fetchall())
    assert "synthetic-private-detail" not in stored and KEY not in repr(result)


@pytest.mark.parametrize("decision,candidate,status", [
    ("SELECT", "candidate-1", "SELECTED"), ("ABSTAIN", None, "ABSTAINED"),
])
def test_valid_selection_has_no_diagnostic(tmp_path, decision, candidate, status):
    envelope = completion(choices=[{"finish_reason": "stop", "message": {
        "role": "assistant", "content": output(decision=decision, candidate_id=candidate)}}])
    dispatcher, _, calls = setup_dispatch(tmp_path, lambda _: httpx.Response(200, json=envelope))
    result = dispatch(dispatcher)
    assert result.selection.status == status and result.selection_rejection_code is None
    assert dispatcher.status("request-1").selection_rejection_code is None and len(calls) == 1


@pytest.mark.parametrize("code", [*CODES, *INVALID_CODES],
                         ids=[f"capture-{i}" for i in range(len(CODES) + 10)])
def test_capture_projects_only_exact_allowlisted_code_without_formatting(tmp_path, monkeypatch, code):
    rejection = SelectionRejected("INVALID_SCHEMA", KEY + " synthetic-private-detail")
    rejection.code = code

    def reject(**_):
        raise rejection

    monkeypatch.setattr(dispatch_module, "select_all_in_one", reject)
    dispatcher, budget, calls = setup_dispatch(tmp_path)
    result = dispatch(dispatcher)
    expected = code if type(code) is str and code in CODES else "UNCLASSIFIED"
    assert result.selection_rejection_code == expected and result.failure == "INVALID_SELECTION"
    assert budget.summary()["used"] == "0.00120000" and len(calls) == 1
    assert dispatcher.status("request-1").selection_rejection_code == expected
    assert KEY not in repr(result) and "synthetic-private-detail" not in repr(result)
    with sqlite3.connect(dispatcher.path) as db:
        stored = repr(db.execute("SELECT * FROM ai_dispatches").fetchall())
    assert KEY not in stored and "synthetic-private-detail" not in stored


def test_expired_evidence_records_code_and_overrun_preserves_underlying_rejection(tmp_path):
    current = [NOW]

    def late(_):
        current[0] += timedelta(seconds=31)
        return httpx.Response(200, json=completion(usage={"cost": 150.125}))

    dispatcher, budget, calls = setup_dispatch(tmp_path, late, clock=lambda: current[0])
    result = dispatch(dispatcher)
    assert result.failure == "BUDGET_OVERRUN_HOLD" and result.selection is None
    assert result.selection_rejection_code == "EXPIRED" and budget.summary()["overrun_hold"]
    assert dispatcher.status("request-1").selection_rejection_code == "EXPIRED"
    assert dispatcher.reconcile_charge("request-1").selection_rejection_code == "EXPIRED"
    assert len(calls) == 1


@pytest.mark.parametrize("usage", [{}, {"cost": None}])
def test_unknown_cost_does_not_validate_selection_or_create_diagnostic(tmp_path, monkeypatch, usage):
    def forbidden(**_):
        pytest.fail("Unknown charges must not authorize selection")

    monkeypatch.setattr(dispatch_module, "select_all_in_one", forbidden)
    dispatcher, budget, calls = setup_dispatch(
        tmp_path, lambda _: httpx.Response(200, json=completion(usage=usage)))
    result = dispatch(dispatcher)
    assert result.selection_rejection_code is None and result.status == "UNCERTAIN"
    assert budget.summary()["uncertain"] == "0.01000000" and len(calls) == 1


def test_record_coalesces_diagnostic_with_final_failure_atomically(tmp_path):
    dispatcher, _, _ = setup_dispatch(tmp_path)
    dispatch(dispatcher)
    with sqlite3.connect(dispatcher.path) as db:
        db.execute("""CREATE TRIGGER atomic_rejection BEFORE UPDATE ON ai_dispatches
            WHEN NEW.failure='INVALID_SELECTION' AND NEW.selection_rejection_code IS NULL
            BEGIN SELECT RAISE(ABORT,'missing diagnostic in final update'); END""")
    dispatcher._record("request-1", "SETTLED", "INVALID_SELECTION",
                       selection_rejection_code="INVALID_SCHEMA")
    dispatcher._record("request-1", "SETTLED", "BUDGET_OVERRUN_HOLD")
    result = dispatcher.status("request-1")
    assert result.selection_rejection_code == "INVALID_SCHEMA" and result.http_status == 200


@pytest.mark.parametrize("value", [KEY, "invalid_schema", "INVALID_SCHEMA ", 17, b"INVALID_SCHEMA"])
@pytest.mark.parametrize("path", ["constructor", "status", "reconcile-known", "reconcile-null",
                                  "pending", "apply"])
def test_corrupt_diagnostic_fails_before_accounting(tmp_path, monkeypatch, value, path):
    dispatcher, budget, calls = setup_dispatch(tmp_path)
    dispatch(dispatcher)
    with sqlite3.connect(dispatcher.path) as db:
        db.execute("UPDATE ai_dispatches SET status='CHARGED',selection_rejection_code=?", (value,))
        if path == "reconcile-null":
            db.execute("UPDATE ai_dispatches SET exact_cost=NULL")
    before = dispatcher.path.read_bytes()
    forbid_accounting(monkeypatch, budget)
    if path == "pending":
        result = dispatcher.dispatch(replace(selection_request(), request_id="request-2"),
                                     maximum_cost_usd="0.01", quote=quote(), enabled=True)
        assert result.failure == "PENDING_CHARGE_RECONCILIATION" and len(calls) == 1
    else:
        with pytest.raises(ValueError, match="^Invalid selection rejection code$") as error:
            if path == "constructor":
                AIDispatcher(dispatcher.path, budget, dispatcher.client)
            elif path == "status":
                dispatcher.status("request-1")
            elif path == "apply":
                with dispatcher._connect() as db:
                    row = db.execute("""SELECT *,typeof(http_status) AS http_status_type,
                        typeof(selection_rejection_code) AS selection_rejection_code_type
                        FROM ai_dispatches""").fetchone()
                    dispatcher._apply_charge_locked(db, row)
            else:
                dispatcher.reconcile_charge("request-1")
        assert KEY not in str(error.value) and dispatcher.path.read_bytes() == before


def test_constructor_requires_exact_code_even_with_nocase_collation(tmp_path, monkeypatch):
    path = tmp_path / "dispatch.sqlite3"
    legacy_store(path, version=3, create=V3_CREATE.replace(
        "selection_rejection_code TEXT", "selection_rejection_code TEXT COLLATE NOCASE"), rows=[])
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO ai_dispatches VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (*ROWS[0], 200, "invalid_schema"))
    budget, client = local_dependencies(tmp_path)
    forbid_accounting(monkeypatch, budget)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="^Invalid selection rejection code$"):
        AIDispatcher(path, budget, client)
    assert path.read_bytes() == before


@pytest.mark.parametrize("route", ["reconcile", "pending"])
def test_known_charge_repair_preserves_code_without_selection_replay(tmp_path, monkeypatch, route):
    dispatcher, budget, calls = setup_dispatch(tmp_path)
    dispatch(dispatcher)
    with sqlite3.connect(dispatcher.path) as db:
        db.execute("""UPDATE ai_dispatches SET status='CHARGED',failure='INVALID_SELECTION',
                   selection_rejection_code='INVALID_SCHEMA'""")

    def forbidden(**_):
        pytest.fail("Accounting repair must not reconstruct selection")

    monkeypatch.setattr(dispatch_module, "select_all_in_one", forbidden)
    if route == "reconcile":
        dispatcher.reconcile_charge("request-1")
    else:
        # An independent reservation holds the remaining budget, so the new identity
        # exercises pending-charge repair and then stops before another generation.
        budget.reserve("unrelated-hold", "0.9988")
        result = dispatcher.dispatch(replace(selection_request(), request_id="request-2"),
                                     maximum_cost_usd="0.01", quote=quote(), enabled=True)
        assert result.failure == "BUDGET_BLOCKED"
    readback = dispatcher.status("request-1")
    assert readback.status == "SETTLED" and readback.selection_rejection_code == "INVALID_SCHEMA"
    assert readback.selection is None and readback.http_status == 200
    assert budget.summary()["used"] == "0.00120000" and len(calls) == 1


@pytest.mark.parametrize("version", [0, 1, 2, 3])
def test_known_versions_preserve_all_original_evidence_and_never_infer_code(tmp_path, version):
    path = tmp_path / "dispatch.sqlite3"
    create = LEGACY_CREATE if version < 2 else (
        LEGACY_CREATE[:-1] + ", http_status INTEGER)" if version == 2 else V3_CREATE)
    legacy_store(path, version, create=create, rows=[])
    with sqlite3.connect(path) as db:
        for original in ROWS:
            row = (*original, 429) if version == 2 else (
                (*original, 429, None) if version == 3 else original)
            db.execute(f"INSERT INTO ai_dispatches VALUES ({','.join('?' for _ in row)})", row)
    budget, client = local_dependencies(tmp_path)
    AIDispatcher(path, budget, client)
    AIDispatcher(path, budget, client)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert db.execute("PRAGMA table_xinfo(ai_dispatches)").fetchall() == V3_METADATA
        actual = db.execute("SELECT * FROM ai_dispatches ORDER BY request_id DESC").fetchall()
    assert [row[:8] for row in actual] == ROWS
    assert [row[8] for row in actual] == ([429, 429] if version >= 2 else [None, None])
    assert all(row[9] is None for row in actual)


@pytest.mark.parametrize("declaration", ["INTEGER", "TEXT NOT NULL", "TEXT DEFAULT NULL",
                                        "TEXT GENERATED ALWAYS AS (failure) VIRTUAL"])
def test_incompatible_v3_diagnostic_layout_rolls_back(tmp_path, monkeypatch, declaration):
    path = tmp_path / "dispatch.sqlite3"
    legacy_store(path, version=3, create=V3_CREATE.replace("selection_rejection_code TEXT",
                                                         "selection_rejection_code " + declaration),
                 rows=[])
    budget, client = local_dependencies(tmp_path)
    forbid_accounting(monkeypatch, budget)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Invalid AI dispatch storage"):
        AIDispatcher(path, budget, client)
    assert path.read_bytes() == before


@pytest.mark.parametrize("case", ["missing", "renamed", "extra", "view", "pk", "v0-ten-columns",
                                 "v1-ten-columns", "v2-ten-columns"])
def test_other_incompatible_v3_forms_fail_closed(tmp_path, monkeypatch, case):
    path = tmp_path / "dispatch.sqlite3"
    create, version = V3_CREATE, 3
    if case == "missing":
        create = LEGACY_CREATE[:-1] + ", http_status INTEGER)"
    elif case == "renamed":
        create = create.replace("selection_rejection_code", "renamed")
    elif case == "extra":
        create = create[:-1] + ", extra TEXT)"
    elif case == "view":
        create = "CREATE VIEW ai_dispatches AS SELECT 1 AS selection_rejection_code"
    elif case == "pk":
        create = create.replace("request_id TEXT PRIMARY KEY", "request_id TEXT").replace(
            "selection_rejection_code TEXT", "selection_rejection_code TEXT PRIMARY KEY")
    else:
        version = int(case[1])
    legacy_store(path, version, create=create, rows=[])
    budget, client = local_dependencies(tmp_path)
    forbid_accounting(monkeypatch, budget)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Invalid AI dispatch storage"):
        AIDispatcher(path, budget, client)
    assert path.read_bytes() == before


@pytest.mark.parametrize("source", ["empty", "legacy", "v2-bad-http"])
def test_failed_create_or_intermediate_alter_restores_source(tmp_path, monkeypatch, source):
    path = tmp_path / "dispatch.sqlite3"
    if source != "empty":
        legacy_store(path, version=2 if source == "v2-bad-http" else 1)
        if source == "v2-bad-http":
            with sqlite3.connect(path) as db:
                db.execute("ALTER TABLE ai_dispatches ADD COLUMN http_status INTEGER")
                db.execute("UPDATE ai_dispatches SET http_status=99")
    budget, client = local_dependencies(tmp_path)
    forbid_accounting(monkeypatch, budget)
    connect = sqlite3.connect

    class RejectMigration(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            normalized = " ".join(sql.upper().split())
            if (source == "empty" and normalized == "PRAGMA USER_VERSION=3"):
                assert len(super().execute("PRAGMA table_xinfo(ai_dispatches)").fetchall()) == 10
                raise sqlite3.OperationalError("injected create failure")
            if source == "legacy" and normalized.startswith(
                "ALTER TABLE AI_DISPATCHES ADD COLUMN SELECTION_REJECTION_CODE"
            ):
                assert len(super().execute("PRAGMA table_xinfo(ai_dispatches)").fetchall()) == 9
                raise sqlite3.OperationalError("injected intermediate failure")
            return super().execute(sql, parameters)

    def injected(target, **kwargs):
        if target == path:
            kwargs["factory"] = RejectMigration
        return connect(target, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", injected)
        with pytest.raises((ValueError, sqlite3.OperationalError)):
            AIDispatcher(path, budget, client)
    with connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == (
            0 if source == "empty" else 2 if source == "v2-bad-http" else 1)
        if source == "empty":
            assert db.execute("SELECT * FROM sqlite_schema").fetchall() == []
        else:
            metadata = V2_METADATA if source == "v2-bad-http" else V2_METADATA[:8]
            assert db.execute("PRAGMA table_xinfo(ai_dispatches)").fetchall() == metadata
            rows = db.execute("SELECT * FROM ai_dispatches ORDER BY request_id DESC").fetchall()
            assert [row[:8] for row in rows] == ROWS
            if source == "v2-bad-http":
                assert all(row[8] == 99 for row in rows)
