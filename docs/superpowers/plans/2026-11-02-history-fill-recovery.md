# History-only owned execution recovery Implementation Plan

## CURRENT IMPLEMENTATION STATUS (2026-11-02)

Local code and synthetic/offline proof implement separate historical credit/replay and the explicit worker recovery route. Task1 and Task2 passed their task-scoped independent reviews; Task3 independent review, whole-branch review and exact final-source CI remain pending. Main merge/readback and main push CI are separate later gates. No actual authenticated provider issuance, DEMO execution, complete ledger, TP/SL qualification or PC-off hosting has been verified; runtime remains BLOCKED and policy STOPPED. Backup manifest v3 and runtime summary V4 are distinct formats.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Execute sequentially with task-scoped independent reviews. Steps use checkbox syntax.

**Goal:** Recover missing owned executions from authenticated history without fabricating native evidence or enabling trading.

**Architecture:** Separate historical data/credit/decision/resolution types use the existing receipt, history writer, shared execution guard and atomic runtime inbox/projection. New V4 replay preserves all native-only V1/V2/V3 bytes and source provenance. An explicit worker path owns the GET and lifecycle fencing.

**Tech Stack:** Python 3.12, nautilus-trader 1.231.0, SQLite WAL, existing pytest/Ruff/mypy and isolated Linux CI.

**Spec:** `docs/superpowers/specs/2026-11-02-history-fill-recovery-design.md`. Read the spec and only your task brief. Accepted base abc47d7304e8ff4e238b25e0edfef009a78bfbec.

## Global Constraints

- Python 3.12 and nautilus-trader 1.231.0 remain pinned. No actual orders (including DEMO), actual provider/native effects, private operator files, paid AI, extra servers, dependency changes or SDK fork. Only synthetic fixtures and temporary stores.
- No ACK, terminal confirmation, reservation release, qualification unblocking, protection repair, native-cache restore, strategy cycle, UI or hosting changes. No automatic calls, timers, endpoints, CLI flags, inferred query start, or new history writer.
- Never fabricate NativeExecutionCandidate, NativeFillEvidence, native source kind, receive/init time or reconciliation claim. First history credit requires an exact same-fresh-receipt order member and immutable owned ENTRY/EXIT command; PENDING/REJECTED/local terminal stay ineligible for NEW credit.
- Reuse execution_guard and atomic apply_position_event with captured revision, fresh receipt+lease validation, cache install only after commit. Prior-credit contradictions remain inspectable after command ineligibility. Preserve first quantity/source; do not erase decisions.
- Preserve existing serialized types, V1/V2/V3 bytes/hashes and native-only V3 exactly17 shape. New-record V4 is exactly23 as specified. Strict native gate remains exactly36 actual tests without network or skips. Backup manifest v3 is unchanged by summary V4.
- Actual authenticated provider issuance, DEMO operation and hosting remain unverified; runtime BLOCKED and policy STOPPED persist. Preserve originals, worktrees and ignored evidence; no cleanup. User already authorizes necessary branch/PR/main merge after review+exact-source CI, no routine approval question.
- Recovery consumes ONLY normalized rows accepted by the unchanged history parser and validate_history_record. Parser-invalid Delivery/unknown types/non-USDT fee currency fail closed before receipt/credit; do not invent durable normalized contradictions or expand the parser. Late unsupported/blank-ID matrix below means parser-valid rows only.

## Review Focus

1. Existing historical credit with a late changed/missing client ID, venue ID, time or execution_type still records contradiction, including after local terminal confirmation (Task2).
2. History first, then late native variants, and native first, then recovery, share one guard across positions without duplicate quantity or fabricated provenance (Tasks1/2).
3. Missing fresh order proof cannot borrow old stored proof; old cross-window contradictory variants nevertheless veto new credit (Task2).
4. Summary V4, full-record digests and command linkage survive real next-day backup/restore without rewriting native-only V3 or restoring live authority (Tasks1/3).
5. Stop/session/binding/lease epoch changes after GET or between batch items cannot write history/accounting after ownership is lost; prior committed items remain durable (Task3).

## Task 1: Closed historical records, pure replay and compatible summary

**Files:** Create `src/multi_exchange_trader/execution/history_recovery.py`, `tests/execution/test_history_recovery.py`; modify `execution/codec.py`, only pure replay/summary portions of `execution/runtime.py`; add literal accepted-base V3 hash assertion in `tests/execution/test_execution_credit.py` if appropriate. No live recovery methods yet.

