# Adopted codec slice; controlling addendum

Read 2026-11-03-risk-v2-intent-codec-adoption.md FIRST. Its new63-bit generation and all-three-goldens-first requirements supersede only the named historical candidate statements below. Retained candidate body follows; actual T raw dependency pins are in the root SDD manifest.

---

# Risk V2 Task10 compact durable intent and closed codec — SPEC CANDIDATE

2026-11-03. PROPOSED, NOT ADOPTED. Source-free design/preflight only. This does not complete Task10 or qualify a source, assessment, native request, receipt, order, restart, or product. No financial values, strategy defaults, TTLs, fees, credentials, current-source facts, or effect authority are introduced.

## Basis and boundary

Frozen read root R = `F:/자동매매프로그램/worktrees/risk-v2-core-20261103`. Parent-supplied source commit = `d304f6722b45619535d2efa892cd5bad81087ce9`, described by root as the accepted pure core equivalent to main/CI134. This author did not run Git or independently establish those release claims. Read only this frozen tree, original Task10, spec §15/16, accepted contracts/evidence/math and necessary persistence/portable context. Two external candidate documents are the entire write scope; no app imports, tests, databases, network, SDK/native, provider, environment/private/default-store reads, installs, orders, AI, server actions, source changes, or subagents were performed.

Original requirements remain controlling: `docs/superpowers/plans/2026-11-03-risk-v2.md` Task10, `docs/superpowers/specs/2026-11-03-risk-v2-design.md` §15/16. Accepted modules are unchanged. Names below use `c = execution.risk_v2_contracts`, `e = execution.linear_risk_evidence_v2`, and `p = execution.protection`.

## Chosen minimal architecture

Create only `src/multi_exchange_trader/execution/risk_v2_codec.py` and `tests/execution/test_risk_v2_codec.py` in first implementation scope. The module owns the new frozen DTO, fixed projection/reconstruction recipes and two public codec functions. It consumes accepted classes/validators; it does not modify them or add generic serialization infrastructure. A later, separately owner-agreed integration scope connects runtime and native/offline seams. A passing pure codec is only the Task10 codec slice, never whole Task10 complete.

Considered alternatives: extending V1 `codec.py` would change its registry/archive behavior and retain its generic runtime type inspection; persisting raw `LinearRiskInputsV2` would duplicate catalog/tier/account/source material and exceed compact intent responsibility. Neither is selected. Explicit contextual recipes plus existing canonical scalar encoder minimize new surface while retaining exact validation.

## Public contract and rejection

Exactly the nine §15 fields, all required, no defaults and `@dataclass(frozen=True)`:

| Field | Exact type |
|---|---|
| schema_version | `Literal[2]`, exact int; bool rejected |
| command_key | str |
| position_id | str |
| plan_id | str |
| strategy | existing `execution.modes.StrategyReference` |
| envelope | existing `c.FixedLimitEnvelopeV2` |
| assessment | existing `e.LinearEntryAssessmentV2` |
| protection_policy | existing `p.ProtectionPolicy` |
| source_record_sha256s | `tuple[str, ...]` |

Public signatures remain `encode_entry_intent_v2(value: DurableEntryIntentV2) -> str` and `decode_entry_intent_v2(payload: str) -> DurableEntryIntentV2`. Constructor, encoder and decoder repeat complete exact validation; a constructed object is not trusted after `object.__new__`/`object.__setattr__` forgery. Export no authority receipt, public registry extension, generic `pack/unpack`, arbitrary-object encoder, or new financial qualification type.

Use the existing exact `c.RiskV2Rejected` class, not a parallel error hierarchy. Malformed wire/unsupported schema or type/missing or extra field/duplicate key/noncanonical representation/false digest/inconsistent supplied record -> `RISK_V2_INPUT_INVALID`; resource bounds -> `RISK_V2_RESOURCE_LIMIT`; cross-intent scope/strategy conflict -> `RISK_V2_SCOPE_MISMATCH`; policy revision/hash disagreement -> `RISK_V2_REVISION_MISMATCH`; protection stop/target direction disagreement -> `RISK_V2_INVALID_STOP`. Preserve meaningful existing `c/e` validator rejection codes. Catch expected syntax/encoding/Decimal/time conversion errors only and redact to the fixed vocabulary; do not swallow arbitrary programming failures or synthesize fallback data.

