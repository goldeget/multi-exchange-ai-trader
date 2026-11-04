"""Synthetic ledgers only; original accounting/dispatcher, no provider request."""

import hashlib
import json
import runpy
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from multi_exchange_trader.ai.budget import BudgetBook, money
from multi_exchange_trader.ai.dispatch import AIDispatcher
from multi_exchange_trader.ai.generation import OpenRouterGenerationClient, quote_from_metadata
from multi_exchange_trader.ai.openrouter_metadata import (
    OpenRouterModelMetadata,
    OpenRouterModelResult,
)
from multi_exchange_trader.execution.modes import (
    Candidate,
    PolicyReference,
    PublicSnapshot,
    SelectionLimits,
    SelectionRequest,
    SelectionScope,
    StrategyReference,
    build_all_in_one_request,
)

TOOL = Path(__file__).resolve().parents[2] / "scripts/operations/import_ai_history.py"
NOW = datetime.now(UTC)
MODEL = "test/fixture-model"


class NoGeneration:
    def generate(self, *_args: object, **_kwargs: object) -> None:
        pytest.fail("offline accounting must never generate")


def request(identifier: str) -> SelectionRequest:
    strategy = StrategyReference("trend", "v1")
    scope = SelectionScope("fixture-account", "DEMO", ("BTCUSDT",), (strategy,),
                           ("BTCUSDT",), (strategy,))
    policy = PolicyReference("fixture-account", "DEMO", "policy-v1")
    snapshot = PublicSnapshot("snapshot", NOW, NOW + timedelta(seconds=30))
    candidate = Candidate("candidate", "fixture-account", "DEMO", "BTCUSDT", strategy,
        "fixture-signal", "snapshot", "policy-v1", "fixture-plan", True,
        NOW, NOW + timedelta(seconds=30))
    return build_all_in_one_request(request_id=identifier, scope=scope, policy=policy,
        snapshot=snapshot, candidates=(candidate,), limits=SelectionLimits(4, 4096, 120, 80, 60, 60, 60, 60),
        now=NOW, expires_at=NOW + timedelta(seconds=30))


def price_quote() -> Any:
    metadata = OpenRouterModelResult(MODEL, "CATALOGUE_FOUND", None, NOW,
        OpenRouterModelMetadata(MODEL, "Fixture", 4096,
            (("prompt", "0.000001"), ("completion", "0.000002"))))
    return quote_from_metadata(metadata, now=NOW)


def new_pair(root: Path) -> tuple[Path, Path]:
    root.mkdir()
    budget, dispatch = root / "budget.sqlite3", root / "dispatch.sqlite3"
    book = BudgetBook(budget)
    AIDispatcher(dispatch, book, cast(OpenRouterGenerationClient, NoGeneration()))
    return budget, dispatch


def rows(path: Path, table: str) -> list[tuple[Any, ...]]:
    assert table in {"ai_settings", "reservations", "ai_dispatches"}
    with sqlite3.connect(path) as db:
        return db.execute('SELECT * FROM "' + table + '" ORDER BY 1').fetchall()


@pytest.fixture
def ledgers(tmp_path: Path) -> dict[str, Any]:
    canonical = new_pair(tmp_path / "canonical")
    book = BudgetBook(canonical[0])
    book.configure(0, "0")
    haiku, qwen = new_pair(tmp_path / "haiku"), new_pair(tmp_path / "qwen")
    requests = (request("fixture-haiku-one"), request("fixture-haiku-two"), request("fixture-qwen"))
    quoted = price_quote()
    for pair, chosen, costs in ((haiku, requests[:2], ("0.00010000", "0.00006450")),
                                (qwen, requests[2:], (None,))):
        source = BudgetBook(pair[0])
        source.configure(0, "1")
        for item, cost in zip(chosen, costs, strict=True):
            reservation = "dispatch_" + hashlib.sha256(item.request_id.encode()).hexdigest()
            source.reserve(reservation, "0.01")
            source.uncertain(reservation)
            if cost is not None:
                source.settle(reservation, cost)
            binding = hashlib.sha256((repr(item) + repr(quoted) + MODEL + money(Decimal("0.01"))).encode()).hexdigest()
            stamp = NOW.isoformat()
            with sqlite3.connect(pair[1]) as db:
                db.execute("INSERT INTO ai_dispatches VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (item.request_id, binding, "0.01000000", "SETTLED" if cost else "UNCERTAIN",
                     None if cost else "COST_UNAVAILABLE", cost, stamp, stamp, None, None))
    with sqlite3.connect(qwen[1]) as db:
        db.execute("ALTER TABLE ai_dispatches DROP COLUMN selection_rejection_code")
        db.execute("ALTER TABLE ai_dispatches DROP COLUMN http_status")
        db.execute("PRAGMA user_version=1")
    return {"canonical": canonical, "sources": (("haiku_20261103", *haiku), ("qwen_20261103", *qwen)),
        "output": (tmp_path / "staging" / "budget.sqlite3", tmp_path / "staging" / "dispatch.sqlite3"),
        "requests": requests, "quote": quoted}


