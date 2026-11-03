# Controlling Task11 journal-only adoption

Read controlling adoption below FIRST; it supersedes the named historical root candidate that follows. Accepted design closure and actual checkout pins are preserved in root SDD. This is journal-only, not runtime/native/current authority or product completion.

# Task11 journal-only controlling adoption

2026-11-03. Root FULL READ/hash verified design review12995B/f550ce35e80a6b10e321890a4abc90171513de69bec78d7dcb100715053c729b and adopts its C1/I1-I4 plus the explicit M1 choices below. This document controls the prior root candidate8934B/8fcd57a23d2dc71c4fc8bfbc1cba42f1d787d6e57cedc2050e1567afd418b8a1 wherever wording differs. The original candidate, entry preflight and review remain immutable context. C/I labels in that design report denote blocking correctness omission/required ruling, not observed running-program defect counts.

Scope remains existing journal.py + NEW execution/reservations_v2.py + NEW tests/execution/test_reservations_v2.py. New helper is pure local DTO/identity/projection only; existing journal is the only SQLite writer. Existing c/e/codec/runtime/coordinator/ownership/native/restart/portable/config/locks/tests stay readonly. No current receipt/authority/real order or V1 rewrite. All five new public signatures and two DTO fields remain exact spec15/16. No capital/fee/TTL/user policy defaults are invented.

## C1 — mixed active exposure, not stored-owner authority

When any scoped V2 hold exists, legacy register rejects any NEW scalar reservation of ANY CommandKind and any NEW ENTRY regardless of reservation. Legacy claim refuses a V2 record, and refuses claiming any scalar-reserved legacy record or nonterminal legacy ENTRY alongside V2 holds. This extra scalar claim guard applies only to mixed active exposure; ordinary unreserved EXIT/CANCEL/LEVERAGE/control commands remain usable.

Every V2 snapshot/register/claim transaction inspects all scoped commands and rejects any active legacy scalar reservation of any kind OR any nonterminal legacy ENTRY, including unreserved ENTRY, with RISK_V2_LEGACY_RESERVATION_RECONCILIATION_REQUIRED. This guard runs before idempotent success/state-based None. Missing quantity is never invented. Historical get/list remain read interfaces. Terminal V1 history and capacity-neutral exact terminal replay remain possible after structural/local-pin checks. Metadata/terminal V2 history alone does not disable V1 permanently: restrictions follow actual mixed active exposure, never a stored epoch as live-owner proof.

This fences only the upgraded journal's own APIs. Old executables/direct SQL/native writers, actual whole-account completeness and live publication/lease/session authority remain Task13. No local value becomes external authority.

## I1 — epoch domain and finite increments

owner_epoch and non-None expected_previous_owner_epoch use contracts._identifier_v2 exactly: ASCII [A-Za-z0-9][A-Za-z0-9_.:/-]{0,95}. Validate before mutation and use the same domain at bind/snapshot/admission/claim. Account IDs retain their separate ProtectionScope contract. Revision exact int0..2**63-1, bool refused. First bind1, changed epoch increments, same-epoch/current-pin replay unchanged; at max an unchanged replay may succeed but every increment fails before commit.

Lazy initialization creates both version2 tables only through explicit bind; constructor/open/V1 reads create neither. Both-absent may create. Partial/unknown schema fails closed. Existing valid tables with absent scoped metadata count as first-bind state ONLY when that scope has zero V2 reservation rows. Missing metadata cannot reset a populated scope. Never change user_version, delete/recreate tables, rewrite old capture/digest or invent old quantities.

## I2 — prospective aggregate resource closure

Inside BEGIN IMMEDIATE, validate the prospective post-insert ALL-held canonical projection including candidate, resulting revision and no-exclusion calculation snapshot before COMMIT. Apply existing canonical65536-byte bound, component1024/mark100 limits and exact aggregate Decimal conversion/ordering. Current pre-state fitting is insufficient. RISK_V2_RESOURCE_LIMIT rolls back command/reservation/revision/audit atomically; no poisoned unreadable post-state. Bound active set, not unbounded terminal history. No new economic cap.

## I3 — immutable retained row identity and downgrade refusal