## Wire proposal requiring root adoption

These are technical candidate decisions; §15 specifies closed/versioned/size-bounded JSON but does not name outer keys or checksum framing:

* Outer object has exactly five keys: `schema`, `wire_version`, `record_type`, `fields`, `content_sha256`.
* Literal values are `schema="RISK_V2_DURABLE_INTENT"`, `wire_version=2` (exact int), `record_type="DurableEntryIntentV2"`. `fields` has exactly the nine DTO field names above, including `schema_version=2`.
* `content_sha256` is lowercase SHA256 of canonical ASCII/UTF-8 bytes of the other four outer keys. Include the schema/version/type in this domain. Do not hash a repr, Python object, caller-supplied digest, or V1 JSON.
* All nested record objects are untagged, closed objects whose exact type is fixed by their contextual slot. Only `liquidation.outcome` is a union, dispatched exclusively by existing exact `kind` (`POSITIVE_ROOT` or `NO_POSITIVE_ROOT`) and its closed field set. There is no nested caller-selected class name.
* Keys sorted lexically, no whitespace, `ensure_ascii=True`, arrays in their prescribed order. Reuse actual `c._json_size_v2(value: object) -> int` then `c._json_text_v2(value: object) -> str` on a trusted closed scalar tree; its sequences are exact tuples, not lists. Use hashlib SHA256 on the resulting ASCII bytes. Do not change either helper or existing semantic digest domains.
* Decoder accepts only this canonical complete wire spelling and requires `encode_entry_intent_v2(restored) == payload`. Thus every accepted raw wire re-emits byte-for-byte, without a tenth raw-payload field or mutable sidecar. Whitespace/key-order/escape aliases/alternate numeric/time spellings fail rather than normalize silently. This canonical-only acceptance is a proposal resolving exact raw replay; V1 acceptance remains unchanged.

The outer checksum detects accidental corruption and ordinary one-field mutations. Someone able to rewrite all fields and every dependent hash can create another internally consistent inert intent; no unsigned local checksum authenticates venue truth, source contents, actual approved plan, or issuer identity. Tests must retain this limitation, not promise rejection of every coherently rewritten fake.

## Closed graph and exact reconstruction

Allowed record set is exactly these 24 classes, all already existing except the new root DTO:

`DurableEntryIntentV2`; `StrategyReference`; `ProductScope`; `ProtectionScope`; `InstrumentKey`; `RiskScopeV2`; `InstrumentPinV2`; `FixedLimitEnvelopeV2`; `LinearEntryAssessmentV2`; `LinearEntryPreparationV2`; `LinearSizedEntryV2`; `LinearAdmissionValuationV2`; `LinearTierBoundV2`; `MinimumAmountAssessmentV2`; `LinearRiskBindingV2`; `RiskComputationRefV2`; `RiskObservationRefV2`; `LinearMarginAssessmentV2`; `LinearLiquidationAssessmentV2`; `LinearLeverageReadbackV2`; `ExactRiskRatioV2`; `LiquidationPositiveRootV2`; `LiquidationNoPositiveRootV2`; `ProtectionPolicy`.

Use literal field-name/leaf-kind/contextual-child recipes written for these classes, plus direct trusted constructors. Exact allowed field sets can be checked against `dataclasses.fields` on a fixed trusted class as a maintenance assertion, never inferred from a payload class. No arbitrary `get_type_hints`, module-name imports, eval, payload-driven `getattr`, enum/object repr, `__dict__`, `asdict`, iterables, duck typing, subclass hooks, or generic unknown-type fallback. No support for every c/e class just because it exists.

Projection reuse is exact: envelope -> `c._envelope_payload_v2(value: object) -> dict[str, object]`; assessment -> **`e._assessment_payload_v2(value: object) -> dict[str, object]`** (not in c); scope/pin and nested graph use their existing c/e payload validators. These return the accepted compact field shape. Each nested constructor invokes its own accepted `__post_init__`; revalidate the assembled full assessment again. New explicit helpers project/reconstruct the nine-field root and the existing five-field ProtectionPolicy; exact strategy/account/product/key checks use c's trusted-field/payload recipes. Decoder must reject misplaced otherwise-allowed records and wrong union branches.

