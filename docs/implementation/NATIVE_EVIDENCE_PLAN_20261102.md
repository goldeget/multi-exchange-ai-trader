# Native Capability, Execution Provenance and Recovery Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish offline, independently tested native capability facts, quarantine uncorroborated native fills, and diagnose exact native-cache recovery mismatches without authorizing execution.

**Architecture:** Keep data observations separate from authority. Real pinned native objects run only in the existing OS-network-isolated test harness; the runtime treats native fill callbacks as candidates until independently authenticated execution evidence exists. Recovery inspection reads existing cache objects and compares manifests; it never restores, submits, adopts, or resumes anything.

**Tech Stack:** Python 3.12, NautilusTrader 1.231.0, pytest, existing closed execution codec/journal, Linux network namespaces, existing Ruff/mypy/CI.

**Spec:** [NATIVE_EVIDENCE_SPEC_20261102.md](./NATIVE_EVIDENCE_SPEC_20261102.md), including immutable upstream source links. Read both documents before implementation. This plan deliberately implements audit steps A, the non-authorizing part of B, and read-only diagnostics from C. Protective repair, cache restoration and actual-DEMO qualification remain subsequent projects.

## Global Constraints

- `nautilus-trader==1.231.0`; upstream commit `27a8e54e7ac3c57d6cbf8891f0283dfbaee97317`. Do not upgrade or fork the SDK.
- DEMO, Bybit LINEAR, one-way `position_idx=0`; keep all production/runtime qualification gates closed and `production_supported=False`.
- No actual native writes, provider calls, private files, real databases, Redis installation, new server, paid AI, or provider/server cost. Tests use synthetic fixtures and temporary databases only.
- No REST writer, custom kwargs workaround, trigger-source downgrade, atomic-bracket claim, or inferred attached-child lineage.
- Explicit standalone `MarkPrice` is unsupported by the pinned Strategy → DEMO submit path; attached `tp_trigger_by/sl_trigger_by=MarkPrice` is forwarded. LastPrice/IndexPrice explicit standalone forwarding is also unproven/unsupported, not an automatic fallback.
- Unknown `trigger_by` being silently ignored is a failing capability, not successful support.
- No caller-supplied `authenticated`, `complete`, `qualified`, or `allow_execution` boolean creates authority. Typed immutable observations are data, not authentication.
- Work on the parent's finalized history branch, preserving its behavior. Do not assume old line numbers or totals remain current. Do not edit concurrent history work or add a second execution-history writer.
- Preserve the user-authorized continuing implementation method; no routine approval question is required by this plan. Implementation proceeds under the existing user authorization; authoring this plan performed no effects.

## Review Focus

- A non-reconciliation OrderFilled or FillReport still has unproven transport provenance; Task 2/3 must quarantine it too.
- A reused real execution ID with changed fee, price, quantity, venue ID or timestamp must conflict rather than deduplicate; Task 2 pins each field.
- Legacy persisted native fill tuples have no provenance; Task 3 must block ambiguous restore without silently deleting evidence or recomputing a misleading zero position.
- Equal checkpoint labels cannot prove backup authenticity, and cache state can change while inspected; Task 4 returns diagnostics only and rejects revision changes.
- An unrelated manual order must never be claimed or canceled merely because instrument/price matches; Task 4 reports additional/unowned cache entries and Task 3 rejects wrong ownership.

## Files and dependency graph

All paths below are relative to the repository. Each new module has one responsibility.

| File | Responsibility |
|---|---|
| `src/multi_exchange_trader/execution/native_capabilities.py` | Pure pinned capability description, no native loading or effect |
| `src/multi_exchange_trader/execution/native_events.py` | Preserve native fill class/reconciliation metadata without claiming source authenticity |
| `src/multi_exchange_trader/execution/execution_provenance.py` | Candidate, corroboration-observation and quarantine records; exact data comparison |
| `src/multi_exchange_trader/execution/runtime.py` | Persist quarantine before returning; stop native candidates from becoming credited fills |
| `src/multi_exchange_trader/execution/codec.py` | Closed registration of new safe evidence records |
| `src/multi_exchange_trader/execution/native_recovery.py` | Read-only cache/checkpoint inspection and deterministic mismatch codes |
| `tests/execution/test_native_capabilities.py` / `_linux.py` | Portable capability rules / actual-wheel forwarding contracts |
| `tests/execution/test_execution_provenance.py` / `_linux.py` | Exact comparison / actual native event metadata |
| `tests/execution/test_native_quarantine.py` | Durable runtime quarantine, replay and old-schema refusal |
| `tests/execution/test_native_recovery.py` / `_linux.py` | Read-only mismatch diagnostics / actual Cache and NETTING identities |
| `.github/workflows/ci.yml` | Include all new native modules and update exact collected count |
| `docs/implementation/BYBIT_NATIVE_BRIDGE_KO.md` | Explain candidate/quarantine and capability limitations |

