"""Temporary local approval fixtures, never an installed strategy or financial grant."""

import hashlib
import importlib
import json
import sqlite3
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from multi_exchange_trader.execution.modes import StrategyReference
from multi_exchange_trader.execution.runtime_policy import RuntimePolicyBook


def api() -> ModuleType:
    return importlib.import_module("multi_exchange_trader.execution.strategy_publication_v1")


def artifact(name: str = "fixture-alpha", implementation: bytes = b"fixture implementation") -> Any:
    configuration = b'{"window":12,"fixture_only":true}'
    record = {
        "schema_version": 1, "action": "APPROVE_STRATEGY_V1",
        "reference": {"strategy_id": name, "revision": "r1"},
        "implementation_sha256": hashlib.sha256(implementation).hexdigest(),
        "config_sha256": hashlib.sha256(configuration).hexdigest(),
        "config_schema_version": 1, "engine_contract_version": "fixture-engine-v1",
        "permitted_instruments": [{"venue": "BYBIT", "product": "LINEAR", "symbol": "ETHUSDT"}],
        "approval_reference": "fixture-explicit-approval", "approval_source_reference": "fixture-record",
        "approval_environments": ["DEMO"],
    }
    return api().StrategyArtifactBytesV1(implementation, configuration, json.dumps(record).encode())


def fixture_book(tmp_path: Path) -> RuntimePolicyBook:
    book = RuntimePolicyBook(tmp_path / "runtime-policy.sqlite3")
    policy = book.read()["policy"]
    policy["legacy_v1"]["capital_usdt"] = "80.00"
    book.save_v2(0, policy, expected_strategy_generation=None)
    return book


