# Durable History Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve bounded authenticated history observations, conflicts, and honest query coverage across worker restart and offline backup/restore.

**Architecture:** A single-account SQLite history store records immutable typed event variants and their original snapshot provenance in one transaction. Separate query views report gaps and contradictions without issuing evidence qualification. The existing owner-loop worker explicitly collects history; the existing offline snapshot tool includes it through a versioned inventory contract.

**Tech Stack:** Existing Python `>=3.12,<3.13`, standard-library SQLite/Decimal/dataclasses, pytest; no new dependencies. Offline recovery remains Python 3.12 standard library only.

**Spec:** `docs/implementation/HISTORY_PERSISTENCE_SPEC_KO_20261102.md`, section “가장 작은 독립 NEXT: 영속 이력 수집과 coverage 저장”; read together with current `execution/history.py`, `execution/worker.py`, and `scripts/operations/state_snapshot.py`. This plan assumes the canonical `execution.sqlite3` worker layout and legacy-journal blocker have landed. Recheck these interfaces before execution because adjacent fixes were in progress when this plan was written.

## Global Constraints

- “현재 `history.py:110`의 `venue_event_complete=False`, `flat_verified=False`를 보존한다.”
- “조회 구간을 완전히 읽었다는 사실과 거래소 이벤트가 모두 도착했다는 사실을 분리한다.”
- “조회 실패 시 watermark 미전진”; persist successful complete snapshots only, atomically.
- “기존 `--require-runtime` 의미를 조용히 바꾸지 않는다.” It continues to require the existing four runtime stores.
- “기존 한도·자정 baseline·신호 주기·history 최초 시작 시점을 임의 생성하지 않는다.”
- No scheduler, automatic polling interval/startup flag, operational defaults, strategy work, qualification change, native write, command replay, reservation release, paid AI, or real credential/database/network access in this patch.
- Constructors/passive start do not access the network. Tests use temporary databases and injected transports only. Runtime remains BLOCKED, policy STOPPED, and `DailyRealizedLoss.ledger_qualified` false.
- Preserve bounded parser validation, fixed safe diagnostics, exact monetary values, UTC provenance, owner-thread SQLite access, strict recovery inventory/schema checks, and offline stopped-writer attestation. Do not manufacture new freshness on load or restore.

## Review Focus

1. Repeated account-wide transactions in different symbol queries must appear once economically, including transfers with an empty symbol (Task 1/2 tests).
2. A later-arriving older order observation must not become a false fill regression; equal venue update times with different contents must remain a conflict (Task 1 tests).
3. A conflict discovered later must taint previously queried coverage, including other symbols affected by an account-wide transaction, without modifying old rows (Task 2 tests).
4. Stop, binding expiry, or lease loss while a GET is awaiting must prevent any subsequent history database access; native fills must still progress during the GET (Task 3 tests).
5. Missing constraints/triggers, downgraded manifests, and mixed-account stores must fail before backup or restore publication; genuine legacy backups must remain readable (Task 4 tests).

---

## File boundaries and shared contract

- Create `src/multi_exchange_trader/execution/history_store.py`: scoped store, append-only schema, canonical typed serialization, ingestion/conflicts, receipts, integrity validation.
- Create `src/multi_exchange_trader/execution/history_coverage.py`: immutable query result types and interval/provenance composition; no clock, network, or writable connection.
- Modify `src/multi_exchange_trader/execution/history.py` only to expose reusable validation of existing typed records/snapshots; retain reader behavior and flags.
- Modify `src/multi_exchange_trader/execution/worker.py`: store lifecycle and explicit `collect_history` only.
- Modify `scripts/operations/state_snapshot.py`: strict history schema validation, version-3 inventory, explicit history-bundle requirement, legacy compatibility.
- Create `tests/execution/test_history_store.py`, `tests/execution/test_history_coverage.py`; extend `tests/execution/test_worker.py`, `tests/execution/test_worker_lifecycle.py`, `tests/operations/test_state_snapshot.py` and existing history/qualification regressions as needed.
- Update `docs/implementation/NATIVE_WORKER_20261102.md` and `docs/implementation/AUTOMATIC_RUNTIME_STATUS_KO.md` with the exact bounded capability and remaining gaps.

