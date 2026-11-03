# Adopted codec slice; controlling addendum

Read ../specs/2026-11-03-risk-v2-intent-codec-adoption.md FIRST. Two NEW files only; all three complete independent goldens are first construction, and NEW V2 generation is0..2^63-1. Later E/F and Tasks11/12/13 remain separate. Retained candidate plan follows.

---

# Risk V2 Task10 Compact Durable Intent and Closed Codec Implementation Plan — CANDIDATE

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task under root's existing ownership/verification procedure. Steps use checkbox (`- [ ]`) syntax. This source-free candidate author has not implemented or executed them. Root owns Git/effects; no nested subagents are authorized by this candidate.

**Goal:** Preserve a compact, internally validated Risk V2 intent with exact closed versioned JSON and later restore it for observe/manage only, while preserving V1 bytes, archives, native cache and uncertainty semantics.

**Architecture:** One new pure module owns exactly the nine-field DTO plus fixed contextual reconstruction/projection recipes. It reuses accepted c/e validators and c's one canonical scalar encoder. A separately owner-agreed second slice connects version dispatch to existing runtime/native/offline boundaries; no receipt/currentness or financial writer is supplied by serialization.

**Tech Stack:** Existing Python/stdlb dataclasses, JSON, hashlib, Decimal/datetime and accepted pure Risk V2 modules; existing pytest/Ruff/strict mypy; no new dependency or install.

**Spec:** `F:/자동매매프로그램/final-completion-audit-20261103/RISK_V2_INTENT_CODEC_SPEC_CANDIDATE_20261103.md`, against original `R/docs/superpowers/specs/2026-11-03-risk-v2-design.md` §15/16 and original Task10 plan. R=`F:/자동매매프로그램/worktrees/risk-v2-core-20261103`; root supplied frozen commit `d304f6722b45619535d2efa892cd5bad81087ce9`. See spec source byte pins. This plan is PROPOSED, NOT ADOPTED, and is not completion evidence.

## Global constraints

- First implementation scope: create only `src/multi_exchange_trader/execution/risk_v2_codec.py` and `tests/execution/test_risk_v2_codec.py`; all accepted source/tests/support retain exact bytes. No runtime/restart/offline edits until root explicitly agrees owners/seams.
- DurableEntryIntentV2 has exactly9 required fields: schema_version,command_key,position_id,plan_id,strategy,envelope,assessment,protection_policy,source_record_sha256s. Version exact2. No default/receipt/current/source-qualified property or tenth raw field.
- Five-key framing/checksum, canonical-only wire, PLAN+SUBMIT inert history, sorted/unique source hashes1..1024 and protection technical limits are candidate decisions identified in the spec; root must adopt/revise them as technical decisions before implementation. No new end-user approval flow is requested.
-64KiB means at most65536 UTF-8 bytes, including framing. Decoded-key duplicates/unknown versions/types/fields and noncanonical leaf representations fail closed. Source hashes are references, not proof of stored source contents.
- No generic serializer framework, arbitrary-object/type-hint introspection, payload-driven import/eval, fallback to V1, schema downgrade, migration or hidden authority marker.
- Preserve V1 codec/raw JSON/Decimal representations/archive IDs/unknown state; preserve native cache serializer, original event stream, schema1 and byte-identical packaged/portable native format. Restore never resends, reprices, upsizes or recreates a current risk receipt.
- No operator money/fee/default strategy/TTL/paid value or actual order/source facts are invented. All tests use explicit synthetic fixtures and temporary stores only where their owner stage permits them.
- Fixed Python is `C:/Users/PC/.codex/tmp/trader-receipts-20261102-venv/Scripts/python.exe`; no uv/sync/install. Set PYTHONDONTWRITEBYTECODE=1, PYTHONPATH/MYPYPATH=R/src, and TRADER_CREDENTIAL_FILE,TRADER_ACCOUNT_PROFILE_FILE,BYBIT_ENV_FILE,AI_ENV_FILE,ANTHROPIC_ENV_FILE all literal `off` before any later test.
- Current document-only task performed no test, app import, DB/browser/network/provider/SDK/native/private/default-store/env inspection, order/AI/server/install action, source/Git change or subagent call. Later commands below are instructions, not receipts.