`LinearEntryAssessmentV2` already contains preparation (sizing, valuation, tier bound, minimum, binding, capacity/display), optional margin/liquidation/readback, reserve, blockers/formula/buffer result. Its binding stores pins and hashes, not raw observations. Do not persist `InstrumentObservation`, `LinearRiskInputsV2`, `LinearRiskPolicyV2`, `RiskRequirementsV2`, `LinearTierSnapshotV2`, raw rows/full tier table, account components/marks, `RiskAssessmentReceiptV2`, model/source registries, private credentials or current owner generation objects. Hash references do not establish their presence in a bounded source store.

## Leaf recipes and preallocation order

All Decimal slots are strings from actual `risk_v2_math.decimal_text_v2(value: Decimal) -> str`; no float, JSON-number Decimal, normalize, quantize, context arithmetic, or V1 `_decimal_text` conversion. Preserve Decimal sign/digits/exponent exactly, including trailing zeros, scientific exponent and permitted signed zero. Decode only exact str; bound lexical coefficient/exponent/adjusted exponent before constructing Decimal or powers; parse finite Decimal then require `decimal_text_v2(result) == raw`. Apply each accepted field's source/derived limits and positivity after construction. Source Decimal <=256 digits, absolute exponent/adjusted exponent <=1000 where its c/e validator marks `source=True`; derived uses accepted 65536-digit/scale and integer-work bounds, not the source256 rule. Whole wire <=65536 bytes can be stricter. Preserve exact integer tuples for `ExactRiskRatioV2`: nonnegative numerator, positive denominator, sign0/exponent0, gcd1, zero0/1; positive root numerator>0. Equivalent `100.0`, `1E+2`, unreduced or negative-zero ratio leaves fail.

All datetime slots use exact `datetime` with exact stdlib `timezone` accepted by `c._utc_v2`, converted via `c._time_text_v2(value: object) -> str` to UTC `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`. Decoder requires that spelling, exact datetime/UTC semantics, and round-trip equality. This preserves the accepted instant, not the original non-UTC offset spelling. No naive/custom tzinfo/date/datetime subclass. No date slot exists in this allowed graph. JSON ints are exact ints only (schema versions, generation); bool is accepted only in its explicit slot (`reduce_only=False`). JSON floats/NaN/Infinity are rejected everywhere. No process-wide int/Decimal limits or global Decimal context are changed.

Encode order: (1) exact trusted record/type/field/tuple preflight, bounded depth <=32; (2) before any Decimal formatting, joining, large integer text, or transformed tree allocation, inspect existing Decimal tuples and known fixed child slots and accumulate a conservative lower bound for raw scalar bytes plus fixed field/JSON scaffolding; stop above65536 and validate individual numeric work bounds; (3) existing accepted validators produce the compact scalar tree; (4) `c._json_size_v2` checks the full unsigned and final framed tree before `_json_text_v2`; (5) hash/emit and final <=65536-byte check. Counting repeated fields includes each actual wire occurrence; a huge shared object is not amortized. Preflight accesses only fixed-class fields through `object.__getattribute__`, as c does, never hooks arbitrary objects. Do not first call arbitrary `str(value)` or produce a many-megabyte intermediate and only then reject.

Decode order: (1) type(payload) is str; reject `len(payload)>65536` before UTF-8 allocation; then valid strict UTF-8 and exact 1..65536 bytes; (2) inert string-aware lexical pass bounds nesting<=32 and number-token size, without parsing string contents as structure; (3) stdlib `json.loads` with duplicate-detecting `object_pairs_hook`, rejecting float/constant callbacks, and bounded integer-token conversion; duplicate comparison uses decoded key names (including `"schema"` versus `"\u0073chema"`), at every depth; (4) exact outer framing/version/type/keys, fixed contextual field/array bounds and closed leaf representations before record construction; (5) bounded canonical unsigned digest check; (6) direct constructors, accepted nested validators, full intent validation; (7) exact canonical full-string re-encode comparison. Array sizes are checked before tuple conversion; Decimal lexical bounds before Decimal conversion. A64KiB input alone is not a depth or numeric work defense. Do not temporarily change `sys.set_int_max_str_digits`; bounded manual integer-token conversion is acceptable for the existing large generation-int contract.

## Cross-intent invariants

Validate supplied records first, then compare trusted projections; never perform nested dataclass equality before exact field validation.