Never DELETE or clear V2 reservation identity at release; keep terminal row, exclude it only from held predicate. Exact terminal SQLite integer0/1, kindENTRY, accepted canonical V2 payload/re-encode/hash, deterministic client ID, NULL all-three legacy scalar columns, complete scoped meta/reservation linkage and exact reservation-to-intent equality are required. Detect reservation-only orphans separately and detect malformed rows before state filtering.

ENTRY carrying the reserved V2 frame discriminator but missing its reservation row is rejected, never reinterpreted as unreserved V1. Actual codec607-616 names that reserved discriminator schema='RISK_V2_DURABLE_INTENT'; recognize it independently of wire_version so unknown/corrupted V2 versions cannot fall back. The existing codec alone validates the complete V2 frame. Preserve legitimate legacy payload bounds/semantics; do not classify every arbitrary schema_version=2 dictionary as this intent. One-sided missing-row downgrade is covered; coordinated replacement of all DB evidence is not authenticated by local hashes. Malformed schema/row INPUT_INVALID; wellformed identity disagreement LOCAL_ADMISSION_CONFLICT.

Release keeps the existing explicit caller-confirmed-terminal evidence contract and UNKNOWN refusal, increments capacity revision exactly once and retains full immutable history. ACK/cancel/zero/partial/timeout/rejection alone never automatically release. Actual venue terminal proof is a later runtime owner responsibility.

## I4 — exact PLAN preservation

NEW registration accepts any codec-valid PLAN with non-None required_reservation_quote when reservation margin exactly matches and full supplied local capacity passes. This includes margin-only PLAN lacking liquidation/readback. Preserve its EXACT original stage/blockers/wire bytes: do not manufacture or require PLANNING_ONLY, remove LIQUIDATION/LEVERAGE_READBACK blockers, promote to SUBMIT, or narrow historical decoder. None reserve is rejected as missing margin evidence, never replaced with zero. Local SENDING/claim is accounting state only; current authentic SUBMIT/native authority remains absent from this slice.

## M1 — frozen replay/exclusion behavior

After refreshed local pins and all structural/immutable/mixed-legacy checks, exact replay returns the existing record without NEW insertion economic-capacity checks, extra audit or revision. It cannot double count or ignore stale pins. Claim checks pins before state; absent key is LOCAL_ADMISSION_CONFLICT; matching nonpending returnsNone.

exclude is None or an existing exact scoped V2 reservation command. Unknown/missing/legacy/control key is LOCAL_ADMISSION_CONFLICT. Retained terminal V2 reservation may be excluded and has zero economic effect. Exclusion changes only returned economic components/risk/margin/count, NEVER all-held hash/revision. Snapshot covers every held instrument in account/environment, not selected strategy/symbol. Use accepted component instrument/client ordering separately from held entry command/client ordering.

## Other candidate clauses and validation

All canonical record types/field trees in prior candidate remain fixed: LocalEntryCommandIdentityV2, LocalHeldQuantitySetV2 and LocalReservationSnapshotIdentityV2 through existing contracts._canonical_sha256_v2 only. Independent literal expected canonical bytes/hashes precede helper implementation. Exact codec wire/native ID/derived decimal identity remains unchanged. All required risk/account mode/UTC loss-day/external conflict/counter/margin-capacity checks supplement value_linear_admission_v2; that helper alone does not perform them. Use full required reserve, not only cost allowance. Do not fabricate unavailable full model binding/currentness.

Temporary-file DB tests only, fixed Python/five secret selectors off/new isolated output paths. Meaningful RED/GREEN covers C1/I1-I4/M1 plus original race/rollback/identity/mark-drift/bounds cases, then one stable newreservation+legacy journal/coordinator/runtime regression and touched Ruff/strict mypy. Existing fixture-local TestClient paths if actually required remain explicitly isolated; operator/default/external stores, SDK/native/provider/network/order/AI/server/install effects are forbidden. Root owns Git/CI/main. Per-child source before/after hashes, PID/argv/UTC/exits/separate outputs required; failures and originals preserved, no cleanup.

Runtime Task10/11 integration, Task12 native request/restore/8isolated tests, current source/model/issuer/publication/venue completeness/terminal proof and portable capture timing remain separate gates. Unchanged native schema1/three-file portable constraints are preserved; real need for format change requires owner-reviewed parity. No whole Task11/full63 completion follows journal-only implementation.