**Interfaces:** Produce the six exact public dataclasses and field lists from the spec: HistoryExecutionEvidence, HistoryOrderEvidence, HistoryExecutionCredit, HistoryRecoveryConflict, NativeHistoryResolution, HistoryRecoveryResult; HistoryRecoveryReplayState(credits,decisions,resolutions). Fixed code set as spec. Produce history_decision_event_id(conflict)->str, history_resolution_event_id(resolution)->str, history_credit_digest(credit)->str and reduce_history_event(state,event,*,candidates,native_replay,replay)->tuple[ProtectionProjection,CreditReplayState,HistoryRecoveryReplayState]. Use immutable tuples; pure function validates registered order/provenance/limits and prior record/candidate relationships, no I/O. Add common exact historical/native comparison helper as needed, without constructed native stand-ins. `_ReplayContext` adds historical state, `_summary(state,identities=None,replay=None,history_replay=None)` produces old outputs unless new state exists. `_reduce_event` supports new cases. Live/replay journal linkage is Task2.

- [ ] Read spec's record/format requirements and inspect current pure reducer/codec. Capture accepted-base V3 whole-summary codec SHA256 with a deterministic fixture before editing its algorithm.
- [ ] RED: closed schema round-trip/missing/extra/wrong field and digest validation, no authority serialization; positive history credit with original source/time/economics, exact decimals/rebate and no native candidate; same-source order proof structural refusals; overfill/exit-exposure refusal no new credit; duplicate/conflict preservation; real late native resolution valid/invalid/missing candidate; V4 exactly23 and full-record digest sensitivity. Assert literal native-only V3 hash unchanged.
- [ ] Implement the minimum focused record/reducer/codec integration with bounded validation using existing validate_history_record and exact existing history variant algorithm for full-row hash binding. Do not add fields to old serialized dataclasses or alter native identity/hash semantics. V4 must read/recompute; _persist rejects the new authority-bearing event classes.
- [ ] GREEN and regression: `python -m pytest tests/execution/test_history_recovery.py tests/execution/test_execution_credit.py tests/execution/test_codec.py tests/execution/test_native_quarantine.py -q`; Ruff changed files and mypy changed production modules; git diff --check. Document exact results (overlapping counts not summed).
- [ ] Commit only owned files, self-review, write task-1-report.md with files, SHA/tree, tests/limits and handoff interfaces. Root dispatches independent spec+quality review before Task2.

## Task 2: Receipt-gated live recovery and both source arrival orders

**Files:** Modify `execution/runtime.py`; create `tests/execution/test_runtime_history_recovery.py`; modify `tests/execution/test_runtime_credit.py` only for necessary cross-source regressions. Minimal pure helper corrections in history_recovery.py allowed only with a documented discovered gap, not redesign. No worker changes yet.

**Interfaces:** Consume Task1's closed records and pure reducer. Produce `NativeExecutionRuntime.recover_history_execution(*,receipt:AuthenticatedHistoryReceipt,binding:AuthenticatedAccountBinding,observation_key:str,execution_id:str)->HistoryRecoveryResult`. Extend `corroborate_execution` to handle a prior HistoryExecutionCredit via receipt-gated NativeHistoryResolution/conflict. Factor `_request_for_state(record:CommandRecord,state:ProtectionProjection)->NativeOrderRequest` (or equivalently named nonrecursive private helper) for shared immutable command replay checks. Existing `_request` delegates. All new replay events verify command_key/payload_hash/current immutable owned request independently of current eligibility. Shared prior-credit validation accepts exact native OR history credit type only and validates correct guard/position/provenance. New first-credit path excludes all persisted native candidates at its guard.