## Review focus

1. Unicode/escaped decoded duplicate keys must fail at every object depth; canonical wire plus UTF8 bounds must be checked before allocation/construction. Task B tests this.
2. Subclasses/forged missing nested fields/Decimal hooks must not run arbitrary code or bypass exact graph validators. Task C tests every allowed graph node.
3. A recomputed outer checksum must not hide envelope/assessment/protection or model binding disagreement; conversely a coherent opaque-source rewrite is still inert metadata, not an authentication claim. Task C tests both limits.
4. Canonical Decimal tuples and UTC time spellings must survive full wire and immutable identity; alternate spellings cannot be normalized silently. Task B tests this and Task E pins row identity.
5. Restoring mixed V1/V2 or missing native/source state must preserve UNKNOWN/reservations and never reach submit/reprice/current receipt creation. Task E/F own these later integration tests; pure codec passing leaves them pending.

## Ownership, dependencies and order

A (literal fixture/contracts) -> B (bounded wire) -> C (full semantic/forgery graph) -> D (focused codec freeze/review). This is the first2-file independent slice. No existing product or test source is owned by it.

E (runtime observe/manage version dispatch, root runtime owner) depends on accepted D plus agreed row/native mapping and Task11 local identity/reservation layout. F (native exact verification and portable preservation, restart/offline owners) depends on E and Task12 concrete V2 fixed-limit request. Task13 current producer/source/selection/lease remains a separate dependency for any future new financial admission. Root must not report full Task10 before agreed E/F restore coverage is complete, and must not report financial readiness from Task10.

## Task A — independent literals and exact nine-field DTO contract

**Files:** Create the two new files only. No c/e/support changes.

**Consumes:** c.FixedLimitEnvelopeV2; e.LinearEntryAssessmentV2; modes.StrategyReference; p.ProtectionPolicy; accepted validator graph. `e._assessment_payload_v2`, not c.

**Produces:** Frozen required-field `DurableEntryIntentV2` and importable public stubs `encode_entry_intent_v2(value: DurableEntryIntentV2) -> str`, `decode_entry_intent_v2(payload: str) -> DurableEntryIntentV2` for meaningful behavioral RED.

- [ ] Preserve byte copies/relocation map of spec-pinned accepted dependencies and original regression tests in root's authorized evidence location before edits. Record actual hashes, not just Git IDs. A new candidate may need root to update its pin baseline; do not silently update an old manifest.
- [ ] In the new test file create only synthetic explicit fixture constructors: scope/account/key/strategy, immutable envelope, compact preparation and PLAN missing models. Use accepted constructors for actual DTOs, but independently hand-author expected wire's complete literal field map. Copy these literal leaf anchors unchanged: protection object `{"generation":0,"revision":"POLICY_V1","stop_price":"90.00","take_profit_price":"120.000","trigger_source":"MARK_PRICE"}`, ratio `{"denominator":"3","numerator":"200"}`, UTC `2026-11-03T00:00:00.000000+00:00`.
- [ ] Complete the PLAN golden with explicit synthetic IDs/revisions/pins/times/quantities/costs and all current preparation fields. Compute expected envelope/sizing/valuation/tier/minimum/binding digests independently from literal trees with stdlib sorted JSON+hashlib; freeze their full hex strings and the complete final wire/checksum/byte count as literals in the test. Do not derive expected fields/digests from c/e validators, new codec or helpers imported from old tests. The golden's actual DTO may use existing helpers. No new support file is needed.
- [ ] Add `test_intent_has_exact_nine_required_frozen_fields`: assert dataclass field names/order/types, no defaults (MISSING), required construction, FrozenInstanceError on assignment, schema bool rejection, and no qualified/submission_ready/receipt field. Add `test_complete_plan_literal_wire_golden` asserting exact encoded string, decoded exact type, every Decimal.as_tuple and UTC timestamp, encode(decode(raw))==raw.
- [ ] Add minimal importable new DTO/function stubs. Run only the golden behavioral test; it must fail on encoded output/NotImplementedError/assertion, not ModuleNotFoundError/collection failure. Capture separate command/stdout/stderr/actual child exit as RED. Then implement exact DTO validation and encoding only as needed by Task B/C, not a blanket permissive stub to turn tests green.