1. command_key/position_id/plan_id use `c._identifier_v2`; all required. `source_record_sha256s` is exact tuple length1..1024, lowercase 64hex `c._digest_v2`, strictly lexical ascending and unique. No sorting/deduplicating caller input. This1..1024 source-reference bound is an explicit Task10 candidate technical rule; §15 says sorted/unique without its own count. Reject a64KiB-overflow even if count<=1024. Do not infer that the tuple contains every required source merely from labels.
2. Root strategy's exact strategy_id+revision equal envelope.scope.strategy and assessment.preparation.binding.scope.strategy. The root nine fields do not contain another account, command-plan mapping, native client ID, approved plan digest or selection TTL. Scope lives in envelope and all nested assessment scopes, and is already checked by c/e. Persist no invented plan object or derived account authority.
3. Exact envelope projection equals `assessment.preparation.sizing.envelope` projection: schema/product/account/environment/strategy, instrument pin/native identity/catalog/parser/source/spec revisions/raw hash/clocks, policy revision/hash, side/Q/limit/order type/IOC/ENTRY/non-reduce-only/origin quote/expiry all match, including Decimal representation. Binding envelope/policy/sizing/minimum/valuation/tier/margin/LP hashes are recomputed by accepted helpers and compared by accepted validators. Binding pin equals the envelope pin. Neither repr equality nor numeric equality permits a representation change across repeated immutable envelope leaves.
4. ProtectionPolicy exact five fields: revision and trigger_source use c identifier validation, generation exact nonnegative int via `c._count_v2`, stop/target exact finite positive source Decimal limits. Its revision equals envelope.policy_revision. Its stop exact decimal spelling equals preparation.sizing.stop_price. BUY requires stop<limit<take_profit; SELL requires take_profit<limit<stop, using exact fractions after validation. No trigger-source mapping or actual child protection qualification is inferred; no strategy revision is compared with a policy revision merely because both are strings.
5. Reuse all accepted e validation: required reservation arithmetic/capacity, stage/blocker/order, internal source/model/binding digest links and known clocks, positive-root/no-root branch domain and no-root side/mode/input hash, ordered1..1024 unique consumed/covered tier IDs in supplied range order (not lexical reorder), covered subset of consumed, positive candidate member, actual margin/LP common model-input and exact-calculation hashes. Existing PLAN can retain missing models, but supplied malformed records never disappear.
6. Candidate decision: retain both accepted PLAN and SUBMIT stages as inert durable evidence; never turn PLAN into SUBMIT or require now/currentness in decode. Decode uses stored assessed_at and original expiries for internal consistency; a valid historical expired-now intent can be observed. Neither a historical SUBMIT assessment nor zero blockers is current ENTRY authority. The decoder cannot recompute full-policy buffer/requirements/current account/source truth from compact data; actual producer later resolves them.

## Immutable local identity and restore boundary

Task11 owns `entry_command_identity_sha256_v2(account_scope: ProtectionScope, command_key: str, intent: DurableEntryIntentV2, reservation: ReservationV2) -> str`, not this codec slice. §16's exact domain is `LocalEntryCommandIdentityV2`; fields are account_id/environment/command_key/kind=ENTRY/intent_sha256/reservation. `intent_sha256` hashes the exact `encode_entry_intent_v2(intent).encode("utf-8")`, including the framing content checksum; the reservation includes version/instrument/envelope hash/Q/risk/margin. Compare scope/key with the actual row and the existing deterministic client_order_id separately. Decoder must not claim/bind owner epoch, increment capacity revision, release reservation, create receipt or make a pending order dispatchable.

Restoration is observe/manage only. Preserve unresolved PENDING/SENDING/UNKNOWN/ACK and original IDs/Decimal representations. Existing startup SENDING->UNKNOWN behavior is an owner operation after validation; decoding itself mutates nothing. Never reconstruct V2 as a V1 MARKET request, resend/reprice/upsize, fabricate ACK/fill/terminal proof, renew plan selection or risk freshness, or reconstitute current `RiskAssessmentReceiptV2`. Missing source references remain a future current-producer blocker.

## Integration ownership map and minimal seams