### Storage schema, version 1

File: `history.sqlite3`, `PRAGMA user_version=1`, WAL, synchronous FULL, foreign keys ON, trusted_schema OFF. One `ProtectionScope` per file; empty stores still contain their scope. Reject an existing unsupported/partial schema instead of repairing it. Every column below is NOT NULL. Text uses BINARY identity; hashes are lowercase 64-character SHA-256 hex; all times/ranges and bool-as-0/1 values receive strict type/range validation.

| Table | Ordered columns | Required key / references |
|---|---|---|
| `history_scope` | `id INTEGER, account TEXT, environment TEXT` | PK(id), CHECK(id=1); exactly one row |
| `history_snapshots` | `observation_key TEXT, snapshot_id TEXT, symbol TEXT, start_ms INTEGER, end_ms INTEGER, observed_at TEXT, server_observed_at TEXT, transport_injected INTEGER` | PK(observation_key) |
| `history_variants` | `kind TEXT, record_id TEXT, variant_hash TEXT, symbol TEXT, event_ms INTEGER, updated_ms INTEGER, payload TEXT` | PK(kind,record_id,variant_hash) |
| `history_members` | `observation_key TEXT, kind TEXT, record_id TEXT, variant_hash TEXT` | PK(observation_key,kind,record_id); FK(observation_key) to snapshots; FK(kind,record_id,variant_hash) to variants |
| `history_conflicts` | `conflict_key TEXT, kind TEXT, record_id TEXT, left_observation_key TEXT, right_observation_key TEXT, reason TEXT` | PK(conflict_key); separate FKs for both observation keys to snapshots |

All FKs use NO ACTION, no cascading mutation. Each of the five tables has exactly two canonical triggers, `{table}_no_update` and `{table}_no_delete`: `CREATE TRIGGER {name} BEFORE {UPDATE|DELETE} ON {table} BEGIN SELECT RAISE(ABORT, 'history is append-only'); END`. Normal insert is the only mutation API; no REPLACE, pruning, conflict resolution, or UPDATE-upsert. Accept equivalent full UNIQUE BINARY keys as existing recovery does; reject partial/expression/NOCASE/duplicated-key-part substitutes, altered FKs, extra tables/views/triggers, and mismatched trigger bodies. Ordinary nonunique lookup indexes may be added and must not be treated as identity proof.

`kind` is EXECUTION, TRANSACTION, or ORDER. Payload is bounded canonical JSON (UTF-8 <=65536 bytes, exact dataclass field whitelist, sorted keys, compact separators, no NaN, duplicate keys, extra keys or provider/raw fields). Monetary fields are exact finite decimal strings in plain notation; canonical identity ignores representational trailing zeros and signed zero, using Decimal tuples/string manipulation independent of current Decimal precision. Values, not source JSON spellings, are preserved; original provider payloads were never available in the typed snapshot. Other field types exactly match `ExecutionRecord`, `TransactionRecord`, and `OrderHistoryRecord`. `event_ms` is execution/transaction time or order creation time; `updated_ms` equals `event_ms` for executions/transactions and venue `updated_ms` for orders. Stored indexed fields must match decoded payloads.

`variant_hash` hashes canonical payload. `observation_key` hashes canonical scope + every snapshot metadata field + sorted `(kind,record_id,variant_hash)` members. Original `snapshot_id` stays intact and need not be globally unique: exact replay is a no-op; reusing a snapshot ID with different contents stores both observations and a `SNAPSHOT_ID_REUSED` conflict (kind SNAPSHOT, record_id=snapshot_id). `conflict_key` hashes kind/id/reason and the lexically sorted pair of observation keys. Hash matches are checked against canonical contents; a hash collision is a fixed integrity error, never silent deduplication. Membership, new variants, snapshots, conflicts and receipt derivation share one BEGIN IMMEDIATE/COMMIT transaction; failures before commit roll back everything.