---

# Historical candidate, subordinate to controlling adoption above

# Task11 journal-only root technical candidate

2026-11-03. Candidate for independent design/entry review before implementation. NOT source qualification, runtime admission, native support, Task11 whole completion or full63 completion. Root FULL READ verified independent RISK_V2_TASK11_ENTRY_PREFLIGHT_20261103.md17999B/SHA8d837f841954aef2fdac7d81b16421a122af5b050262fd5406ce833f6f59556a and actual spec15/16, journal release contract, current canonical hash owner and exact risk formulas. That report remains immutable historical context; this candidate supplies explicit technical choices.

## Scope and authority

Transfer journal.py, NEW execution/reservations_v2.py and NEW tests/execution/test_reservations_v2.py only. Pure new helper owns exactly two frozen DTOs, validated local identity/held/snapshot hash projections and their scalar validation; it is not another store/writer/serializer/authority framework. journal remains sole SQLite writer. Existing c/e/codec, runtime/coordinator/ownership/native/restart/portable/config/locks/existing tests remain readonly. Root later owns overlapping runtime/restart/portable integration after accepted Task12 shapes. User values remain explicit, no budget/TTL/fee/default-source decisions.

## Adopted local choices

1. Lazy explicit same-execution-DB initialization ONLY from bind_owner_epoch_v2. Existing journal constructor/open/read of V1 must not create V2 tables/rows or alter user_version/digests. Both absent permits create; partial/unknown V2 schema fails closed without repair/drop. Two scoped tables: reservation_meta_v2(account,environment,schema_version,capacity_revision,owner_epoch) and entry_reservations_v2(account,environment,command_key,version,instrument_venue,instrument_product,instrument_symbol,envelope_sha256,base_quantity,risk_amount,margin_amount). Composite scope/key identity; table/row versions2, exact schema/type/required-key constraints validated. Atomic individual DDL/row/audit operations under existing BEGIN IMMEDIATE, no executescript implicit commit. No old capture reseal or scalar conversion. Initialization audit does not contain raw intent/secrets.

2. Append defaulted CommandRecord.reservation_v2:ReservationV2|None, preserving all V1 positional callers. V2 legacy scalar columns NULL. Existing get/list reconstruct and validate exact joined V2 rows; orphan, malformed, dual-version and wrong identity fail closed. Legacy reserved-entry registration/claim cannot bypass active V2 or reuse its key. No rewrite of old payload/Decimal/hash/nativeID or terminal history.

3. Local revision exact int0..2**63-1; refuse bool/overflow before mutation. First bind starts revision1; changed epoch increments; same epoch/current pins is idempotent. Absent meta is revision0/previousNone only for bind. Runtime epoch generation remains separate; never persist process-local acquisition_id as fresh epoch proof. Explicit fixture strings are local labels, not live lease evidence. Snapshot/register/claim need actual bound metadata and same scoped owner_generation.

4. ALL-held identity covers every unconfirmed reservation including excluded candidate and ACK/UNKNOWN/REJECTED holds. Only terminal-confirmed release removes a hold. State-only transition leaves revision/hash unchanged. Exclusion affects economics/components/count, not held hash/local revision. Unknown exclude rejects. Components use actual deterministic client ID as component_id, PENDING_ENTRY origin and decoded side; actual venue-ID nonoverlap/coverage remains Task13.

5. Reuse c._canonical_sha256_v2 with its existing RISK_V2_CANONICAL_V1 wrapper; no second JSON encoder. Record type LocalEntryCommandIdentityV2 fields EXACT: account_id,environment,command_key,kind='ENTRY',intent_sha256,reservation. reservation fields EXACT: schema_version=2,instrument (existing validated venue/product/symbol tree),envelope_sha256,base_quantity,risk_amount,margin_amount. Derived decimal strings use existing V2 derived-text contract, never legacy journal._decimal_text. intent_sha256 hashes exact existing encode_entry_intent_v2 UTF8 bytes. Existing ec_ nativeID algorithm remains separately verified.

