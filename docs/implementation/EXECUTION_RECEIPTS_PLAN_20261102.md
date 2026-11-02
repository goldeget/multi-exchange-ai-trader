# Authenticated Execution Receipts and Durable Credit Implementation Plan

Implementation status, 2026-11-02: Tasks 1, 2A/2B/2C and 3 are implemented and individually independently reviewed. This retained authoring checklist is historical, not a queue of unfinished implementation. The full product remains incomplete; [current runtime status](./AUTOMATIC_RUNTIME_STATUS_KO.md) describes its remaining gates. Positive local fixtures are synthetic, and actual authenticated transport and DEMO operation remain unverified. Whole-branch review and exact-source CI are separate integration gates recorded with the final PR/handoff.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corroborate persisted native candidates against session-owned actual-history receipts and atomically credit accounting without enabling trading.

**Architecture:** The existing authenticated session privately issues bounded opaque receipts from its own GET. The existing history store records observations first; the runtime then commits a stable execution identity and projection in one runtime-state transaction. Durable evidence survives restart; receipt authority does not.

**Tech Stack:** Python 3.12, NautilusTrader 1.231.0, SQLite WAL, existing pytest/Ruff/mypy/isolated-native CI.

**Spec:** [EXECUTION_RECEIPTS_SPEC_20261102.md](./EXECUTION_RECEIPTS_SPEC_20261102.md). Read both before execution. Base `acb829a6481408b00dfc8189eb82ddd4e0b5dab6`, tree `6edf58bad8a76b7f7d73a4a14e3b679e9beaa96b`; PR #19 is already merged and remote main was verified. This worktree starts at that accepted parent; no repeated rebase is needed.

## Global Constraints

- Python 3.12; `nautilus-trader==1.231.0`; Bybit DEMO, LINEAR, one-way `position_idx=0`.
- No actual provider/native effects, private files, real databases, paid AI, new server, dependency change or SDK fork during implementation/tests. Temporary fixtures only.
- No second history writer or REST writer. Existing history storage remains the only historical observation writer.
- No ACK, terminal transition, reservation release, exit authority, resume, qualification flag, protection repair, native-cache restore, strategy cycle, AI or hosting changes.
- History-only fill recovery is excluded. Credit only a persisted quarantine candidate matched to a `Trade` row with a nonempty exact `orderLinkId`; never adopt a manual order.
- Existing execution permission checks and unconditional runtime qualification rejection remain unchanged. Read authentication is separate from permission to trade.
- Receipt bound: 4 total pending-plus-issued per session; TTL min(session max_age, 30 seconds) from GET start. No caller-supplied issuer/authorizer/transport bypass.
- Preserve V1/V2 digest/serialization semantics; use the spec's 17-item V3 only for new credit/decision events. Keep legacy unproven fill refusal.
- Actual positive receipt issuance stays unverified. Private-core synthetic fixtures are not transport evidence. Keep all current execution/ledger/flat qualification flags false.
- Existing user instruction authorizes continuing development and the execution method; do not ask routine plan/continuation permission. Root design/builder review precedes implementation. Preserve ignored progress artifacts; no cleanup.

## Review Focus

1. A reauthentication/close/cancellation during an awaited GET must free capacity and never mint a stale handle (Task 1).
2. Two fills for one order share the old aggregated quarantine marker; crediting one must leave the other unresolved (Task 2).
3. Changed venue/client IDs under the same real execId, including a different logical position, must not obtain a second credit (Task 2).
4. History commits but runtime credit fails/crashes; retry must require fresh authority and never duplicate or erase accounting (Tasks 2/3).
5. A later contradictory history observation for an already credited candidate must retain original quantity and persist exact-source conflict, including after backup/restore (Tasks 2/3).

## Files, interfaces and task sizing

Paths in the task tables are repository-relative. Task 1 owns issuer lifetime, Task 2 owns accounting/replay and scoped history lookups, Task 3 owns worker orchestration and restoration verification. Execute sequentially; shared runtime/worker/codec changes never have simultaneous writers.