**Acceptance:** Golden is genuinely independent and full, all9 fields are required. The source-free candidate supplied literal fragments only; a full valid golden is intentionally a required implementation artifact still outstanding. Do not present fragments or a round-trip through one implementation as independent full-wire proof.

## Task B — closed bounded wire and exact leaf decoding

**Files:** Same two new files.

**Consumes:** `c._fields_v2(value: object, expected: type[object])`, `c._json_size_v2(value: object) -> int`, `c._json_text_v2(value: object) -> str`, `c._time_text_v2(value: object) -> str`, `risk_v2_math.decimal_text_v2(value: Decimal) -> str` and exact spec recipes. Use private local closed recipes, never expose generic pack/unpack.

**Produces:** Both public codec functions with five exact outer keys, SHA256 unsigned framing and full canonical-string acceptance; resource-bounded handling before Decimal/JSON expansion.

- [ ] Write `test_decoded_duplicate_keys_rejected_at_every_depth`: outer schema/schema escape alias, fields.command_key alias, nested envelope scope duplicate, nested ratio duplicate. Assert INPUT_INVALID; use literal JSON text so Python dict construction cannot erase the duplicate.
- [ ] Write `test_unknown_missing_extra_and_future_wire_rejected`: wrong schema/type/version incl bool/1/3; unknown field at root and every nested contextual recipe; wrong union kind/mixed branch; raw InstrumentObservation/LinearRiskInputs/receipt type injection; malformed/truncated JSON; floats/constants; arrays where record required. Recompute checksum when testing semantic/field rejection so checksum alone cannot explain pass.
- [ ] Write `test_exact_decimal_and_time_leaf_wire`: Decimal1.00 vs1 vs1E+2, preserved signed zero where accepted, finite ratio200/3, canonical quantum1E-8; reject numeric Decimal, underscore/space/leading-plus/lowercase-e aliases and exponent spellings that re-encode differently. Reject ratio100.0/1E+2/negativezero/unreduced/zero denominator. Source>256digits or exponent>1000 fails; valid derived>256digits stays allowed if whole wire fits. Time canonical UTC microseconds accepted; Z, missing microseconds, offset alias, naive/date/subclass fail on wire. Encoder accepts only exact stdlib timezone and emits canonical UTC per accepted contracts.
- [ ] Write `test_resource_preflight_before_allocation`: raw char count>65536 rejected before UTF8 allocation, multibyte>65536 bytes rejected, unpaired surrogate fixed rejection, depth33 rejected before JSON recursion, quote/escape-aware scanner ignores braces inside strings, huge decimal exponent/coefficient/int token bounded before conversion, tuple list1025 rejected before tuple construction, derived formatting too large rejected before joining. Instrument local seams/forbidden conversion sentinels to prove ordering, not merely final ValueError. Add exact65536 accepted /65537 rejected wire fixtures by adjusting explicit permissible bounded fields; if valid graph cannot attain exactly65536, exercise internal preflight with literal scalar scaffolding plus nearest valid intent and document why.
- [ ] Implement explicit contextual field descriptors/constructors for the exact24-class allowlist in the spec. JSON lists become tuples only in prescribed slots, then accepted constructors/validators run. No nested record/type tag, unsupported registry extension, dynamic module import, object introspection or coercion is allowed. Field recipes must cover all current e assessment fields, including liquidation_buffer_result and exact outcomes.
- [ ] Implement string-aware inert scan, decoded pair hook, rejecting float/constant callbacks, bounded int lexical conversion; enforce size/depth/sequence/numeric work before derived expansion. Build trusted scalar trees only after input preflight; call c size checker before c encoder for unsigned and full framing. Verify unsigned digest and exact full re-emission. Outer checksum is corruption detection only.
- [ ] Run the B cases only, then repair only newly observed issues. Verify hostile Decimal context traps/flags/precision remain unchanged and no process integer-string setting is changed. Meaningful negative tests must fail in a deliberately weakened implementation, not only when all calls are unavailable.