Conflicts are append-only pairs, never rejection of otherwise valid contradictory evidence:

- EXECUTION/TRANSACTION: same scoped kind+ID, any unequal typed payload -> `EVENT_CONTENT_CHANGED`; retain both variants, regardless of query symbol.
- ORDER: normal later venue update/status/fill change is another observation, not an immutable-event conflict. Same order ID and `updated_ms` but unequal payload -> `ORDER_UPDATE_COLLISION`; differing symbol/side -> `ORDER_IDENTITY_CHANGED`; lower cumulative fill at a strictly greater `updated_ms` -> `ORDER_FILL_REGRESSION`. Compare all stored distinct update points in venue-time order, including when an older point arrives later. Never compare window-local execution sums to infer an order regression. Legitimate amended order quantity/status/leaves are not frozen as identity.
- Same event conflict observed in more snapshots generates deterministic pair evidence; replay inserts nothing twice. No automatic conflict clearing occurs on later matching reads.

### Exact public interfaces

All result dataclasses are frozen. Define the following in `history_coverage.py` (record types imported from history; store imports result types, avoiding circular imports):

```python
HistoryKind = Literal["EXECUTION", "TRANSACTION", "ORDER"]
HistoryRecord = ExecutionRecord | TransactionRecord | OrderHistoryRecord

@dataclass(frozen=True)
class QueryProvenance:
    observation_key: str
    snapshot_id: str
    symbol: str
    window: HistoryWindow
    observed_at: datetime
    server_observed_at: datetime
    transport_injected: bool

@dataclass(frozen=True)
class HistoryConflict:
    conflict_key: str
    kind: Literal["EXECUTION", "TRANSACTION", "ORDER", "SNAPSHOT"]
    record_id: str
    left_observation_key: str
    right_observation_key: str
    reason: str

@dataclass(frozen=True)
class IngestionReceipt:
    observation_key: str
    snapshot_id: str
    duplicate: bool
    new_variant_count: int
    new_conflict_count: int
    conflict_keys: tuple[str, ...]

@dataclass(frozen=True)
class StreamCoverage:
    kind: HistoryKind
    covered: tuple[HistoryWindow, ...]
    gaps: tuple[HistoryWindow, ...]
    last_query_end_ms: int | None
    contiguous_through_ms: int | None

@dataclass(frozen=True)
class HistoryCoverage:
    scope: ProtectionScope
    symbol: str
    window: HistoryWindow
    streams: tuple[StreamCoverage, ...]  # EXECUTION, ORDER, TRANSACTION order
    provenance: tuple[QueryProvenance, ...]
    conflicts: tuple[HistoryConflict, ...]
    query_complete: bool
    conflict_free: bool
    oldest_observed_at: datetime | None
    oldest_server_observed_at: datetime | None
    venue_event_complete: Literal[False] = field(default=False, init=False)
    flat_verified: Literal[False] = field(default=False, init=False)

@dataclass(frozen=True)
class ObservedVariant:
    kind: HistoryKind
    record_id: str
    variant_hash: str
    record: HistoryRecord
    observation_keys: tuple[str, ...]

@dataclass(frozen=True)
class HistoryWindowView:
    coverage: HistoryCoverage
    variants: tuple[ObservedVariant, ...]
```

`HistoryStore(path: Path, scope: ProtectionScope)` provides `ingest(snapshot: HistorySnapshot) -> IngestionReceipt`, `coverage(symbol: str, *, start_ms: int, end_ms: int) -> HistoryCoverage`, `load_window(symbol: str, *, start_ms: int, end_ms: int) -> HistoryWindowView`, `close() -> None`, and context-manager methods. `validate_history_database(db: sqlite3.Connection, *, scope: ProtectionScope | None = None) -> ProtectionScope` validates without writing and returns persisted scope. In `history.py`, add `validate_history_snapshot(snapshot: HistorySnapshot) -> None`; it validates exact dataclass/container/primitive types and existing parser invariants without imposing current-clock freshness. Reuse the existing parser field rules; do not loosen them merely because construction bypassed REST parsing. In particular, compare order update time with the reader's actual available evidence; do not newly require it <= the snapshot's minimum page-server time (pages may legitimately have later server times).