| Owner / files | Smallest later connection / dependency |
|---|---|
| Task10 codec slice, two new files | DTO+pure codec+closed graph validation; depends only accepted c/e/modes/product/key/protection/math. No existing source edits. |
| Runtime owner, runtime.py | Before existing `_plan`/`_policy`/`_entry_mapping`/`_request_for_state` legacy decode paths, add exact entry-payload version classifier; recognize explicit V2 framing, leave known V1 branch byte/behavior unchanged, reject unknown/future/ambiguous forms. Compare row hash/key/kind/scope/position/native owner, decode V2 once; no exception-triggered fallback to V1. `_recover_entry_mapping` and initial replay remain under original guard/lease. Actual queue and fixed native request require Task11/12/13 and original approved selection, never this codec alone. |
| Native restart owner, native_restart_codec.py | `_validate_order` currently uses immutable request order_type/limit_price; owner-agreed V2 request verifier later adds exact LIMIT+IOC/original initialization options/Q/price/product/owned ID checks. `_owners` must consume the agreed V1|V2 owned request shape; no invented generic request type now. Preserve native msgpack-base64 event encoding, original event/state replay, detached staging and supported pinned SDK semantics. |
| Native/offline format owner, execution/native_restart_format.py + scripts/operations/native_restart_format.py | Existing native payload/document schemas stay1 and actual native cache serialization stays msgpack-base64. No format bump merely for an application intent wire; logical application digest already binds every stored row. If journal metadata/schema later changes (Task11), route that separately through owner-agreed offline schema validation. Any real format change keeps these two files byte-identical. No app import in the portable sibling. |
| Offline owner, scripts/operations/state_snapshot.py | Existing check_sqlite is a closed schema validator, so Task11 new tables/version need explicit support; cannot use journal migration/read-write opening as validation. Native application digest and stored raw intent bytes preserved. Portable bundle remains exactly state_snapshot.py, history_snapshot_validation.py, native_restart_format.py, running `-I -S` outside repo/site packages. No fourth helper or dependency on risk_v2_codec/app imports. |
| Task11/12/13 | Reservation/local identity/CAS; exact same native LIMIT IOC path; current source/model/receipt/selection/owner admission respectively. No parallel owner edits without root agreement. |

Task10 full completion requires the owner-agreed observe/manage restore dispatch plus legacy/archive preservation tests. Native LIMIT IOC request verification cannot be called implemented while Task12's concrete request shape remains absent. Root can accept the independent codec slice first and keep integration explicitly pending.

## Independent literal wire anchors and implementation test gates

These fixed ASCII literals must be copied as expectations, never generated by the codec under test. They are wire fragments, not a claim that a complete valid intent fixture has already been executed:

`{"generation":0,"revision":"POLICY_V1","stop_price":"90.00","take_profit_price":"120.000","trigger_source":"MARK_PRICE"}`

`{"denominator":"3","numerator":"200"}`

`"2026-11-03T00:00:00.000000+00:00"`

`["0000000000000000000000000000000000000000000000000000000000000000","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]`

Decimal anchors: `(0,(1,0,0),-2)` -> `"1.00"`; `(0,(1,),2)` -> `"1E+2"`; `(1,(0,),-2)` -> `"-0.00"`; `(0,(1,),-8)` -> `"1E-8"`. First and second are distinct immutable representations, not interchangeable despite numeric equality where applicable. Unicode account `한글` is escaped as `\ud55c\uae00` by the canonical encoder. Time offset aliases are rejected on wire.

The implementation's first fixture step must hand-author a COMPLETE valid PLAN wire with literal9-field body and independently calculated linked digests/checksum, and freeze its full exact bytes/hash inside the new test file. Also add a complete SUBMIT fixture for each liquidation branch. Use only synthetic fixture values, never operator values. Build the expected literal tree and digest from direct stdlib sorted JSON+hashlib, with expected digests frozen literals; do not call any c/e payload/hash or new codec helper to derive the expected output. Production helpers may build the actual DTO separately. This full-intent golden is a required implementation acceptance gate still pending, not evidence produced by this source-free task.

Negative gates include decoded duplicate keys at outer/nested depth; missing/extra/future version/type/branch; float/bool-int/naive-time/noncanonical Decimal; unknown raw/source payload; subclasses, missing dataclass fields, forged every graph node; product/TIF/Q/price/expiry/policy/model/binding digest/stop/target/strategy changes with stale checksum AND with recomputed checksum (so semantic validators must reject); oversized UTF8, escape-expanded size, depth, huge exponent, 1025/source duplicates/order and tier range-order cases; exact 65536/65537 byte boundary; hostile Decimal context/global int-string limit unchanged; V1 original source and archive/raw/Decimal semantics unchanged. A fully coherently rewritten opaque source/model digest must remain merely inert metadata, never qualified.