**Acceptance:** All wire classifications deterministic; unknown V2 never falls back to codec.decode. No memory-heavy normalization precedes rejection. Full golden expected bytes remain independent from production helpers.

## Task C — full compact graph and cross-intent invariants

**Files:** Same two new files.

**Consumes:** `c._envelope_payload_v2(value: object) -> dict[str, object]`, `e._assessment_payload_v2(value: object) -> dict[str, object]`; accepted c/e digest and field validators. Source lookup/current issuer deliberately unavailable.

**Produces:** `DurableEntryIntentV2.__post_init__` and encoder/decoder complete exact revalidation with cross-root scope/strategy/envelope/protection/hash invariants.

- [ ] Write `test_forged_every_allowed_record_and_leaf_rejected`: parameterize24 permitted record classes; forge with object.__new__/object.__setattr__, missing required fields, subclass wrappers, wrong nested DTO, tuple/list substitution, bool-int, Decimal/time subclasses and overridden hooks. Exercise constructor/encode and wire decode where applicable. Verify hooks are never invoked and rejection stays fixed. Do not count unrelated collection failures as negative success.
- [ ] Write `test_source_hash_refs_exact_sorted_unique_bounded`: tuple only, length0/1025 rejected, repeated hash/unordered/uppercase/short/non-str rejected; length1 accepted; do not sort/dedupe, infer raw-source presence or claim1024 entries fit the64KiB total.
- [ ] Write `test_strategy_scope_policy_envelope_and_protection_binding`: account/environment/product/key/native pin/strategy ID+revision mismatch; outer envelope vs preparation.sizing.envelope quantity, Decimal representation, price, origin quote, expiry, policy revision/hash, order_type/TIF/role/reduce-only, pin/spec/source/catalog revision change. Negative cases use unchanged outer checksum and independently recomputed outer checksum in separate rows; the latter must still fail semantic links. Stop must equal sizing stop exactly; BUY stop<limit<target and SELL target<limit<stop, equality/wrong direction rejected. Generation nonnegative exact int; no arbitrary default trigger source or policy-revision-to-strategy-revision equality invented.
- [ ] Write `test_compact_assessment_preserves_both_stages_and_outcomes`: full PLAN with missing models keeps ordered blockers; complete PLAN keeps PLANNING_ONLY; complete SUBMIT keeps no blockers; add complete independent literal SUBMIT wire fixtures for POSITIVE_ROOT200/3 and NO_POSITIVE_ROOT LONG/ISOLATED/ONE_WAY. Reject supplied invalid evidence rather than dropping to None. Preserve common model-input/exact-calculation/margin digest links, ordered unique consumed tiers1..1024/covered subset/candidate membership, causal timestamps and exact reserve constraints via accepted validators. Do not implement new models/formulas/current gate.
- [ ] Write `test_model_hash_mutation_with_recomputed_checksum_rejected`: independently change only margin model-input/exact-calculation, LP margin digest, reference input/revision or no-root input hash; each fails. Add limitation test with a coherently changed opaque source/proof label and all required links: it can serialize as inert metadata but cannot yield any current receipt or native effect. This prevents fake authentication claims.
- [ ] Write `test_historical_validity_not_fresh_authority`: no system clock is read; a stored internally consistent assessed_at/expiry remains decodable after real wall-clock expiry. No `now`/receipt/current producer argument is added to decode. PLAN never upgraded, SUBMIT never grants submission by decode.
- [ ] Implement cross-intent checks in the exact validation order from spec. The nine-field type does not contain an approved plan object or source catalog; require those later in current owner, rather than inventing them here. Existing c/e validators remain byte-identical.
- [ ] Run C cases and full new codec module once stable. Freeze new complete literal goldens and expected independent checksums. Triage every failure/skip/incomplete explicitly.

