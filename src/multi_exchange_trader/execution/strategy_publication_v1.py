"""Explicit server-internal artifact publication, never financial authorization.

The caller must authorize the local operator action before calling publish/revoke.
Actual bytes are retained, never imported or executed. Public UI/provider metadata
must not call these methods. Returned snapshots are ordinary inspectable data.
"""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn

from .modes import StrategyReference
from .policy_sections import _catalog_wire, _reference_wire, catalog_from_wire
from .strategy_catalog import StrategyCatalog, StrategyDescriptor

if TYPE_CHECKING:
    from .runtime_policy import RuntimePolicyBook

# BEGIN PORTABLE STRATEGY PUBLICATION V1 -- copied unchanged into state_snapshot.
PUBLICATION_TABLES_V1 = {
    "strategy_publication_state_v1":
        "(id INTEGER PRIMARY KEY CHECK(id=1),generation INTEGER NOT NULL,policy_revision INTEGER NOT NULL)",
    "strategy_publication_artifacts_v1":
        "(id INTEGER PRIMARY KEY,implementation BLOB NOT NULL,configuration BLOB NOT NULL,approval BLOB NOT NULL)",
    "strategy_publication_history_v1":
        "(generation INTEGER PRIMARY KEY,payload TEXT NOT NULL,previous_sha256 TEXT NOT NULL,sha256 TEXT NOT NULL)",
}
PUBLICATION_LIMIT_V1 = 64 * 1024
PUBLICATION_IMPLEMENTATION_LIMIT_V1 = 1024 * 1024


def _publication_fail_v1() -> NoReturn:
    raise ValueError("STRATEGY_PUBLICATION_INVALID")