| Task | New production module / target size | Existing integration seams |
|---|---|---|
| 1 | `execution/history_receipts.py`, approximately 120–200 lines, opaque handle and private bounded registry only | `account_binding.py` session methods/lifetime, existing history reader; tests in one focused module |
| 2 | `execution/execution_credit.py`, approximately 250–400 lines, durable records/normalization/pure credit replay | `runtime.py`, `state_store.py`, `history_store.py`, `history_coverage.py`, `codec.py`; split reducer helpers into this module rather than enlarging runtime with a second engine |
| 3 | No new production subsystem | `worker.py`; focused worker/restore tests and status docs; operation script edits only if a demonstrated validation gap requires them |

Sizes are review guides, not reasons to omit checks. Each task ends with focused verification, commit and independent spec/quality review. No implementation began when this plan was authored.

## Task 1: Bounded actual-reader-owned receipt

**Files:** Create `src/multi_exchange_trader/execution/history_receipts.py`, `tests/execution/test_history_receipts.py`; modify `src/multi_exchange_trader/execution/account_binding.py`. Read `history.py:BybitHistoryReader` without duplicating its pagination/parser.

**Interfaces produced:**
- `AuthenticatedHistoryReceipt`: opaque, public construction/copy/deepcopy/pickle/codec rejected; redacted repr.
- Frozen `HistoryReceiptView(snapshot: HistorySnapshot, binding_reference: str)`: data only; no codec registration.
- Session `require_observation_binding(binding: AuthenticatedAccountBinding, *, scope: ProtectionScope) -> BybitAccountIdentity` uses existing binding validation plus internal transport rejection; leaves `require_execution_binding` untouched.
- Session `async read_history_receipt(binding, *, scope, symbol: str, start_ms: int, end_ms: int) -> AuthenticatedHistoryReceipt`.
- Session `require_history_receipt(receipt, *, binding, scope) -> HistoryReceiptView`; `discard_history_receipt(receipt) -> None`.
- Receipt registry is private to the exact session. Pending slots have unique identity/generation and GET-start wall/monotonic time. Binding reference is a generated opaque ID for that binding, never a credential or authentication flag.

- [ ] Write RED tests `test_public_receipt_rejects_injected_transport`, `test_receipt_cannot_be_constructed_copied_or_serialized`, `test_foreign_handle_and_view_do_not_authorize`, and `test_read_only_identity_does_not_gain_execution_permission`. Assert injected route mints zero handles and write permission behavior is unchanged.
- [ ] Add `test_four_pending_and_issued_slots_are_bounded`, `test_expiry_prunes_without_evicting_live_handles`, `test_cancelled_failed_get_frees_slot`, `test_reauth_close_and_key_change_revoke_inflight`, `test_clock_rollback_and_stale_server_time_reject`. Use private-core fixture access only to exercise registry lifecycle; label it synthetic. Never turn injected transport into public-route success.
- [ ] Run `uv run pytest -q tests/execution/test_history_receipts.py`; confirm missing interface/behavior failures, not unrelated setup errors.
- [ ] Implement the private registry and exact session methods. Guard before GET, after GET and at receipt lookup; invalidate generations on reauth/close. Lazy-import the existing reader inside the session method. A finally path releases pending capacity on BaseException including cancellation. No caller snapshot/reader parameter.
- [ ] Run `uv run pytest -q tests/execution/test_history_receipts.py tests/execution/test_account_binding.py tests/execution/test_history.py`; run focused Ruff and `uv run mypy src`. Expected: no failures/type/lint errors; no provider/network observations.
- [ ] Commit `feat: issue bounded session-owned history receipts` with only Task 1 files; review before Task 2.

## Task 2: Stable accounting credit and replay

**Files:** Create `src/multi_exchange_trader/execution/execution_credit.py`, `tests/execution/test_execution_credit.py`; modify `execution/runtime.py`, `execution/state_store.py`, `execution/history_store.py`, `execution/history_coverage.py`, `execution/codec.py` under `src/multi_exchange_trader/`; extend `tests/execution/test_state_store.py`, `test_history_store.py`, `test_native_quarantine.py`, `test_codec.py`.

