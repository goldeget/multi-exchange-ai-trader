# Controlling Task12-A bridge-only adoption

Read controlling adoption first. It supersedes named historical candidate differences below. Design closure is not implementation/native/runtime/currentness authority.

# Task12-A controlling bridge-only adoption

2026-11-03. Root FULL READ design review15290B/a1fbee3d8ca6bd917e7ff4243c029cf00660a9679e43b1b0dbeb4a53368afdaa and adopts I1-I4/M1-M3 below. This controls differences from immutable root candidate9856B/5891fac058b46fabe33b87e5a4e22a620b6444385d559c3d8bf50ca281471a5e. Conditional design findings are resolved as technical implementation rules, not by executed code. Same two owned files only: bybit_native.py + NEW test_bybit_fixed_limit.py. No runtime/journal/recovery/restart/portable/workflow/effect/producer changes.

## I1 — original key, identity and cancellation replay

Keep a detached immutable full V2 identity snapshot under the ORIGINAL registration client ID. Cover all validated OwnedOrder fields, exact scope/idx/attachment and full envelope commitment; preserve Decimal representation through immutable tuples/text, not numeric equality alone. No nested mutable dataclass references inside the snapshot. Reject reusing a registered owner request object under a different client ID. A private association or scan bounded to registered bridge maps suffices, no public registry/authority.

For exact idempotent incoming clones, validate both stored owner and incoming object against the original snapshot; keep the original registered owner object rather than replacing it on every replay. This keeps owner-object tracking bounded to actual registered maps and makes an original object's client-ID mutation detectable. Do not overwrite conflicting identity. A distinct genuinely new command still needs the external durable caller's actual claim; these local labels do not grant it.

Resolving ORIGINAL map key checks the current stored object's client ID and complete immutable snapshot, not merely a lookup using its mutated new ID. Validate stored V2 identity BEFORE returning memoized cancellation. Do not require native order still active for an already invoked cancellation replay: it may legitimately have closed. First cancellation resolves/validates real cached order and rechecks identity after converter/cache callbacks before invoking. Legacy V1 behavior remains unchanged. Cover original-key access, attempted mutated-new-key submission, attachment/generation mutation before cancel replay, and unmodified cancel replay after native closure.

## I2 — last callback and reentrancy

After all factory/ID/Q/price/protection conversions, revalidate BOTH original registered request identity and current/initial native order immediately before SUBMITTING insertion and writer handoff. Closed params are built freshly from validated values; no caller dictionary/arbitrary override or stale request callback params. Local failure before invocation adds no new submission result and invokes no writer.

V2 consume validates registered identity before existing decoder and again after it returns; callback mutation cannot make evidence appear bound to a changed mapping. Do not require cache/active state to consume legitimate terminal/late-fill evidence, replace the evidence owner or claim channel/current-source proof.

Install same-ID build guard before converters/factory; remove only a guard this invocation actually acquired, in finally. Rejected inner recursive build cannot clear another active guard or create UNKNOWN. Cache SUBMITTING once all checks pass and BEFORE actual strategy invocation. Strategy reentrancy observes the cached result and invokes once. If outer invocation subsequently raises, latest cached state becomes UNKNOWN; later calls return UNKNOWN without resending. Earlier immutable SUBMITTING objects do not retrospectively change. No price/quantity adjustment, ID replacement, reservation release or crash replay authority.

## I3 — exact native checklist

At BUILD require exact bound LimitOrder and exactly one exact bound OrderInitialized seed; current initialized status checked explicitly. Current client/instrument IDs match request and seed; current strategy_id equals actual strategy.id and seed strategy_id; current trader ID is nonmissing and matches seed. Do not invent native StrategyId formatting from application StrategyReference or authenticate account solely from labels.

Only CURRENT order requires account_id=None/venue_order_id=None at BUILD; OrderInitialized need not expose those fields. CACHE retains existing actual account/strategy/active/not-pending-cancel guards, seed/current envelope checks and refusal of any exact bound OrderUpdated event. Full native inventory/typed account-netting/event-allowlist/terminal restore is deferred, not supplied by this cache verifier.

