# History-only owned execution recovery

## CURRENT IMPLEMENTATION STATUS (2026-11-02)

Local code and synthetic/offline proof implement separate historical credit/replay and the explicit worker recovery route. Task1 and Task2 passed their task-scoped independent reviews; Task3 independent review, whole-branch review and exact final-source CI remain pending. Main merge/readback and main push CI are separate later gates. No actual authenticated provider issuance, DEMO execution, complete ledger, TP/SL qualification or PC-off hosting has been verified; runtime remains BLOCKED and policy STOPPED. Backup manifest v3 and runtime summary V4 are distinct formats.


Accepted base: `abc47d7304e8ff4e238b25e0edfef009a78bfbec`, tree `127a2c129c43cbd0ad8014be0fe333eeeda249d2`. User authorizes this feature's implementation, independent review, verification, and main merge. This is the next bounded development unit, not a product-completion or operating approval.

## Intent and scope

Recover an owned Bybit DEMO USDT LINEAR Trade execution when its native callback never arrived. The existing native-only receipt path stays available. A separately named history record preserves actual history economics/time and immutable owned intent; it never fabricates a NativeExecutionCandidate, NativeFillEvidence, native source kind, receive/init time, or native reconciliation claim.

Python 3.12 and nautilus-trader 1.231.0 remain pinned. No actual orders (including DEMO), actual provider/native effects, private operator files, paid AI, extra servers, dependency changes or SDK fork. Only synthetic fixtures and temporary stores. No ACK, terminal confirmation, reservation release, qualification unblocking, protection repair, native-cache restore, strategy cycle, UI or hosting changes. No automatic calls, timers, endpoints, CLI flags, inferred query start, or new history writer.

## Approaches and engineering decision

1. Infer ownership from an execution's client ID alone: small change but insufficiently checks immutable order identity and intent. Rejected.
2. Manufacture a native candidate from history: reuses old accounting but misrepresents source evidence. Rejected.
3. Separate truthful historical credit with fresh same-receipt order proof and the shared account execution guard: selected by the controller as the conservative implementation of the user's requested feature.

These are engineering choices under the user's instruction to implement and merge reviewed changes; investment, risk budgets and live authority remain undecided and unused. Missing positive order proof deliberately leaves a row UNRESOLVED, including when only an older stored order or native ACCEPTED mapping exists. Existing related stored evidence still constrains contradictions. New credits for locally terminal-confirmed commands remain excluded; accounting correction after local terminal confirmation is not enabled by this unit.

## Authority and first credit

Runtime pins the exact BybitDemoAccountSession and HistoryStore. A live call requires that session's unexpired AuthenticatedHistoryReceipt, exact binding/scope, and whole-snapshot equality to the committed observation key. Preserve pending-plus-issued bound 4 and TTL min(session max_age,30s), without public construction, transport injection or receipt codec support. Revalidate receipt and lease immediately before the accounting transaction.

For a new execution guard:

- A Trade row with nonempty exact client and venue IDs must belong to the exact fresh receipt and requested symbol/window.
- Exactly one persisted owned ENTRY or explicit EXIT command and registered OwnedOrder must match scope, client, position, instrument, side, role, quantity and policy provenance through immutable command data. Record command_key and payload_hash. States PENDING, REJECTED and local terminal_confirmed are ineligible. Eligible SENDING/UNKNOWN/ACKNOWLEDGED do not become ACKed or terminal by recovery.
- A matching ORDER member in the SAME fresh receipt is mandatory. Match client/venue ID, symbol, side, position_idx=0, reduce_only and full immutable order quantity against owned intent. Venue status does not infer local terminal status. Do not compare market execution price to the intent's reference price. Preserve partial fills, exact Decimal values and negative fees/rebates.
- All relevant stored order variants (client OR observed/known venue ID), execution/snapshot conflicts, durable native venue mappings and existing execution decisions constrain acceptance across windows. Missing/contradictory data cannot be hidden by current-window filtering. Historical contradiction sources keep original provenance; never relabel old transport as current.
- Any persisted native candidate at the same execution guard makes first history-only credit UNRESOLVED with NATIVE_CORROBORATION_REQUIRED; callers use the existing native corroboration path. A mismatch cannot escape its candidate by using recovery.
- Reuse protection.apply_fill for cumulative intent and owned exit exposure limits. Refusals persist a sourced decision without a credit guard. Unowned, missing-ID, unsupported rows without an owned target remain UNRESOLVED, preserved in history only.