### Query semantics

- All query intervals are inclusive `[start_ms,end_ms]`, validated with existing `HistoryWindow` (including its seven-day bound). This patch does not add unbounded scans or assume the first collection start. Intersections and adjacency use integer milliseconds: `[a,b]` and `[b+1,c]` are contiguous.
- Execution/order coverage uses persisted snapshots for requested symbol. Transaction coverage uses all persisted snapshots for the account because reader transaction requests are account-wide. A complete empty response counts as successful query coverage for each endpoint; pagination failure creates no snapshot/coverage.
- Clip successful intervals to request, merge overlaps/adjacency, compute explicit gaps. `last_query_end_ms` is the greatest clipped covered endpoint, not an event-completeness cursor. `contiguous_through_ms` is the end of coverage connected to the caller's explicit start, or None when that start is uncovered. `query_complete` means all three streams have no gaps. Contradictions remain visible through `conflict_free=False`; they do not rewrite the historical fact that a query finished.
- Relevant conflicts include conflicts on variants belonging to contributing observations and snapshot-ID conflicts. Any conflicted event taints every observation containing that kind+ID, including earlier matching variants. Account transaction conflicts propagate to overlapping account transaction coverage of other symbols. This conservative invalidation is computed from stored evidence; never UPDATE an old coverage row. Report gaps and conflict flags independently; neither is a trade/risk authorization.
- `load_window` returns all distinct execution/order variants for requested symbol with `event_ms` in the window, and account-wide transaction variants in the window, including transfers/other symbols. Each distinct typed variant appears once with all original observation keys; conflicting variants remain explicit. Orders are filtered by creation time, matching the endpoint; this is not an open-orders inventory or proof of absence of older orders. Do not select a convenient winning variant or pass this aggregate off as `HistorySnapshot` to the existing loss calculator.
- Provenance lists contributing snapshots deterministically, preserving full original windows, injected flags, and times. View-only records observed through a larger/different request retain their original provenance too. Minimum timestamps come from that original provenance; no clock call occurs in query composition. A union of observations is never labeled fresh, qualified, or venue-event complete.

## Task 1: Atomic immutable observations and persistent conflicts

**Files:** create `history_store.py`, initial types in `history_coverage.py`, `tests/execution/test_history_store.py`; modify `history.py` validator only.

**Interfaces:** consumes existing `HistorySnapshot` and record dataclasses; produces schema, validator, store lifecycle/`ingest`, and `IngestionReceipt` above. Coverage methods are Task 2.

- [ ] **Step 1 — Write failing tests:** `test_exact_replay_and_decimal_equivalence_are_idempotent` asserts second receipt.duplicate and no row growth; `test_account_transactions_share_one_variant_across_symbols` asserts one TRANSACTION variant with two memberships and one transfer; `test_changed_event_payload_persists_both_variants_and_conflict_after_reopen` asserts originals unchanged and repeat conflict count zero; `test_order_update_collision_regression_and_out_of_order_arrival` tests normal 0.4->0.8 fill, older 0.4 arriving after 0.8 without regression, true 0.8->0.4 regression, equal-update collision, and smaller local execution windows without false regression. Add `test_snapshot_id_reuse_is_durable_conflict`, `test_scope_and_forged_typed_snapshot_rejected_without_rows`, and `test_atomic_failure_before_commit_leaves_no_receipt_or_partial_rows` (inject failure after variant insertion and after snapshot/membership insertion; reopen confirms rollback).
- [ ] **Step 2 — Verify red:** `uv run pytest -q tests/execution/test_history_store.py`; expected missing store/interface failure before implementation.
- [ ] **Step 3 — Implement the schema and exact Task 1 interfaces.** Store/validate only whitelisted typed fields. Validate scope even for empty DBs. Open existing files without repair, ensure full canonical hashes/members/references/metadata consistency, reject malformed flags, records, decimal strings and UTC values. Recompute required conflict relationships during integrity validation so an omitted conflict row cannot silently restore a conflict-free result. Enforce DB triggers and same-thread connection ownership. Preserve injected provenance, not credentials.
- [ ] **Step 4 — Verify green:** `uv run pytest -q tests/execution/test_history_store.py tests/execution/test_history.py`; expected PASS. Include strict-schema tests for missing/NOCASE/partial keys, foreign keys, triggers, duplicate identities and mismatched indexed payload fields. Test direct UPDATE/DELETE failure for every table and exact amounts after reconnect.
- [ ] **Step 5 — Review and commit:** fresh gate checks atomicity, key scope, safe serialization, append-only enforcement and order conflict semantics. Stage only Task 1 files; commit `feat: persist immutable history observations and conflicts`.