**Consumes:** Task 1 exact session/receipt methods; frozen `compare_execution_candidate`, `NativeExecutionCandidate`, `FillQuarantineRecord`; existing `ProtectionProjection`, journal commands, `apply_fill` and `apply_position_event`.

**Interfaces produced:**
- Frozen `ExecutionObservationReference(scope, binding_reference, observation_key, snapshot_id, symbol, start_ms, end_ms, observed_at, server_observed_at)` with ProtectionScope/str/int/UTC datetime types matching names. Validate bounded identifiers/window and original timestamps.
- Frozen `CorroboratedExecutionCredit(candidate_id: str, candidate: NativeExecutionCandidate, observation: VenueExecutionObservation, source: ExecutionObservationReference, history_variant_hash: str, command_key: str)`. All fields retained, no authority boolean. `candidate` identifies position/client; `observation` identifies exact authenticated row economics.
- Frozen `ExecutionCorroborationConflict(candidate_id: str, scope: ProtectionScope, instrument_id: str, execution_id: str, venue_order_id: str, client_order_id: str, source: ExecutionObservationReference, history_variant_hash: str, history_conflict_keys: tuple[str, ...], mismatch_codes: tuple[str, ...], prior_credit_event_id: str | None)`. Fixed allowlisted codes; exact source references, no provider body.
- Frozen `StoredExecutionEvidence(record: ExecutionRecord, variant_hash: str, observation_key: str, conflict_keys: tuple[str, ...])` in `history_coverage.py`; not a receipt or capability.
- `HistoryStore.execution_evidence(observation_key: str, execution_id: str) -> StoredExecutionEvidence` reads exact snapshot membership and all relevant stored execution conflicts in one read transaction; missing/ambiguous membership raises fixed HistoryError.
- `RuntimeStateStore.get_inbox_event(event_id: str) -> InboxRecord | None`, scoped across positions. Existing `apply_position_event` remains the sole atomic inbox/projection commit.
- `execution_guard(scope: ProtectionScope, instrument_id: str, execution_id: str) -> str` returns `credit:` plus SHA-256 of canonical structured guard fields. Full venue/client identity remains immutable payload, so changed IDs collide and conflict.
- `CreditReplayState` holds guard→credit records, resolved candidate IDs and decision records; reconstructed from durable events, never authority. Pure `reduce_credit_event(state: ProtectionProjection, event: CorroboratedExecutionCredit | ExecutionCorroborationConflict, *, candidates: tuple[NativeExecutionCandidate, ...], replay: CreditReplayState) -> tuple[ProtectionProjection, CreditReplayState]` validates exact existing ownership/candidates and preserves all unrelated conflicts.
- Extend runtime constructor with optional paired `receipt_session: BybitDemoAccountSession | None = None`, `history_store: HistoryStore | None = None`; exact concrete types/scopes, both present or both absent. These objects are pinned for corroboration; no per-call substitute store/session/authorizer.
- Runtime `corroborate_execution(*, receipt: AuthenticatedHistoryReceipt, binding: AuthenticatedAccountBinding, observation_key: str, candidate_id: str) -> ExecutionCreditResult`. Frozen result fields `candidate_id`, `status: Literal['CREDITED','DUPLICATE','CONFLICT','UNRESOLVED']`, `reason_codes: tuple[str, ...]`; data only.