Use existing execution_guard(scope,instrument_id,execution_id), shared by both sources and all positions. Atomic RuntimeStateStore.apply_position_event commits the single credit inbox and projection at the captured revision. Update projection caches only after commit; rollback/CAS failure leaves no partial accounting. History ingest precedes accounting in a separate transaction. Each batch item commits independently.

## Prior credits and late evidence

The first native OR historical credit remains authoritative immutable accounting data. Same execution requery returns DUPLICATE without replacing source/timestamps/fees or changing projection revision. Compare all normalized history fields for history-to-history equality (including value, fee_rate and closed_quantity); compare exact shared native identity/economics/event time for cross-source equality. Native nanoseconds must exactly equal history milliseconds * 1_000_000.

Recheck prior credits even if parser-valid observed client/venue IDs are now empty, execution_type changed, or the local command became ineligible. Resolve an owned target through the prior guard when observed IDs no longer map. Persist observed contradictions faithfully and retain original quantity. Never overwrite/reverse the first credit. Global execution collisions across positions cannot get a second credit. Stored decisions remain blocking; do not silently clear contradictory evidence on a later match.

Recovery consumes ONLY normalized rows accepted by the unchanged history parser and validate_history_record. Late missing-ID/non-Trade/currency cases are limited to parser-valid observations (for example blank client, Funding with blank venue, supported AdlTrade/BustTrade/Funding). Invalid Delivery/unknown execution type/non-USDT fee currency fails existing read/validation with fixed errors, preserves prior bytes/accounting, and cannot yield a receipt, invented normalized decision or credit. No raw-observation or parser expansion is included.

History-first then native: retain genuine late native quarantine, no accounting on arrival. Existing corroborate_execution compares a fresh exact receipt against that candidate AND the historical guard; exact match appends NativeHistoryResolution with no second credit. It resolves only that candidate's corroboration marker when all candidates for the order are resolved; unrelated conflicts remain. Mismatch appends sourced conflict, preserving the historical credit. Source-distinct variants remain evidence; never resolve an inconsistent variant by matching a different one.

Native-first then recovery: recognize the prior native guard and use its real existing candidate through existing corroboration. Exact match is DUPLICATE and contradiction is persisted; translate the returned result to the history result shape. Never create a historical credit for that guard. If prior credits/owned command linkage are malformed, fail closed.

## Legacy native-only replay compatibility

Pre-existing native-only V3 ExecutionCorroborationConflict -> CorroboratedExecutionCredit references retain their original data-only semantics: exact prior type, guard/account/instrument and prior inbox position against that prior candidate's own position are checked without retroactively imposing new journal linkage. This preserves old record fields, hashes and meaning, grants no authority and stays BLOCKED; placeholder test fixtures are not evidence of actual operator history. Every NEW historical credit/decision/resolution replay and EVERY live prior-credit use verifies immutable journal/request/key/hash linkage as applicable.

## Closed durable records and replay

New module `execution/history_recovery.py`; old serialized dataclass fields remain unchanged. Register the existing normalized ExecutionRecord and OrderHistoryRecord as data-only codec types; wrappers explicitly call validate_history_record with their source symbol/window. No record/view/reference grants live authority.

- HistoryExecutionEvidence(source: ExecutionObservationReference, execution: ExecutionRecord, execution_variant_hash: str).
- HistoryOrderEvidence(source: ExecutionObservationReference, order: OrderHistoryRecord, order_variant_hash: str).
- HistoryExecutionCredit(execution: HistoryExecutionEvidence, order: HistoryOrderEvidence, owned_order: OwnedOrder, command_key: str, command_payload_hash: str). Positive wrappers share the same source. Construction/replay enforce the structural ownership conditions above.
- HistoryRecoveryConflict(execution: HistoryExecutionEvidence, owned_order: OwnedOrder, command_key: str, command_payload_hash: str, mismatch_codes: tuple[str,...], history_conflict_keys: tuple[str,...], order_sources: tuple[HistoricalOrderEvidence,...], prior_credit_event_id: str|None, candidate_id: str|None). Observed row stays separate from the target's owned identifiers. Fixed closed codes; bounded unique conflict keys (256) and original order sources (128), consistent with existing records. Content-address the full record as history-decision:+SHA256(codec.encode(record)).
- NativeHistoryResolution(candidate_id: str, prior_credit_event_id: str, credit_digest: str, execution: HistoryExecutionEvidence). Prior guard must reference the exact HistoryExecutionCredit whose complete encoded digest is credit_digest; candidate must already exist and match the fresh observed shared values and original historical credit. Content-address the full record as history-resolution:+SHA256(codec.encode(record)).
- HistoryRecoveryResult(execution_id: str, status: Literal[CREDITED,DUPLICATE,CONFLICT,UNRESOLVED], reason_codes: tuple[str,...]); returned data only, not an authority record.