## Task 2: Coverage and provenance-preserving window views

**Files:** `history_coverage.py`, `history_store.py`, `tests/execution/test_history_coverage.py`.

**Interfaces:** consumes Task 1 schema and validated immutable records; produces `coverage`/`load_window` and remaining dataclasses above. Internal `compose_coverage(scope: ProtectionScope, symbol: str, window: HistoryWindow, provenance: tuple[QueryProvenance, ...], conflicts: tuple[HistoryConflict, ...]) -> HistoryCoverage` is pure; store selects relevant records/conflicts first.

- [ ] **Step 1 — Write failing tests:** `test_inclusive_boundaries_adjacency_and_one_ms_gap` uses `[1000,2000]`, `[2001,3000]` and a missing `[2000,2000]`; assert exact covered/gap ranges and explicit-start contiguous cursor. `test_empty_complete_queries_do_not_prove_venue_completeness` asserts query_complete true and both fixed flags false. `test_transaction_coverage_is_account_wide_but_order_execution_coverage_is_symbol_bound` asserts BTC snapshots alone leave ETH execution/order gaps. `test_late_events_extend_old_window_without_refreshing_provenance` inserts a late event on requery, asserts one variant per economic value and original minimum timestamps. `test_new_conflict_taints_previous_and_other_symbol_transaction_views` asserts old query_complete remains true but conflict_free becomes false after reopen. `test_union_view_retains_order_creation_scope_and_every_variant` retains conflicts and excludes older-created orders without claiming flatness. Query before/after reopen must be equal; Decimal context precision changes must not alter hashes/values.
- [ ] **Step 2 — Verify red:** `uv run pytest -q tests/execution/test_history_coverage.py`; expected missing query-interface failure.
- [ ] **Step 3 — Implement the exact Task 2 interfaces and inclusive union/gap algorithm.** Keep raw query coverage separate from conflicts and true event completeness. Deduplicate transactions by scoped ID+variant, retain all conflicting variants/provenance, and never regenerate a snapshot or a current timestamp.
- [ ] **Step 4 — Verify green:** `uv run pytest -q tests/execution/test_history_store.py tests/execution/test_history_coverage.py tests/execution/test_history.py`; expected PASS.
- [ ] **Step 5 — Review and commit:** gate checks interval boundaries, event-vs-query cursor distinction, account-wide propagation, timestamps, and no qualified aggregate. Commit `feat: expose durable history coverage and provenance`.

## Task 3: Explicit owner-loop worker ingestion

**Files:** `worker.py`, `tests/execution/test_worker.py`, `tests/execution/test_worker_lifecycle.py`, `tests/execution/test_worker_layout.py` if inventory fixtures need the new file.

**Interfaces:** consumes Task 1/2 `HistoryStore` and `IngestionReceipt`; produces `async ExecutionWorker.collect_history(self, symbol: str, *, start_ms: int, end_ms: int) -> IngestionReceipt`. Existing `read_history` remains read-only, with its signature unchanged.

