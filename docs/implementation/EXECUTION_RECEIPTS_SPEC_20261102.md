# Authenticated execution receipts and durable accounting credit

Date: 2026-11-02. Design only; no implementation, tests, provider requests, secrets or actual account observations performed while authoring.
Base: `acb829a6481408b00dfc8189eb82ddd4e0b5dab6`, tree `6edf58bad8a76b7f7d73a4a14e3b679e9beaa96b`. PR #19 is merged; remote main verified 2026-11-02 before implementation.

## Goal and boundary

Corroborate an already-persisted native fill candidate with execution history obtained by the same authenticated actual DEMO session, then atomically record accounting credit and its projection exactly once. This continues the authorized product development after the independent offline-preparation milestone. It neither activates trading nor completes the final automatic product.

- Python 3.12; `nautilus-trader==1.231.0`; Bybit DEMO, LINEAR, one-way `position_idx=0`.
- No actual provider/native effects, private files, real databases, paid AI, new server, dependency change or SDK fork during implementation/tests. Temporary fixtures only.
- No second history writer or REST writer. Existing history storage remains the only historical observation writer.
- No ACK, terminal transition, reservation release, exit authority, resume, qualification flag, protection repair, native-cache restore, strategy cycle, AI or hosting changes.
- History-only fill recovery is excluded. Credit only a persisted quarantine candidate matched to a `Trade` row with a nonempty exact `orderLinkId`; never adopt a manual order.
- Existing execution permission checks and unconditional runtime qualification rejection remain unchanged. Read authentication is separate from permission to trade.

## Existing seams verified at the base

`account_binding.py` owns an exact `BybitReadOnlyClient`, opaque binding and credential/session checks. `require_binding` allows injected transport; `require_execution_binding` additionally rejects it and checks trading permissions. `BybitHistoryReader.read_history` checks the binding per page and returns a constructible `HistorySnapshot`. `HistoryStore.ingest` returns a constructible `IngestionReceipt`; neither object grants authority.

`ExecutionWorker.collect_history` checks admission, worker/session/binding identity, scope, symbol and lease acquisition before and after GET. `RuntimeStateStore.apply_position_event` atomically writes an inbox event plus revision-checked projection. History, runtime state and command journal use separate SQLite connections. History-first processing is not a three-database atomic transaction.

Final Task 3 runtime quarantines native fills without ACK/credit. `_candidate_identity` normalizes Decimal scale and excludes init/receipt times but retains native source metadata. `_states`, `_summary`, `_reduce_event` and `_persist` replay V1/V2; V2 preserves the candidate identity count/digest. The existing corroboration-required conflict is aggregated by client order ID. Legacy native-fill tuples are refused.

## Session-owned receipt

Add opaque `AuthenticatedHistoryReceipt` with no public constructor, copy/deepcopy/pickle support or codec registration. The exact issuing session stores each live handle and its immutable `HistoryReceiptView(snapshot, binding_reference)` privately. The view is observation data, not authority. An exact handle lookup, not a public boolean or reference string, validates issuance.

`BybitDemoAccountSession.require_observation_binding(binding, *, scope)` runs existing binding checks and rejects the session's internally injected transport. It deliberately does not require linear trading grants; `require_execution_binding` is unchanged. Independently supplied expected UID/parent/master remain mandatory; no returned metadata may become its own expectation.

`await read_history_receipt(binding, *, scope, symbol, start_ms, end_ms) -> AuthenticatedHistoryReceipt` invokes the existing exact `BybitHistoryReader` internally using the session-owned client. No caller reader/snapshot/transport/issuer callback is accepted. Use a lazy import to avoid the existing history-to-session import cycle. Validate actual transport, binding and scope before GET, after GET and before minting.

`require_history_receipt(receipt, *, binding, scope) -> HistoryReceiptView` checks exact handle/session/binding identity, credential/host invariants and freshness. `discard_history_receipt(receipt) -> None` is idempotent for absent/expired handles and never accepts a foreign handle as valid.

Allow at most **4 total pending-plus-issued receipts per session**. Reserve capacity before awaiting; prune expired entries on every receipt operation. Lifetime is **min(session max_age, 30 seconds)** from GET start, checked with monotonic time and original wall/server observation times. Invalid/negative clock movement rejects. No eviction of a still-valid handle to admit another request. Reauthentication/close clear pending and issued generations; an older in-flight read cannot mint after either. Cancellation/error frees its slot and mints nothing. All repr/errors are fixed/redacted.

## Durable data and exact matching

Introduce closed codec records in `execution_credit.py`: `ExecutionObservationReference`, `CorroboratedExecutionCredit`, `ExecutionCorroborationConflict`. They contain normalized observations, fixed codes and exact source identifiers, never raw provider bodies or secrets. They are durable local evidence, never live capabilities.

The reference contains scope, binding reference, history observation key/snapshot ID, symbol, explicit start/end milliseconds and original local/server observation times. A credit additionally retains full immutable execution economics, command/client/position mapping, normalized history variant hash and exact candidate ID. Conflicts retain candidate ID, actual execution/order IDs, observation/variant identifiers and relevant history conflict keys; every persisted conflict must identify its sources.

`HistoryStore.execution_evidence(observation_key, execution_id)` returns the exact normalized member and variant hash plus all stored execution-identity conflicts, including outside the queried window. Missing/ambiguous membership is rejected. This is a scoped read, not another ingestion path or authority issuer.

Exact comparison includes account/environment, instrument, execution ID, venue order ID, client ID, side, quantity, price, fee/currency and execution time. Convert history `time_ms * 1_000_000` exactly; no timestamp tolerance. Resolve instrument from the immutable owned request, not a guessed symbol suffix. Preserve negative maker rebates. Retain and check history conflicts for other normalized row economics too.