- [ ] Write RED tests for exact match, fee rebate, exact millisecond→nanosecond conversion, missing client ID, manual/unowned ID, wrong instrument/side, non-Trade type, PENDING/REJECTED/terminal command and native timestamp differing by one nanosecond. Assert zero credit for every mismatch and `credit_authorized=False` on existing comparison results.
- [ ] Add `test_venue_or_client_change_same_execid_never_double_credits`, including another logical position; `test_exact_requery_keeps_first_receipt_metadata`; `test_changed_fee_before_and_after_credit_preserves_conflict`; `test_multiple_canonical_quarantines_block_credit`; `test_source_metadata_variants_do_not_auto_clear_identity_conflict`. Preserve original credited quantity after contradiction.
- [ ] Add `test_credit_one_of_two_fills_keeps_order_quarantine_marker`, `test_last_exact_resolution_removes_only_required_marker`, `test_late_candidate_reopens_unresolved_state`, `test_unrelated_conflicts_survive_credit`. Assert unresolved zero quantity is never flat or qualified.
- [ ] Add `test_credit_inbox_and_projection_commit_together`, `test_failed_commit_does_not_update_cache`, `test_duplicate_returns_current_revision`, `test_cas_conflict_requires_new_reduction`, `test_stored_credit_record_cannot_call_live_credit`, `test_restart_replays_credit_without_live_receipt`; inspect temporary DB rows and effect spies.
- [ ] Pin existing V1/V2 fixtures/hashes unchanged and add V3 roundtrip/replay/tamper cases. V3 is 17 items: V2 fields with V3 tag plus credit count/digest and decision count/digest; validate exact shape. Reconstruct credits/resolutions before checking the summary. Legacy six-field native fill refusal remains unchanged.
- [ ] Run `uv run pytest -q tests/execution/test_execution_credit.py tests/execution/test_state_store.py tests/execution/test_history_store.py tests/execution/test_native_quarantine.py tests/execution/test_codec.py`; establish focused RED.
- [ ] Implement typed records/closed codec registration and pure reduction. Preserve V2 candidate hashing; keep distinct metadata variants unresolved. Track per-candidate resolution so order-level marker removal cannot clear another fill. Keep decision source references exact and fixed-code/redacted.
- [ ] Implement the two scoped storage readers. Runtime pins session/history, validates actual receipt and its exact stored row, scans existing guard across positions, rejects relevant historical conflicts even outside current query, checks command/order quantity and commits with the stable guard. A changed guard appends a separate content-addressed conflict event; exact requery compares stable economics before returning current projection. Do not pass changing receipt metadata into an exact duplicate insertion.
- [ ] Integrate `_states/_summary/_reduce_event/_persist` with pure CreditReplayState, keeping existing candidate index semantics. Before commit invalidate readbacks/pause entries and recheck receipt/lease without await. Credit cache installs only after commit. A conflict on another position retains the original credit and records its event ID. No journal writes occur.
- [ ] Run focused tests plus `tests/execution/test_runtime.py tests/execution/test_protection.py tests/execution/test_execution_provenance.py`, Ruff and mypy. Positive core fixtures may populate private registry state solely in tests; public injected receipt rejection must still pass. This proves local mechanics, not actual authority.
- [ ] Commit `feat: atomically credit corroborated execution candidates`; review before Task 3. Record any discovered interface difference rather than changing authority boundaries to pass fixtures.

## Task 3: Explicit worker corroboration and restoration

**Files:** Modify `src/multi_exchange_trader/execution/worker.py`; create `tests/execution/test_worker_corroboration.py`; extend `tests/execution/test_worker_history.py`, `tests/operations/test_state_snapshot.py`; update `docs/implementation/AUTOMATIC_RUNTIME_STATUS_KO.md` and `docs/implementation/NATIVE_WORKER_20261102.md`. Read `scripts/operations/state_snapshot.py` and `history_snapshot_validation.py`; modify only if new event validation requires it, preserving standalone standard-library execution and existing schema checks.

**Consumes:** Task 1 session receipt, Task 2 pinned runtime dependencies/result and history lookup. No duplicate history parser/store.

**Produces:** `async ExecutionWorker.corroborate_native_fills(symbol: str, *, start_ms: int, end_ms: int) -> tuple[ExecutionCreditResult, ...]`, decorated with existing `_admitted`. Result order is deterministic by candidate ID. Empty tuple means no candidate was processed, never flatness. No automatic invocation, timer, endpoint or CLI flag.