- [ ] **Step 1 — Write failing tests:** `test_collect_history_persists_on_owner_loop_and_reopens` asserts receipt and restored view; `test_collect_history_rejects_scope_symbol_binding_and_lease_changes` covers before request and after await (including releasing/reacquiring same lock object, not merely a missing lock). `test_stop_during_history_await_never_accesses_closed_store` records store accesses and releases the fake read only after stop; assert rejection and unchanged history. `test_history_failure_and_cancellation_do_not_advance_coverage` covers pagination failure and cancellation. `test_native_fill_progresses_during_slow_history_read` blocks fake GET with asyncio events and asserts fill persists before releasing it. `test_history_open_failure_releases_all_worker_resources` checks lifecycle cleanup. Assert passive start/collection does not dispatch AI/native commands, policy remains STOPPED, runtime BLOCKED, and legacy journal handling remains unchanged.
- [ ] **Step 2 — Verify red:** `uv run pytest -q tests/execution/test_worker.py -k history`; expected missing collect_history interface.
- [ ] **Step 3 — Implement explicit collection and lifecycle.** Open `_history_store` in `start_passive` under the acquired lock, same owner thread as journal/runtime-state, using `directory / "history.sqlite3"` and current `ProtectionScope`; close it on startup failure and shutdown. Before GET: enforce `self.runtime` owner-loop/lifecycle, reject a history-stop flag, read `runtime.status` to run the existing lease guard, validate symbol and session binding. Capture the exact runtime/lease generation. Await existing `read_history`; then recheck owner-loop, stop flag, same runtime/generation, `runtime.status`, session binding, and returned exact scope/symbol/window before synchronous ingest. No await between final guard and commit; no SQLite in `to_thread`. Reuse the final PR17 worker `_admitted` decorator and sticky `_stopping` fence for the whole collection coroutine. Capture the exact lease acquisition epoch, and after GET reject `_stopping` before any history-store access. Stop requested during GET refuses while retaining stores/lease; the admitted read then rejects its result, accounting finishes, and the caller explicitly retries stop. Do not add a competing shutdown flag or bypass the operation counter. Apply the PR17 final shutdown fix before Task 3 starts. Cancellation/GET failure never calls ingest. Cleanup must attempt all store closes and release the lock even if one close fails.
- [ ] **Step 4 — Verify green:** `uv run pytest -q tests/execution/test_worker.py tests/execution/test_worker_lifecycle.py tests/execution/test_worker_layout.py tests/execution/test_worker_cli.py`; expected PASS, no CLI scheduler/startup behavior changes.
- [ ] **Step 5 — Review and commit:** gate checks owner thread, await races, lease epochs, callback progress, cleanup and unchanged authority. Commit `feat: persist explicitly collected worker history`.

## Task 4: Versioned offline history backup and recovery compatibility

**Files:** `scripts/operations/state_snapshot.py`, `tests/operations/test_state_snapshot.py`, both implementation status documents listed above.

**Interfaces:** consumes Task 1 schema/invariants and Task 2 observable views. Preserve existing signatures by adding optional `require_history: bool = False` beside `require_runtime` to `inventory` and `verify`; expose `--require-history` for snapshot/verify/restore. Keep `RUNTIME_SCHEMAS`/`--require-runtime` membership exactly the original four stores. Add a distinct history schema allowlist and validator `check_history(db: sqlite3.Connection) -> tuple[str, str]` in the standalone tool, equivalent to Task 1 validation but with no application import/dependency. Contract-parity tests feed malformed fixtures through both validators.