Task 1 is independent. Task 2 precedes Task 3. Task 4 may proceed independently against the agreed records, but changes to `runtime.py`, `codec.py` and CI must be integrated serially. Each task ends with a focused commit and review.

## Task 1: Pin actual parameter-forwarding capabilities

**Files:** Create `native_capabilities.py`, `test_native_capabilities.py`, `test_native_capabilities_linux.py`; modify CI's native module list/count.

**Interfaces:**

`describe_native_capability(*, native_version: str, command_kind: Literal["ATTACHED_ENTRY", "STANDALONE_STOP", "STANDALONE_TAKE_PROFIT"], trigger_source: Literal["MarkPrice", "LastPrice", "IndexPrice"]) -> NativeCapability`.

Frozen `NativeCapability` contains version, source commit, environment=`DEMO`, product=`LINEAR`, command kind/source, `parameter_forwarding: Literal["SUPPORTED", "UNSUPPORTED"]`, fixed reason codes, and `production_supported: Literal[False]`. It describes audited forwarding only; it cannot activate dispatch. Unknown versions/commands/sources raise a fixed ValueError. Standalone explicit source returns `UNSUPPORTED / EXPLICIT_TRIGGER_SOURCE_NOT_FORWARDED`.

- [ ] Write portable RED tests for wrong version and attached-versus-standalone distinction; assert every result remains production unsupported.
- [ ] Write **12 collected native cases** using `isolated_native_runtime`, `native_harness`, `adapter_boundary` and `module` from `test_bybit_native_linux.py`: six attached combinations (BUY/SELL × three sources), four standalone combinations (STOP_MARKET/MARKET_IF_TOUCHED × BUY/SELL with MARK_PRICE), one public PyO3 submit-signature check, one ignored-generic-`trigger_by` check. Every conditional test checks exact client ID, quantity, side, reduce-only, position index, trigger price and NO_TRIGGER emulation. The attached tests check actual `BybitNativeTpSlParams` source fields.
- [ ] Run portable tests to establish RED. Run native cases only through the existing `sudo unshare --net -- env -i ... REQUIRE_NATIVE_CONTRACTS=1 NATIVE_NETWORK_ISOLATED=1 ...` job, never directly in a network-enabled session. The real OrderFactory and Strategy produce SubmitOrder; the real Python adapter executes with FakeHttpBoundary replacing the Rust HTTP boundary. Do not instantiate/connect the real Rust client. Assert only what reaches that boundary and what the public PyO3 signature exposes; do not label this a wire-level HTTP serialization test.
- [ ] Implement the pure capability descriptor and test fixtures. Do not extend NativeOrderRequest to protective roles or change any dispatch gate. The standalone tests should PASS by proving the capability is unsupported, not by arranging a fallback to default venue semantics.
- [ ] Run `uv run pytest -q tests/execution/test_native_capabilities.py`, focused Ruff/mypy, and the isolated native job. Update CI count using the counting rule below. Commit `test: pin native protection parameter forwarding`.

## Task 2: Preserve candidate provenance and define exact corroboration data

**Files:** Create `execution_provenance.py`, `test_execution_provenance.py`, `test_execution_provenance_linux.py`; modify `native_events.py` and decoder tests in `test_bybit_native.py`; extend closed `codec.py` registration and `test_codec.py`.

**Interfaces:**