An authenticated nonempty client ID must match a precommitted owned command and registered order. PENDING/REJECTED/terminal commands are ineligible. An unknown venue ID may be bound by that exact row only if no existing conflicting mapping exists; a different known venue ID blocks. Matching symbol/price alone never establishes ownership. If an order-history member is present, contradictory side, identity, position index or reduce-only blocks; its absence alone is not failure because creation time can predate the execution window.

Only `Trade` rows are creditable; Funding/AdlTrade/BustTrade/Delivery and unmatched history rows remain unresolved. Multiple distinct canonical quarantine variants under the execution guard, or an existing execution-identity conflict, block automatic credit even if one row matches. Source/reconciliation metadata variants are not silently coalesced in this package. `compare_execution_candidate` remains data-only with `credit_authorized=False`.

## Atomic credit, collision handling and replay

The full immutable replay key is `(account, environment, instrument, venue_order_id, execution_id)`. A stronger unique guard `(account, environment, instrument, execution_id)` prevents changed venue/client IDs from producing another credit. Hash canonical structured encoding, never concatenated ambiguous identifiers. The runtime inbox's scoped primary key can enforce this guard; no extra database/table is required.

Add scoped `RuntimeStateStore.get_inbox_event(event_id)` and an internal stable-key runtime credit persistence helper. Under the runtime mutex and valid lease, inspect existing guard content, verify the live receipt immediately before mutation, reduce through existing quantity/reduce-only checks, then call `apply_position_event` for atomic credit event plus projection. Never update in-memory cache before commit.

Exact economic/ownership replay returns the current projection without reduction and retains the first credit's receipt metadata. A fresh snapshot ID/time is not an economic conflict. A changed guard payload before credit blocks; after credit it preserves original credited quantity and durably records conflict, invalidates readbacks and pauses entries. Never overwrite or automatically reverse old accounting. Different candidate positions cannot evade the account-wide guard; record the contradiction on the affected candidate's projection with the original credit's exact source reference and keep account execution blocked.

Before new credit, validate all stored relevant history variants against the receipt's exact member. Failed revision CAS, persistence or receipt validation cannot credit. Retrying after a CAS conflict requires fresh state reduction and valid receipt/lease; retry after process loss requires a new authenticated read. History already committed remains available without implying a prior credit.

Use a pure replay helper `CreditReplayState` to track credited guards, candidate resolutions and corroboration conflicts alongside the existing candidate identities. Preserve V1/V2 serialization and digest algorithms byte-for-byte. New events use `RUNTIME_POSITION_V3`, a **17-item tuple**: existing V2 fields with the tag changed, followed by credit count/digest and decision count/digest. For empty candidate identities, fill V2 count with 0 and its existing empty-key SHA-256 digest. Digests use sorted canonical complete durable records. Existing V1/V2 summaries stay unchanged until a new credit/decision event exists.

Replay keeps every quarantine record. Derive unresolved candidate IDs from quarantine minus exact credited resolutions; remove the aggregated corroboration-required marker for an order only when no unresolved candidate for that order remains. Never clear execution-identity or unrelated conflicts. Late new candidates restore the unresolved marker. Replayed credit preserves quantity once but does not recreate a receipt, clear reconciliation flags, certify complete exposure or make zero quantity flat. Provenance-less legacy fill restore remains refused.

## Worker integration and lifetime

Pin optional paired `receipt_session` and `history_store` dependencies in the runtime constructor; absent dependencies reject corroboration. Require exact concrete types/scope; no validator callback. Worker supplies its owned objects when constructing runtime. Runtime `corroborate_execution(*, receipt, binding, observation_key, candidate_id)` uses these pinned objects, not caller-supplied stores or sessions, and performs no GET.

`await ExecutionWorker.corroborate_native_fills(symbol, *, start_ms, end_ms)` is explicit only. Capture worker/runtime/session/binding/lease-generation/window, get receipt, repeat post-await admission fences, ingest its exact snapshot through the existing history store, then process relevant persisted candidates synchronously under runtime guards. No await/thread handoff between final checks and each atomic credit. Release the receipt in finally. Stop, lease replacement, session replacement or binding refresh during GET prevents ingestion and credit.

Return per-candidate data statuses `CREDITED`, `DUPLICATE`, `CONFLICT`, `UNRESOLVED` with fixed reasons; no ready/qualified/authorized property. Previously credited candidates must also be checked for newly observed contradictory history. Unmatched history rows never receive credit. Partial batch commits are reported by candidate; there is no batch-wide atomicity claim. No automatic collection interval or start flag is added.

## Validation and finite completion

Three sequential tasks: session receipt; atomic credit/replay; explicit worker/history/restore integration. Offline tests use temporary stores and synthetic fixtures. Private-core tests may inspect handle internals under the existing reflection-outside-trust-boundary rule, but there is no production test issuer, injectable authorizer or bypass. Injected transports must always fail the public receipt route. Mocked/private-core successes are not actual authenticated transport evidence.

Test new event backup/restore using the existing v3 snapshot contract and standard-library operation scripts, retaining original times, quantity, conflicts and BLOCKED/STOPPED. No restored token or re-credit arises. Keep existing native tests/count unchanged unless actual additions are explicitly reviewed; no new native behavior is proposed.

Finish with focused task reviews, integrated required checks and accurate status documentation. Actual positive receipt issuance/transport observations remain unverified until operator-confirmed identity and an actual read are available. Existing permissions may permit read observations while still blocking execution. No investment amount, model, AI budget, hosting choice or account identity is fabricated to finish this package.