def test_actual_bytes_publish_without_selecting_strategy_or_changing_user_fields(tmp_path: Path) -> None:
    book = fixture_book(tmp_path)
    before = book.read()["policy"]
    owner = api().StrategyPublicationOwnerV1(book)
    supplied = artifact()
    result = owner.publish((supplied,), expected_policy_revision=1, expected_generation=0)
    current = book.read()
    assert (result.generation, result.policy_revision) == (1, 2)
    assert current["schema_version"] == 2 and current["desired_state"] == "STOPPED"
    assert current["live_order_enabled"] is False
    assert current["policy"]["legacy_v1"] == before["legacy_v1"]
    assert current["policy"]["scope"] is current["policy"]["risk_v2"] is None
    assert current["policy"]["strategy"]["allowed"] is None
    assert current["policy"]["strategy"]["selected"] is None
    descriptor = result.catalog.entries[0]
    assert descriptor.implementation_sha256 == hashlib.sha256(supplied.implementation).hexdigest()
    assert descriptor.config_sha256 == hashlib.sha256(supplied.configuration).hexdigest()
    assert descriptor.approval_source_sha256 == hashlib.sha256(supplied.approval_record).hexdigest()
    with sqlite3.connect(book.path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (3,)
        assert db.execute("SELECT implementation,configuration,approval FROM strategy_publication_artifacts_v1").fetchone() == (
            supplied.implementation, supplied.configuration, supplied.approval_record)
    assert api().StrategyPublicationOwnerV1(RuntimePolicyBook(book.path)).current() == result


def test_any_book_enforces_generation_catalog_and_policy_cas(tmp_path: Path) -> None:
    book = fixture_book(tmp_path)
    owner = api().StrategyPublicationOwnerV1(book)
    published = owner.publish((artifact(),), expected_policy_revision=1, expected_generation=0)
    separate = RuntimePolicyBook(book.path)
    policy = separate.read()["policy"]
    for generation in (None, 0, True):
        with pytest.raises(ValueError):
            separate.save_v2(2, policy, expected_strategy_generation=generation, policy_sections_version=1)
    forged = json.loads(json.dumps(policy))
    forged["strategy"]["catalog"] = None
    with pytest.raises(ValueError):
        separate.save_v2(2, forged, expected_strategy_generation=1, policy_sections_version=1)
    policy["legacy_v1"]["capital_usdt"] = "90.00"
    result = separate.save_v2(2, policy, expected_strategy_generation=1, policy_sections_version=1)
    assert result["revision"] == 3
    assert owner.current().policy_revision == 3 and owner.current().generation == published.generation
    with pytest.raises(ValueError):
        owner.publish((artifact("fixture-beta"),), expected_policy_revision=2, expected_generation=1)
    assert separate.read() == result


def test_revocation_prunes_only_affected_references_and_never_reselects(tmp_path: Path) -> None:
    book = fixture_book(tmp_path)
    owner = api().StrategyPublicationOwnerV1(book)
    owner.publish((artifact(), artifact("fixture-beta")), expected_policy_revision=1, expected_generation=0)
    policy = book.read()["policy"]
    refs = [{"strategy_id": name, "revision": "r1"} for name in ("fixture-alpha", "fixture-beta")]
    policy["strategy"].update(allowed=refs, selected=refs)
    book.save_v2(2, policy, expected_strategy_generation=1, policy_sections_version=1)
    result = owner.revoke(StrategyReference("fixture-alpha", "r1"),
                          expected_policy_revision=3, expected_generation=1)
    current = book.read()
    assert (result.generation, current["revision"]) == (2, 4)
    assert current["policy"]["strategy"]["allowed"] == [refs[1]]
    assert current["policy"]["strategy"]["selected"] == [refs[1]]
    assert current["policy"]["legacy_v1"]["capital_usdt"] == "80.00"
    assert result.catalog.entries[0].approval_status == "REVOKED"
    with pytest.raises(ValueError):
        owner.require_current(StrategyReference("fixture-alpha", "r1"), generation=2, policy_revision=4)
    assert owner.require_current(StrategyReference("fixture-beta", "r1"), generation=2,
                                 policy_revision=4) is not None
    owner.publish((artifact(),), expected_policy_revision=4, expected_generation=2)
    assert book.read()["policy"]["strategy"]["selected"] == [refs[1]]


@pytest.mark.parametrize("fault", ["bytes", "approval-action", "duplicate-json", "oversize", "hash-only"])
def test_invalid_or_hash_only_approval_cannot_publish(tmp_path: Path, fault: str) -> None:
    book = fixture_book(tmp_path)
    owner = api().StrategyPublicationOwnerV1(book)
    supplied = artifact()
    implementation, configuration, approval = supplied.implementation, supplied.configuration, supplied.approval_record
    if fault == "bytes":
        implementation += b"changed"
    elif fault == "approval-action":
        record = json.loads(approval)
        record["action"] = "APPROVED"
        approval = json.dumps(record).encode()
    elif fault == "duplicate-json":
        approval = approval[:-1] + b',"schema_version":1}'
    elif fault == "oversize":
        implementation = b"x" * (1024 * 1024 + 1)
    else:
        implementation = b""
    before = book.read()
    with pytest.raises(ValueError):
        owner.publish((api().StrategyArtifactBytesV1(implementation, configuration, approval),),
                      expected_policy_revision=1, expected_generation=0)
    assert book.read() == before and owner.current() is None


def test_reference_content_is_immutable_across_new_valid_approvals(tmp_path: Path) -> None:
    book = fixture_book(tmp_path)
    owner = api().StrategyPublicationOwnerV1(book)
    first = owner.publish((artifact(),), expected_policy_revision=1, expected_generation=0)
    with pytest.raises(ValueError):
        owner.publish((artifact(implementation=b"changed actual implementation"),),
                      expected_policy_revision=2, expected_generation=1)
    assert owner.current() == first


def test_policy_update_failure_rolls_back_publication_and_activation(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    book = fixture_book(tmp_path)
    owner = api().StrategyPublicationOwnerV1(book)
    original = book.connect
    before = book.read()
    def denied() -> sqlite3.Connection:
        db = original()
        db.set_authorizer(lambda action, table, _column, _database, _trigger:
                          sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_UPDATE and table == "runtime_policy_v2"
                          else sqlite3.SQLITE_OK)
        return db
    with monkeypatch.context() as patch:
        patch.setattr(book, "connect", denied)
        with pytest.raises(sqlite3.DatabaseError):
            owner.publish((artifact(),), expected_policy_revision=1, expected_generation=0)
    assert book.read() == before and owner.current() is None
    with sqlite3.connect(book.path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (2,)
        assert db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'strategy_publication_%'").fetchall() == []


def test_partial_or_removed_active_extension_is_not_a_legacy_draft(tmp_path: Path) -> None:
    book = fixture_book(tmp_path)
    owner = api().StrategyPublicationOwnerV1(book)
    owner.publish((artifact(),), expected_policy_revision=1, expected_generation=0)
    with sqlite3.connect(book.path) as db:
        db.execute("DROP TABLE strategy_publication_state_v1")
    with pytest.raises(ValueError):
        RuntimePolicyBook(book.path)