Use existing fixed comparison/credit reason codes plus HISTORY_CURRENT_ORDER_REQUIRED, NATIVE_CORROBORATION_REQUIRED and HISTORY_RECOVERY_CONFLICT. Additional fixed codes are an explicit ledger ruling with a named regression, never provider strings. Validate digests as lowercase 64 hex. Each wrapper variant hash must equal the existing history store's normalized complete-record variant algorithm, reusing it rather than duplicating a new algorithm. Source and row validation remain bounded and exact. Execution IDs use existing nonempty <=256-character bounds.

HistoryRecoveryReplayState contains separate immutable tuples credits, decisions, resolutions. Native CreditReplayState record membership/digests stay unchanged. Pure reduction receives both replay states and existing real candidates; applying a resolution may extend native resolved_candidate_ids only as derived state. Replay checks exact owned order plus journal command key/payload hash and immutable command linkage, but not current command eligibility or fresh authority. Factor a state-accepting immutable-request helper to avoid recursively loading projections from replay. Reject malformed inbox IDs, wrong prior credit type/digest, missing command/candidate or summary mismatch before recovery writes. Generic _persist rejects new credit/decision/resolution writes; only receipt-gated paths commit them.

V1/V2/V3 algorithms, tuple shapes, bytes and hashes remain unchanged for streams with no new records. V4 is exactly 23 items: existing 17-item V3 content with tag RUNTIME_POSITION_V4, then historical credit count/full-record digest, history decision count/full-record digest, resolution count/full-record digest. Existing native sections retain their old membership and algorithms. Emit V4 iff at least one new tuple is nonempty. Historical-only streams use zero/empty native sections with existing algorithms. Pin a literal V3 summary hash from the accepted base before implementation and verify it unchanged.

## Explicit worker operation

Add recover_history_fills(symbol,*,start_ms,end_ms)->tuple[HistoryRecoveryResult,...]. Mirror established admission/worker/runtime/session/binding/store/scope/lease-acquisition fencing before GET, after await before ANY history SQL, and before each runtime call; use captured issuer in finally discard. Ingest the exact receipt snapshot first. Process every current receipt execution sorted by (time_ms, execution_id, venue_order_id), so economic chronological order is deterministic even when provider order differs. Do not filter non-Trade/missing-ID rows before prior-credit contradiction inspection. Empty/completed batches do not imply complete ledger or flatness. Stop/failure may follow earlier committed rows; do not report batch atomicity or auto-retry failures.

## Acceptance and limits

Tests must cover both arrival orders, changed/missing IDs and non-Trade after credit, terminal requery, normalized historical-only field changes, cross-position collisions, native variants, partial fills, exit limits, prior decisions, exact decimals/rebates, missing fresh order even with old proof, receipt misuse/expiry/injection, whole observation mismatch, history-first interruption, SQL rollback/CAS, and all worker lifecycle fences. Journal commands/reservations, runtime BLOCKED, policy STOPPED, qualification and external effects remain unchanged.

Use the real standalone snapshot/verify/restore subprocess on temporary v3 history+runtime file sets (backup manifest v3 is distinct from runtime summary V4), then reopen next day with new session/worker/bridge, no restored authority. Prove original historical credit/source/economics/quantity/conflicts/resolution digests, native late arrival dedup, fresh next-day receipt, and original bytes preserved. Leave strict Linux native gate exactly 36 actual unskipped cases. Exact final source CI and independent task/whole-branch review precede authorized main merge; verify merge SHA/tree and separate main push CI afterward.

Actual provider authentication and DEMO execution remain unverified. No complete ledger, flat/protection/terminal/reservation authority or PC-off hosting claim follows from this feature. Bybit's current primary docs were checked for multiple fills, millisecond time, identity fields, order-history delays and bounded queries: https://bybit-exchange.github.io/docs/v5/order/execution and https://bybit-exchange.github.io/docs/v5/order/order-list (2026-11-02). No new endpoint or parser is required.