def _publication_encoded_v1(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _publication_json_v1(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes or not 0 < len(raw) <= PUBLICATION_LIMIT_V1:
        _publication_fail_v1()
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                _publication_fail_v1()
            result[key] = value
        return result
    def constant(_value: str) -> NoReturn:
        _publication_fail_v1()
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("STRATEGY_PUBLICATION_INVALID") from None
    if type(value) is not dict:
        _publication_fail_v1()
    return value


def _publication_catalog_v1(entries: list[dict[str, Any]]) -> dict[str, Any]:
    canonical = [{**entry,
                  "permitted_instruments": sorted(entry["permitted_instruments"],
                      key=lambda key: (key["venue"], key["product"], key["symbol"])),
                  "approval_environments": sorted(entry["approval_environments"])} for entry in entries]
    payload = {"schema_version": 1, "entries": sorted(canonical,
        key=lambda entry: (entry["reference"]["strategy_id"], entry["reference"]["revision"]))}
    digest = hashlib.sha256(_publication_encoded_v1(payload).encode()).hexdigest()
    catalog = {"schema_version": 1, "revision": "sha256:" + digest, "entries": entries}
    _catalog_wire(catalog)
    return catalog


def _publication_descriptor_v1(implementation: bytes, configuration: bytes,
                               approval: bytes) -> dict[str, Any]:
    if type(implementation) is not bytes or not 0 < len(implementation) <= PUBLICATION_IMPLEMENTATION_LIMIT_V1:
        _publication_fail_v1()
    _publication_json_v1(configuration)
    record = _publication_json_v1(approval)
    expected = {"schema_version", "action", "reference", "implementation_sha256", "config_sha256",
                "config_schema_version", "engine_contract_version", "permitted_instruments",
                "approval_reference", "approval_source_reference", "approval_environments"}
    if (set(record) != expected or type(record["schema_version"]) is not int
            or record["schema_version"] != 1 or record["action"] != "APPROVE_STRATEGY_V1"
            or record["implementation_sha256"] != hashlib.sha256(implementation).hexdigest()
            or record["config_sha256"] != hashlib.sha256(configuration).hexdigest()):
        _publication_fail_v1()
    descriptor = {key: value for key, value in record.items() if key not in {"schema_version", "action"}}
    descriptor.update(approval_source_sha256=hashlib.sha256(approval).hexdigest(), approval_status="APPROVED")
    _publication_catalog_v1([descriptor])
    return descriptor


def _publication_fences_v1() -> dict[str, str]:
    return {f"{table}_no_{operation.lower()}":
            f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
            "BEGIN SELECT RAISE(ABORT,'publication immutable'); END"
            for table in ("strategy_publication_artifacts_v1", "strategy_publication_history_v1")
            for operation in ("UPDATE", "DELETE")}


def _publication_policy_v1(policy: dict[str, Any], catalog: dict[str, Any]) -> None:
    strategy = policy["strategy"]
    if strategy is None or strategy["catalog"] != catalog:
        _publication_fail_v1()
    lookup = _catalog_wire(catalog)
    for name in ("allowed", "selected"):
        for raw in strategy[name] or []:
            reference = _reference_wire(raw)
            if reference not in lookup or lookup[reference]["approval_status"] != "APPROVED":
                _publication_fail_v1()


def _publication_read_v1(db: sqlite3.Connection, policy: dict[str, Any],
                          policy_revision: int) -> dict[str, Any] | None:
    objects = [(row[0], row[1], row[2]) for row in db.execute("SELECT type,name,sql FROM sqlite_master")]
    extension = {name: (kind, sql) for kind, name, sql in objects if name.startswith("strategy_publication_")}
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if not extension and version in (1, 2):
        return None
    expected_objects = {name: ("table", f"CREATE TABLE {name} {layout}")
                        for name, layout in PUBLICATION_TABLES_V1.items()}
    expected_objects.update({name: ("trigger", sql) for name, sql in _publication_fences_v1().items()})
    if version != 3 or extension != expected_objects:
        _publication_fail_v1()
    for table in PUBLICATION_TABLES_V1:
        if (db.execute(f"PRAGMA index_list({table})").fetchone() is not None
                or db.execute(f"PRAGMA foreign_key_list({table})").fetchone() is not None):
            _publication_fail_v1()
    states = [tuple(row) for row in db.execute("SELECT * FROM strategy_publication_state_v1")]
    if (len(states) != 1 or states[0][0] != 1 or any(type(v) is not int for v in states[0])
            or not 1 <= states[0][1] <= 1024 or states[0][2] != policy_revision):
        _publication_fail_v1()
    lengths = db.execute("SELECT id,length(implementation),length(configuration),length(approval) "
                         "FROM strategy_publication_artifacts_v1 ORDER BY id").fetchmany(101)
    if not 1 <= len(lengths) <= 100:
        _publication_fail_v1()
    for ordinal, row in enumerate(lengths, 1):
        if (row[0] != ordinal or any(type(v) is not int for v in row)
                or not 0 < row[1] <= PUBLICATION_IMPLEMENTATION_LIMIT_V1
                or not 0 < row[2] <= PUBLICATION_LIMIT_V1 or not 0 < row[3] <= PUBLICATION_LIMIT_V1):
            _publication_fail_v1()
    approved: dict[str, dict[str, Any]] = {}
    identities: dict[tuple[str, str], tuple[Any, ...]] = {}
    for implementation, configuration, approval in db.execute(
            "SELECT implementation,configuration,approval FROM strategy_publication_artifacts_v1 ORDER BY id"):
        entry = _publication_descriptor_v1(implementation, configuration, approval)
        digest = entry["approval_source_sha256"]
        reference = _reference_wire(entry["reference"])
        identity = tuple(entry[name] for name in ("implementation_sha256", "config_sha256",
                                                 "config_schema_version", "engine_contract_version"))
        if digest in approved or reference in identities and identities[reference] != identity:
            _publication_fail_v1()
        approved[digest] = entry
        identities[reference] = identity
    history = db.execute("SELECT generation,length(payload) FROM strategy_publication_history_v1 "
                         "ORDER BY generation").fetchmany(1025)
    if len(history) != states[0][1] or any(type(row[1]) is not int or not 0 < row[1] <= PUBLICATION_LIMIT_V1 for row in history):
        _publication_fail_v1()
    current: dict[tuple[str, str], Any] = {}
    used: set[str] = set()
    previous = "0" * 64
    catalog: dict[str, Any] = {}
    for ordinal, row in enumerate(db.execute("SELECT generation,payload,previous_sha256,sha256 "
                                            "FROM strategy_publication_history_v1 ORDER BY generation"), 1):
        generation, payload, prior, digest = row
        if (type(generation) is not int or generation != ordinal or type(payload) is not str
                or prior != previous or type(digest) is not str):
            _publication_fail_v1()
        record = _publication_json_v1(payload.encode("utf-8"))
        actual = hashlib.sha256(b"StrategyPublicationV1\0" + previous.encode("ascii") + b"\0" + payload.encode()).hexdigest()
        if (digest != actual or _publication_encoded_v1(record) != payload
                or set(record) != {"generation", "action", "references", "catalog"}
                or type(record["generation"]) is not int or record["generation"] != ordinal
                or record["action"] not in ("APPROVE", "REVOKE")
                or type(record["references"]) is not list or not 1 <= len(record["references"]) <= 100):
            _publication_fail_v1()
        references = [_reference_wire(raw) for raw in record["references"]]
        if len(set(references)) != len(references):
            _publication_fail_v1()
        catalog = record["catalog"]
        following = _catalog_wire(catalog)
        for entry in following.values():
            approval_hash = entry["approval_source_sha256"]
            if (approval_hash not in approved or entry["approval_status"] not in ("APPROVED", "REVOKED")
                    or {**entry, "approval_status": "APPROVED"} != approved[approval_hash]):
                _publication_fail_v1()
            used.add(approval_hash)
        expected = dict(current)
        if record["action"] == "APPROVE":
            for reference in references:
                if reference not in following or following[reference]["approval_status"] != "APPROVED":
                    _publication_fail_v1()
                expected[reference] = following[reference]
        else:
            if len(references) != 1 or references[0] not in current or current[references[0]]["approval_status"] != "APPROVED":
                _publication_fail_v1()
            expected[references[0]] = {**current[references[0]], "approval_status": "REVOKED"}
        if list(expected.values()) != catalog["entries"]:
            _publication_fail_v1()
        current, previous = following, digest
    if used != set(approved):
        _publication_fail_v1()
    _publication_policy_v1(policy, catalog)
    return {"schema_version": 1, "generation": states[0][1], "catalog": catalog}
# END PORTABLE STRATEGY PUBLICATION V1


@dataclass(frozen=True, repr=False)
class StrategyArtifactBytesV1:
    implementation: bytes
    configuration: bytes
    approval_record: bytes


@dataclass(frozen=True)
class StrategyPublicationV1:
    generation: int
    policy_revision: int
    catalog: StrategyCatalog


class StrategyPublicationOwnerV1:
    """Internal explicit action boundary; publication provenance is not trading consent."""

    def __init__(self, book: "RuntimePolicyBook") -> None:
        from .runtime_policy import RuntimePolicyBook
        if type(book) is not RuntimePolicyBook:
            _publication_fail_v1()
        self._book = book

    def current(self) -> StrategyPublicationV1 | None:
        with self._book._operation_v2() as db:
            db.execute("BEGIN")
            snapshot = self._book._read(db)
            publication = _publication_read_v1(db, snapshot["policy"], snapshot["revision"])
            return self._snapshot(publication, snapshot["revision"])

    @staticmethod
    def _snapshot(publication: dict[str, Any] | None, revision: int) -> StrategyPublicationV1 | None:
        if publication is None:
            return None
        return StrategyPublicationV1(publication["generation"], revision,
                                     catalog_from_wire(publication["catalog"]))

    def require_current(self, reference: StrategyReference, *, generation: int,
                        policy_revision: int) -> StrategyDescriptor:
        if type(reference) is not StrategyReference:
            _publication_fail_v1()
        current = self.current()
        if (current is None or type(generation) is not int or type(policy_revision) is not int
                or current.generation != generation or current.policy_revision != policy_revision):
            _publication_fail_v1()
        for entry in current.catalog.entries:
            if entry.reference == reference and entry.approval_status == "APPROVED":
                return entry
        _publication_fail_v1()

    def publish(self, artifacts: tuple[StrategyArtifactBytesV1, ...], *, expected_policy_revision: int,
                expected_generation: int) -> StrategyPublicationV1:
        if type(artifacts) is not tuple or not 1 <= len(artifacts) <= 100:
            _publication_fail_v1()
        entries = []
        for artifact in artifacts:
            if type(artifact) is not StrategyArtifactBytesV1:
                _publication_fail_v1()
            entries.append(_publication_descriptor_v1(artifact.implementation, artifact.configuration,
                                                      artifact.approval_record))
        references = [_reference_wire(entry["reference"]) for entry in entries]
        if len(set(references)) != len(references):
            _publication_fail_v1()
        return self._change(expected_policy_revision, expected_generation, entries, artifacts, None)

    def revoke(self, reference: StrategyReference, *, expected_policy_revision: int,
               expected_generation: int) -> StrategyPublicationV1:
        if type(reference) is not StrategyReference:
            _publication_fail_v1()
        key = _reference_wire({"strategy_id": reference.strategy_id, "revision": reference.revision})
        return self._change(expected_policy_revision, expected_generation, [], (), key)

    def _change(self, revision: int, generation: int, entries: list[dict[str, Any]],
                artifacts: tuple[StrategyArtifactBytesV1, ...],
                revoke: tuple[str, str] | None) -> StrategyPublicationV1:
        from .runtime_policy import validate_revision, validate_v2
        revision = validate_revision(revision, increment=True)
        if type(generation) is not int or not 0 <= generation < 1024:
            _publication_fail_v1()
        with self._book._operation_v2() as db:
            db.execute("BEGIN IMMEDIATE")
            snapshot = self._book._read(db)
            if snapshot["schema_version"] != 2 or snapshot["revision"] != revision:
                _publication_fail_v1()
            previous = _publication_read_v1(db, snapshot["policy"], revision)
            if generation != (previous["generation"] if previous is not None else 0):
                _publication_fail_v1()
            catalog = previous["catalog"] if previous is not None else _publication_catalog_v1([])
            selected = _catalog_wire(catalog)
            if revoke is not None:
                if revoke not in selected or selected[revoke]["approval_status"] != "APPROVED":
                    _publication_fail_v1()
                selected[revoke] = {**selected[revoke], "approval_status": "REVOKED"}
                references = [selected[revoke]["reference"]]
            else:
                for entry in entries:
                    selected[_reference_wire(entry["reference"])] = entry
                references = [entry["reference"] for entry in entries]
            updated_catalog = _publication_catalog_v1(list(selected.values()))
            policy = snapshot["policy"]
            strategy = policy["strategy"]
            if previous is None and strategy is not None and strategy["catalog"] not in (None, updated_catalog):
                _publication_fail_v1()  # Existing draft metadata needs its actual complete artifact set.
            if strategy is None:
                strategy = {"section_version": 1, "catalog": None, "allowed": None, "selected": None}
                policy["strategy"] = strategy
            strategy["catalog"] = updated_catalog
            active = {key for key, entry in selected.items() if entry["approval_status"] == "APPROVED"}
            for name in ("allowed", "selected"):
                if strategy[name] is not None:
                    strategy[name] = [raw for raw in strategy[name] if _reference_wire(raw) in active]
            policy = validate_v2(policy)
            if previous is None:
                for name, layout in PUBLICATION_TABLES_V1.items():
                    db.execute(f"CREATE TABLE {name} {layout}")
                for sql in _publication_fences_v1().values():
                    db.execute(sql)
                db.execute("PRAGMA user_version=3")
            existing = {hashlib.sha256(row[0]).hexdigest() for row in db.execute(
                "SELECT approval FROM strategy_publication_artifacts_v1")}
            for artifact, entry in zip(artifacts, entries, strict=True):
                if entry["approval_source_sha256"] not in existing:
                    db.execute("INSERT INTO strategy_publication_artifacts_v1(implementation,configuration,approval) "
                               "VALUES(?,?,?)", (artifact.implementation, artifact.configuration, artifact.approval_record))
                    existing.add(entry["approval_source_sha256"])
            old_hash = db.execute("SELECT sha256 FROM strategy_publication_history_v1 ORDER BY generation DESC LIMIT 1").fetchone()
            prior = old_hash[0] if old_hash is not None else "0" * 64
            payload = _publication_encoded_v1({"generation": generation + 1,
                "action": "REVOKE" if revoke is not None else "APPROVE", "references": references,
                "catalog": updated_catalog})
            digest = hashlib.sha256(b"StrategyPublicationV1\0" + prior.encode("ascii") + b"\0" + payload.encode()).hexdigest()
            db.execute("INSERT INTO strategy_publication_history_v1 VALUES(?,?,?,?)", (generation + 1, payload, prior, digest))
            db.execute("INSERT INTO strategy_publication_state_v1 VALUES(1,?,?) ON CONFLICT(id) "
                       "DO UPDATE SET generation=excluded.generation,policy_revision=excluded.policy_revision",
                       (generation + 1, revision + 1))
            if db.execute("UPDATE runtime_policy_v2 SET revision=revision+1,payload=? WHERE id=1 AND revision=?",
                          (_publication_encoded_v1(policy), revision)).rowcount != 1:
                _publication_fail_v1()
            updated = self._book._read(db)
            publication = _publication_read_v1(db, updated["policy"], updated["revision"])
            result = self._snapshot(publication, updated["revision"])
            assert result is not None
            return result