def call_import(ledgers: dict[str, Any], **changes: Any) -> dict[str, Any]:
    api: dict[str, Any] = runpy.run_path(str(TOOL))
    arguments = {"canonical_budget": ledgers["canonical"][0], "canonical_dispatch": ledgers["canonical"][1],
        "sources": ledgers["sources"], "staging_budget": ledgers["output"][0],
        "staging_dispatch": ledgers["output"][1]}
    arguments.update(changes)
    return cast(dict[str, Any], api["import_ai_history"](**arguments))


def hashes(ledgers: dict[str, Any]) -> dict[Path, str]:
    paths = (*ledgers["canonical"], *(p for source in ledgers["sources"] for p in source[1:]))
    return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def test_merge_keeps_settings_unknown_liability_and_original_bytes(ledgers: dict[str, Any]) -> None:
    before = hashes(ledgers)
    settings = rows(ledgers["canonical"][0], "ai_settings")
    result = call_import(ledgers)
    assert result["state"] == "STAGED_OFFLINE"
    assert result["used"] == "0.00016450"
    assert result["reserved"] == result["uncertain"] == "0.01000000"
    assert result["reservations"] == result["dispatches"] == 3
    assert rows(ledgers["output"][0], "ai_settings") == settings == [(1, 1, "0.00000000")]
    assert hashes(ledgers) == before
    assert len(rows(ledgers["sources"][1][2], "ai_dispatches")[0]) == 8
    assert all(len(row) == 10 for row in rows(ledgers["output"][1], "ai_dispatches"))
    expected = sorted(row for source in ledgers["sources"] for row in rows(source[1], "reservations"))
    assert rows(ledgers["output"][0], "reservations") == expected
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert {item["system"] for item in manifest["inputs"]} == {"canonical", "haiku_20261103", "qwen_20261103"}
    assert all(set(item["systems"]) <= {"canonical", "haiku_20261103", "qwen_20261103"}
               for item in manifest["row_provenance"])
    assert "fixture-haiku-one" not in json.dumps(manifest)


def test_reimport_same_inputs_is_noop_and_identical_rows_deduplicate(ledgers: dict[str, Any]) -> None:
    extra = ("same_haiku", *ledgers["sources"][0][1:])
    sources = (*ledgers["sources"], extra)
    first = call_import(ledgers, sources=sources)
    before = {path: path.read_bytes() for path in ledgers["output"]}
    second = call_import(ledgers, sources=sources)
    assert first == second
    assert first["reservations"] == first["dispatches"] == 3
    assert all(path.read_bytes() == data for path, data in before.items())


@pytest.mark.parametrize("table,column,value", [("reservations", "maximum", "0.02000000"),
    ("ai_dispatches", "binding_hash", "different-binding")])
def test_identity_conflict_does_not_publish_either_file(ledgers: dict[str, Any], tmp_path: Path,
                                                      table: str, column: str, value: str) -> None:
    conflict = new_pair(tmp_path / "conflict")
    source = ledgers["sources"][0]
    for index, name in enumerate(("reservations", "ai_dispatches")):
        with sqlite3.connect(conflict[index]) as db:
            data = rows(source[index + 1], name)
            db.executemany('INSERT INTO "' + name + '" VALUES (' + ','.join('?' for _ in data[0]) + ')', data)
    index = int(table == "ai_dispatches")
    with sqlite3.connect(conflict[index]) as db:
        db.execute('UPDATE "' + table + '" SET "' + column + '"=?', (value,))
    before = hashes(ledgers)
    with pytest.raises(ValueError, match="ROW_IDENTITY_CONFLICT"):
        call_import(ledgers, sources=(*ledgers["sources"], ("conflict", *conflict)))
    assert not any(path.exists() for path in ledgers["output"])
    assert hashes(ledgers) == before