- Add to `NativeFillEvidence`: `source_kind: Literal["NATIVE_ORDER_FILLED", "NATIVE_FILL_REPORT"]` and `native_reconciliation: bool | None`. Decode OrderFilled's actual boolean; missing/invalid metadata for OrderFilled is invalid. FillReport uses None because the field is absent. Neither source means authenticated venue execution.
- `NativeExecutionCandidate(fill: NativeFillEvidence, venue_order_id: str)` is frozen, validates bounded nonempty IDs and consistent types. All IDs are preserved verbatim; never infer missing IDs or classify synthetic fills using guessed prefixes.
- `VenueExecutionObservation` is frozen data with `scope`, `binding_reference`, `history_batch_id`, `execution_id`, `client_order_id`, `venue_order_id`, `instrument_id`, `side`, `quantity`, `price`, `commission`, `commission_currency`, `ts_event_ns`. References identify evidence to be checked by a future trusted issuer; their presence is not proof.
- `compare_execution_candidate(candidate: NativeExecutionCandidate, observation: VenueExecutionObservation) -> ExecutionComparison`, with state `MATCH | CONFLICT`, fixed mismatch codes, and `credit_authorized: Literal[False]`. Compare every economic/identity field; receipt/init timestamps are not execution identity. A successful comparison remains data-only.
- `FillQuarantineRecord(candidate: NativeExecutionCandidate, reason: Literal["AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED"])` is serializable through the closed codec and contains no raw provider payload/secret.

**Future authenticated corroboration contract (document, do not implement an issuer or release here):** The owner of `BybitDemoAccountSession` must validate its opaque binding before and after the exact GET observation, including UID, parent/subaccount expectations, DEMO host and credential session. It must retain actual provider execId/orderId/orderLinkId and the normalized economic row from that same scoped transport; join against a precommitted owned order and venue-order mapping; reject stale/revoked/injected transport authority. Individual-row authenticity does not imply complete history: any terminal or full-exposure conclusion additionally needs bounded-window/cursor completeness and current position reconciliation. Existing history receipt types should be reused only after their final implementation is reviewed against this contract. No public constructor/JSON payload/comparison result becomes a receipt. Until such an issuer and consumption path are separately qualified, all native callback candidates remain quarantined, even MATCH.

This is an **intermediate guard**, not completed fill handling or qualified DEMO readiness. The subsequent production issuer must be owned by the same authenticated actual reader/session that obtained the execution, not by a caller who supplies a transport flag or copied dataclass. Its replay-safe join key must include authenticated account/environment, instrument, actual venue order ID and actual execId, and corroborate the native execution ID exactly; changed economic contents under that key must conflict. Release will additionally need an atomic evidence/projection transaction, durable deduplication across restart and fresh session authority. Saved comparison results, metadata and reference strings alone must never auto-release a candidate. That issuer/release is separate because this offline increment has no actual authenticated transport observations or final integrated history-receipt provenance to qualify; synthetic tests can prove rejection/comparison behavior, not manufacture those inputs.

- [ ] Write portable RED tests for exact MATCH with `credit_authorized=False`; mismatch each identity/economic field separately; absent venue ID; empty exec ID; negative maker rebate preservation; exact replay despite different receive timestamp; forged scope/binding-reference strings giving no authority; type-invalid reconciliation metadata; codec roundtrip and unknown extra fields rejection.
- [ ] Write **three collected native cases** (parameterized): actual OrderFilled with reconciliation False, actual OrderFilled with reconciliation True, actual FillReport. All produce distinct metadata and remain uncredited. Use the same socket-denied real-wheel harness. Do not infer true execution from reconciliation=False; reconciliation=True is not sufficient to identify inferred fills either.
- [ ] Run `uv run pytest -q tests/execution/test_execution_provenance.py tests/execution/test_codec.py tests/execution/test_bybit_native.py` to establish RED, then implement the small immutable records and decoder metadata. Keep existing fixed-error redaction.
- [ ] Run that focused command, Ruff/mypy and isolated native cases to green. Do not add a production release function or an `authenticated=True` argument. Commit `feat: retain untrusted native execution provenance`.

## Task 3: Quarantine before native fills can affect owned exposure

**Files:** Modify `runtime.py` at `consume_native`, `_reduce_event`, `_states`; add `test_native_quarantine.py`; update affected `test_runtime.py`, `test_native_node_linux.py` assertions and bridge documentation. No edits to the concurrent history collector/store are planned.