- [ ] Write RED tests `test_worker_injected_session_never_credits`, `test_stop_during_read_prevents_ingest_and_credit`, `test_lease_reacquire_binding_or_worker_change_rejects_result`, `test_cancel_releases_receipt_capacity`. Extend existing worker lifecycle harness; no fake issuer hook in production.
- [ ] Add `test_history_commit_then_credit_failure_is_retryable`, `test_requery_duplicate_keeps_quantity_and_first_metadata`, `test_credited_candidate_checked_for_later_history_conflict`, `test_history_only_rows_unmatched_not_credited`, `test_batch_partial_commit_never_claims_atomic_completion`. Verify actual temporary history/inbox rows; on an operational exception propagate fixed failure, with prior committed per-candidate state still queryable and replay-safe.
- [ ] Add `test_backup_restore_credit_next_day_keeps_original_times_and_blockers`: save populated history/runtime/journal using existing v3 snapshot tooling, restore into a new temporary generation, instantiate a fresh worker/runtime, and check one credit, conflicts and STOPPED/BLOCKED. Old opaque handles and restored comparison/reference data cannot authorize another credit. Existing manifest pairing/standalone restore validations remain enabled.
- [ ] Run `uv run pytest -q tests/execution/test_worker_corroboration.py tests/execution/test_worker_history.py tests/operations/test_state_snapshot.py`; establish RED.
- [ ] Pass worker-owned session/history into runtime construction. Implement explicit orchestration: capture exact worker/lease/session/binding/window → await session GET receipt → repeat existing collect-history admission fences → resolve and ingest exact snapshot → inspect relevant current and already-credited candidates → synchronous runtime corroboration per candidate → finally discard receipt. Never use a saved receipt/reference as authority.
- [ ] Recheck stop/lease/session/binding and receipt freshness before each commit. Existing history collection/read interfaces remain observation-only. An empty, failed or partial batch never grants completeness. Pure receipt resolution is synchronous; no network call occurs under the runtime mutex.
- [ ] Run focused worker/operation tests and existing `tests/execution/test_worker_lifecycle.py tests/execution/test_worker_shutdown_admission.py tests/execution/test_worker_layout.py tests/execution/test_worker_cli.py`. Run required portable suite, Ruff/mypy, and unchanged isolated-native CI once at final source revision. Record actual totals; do not guess a new native count or weaken the exact 36-case parent gate.
- [ ] Update status docs: authentic receipt path implemented but actual transport observation unverified; exact candidate accounting does not provide complete history, flat proof, ACK/release, restart cache restoration or protection. Record no real keys/orders/AI/server effects and no operator settings chosen.
- [ ] Commit `feat: corroborate quarantined fills through owned history worker`; complete task review and whole-branch review before integration.

## Final verification and handoff

- [ ] Review no public dataclass/reference/boolean, injected transport or generic callback can grant credit authority. No receipt enters the codec; no source/reference contains raw provider payloads.
- [ ] Confirm the immutable guard catches changed venue/client identity and every persisted conflict preserves source IDs. Confirm journal transitions/reservation totals and all effect-spy counts unchanged across positive local credit tests.
- [ ] Confirm V1/V2 compatibility, V3 deterministic replay, original observation times, same-generation backup pairing and legacy refusal. Restored local evidence is not a cryptographic provenance attestation or new receipt.
- [ ] Record final SHA/tree, task review results, required checks and unverified real-transport boundary. Finish this package independently of later history-only recovery, protection capability resolution, native cache persistence, production strategy scheduling and hosting.

## Author self-review / SDD preflight

Spec coverage maps issuer to Task 1, exact matching/atomicity/candidate resolution to Task 2, worker fencing/restore/status to Task 3. Five Review Focus items each have named assertions. Shared names/types above are the contract; Task 3 consumes Task 2 constructor dependencies and results verbatim. No task changes execution permission gates or requires actual observations to pass offline tests.

Requested ledger: `.superpowers/sdd/2026-11-02-execution-receipts/progress.md`. The preflight table and rulings are recorded there. Project-conventional locations override skill defaults; the helper that creates a different workspace/marker is not run. Documentation authoring is complete only after root's design/builder check; this document does not claim implementation or final runtime qualification.