Current and initial kind/TIF use exact enum type AND value matching validated binding members LIMIT/IOC. Current flags is_post_only/is_reduce_only/is_quote_quantity and initial post_only/reduce_only/quote_quantity are exactFalse. Current display_qty and initial options['display_qty'] are None; current expire_time_ns and initial options['expire_time_ns'] exact int0, not bool. Missing required fields/options fail; display0 is not None. Initial price is options['price']. Apply actual no-emulation/contingency/order-list/links/parent/algorithm/spawn constraints with explicit required attributes, never missing-as-permissive defaults. Native converted Q/price/TP/SL must equal exact request numbers, without rounding/fallback.

## I4 — existing V2 text/generation and native resource limits

Do not rely solely on permissive OwnedOrder constructor. Position ID/policy revision use existing c._identifier_v2, exact strings/types; generation is exact nonnegative int with bit_length<=63 as accepted codec (not bool). ENTRY trigger_price and trigger_source are both None. Validate complete envelope through existing c.envelope_sha256_v2 and explicitly match native key/product/account/side/Q/policy values. Exact supported product LINEAR/DEMO, ENTRY/non-reduce-only, ec_32hex native client ID and required exact int idx0 remain.

Owned Q, envelope limit, attached stop/target retain existing native finite positive <=256 coefficient and exponent/adjusted magnitude<=1000 guards BEFORE format. Wider valid inert codec data stays valid history but may be unsupported natively; never narrow codec or globally relax bounds. Mandatory exact AttachedProtection uses existing trigger whitelist and strict BUY stop<limit<target / SELL target<limit<stop. Fresh quote/protection-child/current mode/source/model qualification remains outside A.

## Compatible binding/type/loader choices retained

Exact five request dataclass fields and readonly scope/order_type/limit_price properties; no reference_price or caller TIF/BBO/post-only/quote selector. NativeRequest union confined to bridge finite maps/dispatch. Legacy register_owned/submit remain exact legacy-only; new methods share same lock/maps/writer and refuse every cross-version same-ID collision.

Old NativeOrderFactory Protocol stays unchanged. Private V2 limit Protocol adds required time_in_force, same actual factory. NativeBindings appends one defaulted fixed_limit_v2 field. FixedLimitBindingsV2 exact five fields from candidate remain: limit_order_type, initialized_event_type, updated_event_type, order_type_limit, time_in_force_ioc. Validate exact record type, usable distinct classes and correct enum member names/types/values on V2 path; fake bindings are structural test dependencies, not native authority.

Existing load_native_bindings(include_fixed_limit_v2=False) defaults exactboolFalse and preserves old five constructors/three imports/optionalNone. Invalid option fails before imports/version checks. ExplicitTrue lazy loads existing SDK namespaces to fill pinned classes/enums. No new loader/client, hidden load on register/submit, SDK import at offline module import or GTC fallback. Parameters stay fresh exact idx0/Full TP-SL keys; protective children remain Market as the existing contract, not MARKET entry.

## M1-M3 and acceptance boundaries

Official tagged factory confirms explicit TIF/defaults; tagged LimitOrder confirms current/initial field spellings/options. Those are static primary-source facts only. Tag commit-to-locked-wheel bytes and actual objects are unverified here. No source/name/default guess substitutes for future pinned Linux execution. Original named8/extraXML/full actual36/6/12, real journal holds/resolver, runtime version mapping/recovery/portable and actual provider/DEMO/PC-off/current-source gates remain separate. A unactivated fake suite completes none of them.

Meaningful independent fake RED/GREEN covers I1-I4 callbacks/reentrancy/ID mutation/replay/fields/missingattrs/resource/precision/both sides/cross-version/default legacy compatibility, then one stable newfake+existing bybit_native regression and touched Ruff/strict mypy. Five secret selectors off/fixed Python/new short fixture paths; no actual SDK/native/network/provider/order/AI/server/install/default-store/Git/CI action by implementation agent. Preserve exact before copies/history/failures and per-child source before/after/argv/PID/UTC/exits/raw outputs. Root owns commit/saved diff/fresh code review/exact CI/main. Freeze report and STOPwrites. production_supported remainsFalse; actual durable claim/current authentic issuer authority is not created by A.


---

# Historical subordinate candidate

# Task12-A unactivated same-bridge fixed LIMIT/IOC candidate

2026-11-03. Technical candidate for independent entry/design review, not implementation or native qualification. Root FULL READ17828B/d72a3f2b0ed66d202cb3e4fc0e0732e85b4b0e36989eb9f853a46eaa07afe183 Task12 preflight and complete existing bybit_native.py. Official tagv1.231.0 field notes are in RISK_V2_TASK12_PINNED_PRIMARY_FIELD_NOTES_20261103.md; actual wheel/tag correspondence is not proven and no SDK was imported. Original full Task12 named8/restore/runtime gates remain future.

