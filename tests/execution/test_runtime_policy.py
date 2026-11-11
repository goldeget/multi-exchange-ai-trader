import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Event

import pytest
from fixtures.runtime_policy_v1 import load_v1

from multi_exchange_trader.execution.risk import RiskScope
from multi_exchange_trader.execution.runtime_policy import KEYS, RuntimePolicyBook

MAX_REVISION = 2**53 - 1


def legacy(tmp_path, revision=7):
    path = tmp_path / "policy.sqlite3"
    old = load_v1()(path)
    draft = dict.fromkeys(KEYS)
    draft.update(capital_usdt="100.00", leverage_min="1.00", leverage_max="3",
                 liquidation_buffer_fraction="0.010", mode="FULL_AUTO", environment="DEMO",
                 allowed_symbols=["BTCUSDT", "ETHUSDT"], selected_symbols=["ETHUSDT"],
                 allowed_strategies=[], selected_strategies=[])
    raw = json.dumps(draft, indent=2) + "\n "
    with closing(old.connect()) as db:
        db.execute("UPDATE runtime_policy SET payload=?,revision=?", (raw, revision))
        db.commit()
    return path, old, draft, raw


def storage(path):
    with sqlite3.connect(path) as db:
        return db.execute("PRAGMA user_version").fetchone()[0], db.execute(
            "SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall(), {
                name: db.execute(f"SELECT * FROM {name}").fetchall()
                for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def save_v2(book, revision, draft):
    return book.save_v2(revision, draft, expected_strategy_generation=None)


def test_fresh_book_is_null_only_v2_without_invented_history(tmp_path):
    path = tmp_path / "fresh.sqlite3"
    result = RuntimePolicyBook(path).read()
    assert result["schema_version"] == 2
    assert result["policy"] == {"legacy_v1": dict.fromkeys(KEYS), "scope": None,
                                "strategy": None, "risk_v2": None}
    assert result["missing_fields"] == ["risk_v2", "scope", "strategy"]
    assert result["blockers"] == ["POLICY_V2_ADMISSION_REQUIRED",
                                    "NATIVE_RUNTIME_NOT_QUALIFIED", "WORKER_NOT_CONNECTED"]
    assert set(storage(path)[2]) == {"runtime_policy_v2"}


def test_explicit_migration_preserves_original_text_and_fences_real_old_writers(tmp_path):
    path, old, draft, raw = legacy(tmp_path)
    book = RuntimePolicyBook(path)
    before = storage(path)
    assert book.read()["schema_version"] == 1
    with pytest.raises(ValueError, match="POLICY_V1_MIGRATION_REQUIRED"):
        book.risk_policy_v2(typed_scope())
    assert "POLICY_V1_MIGRATION_REQUIRED" in book.read()["blockers"]
    assert storage(path) == before
    result = book.migrate_v1(7)
    assert result["revision"] == 8
    assert result["policy"]["legacy_v1"] == draft
    with sqlite3.connect(path) as db:
        for name in ("runtime_policy", "runtime_policy_v1_archive"):
            assert db.execute(f"SELECT * FROM {name}").fetchall() == [(1, 7, raw, "STOPPED")]
            for operation in (f"UPDATE {name} SET revision=9", f"DELETE FROM {name}",
                              f"INSERT OR REPLACE INTO {name} VALUES(1,9,'{{}}','STOPPED')",
                              f"INSERT INTO {name} VALUES(1,9,'{{}}','STOPPED')"):
                with pytest.raises(sqlite3.IntegrityError, match="policy immutable"):
                    db.execute(operation)
    with pytest.raises(ValueError, match="policy schema version"):
        load_v1()(path)
    with pytest.raises(sqlite3.IntegrityError, match="policy immutable"):
        old.save(7, draft)
    with pytest.raises(ValueError):
        book.save(8, draft)
    with pytest.raises(ValueError):
        book.migrate_v1(8)
    assert book.read() == result


def test_old_save_first_causes_stale_migration_without_partial_archive(tmp_path):
    path, old, draft, _ = legacy(tmp_path)
    book = RuntimePolicyBook(path)
    old.save(7, draft | {"capital_usdt": "90.00"})
    before = storage(path)
    with pytest.raises(ValueError):
        book.migrate_v1(7)
    assert storage(path) == before
    result = book.migrate_v1(8)
    assert result["revision"] == 9
    assert result["policy"]["legacy_v1"]["capital_usdt"] == "90.00"


def test_two_migrators_have_one_winner(tmp_path):
    path, _, _, _ = legacy(tmp_path)
    books = [RuntimePolicyBook(path), RuntimePolicyBook(path)]
    def migrate(book):
        try:
            return book.migrate_v1(7)["revision"]
        except ValueError:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(map(str, pool.map(migrate, books))) == ["8", "conflict"]


@pytest.mark.parametrize("stage", range(1, 12))
def test_every_migration_write_stage_rolls_back(tmp_path, monkeypatch, stage):
    path, _, _, _ = legacy(tmp_path)
    book = RuntimePolicyBook(path)
    before = storage(path)
    class FailingConnection(sqlite3.Connection):
        writes = 0
        def execute(self, sql, parameters=()):
            if sql.startswith(("CREATE ", "INSERT ", "PRAGMA user_version=")):
                self.writes += 1
                if self.writes == stage:
                    raise sqlite3.OperationalError("injected stage")
            return super().execute(sql, parameters)
    def connect():
        db = sqlite3.connect(path, factory=FailingConnection)
        db.row_factory = sqlite3.Row
        return db
    monkeypatch.setattr(book, "connect", connect)
    with pytest.raises(sqlite3.OperationalError, match="injected stage"):
        book.migrate_v1(7)
    assert storage(path) == before


@pytest.mark.parametrize("section", ["scope", "strategy", "risk_v2"])
@pytest.mark.parametrize("value", [{}, [], False, {"future_pin": "preserve"}])
def test_future_stored_sections_cannot_be_erased_even_at_current_revision(tmp_path, monkeypatch, section, value):
    book = RuntimePolicyBook(tmp_path / "policy.sqlite3")
    null_draft = book.read()["policy"]
    future = null_draft | {section: value}
    with closing(book.connect()) as db:
        db.execute("UPDATE runtime_policy_v2 SET payload=?,revision=12", (json.dumps(future),))
        db.commit()
    before = storage(book.path)
    statements = []
    original = book.connect
    def traced():
        db = original()
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr(book, "connect", traced)
    with pytest.raises(ValueError):
        save_v2(book, 12, null_draft)
    assert not any(sql.startswith("UPDATE ") for sql in statements)
    assert storage(book.path) == before


@pytest.mark.parametrize("section", ["scope", "strategy", "risk_v2"])
@pytest.mark.parametrize("value", [{}, [], False])
def test_non_null_submitted_sections_are_refused(tmp_path, section, value):
    book = RuntimePolicyBook(tmp_path / "policy.sqlite3")
    before = storage(book.path)
    with pytest.raises(ValueError):
        save_v2(book, 0, book.read()["policy"] | {section: value})
    assert storage(book.path) == before


@pytest.mark.parametrize("revision", [True, 1.0, -1, MAX_REVISION + 1])
def test_revision_wire_bound_is_exact_and_nonmutating(tmp_path, revision):
    path, _, _, _ = legacy(tmp_path)
    book = RuntimePolicyBook(path)
    before = storage(path)
    with pytest.raises(ValueError):
        book.migrate_v1(revision)
    assert storage(path) == before


@pytest.mark.parametrize("revision", [MAX_REVISION, MAX_REVISION + 1])
def test_overflow_or_unsupported_existing_revision_is_preserved(tmp_path, revision):
    path, _, _, _ = legacy(tmp_path, revision)
    before = storage(path)
    if revision > MAX_REVISION:
        with pytest.raises(ValueError):
            RuntimePolicyBook(path)
    else:
        with pytest.raises(ValueError):
            RuntimePolicyBook(path).migrate_v1(revision)
    assert storage(path) == before


def test_v2_risk_refusal_precedes_legacy_key_lookup(tmp_path):
    book = RuntimePolicyBook(tmp_path / "policy.sqlite3")
    with pytest.raises(ValueError, match="POLICY_V2_ADMISSION_REQUIRED"):
        book.risk_policy(RiskScope("a", "LIVE", "BTCUSDT", "s"))


@pytest.mark.parametrize("sql", ["PRAGMA user_version=0", "PRAGMA user_version=3",
    "DELETE FROM runtime_policy", "ALTER TABLE runtime_policy ADD COLUMN extra TEXT"])
def test_contradictory_storage_is_never_repaired(tmp_path, sql):
    path, _, _, _ = legacy(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute(sql)
    before = storage(path)
    with pytest.raises(ValueError):
        RuntimePolicyBook(path)
    assert storage(path) == before


def test_unset_settings_survive_restart_without_authority(tmp_path):
    path = tmp_path / "policy.sqlite3"
    book = RuntimePolicyBook(path)
    envelope = book.read()["policy"]
    draft = envelope["legacy_v1"]
    assert set(draft) == set(KEYS)
    assert all(value is None for value in draft.values())
    draft.update(capital_usdt="100", leverage_min="1", leverage_max="3")
    result = save_v2(book, 0, envelope)
    assert result["live_order_enabled"] is False
    assert result["desired_state"] == "STOPPED"
    assert result["missing_fields"] == ["risk_v2", "scope", "strategy"]
    assert RuntimePolicyBook(path).read() == result
    with pytest.raises(ValueError):
        book.risk_policy(RiskScope("a", "LIVE", "BTCUSDT", "s"))


def test_concurrent_policy_updates_do_not_overwrite(tmp_path):
    book = RuntimePolicyBook(tmp_path / "policy.sqlite3")
    draft = book.read()["policy"]

    def save(_):
        try:
            return save_v2(book, 0, draft)["revision"]
        except ValueError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(map(str, pool.map(save, range(2)))) == ["1", "conflict"]


@pytest.mark.parametrize("patch", [
    {"capital_usdt": True}, {"capital_usdt": "1e5"},
    {"leverage_min": "4", "leverage_max": "3"},
    {"allowed_symbols": ["BTCUSDT"], "selected_symbols": ["ETHUSDT"]},
    {"allowed_symbols": [[], "BTCUSDT"]},
    {"capital_usdt": "100", "max_total_margin": "101"},
    {"liquidation_buffer_fraction": "1"}, {"max_positions": True},
])
def test_invalid_policy_leaves_revision_unchanged(tmp_path, patch):
    book = RuntimePolicyBook(tmp_path / "policy.sqlite3")
    envelope = book.read()["policy"]
    draft = envelope | {"legacy_v1": envelope["legacy_v1"] | patch}
    with pytest.raises(ValueError):
        save_v2(book, 0, draft)
    assert book.read()["revision"] == 0


@pytest.mark.parametrize("section", ["legacy_v1", "scope", "strategy", "risk_v2", "extra"])
@pytest.mark.parametrize("stored", [False, True])
def test_missing_or_unknown_full_sections_fail_without_update(tmp_path, section, stored):
    book = RuntimePolicyBook(tmp_path / "policy.sqlite3")
    original = book.read()["policy"]
    invalid = {key: value for key, value in original.items() if key != section}
    if section == "extra":
        invalid[section] = None
    if stored:
        with closing(book.connect()) as db:
            db.execute("UPDATE runtime_policy_v2 SET payload=?", (json.dumps(invalid),))
            db.commit()
    before = storage(book.path)
    with pytest.raises(ValueError):
        save_v2(book, 0, original if stored else invalid)
    assert storage(book.path) == before


@pytest.mark.parametrize("revision", [True, 1.0, -1, MAX_REVISION, MAX_REVISION + 1])
def test_v2_revision_and_increment_overflow_preserve_storage(tmp_path, revision):
    book = RuntimePolicyBook(tmp_path / "policy.sqlite3")
    draft = book.read()["policy"]
    if type(revision) is int and revision >= MAX_REVISION:
        with closing(book.connect()) as db:
            db.execute("UPDATE runtime_policy_v2 SET revision=?", (revision,))
            db.commit()
    before = storage(book.path)
    with pytest.raises(ValueError):
        save_v2(book, revision, draft)
    assert storage(book.path) == before


def test_last_safe_revision_is_reachable_and_readable_without_rounding(tmp_path):
    book = RuntimePolicyBook(tmp_path / "policy.sqlite3")
    draft = book.read()["policy"]
    with closing(book.connect()) as db:
        db.execute("UPDATE runtime_policy_v2 SET revision=?", (MAX_REVISION - 1,))
        db.commit()
    result = save_v2(book, MAX_REVISION - 1, draft)
    assert result["revision"] == 9007199254740991
    assert type(result["revision"]) is int
    assert RuntimePolicyBook(book.path).read() == result
    before = storage(book.path)
    with pytest.raises(ValueError):
        save_v2(book, MAX_REVISION, draft)
    assert storage(book.path) == before


@pytest.mark.parametrize("generation", [0, False, "1", {}])
def test_no_strategy_generation_is_invented(tmp_path, generation):
    book = RuntimePolicyBook(tmp_path / "policy.sqlite3")
    before = storage(book.path)
    with pytest.raises(ValueError):
        book.save_v2(0, book.read()["policy"], expected_strategy_generation=generation)
    assert storage(book.path) == before


def test_migrated_revision_cannot_precede_its_archive(tmp_path):
    path, _, _, _ = legacy(tmp_path)
    book = RuntimePolicyBook(path)
    book.migrate_v1(7)
    with closing(book.connect()) as db:
        db.execute("UPDATE runtime_policy_v2 SET revision=7")
        db.commit()
    before = storage(path)
    with pytest.raises(ValueError):
        book.read()
    assert storage(path) == before


def test_final_read_failure_rolls_back_complete_migration(tmp_path, monkeypatch):
    path, _, _, _ = legacy(tmp_path)
    book = RuntimePolicyBook(path)
    before = storage(path)
    original = book._read
    calls = 0
    def fail_final(db):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("final validation failure")
        return original(db)
    monkeypatch.setattr(book, "_read", fail_final)
    with pytest.raises(ValueError, match="final validation failure"):
        book.migrate_v1(7)
    assert storage(path) == before


@pytest.mark.parametrize("first", ["old", "migration"])
def test_old_save_and_migration_serialize_in_both_lock_orders(tmp_path, monkeypatch, first):
    path, old, draft, raw = legacy(tmp_path)
    book = RuntimePolicyBook(path)
    locked, release = Event(), Event()
    owner = old if first == "old" else book
    class HoldingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            cursor = super().execute(sql, parameters)
            if sql == "BEGIN IMMEDIATE":
                locked.set()
                assert release.wait(5)
            return cursor
    def connect():
        db = sqlite3.connect(path, timeout=10, factory=HoldingConnection)
        db.row_factory = sqlite3.Row
        return db
    monkeypatch.setattr(owner, "connect", connect)
    def old_save():
        return old.save(7, draft | {"capital_usdt": "90.00"})
    def migrate():
        return book.migrate_v1(7)
    with ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(old_save if first == "old" else migrate)
        assert locked.wait(5)
        loser = pool.submit(migrate if first == "old" else old_save)
        release.set()
        assert winner.result()["revision"] == 8
        with pytest.raises(ValueError if first == "old" else sqlite3.IntegrityError):
            loser.result()
    if first == "old":
        assert book.read()["schema_version"] == 1
        assert book.read()["policy"]["capital_usdt"] == "90.00"
    else:
        assert book.read()["schema_version"] == 2
        assert storage(path)[2]["runtime_policy_v1_archive"] == [(1, 7, raw, "STOPPED")]


def test_commit_failure_rolls_back_migration(tmp_path, monkeypatch):
    path, _, _, _ = legacy(tmp_path)
    book = RuntimePolicyBook(path)
    before = storage(path)
    class CommitFailure(sqlite3.Connection):
        def __exit__(self, exc_type, exc, traceback):
            if exc_type is None:
                self.rollback()
                raise sqlite3.OperationalError("commit refused")
            return super().__exit__(exc_type, exc, traceback)
    def connect():
        db = sqlite3.connect(path, factory=CommitFailure)
        db.row_factory = sqlite3.Row
        return db
    monkeypatch.setattr(book, "connect", connect)
    with pytest.raises(sqlite3.OperationalError, match="commit refused"):
        book.migrate_v1(7)
    assert storage(path) == before


def typed_scope(symbol="BTCUSDT", strategy="alpha", product="LINEAR", account="fixture-account"):
    from multi_exchange_trader.execution.modes import StrategyReference
    from multi_exchange_trader.execution.product_scope import ProductScope
    from multi_exchange_trader.execution.protection import ProtectionScope
    from multi_exchange_trader.execution.risk_v2_contracts import RiskScopeV2
    from multi_exchange_trader.trading_product import InstrumentKey
    return RiskScopeV2(ProductScope(ProtectionScope(account, "DEMO"), product),
                       InstrumentKey("BYBIT", product, symbol), StrategyReference(strategy, "r1"))


def projection_book(policy, revision=17):
    from multi_exchange_trader.execution.policy_sections import section_missing_fields
    book = object.__new__(RuntimePolicyBook)
    book.read = lambda: {"schema_version": 2, "revision": revision, "policy": policy,
                         "missing_fields": section_missing_fields(policy)}
    return book


@pytest.mark.parametrize("mode", ["FULL_AUTO", "ALL_IN_ONE"])
def test_static_projection_uses_exact_heterogeneous_pairs_and_supplied_scope(mode, monkeypatch):
    from decimal import Decimal

    from test_policy_sections import policy_fixture

    from multi_exchange_trader.execution import runtime_policy
    policy = policy_fixture(mode)
    book = projection_book(policy)
    calls = []
    real = runtime_policy.require_strategy_references
    def capture(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)
    monkeypatch.setattr(runtime_policy, "require_strategy_references", capture)
    for symbol, strategy in (("BTCUSDT", "alpha"), ("ETHUSDT", "beta")):
        scope = typed_scope(symbol, strategy)
        result = book.risk_policy_v2(scope)
        assert result.scope == scope and result.revision == "17"
        assert result.caps.max_order_admission_notional == Decimal("100.00")
        assert result.margin_mode == "CROSS"
        assert tuple(ref.strategy_id for ref in calls[-1]["allowed"]) == (strategy,)
    for symbol, strategy in (("BTCUSDT", "beta"), ("ETHUSDT", "alpha")):
        with pytest.raises(ValueError, match="RISK_V2_SCOPE_MISMATCH"):
            book.risk_policy_v2(typed_scope(symbol, strategy))


def test_orphan_missing_path_and_cap_migration_precedence():
    from test_policy_sections import policy_fixture

    from multi_exchange_trader.execution.policy_sections import section_missing_fields
    policy = policy_fixture()
    orphan = {"venue": "BYBIT", "product": "LINEAR", "symbol": "SOLUSDT"}
    policy["scope"]["allowed_instruments"].append(orphan)
    policy["scope"]["selected_instruments"].append(orphan)
    assert section_missing_fields(policy) == ["strategy.eligible_pairs"]
    with pytest.raises(ValueError, match="POLICY_V2_ADMISSION_REQUIRED"):
        projection_book(policy).risk_policy_v2(typed_scope())
    policy["risk_v2"]["caps"]["valuation_rule"] = None
    policy["legacy_v1"]["max_total_notional"] = "999"
    with pytest.raises(ValueError, match="RISK_V2_CAP_POLICY_MIGRATION_REQUIRED"):
        projection_book(policy).risk_policy_v2(typed_scope())


def test_typed_writer_guard_old_frozen_writer_and_capable_reset(tmp_path):
    import importlib.util
    from pathlib import Path

    from test_policy_sections import policy_fixture
    path = tmp_path / "typed.sqlite3"
    spec = importlib.util.spec_from_file_location("multi_exchange_trader.execution.frozen_null_writer",
        Path(__file__).parent / "fixtures/runtime_policy_v2_null_only.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    old = module.RuntimePolicyBook(path)
    book = RuntimePolicyBook(path)
    null_policy = book.read()["policy"]
    typed = policy_fixture()
    result = book.save_v2(0, typed, expected_strategy_generation=None, policy_sections_version=2)
    assert result["policy"] == typed and result["missing_fields"] == []
    assert "STRATEGY_CURRENT_PUBLICATION_REQUIRED" in result["blockers"]
    with pytest.raises(ValueError):
        module.RuntimePolicyBook(path)
    before = storage(path)
    for writer in (old, book):
        with pytest.raises(ValueError):
            writer.save_v2(1, null_policy, expected_strategy_generation=None)
    assert storage(path) == before
    assert RuntimePolicyBook(path).read() == result
    reset = book.save_v2(1, null_policy, expected_strategy_generation=None, policy_sections_version=2)
    assert reset["revision"] == 2
    spot = policy_fixture()
    spot["scope"].update(selected_product="SPOT", allowed_products=["SPOT"])
    with pytest.raises(ValueError):
        book.save_v2(2, spot, expected_strategy_generation=None, policy_sections_version=2)
    assert book.read() == reset
    spot["scope"].update(allowed_instruments=None, selected_instruments=None)
    spot["strategy"].update(allowed=None, selected=None)
    spot["risk_v2"] = None
    switched = book.save_v2(2, spot, expected_strategy_generation=None, policy_sections_version=2)
    assert switched["policy"] == spot
    assert "POLICY_SPOT_RISK_UNSUPPORTED" in switched["blockers"]
    assert RuntimePolicyBook(path).read() == switched


def test_hard_missing_applicable_turnover_amount_precedes_orphan_admission():
    from test_policy_sections import policy_fixture

    from multi_exchange_trader.execution.policy_sections import section_missing_fields
    policy = policy_fixture()
    key = {"venue": "BYBIT", "product": "LINEAR", "symbol": "SOLUSDT"}
    policy["scope"]["allowed_instruments"].append(key)
    policy["scope"]["selected_instruments"].append(key)
    policy["risk_v2"]["caps"]["turnover_requirement"] = "HARD_EXECUTION_QUOTE_CAP"
    assert section_missing_fields(policy) == ["risk_v2.caps.max_execution_quote_amount", "strategy.eligible_pairs"]
    with pytest.raises(ValueError, match="RISK_V2_CAP_POLICY_MIGRATION_REQUIRED"):
        projection_book(policy).risk_policy_v2(typed_scope())


def test_projection_uses_genuine_multiple_eligible_subsets_in_original_orders(monkeypatch):
    from test_policy_sections import policy_fixture

    from multi_exchange_trader.execution import runtime_policy
    from multi_exchange_trader.execution.policy_sections import catalog_from_wire
    policy = policy_fixture()
    entries = policy["strategy"]["catalog"]["entries"]
    entries[1]["permitted_instruments"].insert(0, policy["scope"]["allowed_instruments"][0])
    policy["strategy"]["catalog"]["revision"] = catalog_from_wire(policy["strategy"]["catalog"], check_revision=False).revision
    policy["strategy"]["allowed"].reverse()
    calls = []
    actual = runtime_policy.require_strategy_references
    def capture(*args, **kwargs):
        calls.append(kwargs)
        return actual(*args, **kwargs)
    monkeypatch.setattr(runtime_policy, "require_strategy_references", capture)
    result = projection_book(policy).risk_policy_v2(typed_scope())
    assert result.scope == typed_scope()
    assert [ref.strategy_id for ref in calls[0]["allowed"]] == ["beta", "alpha"]
    assert [ref.strategy_id for ref in calls[0]["selected"]] == ["alpha", "beta"]


@pytest.mark.parametrize("fault", ["account", "environment", "instrument", "reference", "spot", "malformed"])
def test_projection_mismatch_unsupported_and_invalid_stored_boundaries(fault):
    from test_policy_sections import policy_fixture
    policy = policy_fixture()
    scope = typed_scope()
    error = "RISK_V2_SCOPE_MISMATCH"
    if fault == "account":
        scope = typed_scope(account="different")
    elif fault == "environment":
        from dataclasses import replace
        scope = replace(scope, product_scope=replace(scope.product_scope, account=replace(scope.product_scope.account, environment="LIVE")))
    elif fault == "instrument":
        scope = typed_scope("SOLUSDT")
    elif fault == "reference":
        scope = typed_scope(strategy="unknown")
    elif fault == "spot":
        policy["scope"].update(selected_product="SPOT", allowed_products=["SPOT"], allowed_instruments=None, selected_instruments=None)
        policy["risk_v2"] = policy["strategy"] = None
        error = "RISK_V2_UNSUPPORTED_PRODUCT"
    else:
        policy["risk_v2"]["caps"] = None
        policy["scope"]["section_version"] = 99
        error = "POLICY_SECTIONS_INVALID"
    with pytest.raises(ValueError, match=error):
        projection_book(policy).risk_policy_v2(scope)


@pytest.mark.parametrize("stage", ["reread", "commit"])
def test_typed_save_refusal_rolls_back_policy_and_revision(tmp_path, monkeypatch, stage):
    from test_policy_sections import policy_fixture
    book = RuntimePolicyBook(tmp_path / "typed.sqlite3")
    before = storage(book.path)
    if stage == "reread":
        original = book._read
        calls = 0
        def read(db):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError("final typed validation")
            return original(db)
        monkeypatch.setattr(book, "_read", read)
    else:
        class CommitFailure(sqlite3.Connection):
            def __exit__(self, exc_type, exc, traceback):
                if exc_type is None:
                    self.rollback()
                    raise sqlite3.OperationalError("typed commit refusal")
                return super().__exit__(exc_type, exc, traceback)
        def connect():
            db = sqlite3.connect(book.path, factory=CommitFailure)
            db.row_factory = sqlite3.Row
            return db
        monkeypatch.setattr(book, "connect", connect)
    with pytest.raises((ValueError, sqlite3.OperationalError)):
        book.save_v2(0, policy_fixture(), expected_strategy_generation=None, policy_sections_version=2)
    assert storage(book.path) == before


def test_mode_specific_orphan_coverage_and_optional_selection_are_exact():
    from test_policy_sections import policy_fixture

    from multi_exchange_trader.execution.policy_sections import section_missing_fields
    policy = policy_fixture()
    orphan = {"venue": "BYBIT", "product": "LINEAR", "symbol": "SOLUSDT"}
    policy["scope"]["allowed_instruments"].append(orphan)
    # An unselected allowed key cannot orphan FULL_AUTO's selected universe.
    assert section_missing_fields(policy) == []
    assert projection_book(policy).risk_policy_v2(typed_scope()).scope == typed_scope()
    policy["scope"]["mode"] = "ALL_IN_ONE"
    policy["scope"]["selected_instruments"] = None
    policy["strategy"]["selected"] = None
    assert section_missing_fields(policy) == ["strategy.eligible_pairs"]
    with pytest.raises(ValueError, match="POLICY_V2_ADMISSION_REQUIRED"):
        projection_book(policy).risk_policy_v2(typed_scope())
    policy["scope"]["allowed_instruments"].pop()
    assert section_missing_fields(policy) == []
    assert projection_book(policy).risk_policy_v2(typed_scope("ETHUSDT", "beta")).scope == typed_scope("ETHUSDT", "beta")


def test_projection_never_translates_legacy_capital_or_notional_and_binds_global_revision():
    from decimal import Decimal

    from test_policy_sections import policy_fixture

    from multi_exchange_trader.execution.risk_v2_contracts import risk_policy_sha256_v2
    policy = policy_fixture()
    policy["legacy_v1"].update(capital_usdt="1000", max_total_notional="1", leverage_max="80")
    first = projection_book(policy, 7).risk_policy_v2(typed_scope())
    second = projection_book(policy, 8).risk_policy_v2(typed_scope())
    assert first.caps.max_order_admission_notional == Decimal("100.00")
    assert first.caps.max_account_admission_notional == Decimal(200)
    assert first.leverage_max == Decimal("1.00")
    assert first.revision == "7" and second.revision == "8"
    assert risk_policy_sha256_v2(first) != risk_policy_sha256_v2(second)


def test_raw_stored_v2_size_bound_precedes_json_parsing_without_update(tmp_path, monkeypatch):
    book = RuntimePolicyBook(tmp_path / "typed.sqlite3")
    policy = book.read()["policy"]
    with sqlite3.connect(book.path) as db:
        db.execute("UPDATE runtime_policy_v2 SET payload=?", (json.dumps(policy) + " " * 65536,))
    before = storage(book.path)
    with pytest.raises(ValueError, match="policy body limit"):
        book.save_v2(0, policy, expected_strategy_generation=None, policy_sections_version=2)
    assert storage(book.path) == before


def test_uninitialized_scope_is_a_finite_boundary_rejection():
    from test_policy_sections import policy_fixture

    from multi_exchange_trader.execution.risk_v2_contracts import RiskScopeV2
    with pytest.raises(ValueError, match="RISK_V2_SCOPE_MISMATCH"):
        projection_book(policy_fixture()).risk_policy_v2(object.__new__(RiskScopeV2))