**Consumes:** Task 2 `NativeExecutionCandidate` and `FillQuarantineRecord`.

**Produces:** Existing `consume_native(...) -> NativeOrderEvidence` remains an observational return. A fill callback durably records its quarantine candidate, invalidates readbacks, and pauses entries; it does not call `apply_fill`, ACK a SENDING/UNKNOWN command solely from this candidate, release reservations, mark terminal, or authorize an exit. Existing non-fill acknowledgement/cancel behavior retains its independent guards.

- [ ] Add RED tests `test_native_fill_candidate_does_not_change_owned_quantity_or_ack`, `test_quarantine_invalidates_readbacks_and_pauses`, `test_quarantine_survives_restart_without_credit`, `test_quarantine_duplicate_keeps_same_candidate_identity`, `test_same_exec_id_changed_candidate_is_preserved_as_conflict`, and `test_quarantine_persistence_failure_cannot_credit_or_resume`. Assert journal state/reservations, quantity, known fills and effect spy counts before/after, including both reconciliation booleans and FillReport.
- [ ] Add `test_legacy_native_fill_restore_requires_provenance`: a temporary old-schema store containing a six-field native fill tuple must fail closed with `LEGACY_NATIVE_FILL_PROVENANCE_REQUIRED`, retain its bytes/evidence, and not rewrite a zero position. If the final history integration shares this shape and provenance is indistinguishable, refuse ambiguous restore rather than guessing which writer produced it. No automatic migration/adoption occurs in this patch. Preserve pure protection-reducer tests by testing `apply_fill` directly; do not delete their accounting coverage or add production bypasses to preserve obsolete callback assumptions.
- [ ] Run `uv run pytest -q tests/execution/test_native_quarantine.py` to establish RED. Implement quarantine handling before the current fill-identity reduction/ACK branch; add a narrowly validated quarantine record replay branch. Use durable inbox identity derived from canonical candidate fields, excluding receive/init timestamps. Same exec ID with differing canonical content must be retained and flagged, not overwritten. Full provider payloads are never persisted.
- [ ] Run `uv run pytest -q tests/execution/test_native_quarantine.py tests/execution/test_runtime.py tests/execution/test_protection.py tests/execution/test_codec.py`, then the finalized parent's existing history/worker regression set unchanged. Review overlap with their final event schema before editing. Retain `_require_qualification()` unconditionally blocked. Existing real-node callback tests must now assert quarantine rather than fill credit, preserving their collected case count.
- [ ] Document that the projection is incomplete while uncorroborated fills exist; zero credited quantity is not flat proof. Run isolated native regressions and commit `fix: quarantine uncorroborated native fills`.

## Task 4: Read-only native recovery diagnostics

**Files:** Create `native_recovery.py`, `test_native_recovery.py`, `test_native_recovery_linux.py`; update bridge documentation and CI module list/count. No restore implementation or runtime activation hook.

**Interfaces:**

- Frozen `RecoveryCheckpoint(scope, generation_id, journal_revision, ownership_digest, native_version, trader_id, strategy_id)` records describe the application checkpoint and native-cache checkpoint separately. They are data, not attestations; absent native checkpoint is a blocker.
- Frozen `OwnedCacheExpectation(request: NativeOrderRequest, native_account_id: str, trader_id: str, strategy_id: str, venue_order_id: str | None, expected_native_position_id: str | None)` records exact durable expectations, including a previously observed native position link if present. Logical application position IDs remain in `request.owned.position_id`; never substitute them for NETTING IDs.
- `inspect_native_recovery(*, cache: NativeCacheReader, bindings: NativeBindings, expectations: tuple[OwnedCacheExpectation, ...], application_checkpoint: RecoveryCheckpoint, cache_checkpoint: RecoveryCheckpoint | None, observed_revision_before: int, observed_revision_after: int) -> NativeRecoveryCheck`.
- `NativeCacheReader` exposes only `order(client_order_id)`, `position_id(client_order_id)` and `orders()` reads. The actual-cache test confirms pinned signatures; no cache mutation is exposed. `NativeRecoveryCheck` contains `status: CONSISTENT | BLOCKED`, exact fixed-code issues by owned ID, and `production_supported=False`. CONSISTENT means compared data agree only. Revision values are diagnostics, not a substitute for the future worker's exclusive lock/snapshot implementation.