## Small independent scope

Write existing execution/bybit_native.py and NEW tests/execution/test_bybit_fixed_limit.py only, plus new task receipts. No runtime/journal/recovery/restart/portable/CI workflow/current producer/source/effect changes. Same bridge, lock, maps, strategy writer and existing cancel/event path; no REST/SDK client/node or second writer. production_supported remainsFalse. Registration is mapping only; durable claim/current authentic SUBMIT/source/protection/owner authority remain caller/Task13 responsibilities. This slice supplies no actual order execution permission.

## Exact request and type surface

Add frozen LinearFixedLimitNativeOrderRequestV2 with exactly spec15's five fields: product_scope:ProductScope, owned:OwnedOrder, envelope:FixedLimitEnvelopeV2, attached:AttachedProtection, position_idx:Literal[0]. position_idx is REQUIRED with no default; exact int0, never bool/1/2. Readonly scope property returns product_scope.account. Readonly order_type='LIMIT' and limit_price=envelope.limit_price may support common consumers without adding dataclass fields. No reference_price, arbitrary params, BBO, caller TIF/kind/quote quantity/post-only selector or fallback.

Exact nested types/values are revalidated at construction/registration/submission/cache use, including forged frozen objects. Reuse existing c.envelope_sha256_v2 validation; no new serializer. Require product LINEAR/DEMO and exact product_scope==envelope.scope.product_scope; OwnedOrder ENTRY/non-reduce-only, exact supported side/Q/policy revision, exact native ID==envelope.instrument_pin.native_instrument_id with canonical instrument binding, ec_[0-9a-f]{32}, bounded existing position/generation fields. Envelope cannot authenticate position_id/protection generation; runtime later reconstructs those from actual durable intent/row, never from labels alone.

AttachedProtection is mandatory/exact. Revalidate existing allowed trigger_source and bounded stop/target; enforce BUY stop<limit<target or SELL target<limit<stop. Do not infer fresh quote from origin_quote_id or sample/renew source timestamps. Current quote/stop/target safety, real child ownership/capability, actual mode/leverage/source/model qualification remain later gates. Keep existing Full TP/SL Market-child params; those child types are not a MARKET entry fallback. Native caps remain existing <=256 coefficient/±1000 magnitude limits: wider valid inert V2 values may fail at native boundary before formatting, without narrowing codec or rounding.

Add NativeRequest alias = NativeOrderRequest | LinearFixedLimitNativeOrderRequestV2 for the bridge's internal map/finite dispatch only. Runtime/recovery annotations remain unchanged in this slice. Legacy public register_owned/submit keep exact NativeOrderRequest-only semantics; V2 uses new spec register_fixed_limit_v2/submit_fixed_limit_v2. Cross-version same-client-ID collision always conflicts. Scope/version/value validation precedes idempotent return.

## Binding/factory compatibility ruling

Keep the existing NativeOrderFactory Protocol unchanged so old implementations lacking a TIF keyword remain structurally compatible. Add a narrow private V2 limit-factory Protocol with explicit required time_in_force keyword; cast only at the V2 callsite. This is the same actual factory, not another writer or runtime qualification by protocol shape.

Append ONE defaulted NativeBindings.fixed_limit_v2:FixedLimitBindingsV2|None=None. Frozen FixedLimitBindingsV2 fields EXACT: limit_order_type:type[object], initialized_event_type:type[object], updated_event_type:type[object], order_type_limit:object, time_in_force_ioc:object. Classes/enums are lazy loaded from the pinned SDK by the existing loader, injected explicit fake classes only in tests. V2 refuses missing/wrong binding record, never defaults to GTC or name-only order classification.

Extend ONLY existing load_native_bindings with keyword include_fixed_limit_v2:bool=False (exact bool). Default path preserves existing five constructors/three-module imports and returns optional fieldNone. ExplicitTrue also imports the same SDK order/event modules already used by existing native recovery/restart loaders and fills exact LimitOrder/OrderInitialized/OrderUpdated/OrderType.LIMIT/TimeInForce.IOC. No separate loader/client and no hidden load on submission. The switch selects structural binding data, not financial authority; no current runtime caller is changed here. Offline module import never imports SDK.