- [ ] **Step 1 — Write failing tests:** `test_history_monitor_generation_round_trip_preserves_conflicts_and_coverage`, `test_history_requires_execution_and_runtime_state_pair`, `test_history_requirement_does_not_require_ai_or_change_require_runtime`, `test_versions_one_two_restore_without_history`, `test_history_manifest_missing_file_or_downgrade_refused`, `test_history_constraints_and_missing_conflicts_refused_before_publication`, `test_history_other_account_or_environment_refused`, `test_history_wrong_scope_even_when_empty_rejected_on_worker_open`, and `test_restored_history_does_not_qualify_runtime_or_retimestamp_observations`. Use actual application-created temporary stores; compare source bytes before/after and decoded view equality after restore. Exercise retained committed WAL, altered hash+invalid schema, and both store/backup validators against the same schema damages.
- [ ] **Step 2 — Verify red:** `uv run pytest -q tests/operations/test_state_snapshot.py -k history`; expected new file/flag/manifest rejected.
- [ ] **Step 3 — Implement the v3 contract:** version 1 remains base six ledgers+datasets; version 2 remains existing runtime inventory; newly generated snapshots containing history use version 3 with exact base fields plus `runtime_files` and `history_files`, where `history_files == ["history.sqlite3"]` and runtime_files lists present original runtime stores. Require execution.sqlite3 + runtime-state.sqlite3 whenever history is present, even without a flag. `--require-history` requires that triple plus existing base inventory, not paid AI dispatch. Combining both flags requires original four runtime stores plus history. v1/v2 cannot contain history or claim its presence; preserve old valid generation acceptance and partial-policy-only backups. No extra manifest fields, lock files or arbitrary files become allowed. Restore never creates an empty history DB to satisfy a missing required file.
- [ ] **Step 4 — Implement strict history validation and scope consistency:** validate copied databases only, original limits/WAL rules unchanged; compare every account/environment row in execution/runtime-state with history_scope (empty paired stores have no conflicting scope to infer). Reject mixed scopes; worker re-open must also check configured scope. Require exact version, column ordering/types and keys, FKs, canonical trigger bodies, row payload/hash consistency and complete deterministic conflict representation. Preserve all three stores in the same stopped-writer capture and whole-generation restore; never offer independent history restore. Existing hash/inventory rechecks continue around staging/publication. This proves captured file consistency, not the historical authenticity of independently mixed files with recomputed manifests; see limits below.
- [ ] **Step 5 — Verify green and document:** `uv run pytest -q tests/operations/test_state_snapshot.py tests/execution/test_history_store.py tests/execution/test_history_coverage.py tests/execution/test_worker.py tests/execution/test_worker_lifecycle.py tests/execution/test_worker_layout.py`; expected PASS. Update docs with explicit collect API, version/flag matrix, exact coverage/completeness limitations, mutable orders, account transactions, and no automatic history start/interval. Then run existing repository gates once: `uv run pytest -q`, `uv run ruff check src tests scripts`, `uv run mypy src scripts`; use existing isolated pinned-Linux CI for native contracts rather than claiming a Windows skip as native validation.
- [ ] **Step 6 — Review and commit:** gate checks standalone-tool parity, legacy semantics, strict inventory/schema, missing-history refusal, restore scope, source immutability and unchanged qualification. Commit `feat: preserve history in versioned offline state backups`.

## Dependency and completion gates

Task 1 -> Task 2 -> Task 3. Task 4's standalone backup work may proceed after Task 1, but its end-to-end restore assertions depend on Tasks 2/3. These are the only independent reviewer gates; schema scaffolding/tests/docs stay with their owning deliverable. The user already supplied execution authorization; this planning-only handoff does not add a permission gate. Before implementation, executor must read the spec and reconcile any concurrent source changes.

Completion means temporary fixtures survive restart and whole-generation recovery with exact observations/conflicts/query coverage, all specified race/failure checks pass, and existing fixed blockers remain. It does not mean live/demo venue qualification, cache reconstruction, complete account risk evidence, terminal reservation release, protection recovery, scheduler execution, or PC-off deployment.

## Design limits carried forward

- Current snapshots retain the minimum page-server time, not every individual page receipt. Preserve this honest granularity; stronger per-page audit provenance would require a separate reader-contract extension.
- History scope can reject mixed accounts/environments, and stopped-writer capture preserves the current file set. Existing journal/runtime-state files have no common immutable generation token, so this patch cannot prove they were never independently mixed before capture. Do not invent such proof through a new manifest timestamp; cross-store generation lineage is separate work if required.
- Conflict evidence remains unresolved and conservative. No operator adjudication or automatic acceptance of a revised economic event is included; later risk/reconciliation layers must explicitly consume the conflict-bearing views.