Inspect exact cached type/fields, ID, instrument, side, quantity, limit price, reduce-only, trader/strategy/account, expected venue ID and active state. Calculate permitted NETTING ID as `instrument_id-strategy_id`; reject a non-None cached link that differs, and reject a missing expected persisted link. Before any fill, a deliberately absent expected/cached position link is acceptable as a local consistency observation only. Return codes for missing cached order, initialized placeholder, identity mismatch, unowned additional cache entries, missing/checkpoint mismatch, wrong native version and inspection revision change. Do not call `cached_owned_order` if it would prevent collecting all diagnostic issues; reuse its comparisons as a pure helper only if no behavior changes are necessary.

- [ ] Write portable RED tests for exact consistent cache, missing cache order, a fresh initialized placeholder, wrong strategy/account/venue ID, wrong NETTING logical ID, partial cache restore, missing/checkpoint mismatch, duplicate contradictory expectations, unknown additional manual cache order, cache accessor error, and changed observed revision. Every case asserts zero calls to submit/cancel/load_cache/add_order/build_index/reconcile and `production_supported=False`.
- [ ] Write **two collected native cases**: a real Cache populated with actual initialized/accepted order events and correct NETTING mapping is only CONSISTENT; a second fresh Cache lacking those orders remains BLOCKED after the application bridge's `register_owned`. These are offline fixture construction, not a production restore algorithm. No Redis, native node connection, placeholder replay or private SDK patching.
- [ ] Run `uv run pytest -q tests/execution/test_native_recovery.py` to establish RED; implement read-only normalization/comparison with fixed redacted errors. No boolean/property named recovered/ready/qualified is introduced.
- [ ] Run portable tests, isolated native tests and focused Ruff/mypy. Document checkpoint diagnostics cannot prove persistent-cache durability or authentic backup restoration. Commit `feat: inspect native recovery consistency without effects`.

## CI accounting and final verification

The inspected `.github/workflows/ci.yml:69–79` explicitly lists two native modules and requires **19** passing/unskipped cases. Planned additions are Task 1 **12**, Task 2 **3**, Task 4 **2** = **17**; unchanged baseline 19 therefore becomes **36**. Do not merely change the check to `total > 0`.

- [ ] At execution start, collect the rebased native baseline with the existing isolated job. Preserve its cases. If the parent legitimately added native tests, expected total is verified baseline +17, and record each difference; 36 is conditional on the observed baseline of19.
- [ ] Add all three new `_linux.py` modules to the explicit native invocation. Keep exact zero skips/failures/errors and exact expected total, plus assert each new module contributes 12/3/2 JUnit testcases so an omitted module cannot be hidden by unrelated additions. Count parameterized cases, not test functions/subtest assertions.
- [ ] After each task, update the staged exact expected count consistently (baseline+12, +15, +17). The portable suite discovers the new Linux tests but may skip without explicit isolation; those skips never count as native qualification. Portable additions have no guessed repository-wide total: report actual collected/passed/skipped counts from the completed run.
- [ ] Once all tasks pass, run `uv run pytest -q`, `uv run ruff check src tests scripts`, `uv run mypy src`, and the required isolated native job once at the final source revision. Run the parent's existing operation/history checks without changing them. No frontend implementation is planned; let normal CI enforce repository-required frontend checks.
- [ ] Confirm source review sees no global qualification gate change, new writer, native connection, actual-account fixture, secret/environment reader, SDK fork or server deployment. Final status must say offline contracts only and list remaining actual authenticated execution, lineage, history completeness and cache-persistence blockers.

## Self-review / handoff

This plan covers capability facts, event provenance quarantine and read-only recovery diagnostics; it intentionally does not implement authenticated receipt issuance, release/credit of candidates, terminal/full-history qualification, attached-child adoption, protection repair, cache restore, server setup or investment controls. Task interfaces are data-only and consistent across files. Five review-focus failures are assigned concrete tests. New native count is17; existing callback expectations change without removing tests. No implementation or test execution was performed while authoring this plan.