6. Record type LocalHeldQuantitySetV2 fields EXACT: account_id,environment,entries. entries sorted by command_key/client_order_id; each object exact command_key,client_order_id,payload_hash,version=2,instrument,base_quantity,envelope_sha256,risk_amount,margin_amount,position_count=1. Record type LocalReservationSnapshotIdentityV2 fields EXACT: account_id,environment,owner_epoch,journal_revision,held_quantity_sha256,excluded_command_key. Snapshot ID is its hexadecimal digest. observed_at explicit now remains in the accepted snapshot DTO digest; do not mint a new captured risk binding by rereading now. Canonical independent literal fixtures/hash domains must be fixed before implementing helpers; expected bytes/hashes cannot be generated through production helpers.

7. Reservation and Admission DTOs and five NEW public function/method signatures remain exact spec15/16; existing release_reservation is the sixth integration seam. Validate exact closed nested types, scope/account/command/intent envelope/Q/risk/required margin and full current supplied marks. PLAN is registrable only when required_reservation_quote is non-None and complete; retain PLANNING_ONLY and inert history meaning. Missing margin is rejected, never zero-filled. Journal local claim is not current SUBMIT qualification; no runtime/new effect route is wired by this slice. Do not narrow codec historical decoding.

8. Register BEGIN IMMEDIATE checks epoch/revision/all-held hash and stored/proposed immutable identity BEFORE idempotent return. New insertion atomically writes command+reserve+revision+audit. Exact replay requires refreshed local pins and adds no capacity/audit. Compare given policy/account/marks using accepted exact valuation and actual existing risk/daily/drawdown/loss-count/position formulas. Source/model original-binding reconstruction is unavailable from admission and MUST NOT be fabricated. Full required margin <= exact current capacity, not only allowance. New registration changes revision and requires a genuinely new later snapshot/model calculation before claim.

9. Claim checks all local pins and reconstructed identity before state, then conditionally PENDING+terminal0→SENDING; identical hold leaves revision/hash unchanged. Matching nonpending returnsNone; stale pins conflict first. No reset/resend/reprice. Extend existing release_reservation caller-confirmed-terminal contract atomically for V2 revision once; preserve UNKNOWN refusal. This API consumes explicit caller assertion/evidence, not authenticated venue proof. No release merely from ACK/IOCcancel/zero/partial/timeout/late/cancel request. Actual ENTRY terminal verifier belongs future runtime owner.

10. Fixed RiskV2Rejected LOCAL_ADMISSION_CONFLICT for local epoch/revision/hash/immutable key mismatch; LEGACY_RESERVATION_RECONCILIATION_REQUIRED for active V1; existing exact input/resource/scope/revision/freshness/economic codes for their real owner boundaries. Malformed schema/row INPUT_INVALID; wellformed stored identity disagreement local conflict. Unlisted programmer/SQLite IO/commit failures propagate without success or automatic possibly-committed retry. Legacy exception meanings unchanged.

## Verification and remaining owner gates

Implement meaningful tmp-file DB RED→GREEN using independent literals, explicit fixture scopes/amounts/marks/epoch and separate connection per race contender. Cover all eleven independent preflight groups, actual commit/audit rollback, exclude mutation, two contenders, PLAN-with-reserve/None, V1 bypass, exact raw/int/Decimal bounds and hostile context. Once stable one newreservation+existing test_journal/test_coordinator/test_runtime run, touched Ruff/strict mypy. Fixed Python/five selectors off/new isolated paths; fixture DB only, no default/private store, SDK/native/provider/network/order/AI/server/install/Git/remote effects. Root serializes Git/CI. Freeze exact per-command source before/after hashes, PID/times/argv/exits/stdout/stderr; preserve prior failures/artifacts. Fresh independent code review and exact-head full CI remain mandatory.

Task10 runtime/native restore, Task12 exact request/OwnedCacheExpectation union/native8, Task13 actual epoch/owner/publication/current issuer/full-held venue coverage and terminal proof remain separate. Preserve exactly three portable files/old native schemas unless later reviewed owner necessity. Lazy V2 tables change application digest only after explicit new V2 action; old capture verification must precede it. Current program must not claim full Task11 before runtime/restart/portable integration is covered. Task12 static inspection separately notes old restart code does NOT yet prove IOC/post-only; do not adopt that incidental overstatement in the Task11 preflight as a fact.