## Observed source byte pins (SHA256)

Paths below are relative to R, measured by file hashing only. Root docs are mutable historical context, not protected product dependencies; implementation must preserve actual accepted source pins before edits and recheck owner-adopted baseline.

| Path | SHA256 |
|---|---|
| docs/superpowers/plans/2026-11-03-risk-v2.md | 7d2b914f507956206cb7ad3e04f9cf3ec37ecc2cf223408502646cf3e8e6a959 |
| docs/superpowers/specs/2026-11-03-risk-v2-design.md | 682b4d1d366d386db2385525c1bbb631260c8287e1fc2cd556573580bac55500 |
| src/multi_exchange_trader/execution/risk_v2_contracts.py | 7128ab957cc2536f87eb85e4c4bff8c32913b97a745fe6fa01b349a15ba74faa |
| src/multi_exchange_trader/execution/linear_risk_evidence_v2.py | 6c94a6ffed38694f7311557ce79e488bd82785f72fa6e2072e1ef2f32317e8ca |
| src/multi_exchange_trader/execution/risk_v2_math.py | 73725196ed3e28667125d1ee48f3afed35f465cb7e72f3d7603c428411810d11 |
| src/multi_exchange_trader/execution/codec.py | bef1f8a2d387a19cd5e278607b9c25fa9ea67f540440718e27ca7d478e9e615a |
| src/multi_exchange_trader/execution/runtime.py | a4b90c9e12c037ddae0c1d7f9c2a41c81b772c5d1c082b7ac132cb4c57b8e801 |
| src/multi_exchange_trader/execution/native_restart_codec.py | e25cb8a2a3383f4fba27a6acb4a3b195594008cb810bb3f5ccb43def9040f84f |
| src/multi_exchange_trader/execution/native_restart_format.py | d7095957ef3848850043d60b66fd3fc6e9e529aaab0c7a89699140e743f9146f |
| scripts/operations/native_restart_format.py | d7095957ef3848850043d60b66fd3fc6e9e529aaab0c7a89699140e743f9146f |
| src/multi_exchange_trader/execution/protection.py | 026c16d8dfd134824a224caf5a8efa76354a9998b62d985e0c4d132a1ecd9c7c |
| src/multi_exchange_trader/execution/journal.py | fed89d07d1698bc87effcb09d4519f68be2246a3b1fec4bf53fc4458843e1949 |
| src/multi_exchange_trader/execution/product_scope.py | b47397671c0a155774c593499fe43847803b1c6b7a8a11c323c98cc78d77256a |
| src/multi_exchange_trader/execution/modes.py | 1055d80b56394c32863a203ff08994a0932b6e10095dc2a2bf92ae04c7fe712e |
| src/multi_exchange_trader/trading_product.py | faeea028c5a1f0449ad5556082813fc0edcc7ba418d4f0428eafb57e5285ad1c |
| scripts/operations/state_snapshot.py | 642f3c474eabda73bf6616c802558dfbfb9e7dd7ec473f64caa818dc2d64b85a |
| scripts/operations/history_snapshot_validation.py | ce3c3ec224ee2ae64ef3b9d7e79d4135cf46ef14ecd3dbdf4961d1ec3544cc31 |
| tests/operations/test_native_snapshot.py | c04a5db3a52415a131e94be38df0a4cf9b3c6861ab8f648f889d3cb14ea67efd |
| tests/operations/test_policy_snapshot_v2.py | 4365a0a5f7208bd0c32a45c6d11ae9fbebc7a8c021a1ed23be1b859367a6f27a |

## Decisions handed to root

Technical proposals to adopt or revise before implementation: five-key checksum framing; canonical-only input; both PLAN/SUBMIT inert history; explicit1..1024 source-hash bound; protection identifier/source-Decimal technical limits. No end-user approval question is created by these candidates; original Task10 supplies requirements and root owns technical adoption. Actual plan registry consistency, source-store completeness and current risk authority cannot be proven with the nine stored fields. Runtime/native/offline ownership and later integration remain dependencies. No full-intent golden, test result, CI, native restoration, actual source qualification, order or product completion is claimed by this document.