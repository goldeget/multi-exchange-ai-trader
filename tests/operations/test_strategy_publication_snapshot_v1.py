"""Actual temporary generations through the existing portable recovery command."""

import runpy
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fixtures.strategy_publication_v1 import api, artifact
from test_state_snapshot import TOOL
from test_state_snapshot import state as state_fixture

from multi_exchange_trader.execution.runtime_policy import RuntimePolicyBook

state = state_fixture
OFFLINE: dict[str, Any] = runpy.run_path(str(TOOL))


def test_old_policy_without_publication_stays_valid(state: Path) -> None:
    book = RuntimePolicyBook(state / "runtime-policy.sqlite3")
    with sqlite3.connect(book.path) as db:
        OFFLINE["check_policy_v2"](db)
    assert api().StrategyPublicationOwnerV1(book).current() is None


def test_publication_snapshot_verify_restore_preserves_bytes_and_generation(state: Path,
                                                                         tmp_path: Path) -> None:
    book = RuntimePolicyBook(state / "runtime-policy.sqlite3")
    published = api().StrategyPublicationOwnerV1(book).publish(
        (artifact(),), expected_policy_revision=0, expected_generation=0)
    before = {p.relative_to(state).as_posix(): p.read_bytes() for p in state.rglob("*") if p.is_file()}
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    for args in (("snapshot", "--source", state, "--target", backup, "--writers-stopped"),
                 ("verify", "--source", backup), ("restore", "--source", backup, "--target", restored)):
        assert OFFLINE["main"]([str(arg) for arg in args]) == 0
    assert {p.relative_to(state).as_posix(): p.read_bytes() for p in state.rglob("*") if p.is_file()} == before
    assert {p.relative_to(restored).as_posix(): p.read_bytes() for p in restored.rglob("*") if p.is_file()} == before
    recovered = RuntimePolicyBook(restored / "runtime-policy.sqlite3")
    assert api().StrategyPublicationOwnerV1(recovered).current() == published
    with pytest.raises(ValueError):
        recovered.save_v2(1, recovered.read()["policy"], expected_strategy_generation=None,
                          policy_sections_version=2)


@pytest.mark.parametrize("fault", ["partial", "artifact", "history", "generation"])
def test_tampered_publication_rejects_application_and_offline_boundaries(state: Path, fault: str) -> None:
    book = RuntimePolicyBook(state / "runtime-policy.sqlite3")
    api().StrategyPublicationOwnerV1(book).publish((artifact(),), expected_policy_revision=0,
                                                 expected_generation=0)
    with sqlite3.connect(book.path) as db:
        if fault == "partial":
            db.execute("DROP TABLE strategy_publication_state_v1")
        elif fault == "generation":
            db.execute("UPDATE strategy_publication_state_v1 SET generation=2")
        else:
            table = "strategy_publication_artifacts_v1" if fault == "artifact" else "strategy_publication_history_v1"
            db.execute(f"DROP TRIGGER {table}_no_update")
            if fault == "artifact":
                db.execute("UPDATE strategy_publication_artifacts_v1 SET implementation=x'00'")
            else:
                db.execute("UPDATE strategy_publication_history_v1 SET sha256=?", ("0" * 64,))
    with pytest.raises(ValueError):
        RuntimePolicyBook(book.path)
    with sqlite3.connect(book.path) as db, pytest.raises(OFFLINE["SnapshotError"]):
        OFFLINE["check_policy_v2"](db)