## Task D — focused verification, source preservation and codec-slice freeze

**Files:** No additional product source. Root-approved external receipts only.

- [ ] With root-authorized execution context, run new module, then focused old codec regression: `python -m pytest tests/execution/test_risk_v2_codec.py tests/execution/test_codec.py -q`. Preserve exact command, actual child exit, stdout/stderr and source/test hashes. New codec must not register V2 into V1 registry or change old archive decode/encode behavior.
- [ ] Run accepted core covering checks once after stable code: test_risk_v2_math.py, test_risk_v2_contracts.py, test_linear_risk_v2_sizing.py, test_linear_risk_v2_valuation.py, test_linear_risk_v2_tiers.py, test_linear_risk_v2_preparation.py, test_linear_risk_v2_assessment.py. Include test_spot_risk.py only as root-agreed accepted-core preservation coverage, not new SPOT integration. No repeated native/PAPER/broad suite is required by a pure codec edit.
- [ ] Run touched Ruff for the new module/test and strict mypy on the new module with R/src MYPYPATH/PYTHONPATH, explicit package bases; if test-type checking is already root policy, include the new test without changing accepted support to silence it. No blanket ignore or install.
- [ ] Verify every old source/test pin from A remains identical, including codec/runtime/native format/portable siblings. Compare preserved V1 literal archive cases: original IDs and Decimal.as_tuple, original raw payload JSON hashes, existing semantic hashes. No rewriting fixtures, regenerated expected outputs or normalization to make tests pass.
- [ ] Produce final2-file source report and exact source/report/artifact/execution manifest freeze, then stop writes. Root dispatches fresh independent review with full spec/plan/goldens/RED/GREEN/check receipts, not just summary counts. Fixes after freeze require a new explicit root iteration and refreshed evidence. Root alone handles Git/CI/main and decides acceptance.

**Status to report:** `Task10 pure codec slice accepted/pending`, plus exact SHA/hashes/test results and `runtime/native/offline observe-manage integration pending`. Never `Task10 complete`, financial dispatch enabled, actual sources qualified or product complete at this gate.

## Task E — later owner-agreed runtime observe/manage version dispatch

**Files (not owned by first slice):** narrow runtime.py version/restore seams and new owner-agreed tests (proposed `tests/execution/test_runtime_v2_restore.py`). Task11 owns journal changes and local immutable identity; no competing runtime/journal edits.

**Consumes:** accepted codec; actual CommandRecord.payload_json/payload_hash/key/kind/scope/client_order_id; original lease/startup replay; agreed Task11 reservation/identity shape. No receipt restoration.

**Produces:** internal version-specific decode/ownership reconstruction for passive restore only. Exact helper name/signature chosen by runtime owner alongside its existing `_plan`, `_policy`, `_entry_mapping`, `_request_for_state`, `_recover_entry_mapping`; do not add a public generic dispatch framework.

- [ ] Before edits, owner writes fake/offline mixed V1/V2 restore RED cases in its new test: known V1 follows original code; exact V2 uses decode_entry_intent_v2; explicit future/unknown/ambiguous keys block before old `_plan` path. Decode errors never select V1 fallback. Compare raw payload_hash before interpretation, key/kind/scope/position and deterministic native client ID after decode. Wrong row identity with a valid wire blocks.
- [ ] Pin the current MARKET reconstruction touchpoints `_entry_mapping` and `_request_for_state`, plus existing `_plan`/`_policy` uses. Branch exactly once from explicit wire schema/version; V2 cannot be fed to r.EntryDecision or NativeOrderRequest MARKET reconstruction. Reuse old reducers/guard/lease and preserve legacy semantics.
- [ ] Build only passive ownership/projection mapping for V2 using envelope pin/native ID/side/Q and policy generation/revision/stop/target. Actual V2 request factory depends on Task12's agreed type; if absent, explicitly block native cache/request reconstruction. Do not coerce to an old request to complete restore tests.
- [ ] Assert decoder/version dispatch produces no submission, resend/reprice, current readback/risk receipt, queue admission, reservation release or source qualification. Keep UNKNOWN/ACK/PENDING liabilities; original startup owner may convert interrupted SENDING->UNKNOWN after validation. Missing source bytes do not mean order never sent. Current producer remains a future separate gate.
- [ ] Run focused new restore + existing test_runtime.py/test_codec.py (only owner-authorized temporary-store paths), touched checks and preservation once stable. Review/freeze separately. Root accepts this as runtime restore work, not actual native LIMIT IOC proof.