## Build, cache and immutable identity

V2 calls the same factory.limit with exact native ID/side/baseQ/clientID/reduce_only=False/price=envelope.limit_price/time_in_force=actualIOC. No price/Q quantization, favorable SELL-upper assumption, adverse adjustment, MARKET fallback or BBO/post-only override. Verify numeric conversion exactly, including attached prices, before writer invocation.

New finite V2 verifier compares BOTH current native order and original first OrderInitialized. Exact bound LimitOrder/OrderInitialized classes and actual enum types/values LIMIT/IOC; IDs/side/Q/price; current is_post_only/is_reduce_only/is_quote_quantity areFalse; initial post_only/reduce_only/quote_quantity areFalse. Initial price comes from options['price']; actual tagged LimitOrder also supplies display_qty and expire_time_ns. Require noniceberg/noexpiry (None/0), no emulation/contingency/order-list/links/parent/algorithm/spawn transformation using the actual existing native constraints. Missing required fields fail; no permissive defaults. Current and initial field names differ as documented by official tag.

At BUILD require legitimately INITIALIZED order with exactly its seed event and no account/venue assignment. At CACHE preserve existing active/account/strategy/not-pending-cancel checks; compare seed/current envelope and refuse any successful OrderUpdated even if final numbers match. Use exact bound updated-event class, not a string name. Complete recovery inventory/full allowed-event/terminal-history verification belongs later restart/recovery owner; do not call a cache-only check whole restore proof.

Retain an independent immutable private identity snapshot for registered V2 requests, covering the entire validated five-field request (all OwnedOrder fields, full envelope commitment, scope, attachment, idx). A frozen object's later wellformed forced mutation must conflict instead of changing the same referenced cached mapping. A finite immutable scalar tuple is sufficient; no new public digest/authority/store framework. Compare at register/submit and all V2 request-consuming event/cache/cancel boundaries. Recheck after factory/conversion callbacks before handoff. V1 stored representations/semantics remain unchanged.

Use shared _requests/_submissions/_cancellations/RLock. Before actual strategy.submit_order, cache SUBMITTING exactly as the existing writer does; invocation exception yields existing UNKNOWN. Reentrant strategy sink or repeat calls reuse the same result/ID and invoke once. Build failure invokes nothing. A small private build-in-progress guard may reject same-ID factory reentrancy before invocation; it must not fabricate venue state, reset UNKNOWN or send a second order. Exact replay never reprices or releases journal capacity. Fresh bridge mapping never grants crash replay authority.

Parameters are newly built from idx0 and exact existing attached TP/SL keys only. Do not accept a caller dictionary or reuse mutable submitted params. Existing fixed native ValueError meanings/shared c validation owners remain; no new parallel RiskV2 error enum, provider-text leak or catch-to-success. Keep legacy build/cancel/event semantics and same-ID conflict behavior.

## Meaningful fake verification and future Linux stage

Independent fake classes expose exact current/init fields/enums/seed events; no SDK import. RED→GREEN covers both sides, exact factoryargs/baseQ/price/IOC, wrong kinds/GTC/quote/post-only/init mismatch/missingfields, bad scope/native/idx/policy/protection, forged/subclass records, native resource/precision loss, valid post-registration field mutation, cross-version collision, build/strategy/reentrant UNKNOWN/idempotency and real cached-order cancellation/consume ownership. Add import-without-SDK and default legacy loader/factory compatibility checks. Fake result is not real wheel proof.

Once stable one newfake+existing test_bybit_native.py regression and touched Ruff/strict mypy. Fixed Python/five file selectors off/new short fixture-only paths; no actual SDK/native/provider/network/order/paidAI/server/install/Git/CI action by implementation agent. Source before/after/actual argv/PID/time/exits/raw outputs/failures/artifacts frozen; root owns commit/diff/fresh independent code review and exact whole CI.

Later B/C/D stages own explicit runtime durable-version mapping/OwnedCacheExpectation union/recovery+restart V2 validators/Task11 true holds/three-file portable constraints. The exact named8 isolated Linux tests and separate fourth XML are added only when all cases can use the real journal/resolver. Preserve existing native36/restart6/restored12 sets, no unavailable-SDK skips-to-pass. This A slice cannot claim any named8, actual wheel, DEMO/source/readback/protection/PC-off or full63 acceptance.