- [ ] RED: eligible SENDING/UNKNOWN/ACKNOWLEDGED ENTRY, partials/rebate, explicit EXIT within exposure, duplicates across fresh receipt/restart preserve first metadata; no native evidence created. Missing fresh order with old order or native ACK remains UNRESOLVED; unowned/nonTrade/blank IDs/control commands/PENDING/REJECTED/terminal new never credit. Exact command/order immutable mismatch and cross-window historical order contradiction refuse/persist sourced conflict as applicable.
- [ ] RED: history->native exact resolution and mismatch/variants; native->history duplicate/conflict; global same-exec cross-position; changed price/qty/fee/currency/time/client/venue plus history-only value/fee_rate/closed_quantity; prior-credit nonTrade/empty ID/terminal changes remain conflicts. Prior decisions persist and cannot be bypassed. No native candidate path may be escaped by history recovery.
- [ ] RED: exact whole observation required, foreign/expired/revoked/discarded/copied/injected authority cannot commit; final receipt expiry, inbox-write rollback and real CAS failure do not install caches or duplicate quantities. New events through generic _persist forbidden. Tampered command ref/hash/prior type/resolution digest/summary fails restore before recovery writes. Terminal transition AFTER a valid credit does not invalidate replay.
- [ ] Implement live exact joins from history.execution_evidence and order_evidence plus same current snapshot order member; carry original historical order sources into decisions. Use unchanged history writer/schema and RuntimeStateStore transaction; final receipt+lease check before commit. Never let first-credit exclusions suppress prior-credit contradiction inspection.
- [ ] GREEN: `python -m pytest tests/execution/test_runtime_history_recovery.py tests/execution/test_runtime_credit.py tests/execution/test_history_recovery.py tests/execution/test_execution_credit.py tests/execution/test_native_quarantine.py -q`; Ruff and mypy changed production modules; diff check.
- [ ] Commit only owned files, self-review, report exact SHA/tree/commands/interfaces/remaining unknowns in task-2-report.md. Root independent spec+quality review before Task3.

## Task 3: Explicit worker orchestration and next-day backup/restore

**Files:** Modify `execution/worker.py`, test-only `tests/execution/worker_receipt_support.py`, `docs/implementation/AUTOMATIC_RUNTIME_STATUS_KO.md`; create `tests/execution/test_worker_history_recovery.py`. Standalone scripts unchanged unless an actual validation gap is demonstrated and ruled on. Do not modify native-contract test count modules.

**Interfaces:** Produce `ExecutionWorker.recover_history_fills(symbol:str,*,start_ms:int,end_ms:int)->tuple[HistoryRecoveryResult,...]` on the existing worker class. Consume runtime recover_history_execution. Private synthetic fixture adds independent order rows to the existing fake client; never weakens receipt validators or exposes a public test issuer. Initial success setup stages and journal.claims, without native fill/candidate callbacks.

- [ ] RED: no-candidate success, multiple fills sorted by (time_ms,execution_id,venue_order_id), empty and unsupported/missing-ID rows, repeated reads, native-first/history-first, history committed then runtime failure retry, partial batch persistence, receipt disposal on every continuation.
- [ ] RED: stop during GET rejects before any history SQL; between rows stops later commits; runtime/session/binding/store/scope/symbol/lease-acquisition changes and stale receipt cannot persist. No automatic startup/collect trigger, new timer/flag/endpoint, native order/control call, paid AI or journal mutation.
- [ ] Implement guarded explicit orchestration using captured exact session and finally discard. Keep sticky admission fences and existing APIs working. Extend only synthetic fixture to supply execution and order endpoints separately.
- [ ] RED/GREEN real restore: create actual temporary application file set with history-only credit and sourced late conflict, stop worker, run standalone `snapshot`, `verify`, `restore` with `--require-history --require-runtime`, compare original/restored bytes, reopen next day fresh session/worker/native boundary. Verify V4 summary/source/economics/time/quantity/conflicts, no restored receipt authority or network/control calls; obtain a new next-day synthetic receipt for duplicate check and genuine late native resolution without double credit. Preserve source originals and BLOCKED/STOPPED.
- [ ] Update runtime Korean status and the design/plan implementation status to distinguish code/offline proof from actual provider/DEMO/server unknowns. Use focused command `python -m pytest tests/execution/test_worker_history_recovery.py tests/execution/test_worker_corroboration.py tests/execution/test_worker_history.py tests/execution/test_worker_shutdown_admission.py tests/execution/test_worker_lifecycle.py tests/execution/test_worker_layout.py tests/operations -q`; plus changed-file Ruff/mypy and diff check. Existing configured Windows skips reported separately; hosted native gate remains separate.
- [ ] Commit owned files, self-review, task-3-report.md. Root independent spec+quality review then whole-branch review, exact-head CI, authorized PR/main merge/readback and main push CI. Preserve all artifacts. Do not report actual DEMO, ledger completeness, TP/SL or PC-off hosting as complete.