def test_original_dispatcher_refuses_every_imported_id_without_generation(ledgers: dict[str, Any]) -> None:
    call_import(ledgers)
    calls: list[httpx.Request] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("no provider transport may be used")

    client = OpenRouterGenerationClient("sk-or-v1-SYNTHETIC_ONLY_NEVER_REAL", MODEL,
        transport=httpx.MockTransport(refuse))
    book = BudgetBook(ledgers["output"][0])
    original = AIDispatcher(ledgers["output"][1], book, client, clock=lambda: NOW)
    for item in ledgers["requests"]:
        result = original.dispatch(item, maximum_cost_usd="0.01", quote=ledgers["quote"], enabled=True)
        assert result.status == "BLOCKED" and result.failure == "ALREADY_DISPATCHED"
    assert calls == []
    assert book.summary()["uncertain"] == "0.01000000"


def test_nonquiescent_source_and_existing_destination_are_not_overwritten(ledgers: dict[str, Any]) -> None:
    wal = ledgers["sources"][0][1].with_name("budget.sqlite3-wal")
    wal.write_bytes(b"uncheckpointed")
    with pytest.raises(ValueError, match="QUIESCENT_SQLITE_REQUIRED"):
        call_import(ledgers)
    wal.unlink()
    output = ledgers["output"][0]
    output.parent.mkdir()
    output.write_bytes(b"preserve-existing-output")
    with pytest.raises(ValueError, match="OUTPUT_ALREADY_EXISTS"):
        call_import(ledgers)
    assert output.read_bytes() == b"preserve-existing-output"


def test_changed_source_during_merge_aborts_publication(ledgers: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    api: dict[str, Any] = runpy.run_path(str(TOOL))
    original: Callable[..., Any] = api["_merge_rows"]

    def change(*args: Any, **kwargs: Any) -> Any:
        value = original(*args, **kwargs)
        with sqlite3.connect(ledgers["sources"][0][1]) as db:
            db.execute("UPDATE ai_settings SET revision=revision+1")
        return value

    monkeypatch.setitem(api["import_ai_history"].__globals__, "_merge_rows", change)
    with pytest.raises(ValueError, match="INPUT_CHANGED"):
        api["import_ai_history"](canonical_budget=ledgers["canonical"][0],
            canonical_dispatch=ledgers["canonical"][1], sources=ledgers["sources"],
            staging_budget=ledgers["output"][0], staging_dispatch=ledgers["output"][1])
    assert not any(path.exists() for path in ledgers["output"])


def test_missing_canonical_dispatch_appearing_during_import_aborts(ledgers: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    canonical_dispatch = ledgers["canonical"][1]
    canonical_dispatch.unlink()
    api: dict[str, Any] = runpy.run_path(str(TOOL))
    original: Callable[..., Any] = api["_merge_rows"]

    def appear(*args: Any, **kwargs: Any) -> Any:
        value = original(*args, **kwargs)
        with sqlite3.connect(canonical_dispatch) as db:
            db.execute("CREATE TABLE concurrent_owner (id INTEGER)")
        return value

    monkeypatch.setitem(api["import_ai_history"].__globals__, "_merge_rows", appear)
    with pytest.raises(ValueError, match="INPUT_CHANGED"):
        api["import_ai_history"](canonical_budget=ledgers["canonical"][0],
            canonical_dispatch=canonical_dispatch, sources=ledgers["sources"],
            staging_budget=ledgers["output"][0], staging_dispatch=ledgers["output"][1])
    assert not any(path.exists() for path in ledgers["output"])


def test_charged_settlement_cost_mismatch_refuses_both_outputs(ledgers: dict[str, Any]) -> None:
    source_budget, source_dispatch = ledgers["sources"][1][1:]
    identifier = ledgers["requests"][2].request_id
    reservation = "dispatch_" + hashlib.sha256(identifier.encode()).hexdigest()
    BudgetBook(source_budget).settle(reservation, "0")
    with sqlite3.connect(source_dispatch) as db:
        db.execute("UPDATE ai_dispatches SET status='CHARGED',exact_cost='0.00500000'")
    with pytest.raises(ValueError, match="CROSS_LEDGER_MISMATCH"):
        call_import(ledgers)
    assert not any(path.exists() for path in ledgers["output"])


def test_charged_unsettled_crash_retains_known_cost_and_full_hold(ledgers: dict[str, Any]) -> None:
    source_budget, source_dispatch = ledgers["sources"][1][1:]
    before = rows(source_budget, "reservations")
    with sqlite3.connect(source_dispatch) as db:
        db.execute("UPDATE ai_dispatches SET status='CHARGED',exact_cost='0.00500000'")
    report = call_import(ledgers)
    assert report["state"] == "STAGED_OFFLINE"
    assert report["used"] == "0.00016450"
    assert report["reserved"] == report["uncertain"] == "0.01000000"
    assert rows(source_budget, "reservations") == before
    assert any(row[3] == "CHARGED" and row[5] == "0.00500000"
               for row in rows(ledgers["output"][1], "ai_dispatches"))