## Task F — later native/offline owner integration and whole Task10 acceptance

**Files (owner agreement required):** native_restart_codec.py narrow `_owners`/`_validate_order` version checks; package/portable native_restart_format.py only if a real inert format change is necessary; state_snapshot.py only for Task11's agreed schema additions. New tests must live under the owning restart/offline test plan; do not weaken original cases.

**Consumes:** concrete Task12 `LinearFixedLimitNativeOrderRequestV2`, agreed OwnedCacheExpectation union/shape, runtime immutable row mapping, Task11 schema. These are absent from the codec slice; do not invent substitute request classes.

- [ ] Write focused RED for V2 LIMIT/IOC/original init options/side/Q/price/reduce-only/product/pin/owned client ID exact match; mutated SDK order/current/init time-in-force or price blocks. Keep old MARKET/V1 and existing native msgpack-base64 encoding/state/event stream checks unchanged. Real pinned SDK execution belongs this later owner gate and is not authorized by the current document task.
- [ ] Preserve `native-restart.json` document schema1/payload schema1, sealed/active lifecycle, exact generation identity/content hash and logical3-ledger digest unless a separately justified owner-reviewed format revision is required. A new app intent schema2 does not bump native schemas automatically. No current risk receipt is ever serialized or recreated.
- [ ] Any actual native format change updates packaged/portable native_restart_format.py byte-for-byte. The portable3-file bundle remains exactly state_snapshot.py + history_snapshot_validation.py + native_restart_format.py. It must run snapshot/verify/restore under Python `-I -S`, arbitrary cwd, no repository/site packages/app imports/SDK, no fourth helper. Retain full source/WAL copy-only preservation, raw intent bytes and complete application digest. Root must agree any journal metadata/schema2 inspection independently; do not migrate in verification.
- [ ] Run only the relevant new focused native/restart/offline tests and the existing test_native_restart_codec.py under the owner-approved isolated environment. Retain exact legacy native36/restart6/restored12 acceptance sets for the eventual broader root/native gate; no shortening/skip-to-pass/full-suite rerun by this codec author. Portable parity and3-file isolated test are required if format/offline source changed.
- [ ] Independent review must verify full Task10: pure codec, agreed restore version dispatch, old archives/IDs/Decimal/raw source unchanged, observe/manage no-resend and no current receipt restoration, portable constraints, concrete missing-source/native mismatch blockers. Only then root can mark Task10 complete for its persistence scope. Task11/12/13 financial/current-source/native effect dependencies remain separately unverified until their own gates.

## Self-review and exact current deliverable

- Original Task10 step1 duplicate/missing/extra/future/forgery/V1 identity -> A/B/C/D; step2 closed/version/type/size -> B/C; step3 observe/manage dispatch -> E/F; step4 codec/native-restart regression -> D/F. No whole-task completion claim at D.
- §15 exact9 fields/functions and graph -> A/C; compact assessment/opaque receipt excluded -> spec+A/C/E; §16 exact intent-byte hash/local identity -> spec+E with Task11 ownership.
- Known gaps are explicit: proposed framing and PLAN/source-ref rules need root adoption; complete independently frozen full-wire fixtures remain implementation work; runtime/native/offline signatures beyond existing seams require owner agreement and accepted Task11/12 shapes; actual source completeness/model/plan/current issuer facts remain Task13.
- This author read relevant planning skills, accepted requirements and frozen source without Git, source execution or imports. Exactly the external SPEC_CANDIDATE and PLAN_CANDIDATE files were written. File hashes will be reported to root, then writes stop. No source/test/Git/SDD change is included.