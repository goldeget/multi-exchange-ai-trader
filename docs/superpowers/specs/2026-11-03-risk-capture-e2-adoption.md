# E2 capture orchestration controlling adoption

E2A corrected CODE accepted at6096; source-only E2 now authorized. CI/main/external gates remain. Root adoption controls the complete owner preflight below.

# E05 M2-E2 controlling root adoption

2026-11-03. Root FULL READ independent design review13481B/SHA4fc6af7cd93b4b87881a4639ff267db821fa9d93d6a094e418abf215b258340f, owner preflight13561B/686483d4cd0701ad4e900a75cb26276488d5663eff3df522a0b5beaa460a5d7b, candidate6365B/02af52377545ae7b45fe4a3c2b728af4833fddb3c448c884c7e6f22e34e7be9b. Adopt C1-C3 and I1-I3 exactly as below. This controls differences from immutable candidate/preflight. C findings are CLOSED IN DESIGN, not code. M1/M2 behavioral evidence remains pending. No extra user financial input is needed for fake-only implementation.

## E2A first: exact error origin and cleanup

Only existing account/bybit_readonly.py and tests/account/test_bybit_risk_limit_envelope.py. Existing risk method adds ONLY last optional owner_guard:Callable[[],None]|None=None. Route-local marker stores the exact BaseException object ONLY when this guard raises; bare reraise. Noncallable non-None rejects INPUT_INVALID before sends. Initialize local route-or-None before outer validation try. Marker identity is checked against CAUGHT object before every existing translation/retry/secondary read_failed, including known BybitReadError/BybitBlocked/MonitoringBudgetError/httpx/TimeoutError/InstrumentSourceError/RiskV2Rejected, cancellation and programmer errors. No marker by class/code/text and no blanket 'marker exists -> replace fresh cancellation'. No reset/reuse after route failure, new public error family, retry or fallback.

Explicit cleanup table adopted from review section1: earlier quota/ban/fixed rejection/owner/programmer primary survives ordinary cleanup Exception as SAME object. Original cancellation survives ordinary cleanup/completion as SAME object. With no primary, ordinary cleanup error propagates through existing appropriate classification and prevents result. Fresh cleanup CancelledError wins over ordinary primary. Fresh SystemExit/KeyboardInterrupt/other process-control BaseException propagates; do not broadly suppress BaseException. Same cancellation/control object reraised remains itself. Original response quota/ban callback remains first and rejection does not run later owner/origin/body checks. Charge before final send guard remains charged. No invented catch-to-success, error text copying or new telemetry with private values.

## E2A final proof order and original contexts

Split callbackful clock/deadline checking from callback-free transport/operation/group identity checks. Preserve exact finite/nondecreasing monotonic/deadline>= rejection. Snapshot original raw client/lifetime/source/environment/operation ContextVar/receipt ContextVar/exact receipt/token BEFORE first clock/guard. After arbitrary budget/receipt callbacks and all route clocks, invoke external owner_guard, then LAST callback-free original transport/operation/group proof, then exact send/return. No clock, budget callback, require_active_group or external guard after this proof. Recheck exact request after guard before send where reachable callback mutation could alter it; this is a pure proof on the actual request.

Inside entered operation original context must still be selected self._operation and get() is exact budget. None is permitted only pre-entry, not after set. _monitoring_operation retains original ContextVar locally for set/reset, never rereads a replaced field for reset. Existing exact positive legacy behavior remains. No changed shared budget/session, additional ContextVar, authority registry or receipt framework. All response/chunk/retry/close/parse/validator/final paths use the adopted order. E2A guard cannot itself authenticate arbitrary catalog/session objects; E2 owns the composite below.

## E2 after fresh E2A fake regressions and independent code review

Transfer only market_data/instrument_producer.py + execution/history.py and NEW tests/market_data/test_instrument_producer_risk_capture.py + NEW tests/execution/test_bybit_risk_limits_v2.py. Exact required signatures/returns, age/page bounds and finite source-to-risk error mapping are immutable preflight sections1/5. Existing constructors/legacy readers/old tests preserved. No new HTTP client/hidden GET/cache or projection API.

Adopt RAW-only private selector: internally created testing owner uses existing _require_current_for_testing; real owner uses require_current. Genuine full mixed LINEAR+SPOT refresh/current handle/member/budget are mandatory. Production require_current and session require_observation_binding must reject injected fixtures before AND after raw success. No forged handles/_current, production monkeypatch or changed testing taint.

Composite guard snapshots original producer/current/handle/generation/client/environment/testing/pending/full catalog/pin and actual History session/binding/identity/client/credential-pair/scope/closed-revoked state before callbacks. It calls genuine RAW lookup/full pin/freshness and session.require_binding, checks all page UTC/monotonic ages, THEN ends with bounded callback-free comparisons of the complete original higher-owner state. Existing lookup/session methods can call clocks after their own identity checks; their returned equality alone is insufficient. No route clock follows composite guard. Do not output/hash/log credential values; in-process identity comparison is not secret persistence or a new key store.

One original History outer reuse_or_open_receipt spans capture/parse/projection/final proofs; producer reuses exact token/client/deadline. Risk pending marker set before callbacks, overlap refused, stays until finally; normal failure preserves catalog. Same-revision refresh/new handle invalidates original ownership. All11 pin fields/full catalog/source links checked, no instrument refetch or table-derived catalog revision. Each page independently max_age checked, exact UTC/monotonic ratio arithmetic/equality expiry, no tolerance/aggregate shortcut/field casts. Configured cap plus stricter A0 64pages/1MiB/1024rows and group32GET/20s/parent limits apply before append/next send; max_pages is not minimum remaining capacity. Genuine setup GETs count under same budget; private auth counting uses existing session operation scope, no new authenticate budget API.

Actual parser returns Capture(table,raw_pages); producer calls once with complete raw tuple/original full pin/distinct operationID. Raw History returns Capture. V2 History projects capture.table once with original full instrument. After parse/project/session/clock callbacks re-prove full higher owners/original group with no callback through reset/return. Original raw data/lexemes/time retained.

For E2 body-vs-existing group exit faults, authorize one narrow private precedence helper or two local bounded blocks around EXISTING reuse_or_open_receipt. Catch body primary INSIDE yielded scope, allow original exit, apply adopted cleanup table. Ordinary resetfault preserves original body error; successpath resetfault prevents return. Existing receipt.active=False still runs. No budget mutation/restoration/alternative reset, additional token/receipt or exception-owner fabrication. Test direct readers without caller restoring replaced budget._receipt. This is exception cleanup only, not a new currentness/authority framework.

## Acceptance

M1 exact guard-origin/same-object/known-code/all catches/clock-context-replacement/quota-charge/no-send/cleanup ordinary-original/fresh control regressions must pass for E2A, then fresh independent review. M2 genuine catalog+DEMO/raw bytes/full pins/no refetch/samegroup/all caps/same-revision+unused-member/session retirement/final clocks/receipt replacement/expiry+earliestpage/production taint rejection must pass for E2. No actual provider/currentclock/Riskregistry/model/native/DEMO financial/PC-off/full63 claim. Fixed Python/five file selectors off/new short fixture paths/per-child hashes+argv+PID+UTC+raw outputs; no actual SDK/network/order/AI/server/install/default/operator DB/secrets. Root owns source custody/Git/CI/main. Exact whole CI and all later runtime/current-source gates remain separate.


---

# E05 M2-E2 owner entry preflight — 2026-11-03

**CONCRETE WITH ROOT RULINGS R1–R3; no implementation authorized.** E1 is accepted; this proposes E2A guard amendment then E2 raw orchestration, not a repeated E1 audit. No financial question/default TTL. Full63 continues.

R=`F:/자동매매프로그램/worktrees/bybit-risk-public-route-20261103`; EX=`F:/자동매매프로그램/final-completion-audit-20261103`. HEAD5d4874e5403495f8da553a8c343f3872ec0a2bd2 is root/closure-supplied, not Git-queried. Read prior EX §§4–6, full E1 report/accepted group closure and actual corrected source/fixtures below. Current pins supersede historical E1 source pins. Static only; no imports/tests/Git/network/browser/private/env/DB/SDK/native/orders/AI/server/install/subagents/other WIPs. Sole write: this report.

## 1. Scope/API amendment

E2A ownership: existing account/bybit_readonly.py + existing tests/account/test_bybit_risk_limit_envelope.py, optional seam only, then fresh fake regressions/review. E2 ownership: existing market_data/instrument_producer.py + execution/history.py; NEW tests/market_data/test_instrument_producer_risk_capture.py + tests/execution/test_bybit_risk_limits_v2.py (both absent). Source prefix=src/multi_exchange_trader/. Budget/session/A0-A1/contracts/catalog/config/runtime/fixtures/legacy tests read-only. No new client/guard/authority framework.

```python
# Existing E1 signature gains only this last optional keyword:
async def read_risk_limit_envelope(self, key: InstrumentKey, *, cursor: str,
    budget: MonitoringReadBudget, group: ReadGroupToken, deadline: float | None,
    owner_guard: Callable[[], None] | None = None
) -> BybitRiskLimitEnvelopeReadV1: ...

# New producer primitive, raw product only:
async def capture_linear_risk_limit_v1(self, instrument: InstrumentObservation, *,
    catalog_receipt: CurrentCatalogReceipt, expected_catalog_revision: str,
    budget: MonitoringReadBudget, max_age: timedelta, max_pages: int,
    owner_guard: Callable[[], None] | None = None
) -> BybitRiskLimitCaptureV1: ...

# NEW HistoryReader methods, identical required keywords:
async def read_risk_limit_capture_v1(self, instrument: InstrumentObservation, *,
    public_owner: BybitInstrumentCatalogProducer,
    catalog_receipt: CurrentCatalogReceipt, expected_catalog_revision: str,
    budget: MonitoringReadBudget) -> BybitRiskLimitCaptureV1: ...
async def read_risk_limits_v2(self, instrument: InstrumentObservation, *,
    public_owner: BybitInstrumentCatalogProducer,
    catalog_receipt: CurrentCatalogReceipt, expected_catalog_revision: str,
    budget: MonitoringReadBudget) -> LinearTierSnapshotV2: ...
```

Keep constructor/legacy methods unchanged. History uses reuse_or_open_receipt once around capture, parse completion, projection and final checks; producer reuses the SAME token. v2 calls capture once/projector once; no pair/new public projection API or second GET. Raw reader returns Capture. Caller needing both retains it and applies pure projection; invoking both readers is two operations, no hidden cache. Pass existing History _max_age/_max_pages. Direct producer validates same existing bounds: exact timedelta0<age<=5minutes, exact int1..100 pages, with stricter A0/budget caps. No default source/TTL.

## 2. Genuine positive fixture seam, not current authority

Actual producer113–151 internally creates real public client or exact MockTransport `_for_testing` owner.426–447: production require_current ALWAYS rejects _testing; _require_current_for_testing requires it and invokes the same _lookup.449–482 verifies genuine current handle/generation/full catalog/view/envelopes/clocks/expiry. Copied view or newly allocated opaque handle cannot substitute.

Adopt one private RAW-only view helper: real owner uses existing require_current; internally created testing owner uses existing _require_current_for_testing. New capture/readers may use it. No public testing flag, monkeypatched production methods, changed _testing, forged _current/handle or production-check relaxation.

Positive path: _for_testing -> real refresh of full mixed catalog on owned MockTransport -> genuine handle/test member -> raw readers. Same client; setup GETs count, no instrument refetch. Positive clocks can be actual UTC/monotonic with current synthetic time; negatives use injected clocks, no authority-method patch. Separately authenticate genuine DEMO session on DEMO MockTransport (test_history60–85); require_binding accepts fixture, require_observation_binding still rejects. No forged binding/cross-host keys.

_testing/injected association stays true. Capture/snapshot labels erase no taint. Production require_current rejects fixture before/after raw success. Task9 must obtain actual owner/handle/production currentness, not detached DTO/hash authority. No registry/qualified flag.

## 3. E2A source guard ordering

E1 checks transport/group, not catalog/DEMO. Checks outside page await cannot stop internal retry after retirement; optional owner_guard closes that gap.

Store callable per logical route across retries; noncallable non-None fails INPUT_INVALID before send. E2 supplies producer current-catalog guard composed with History's actual session.require_binding(binding,scope). Standalone public capture may omit account guard and makes no DEMO claim. Do not replace shared budget/receipt callbacks, wrap budget, add receipt/client or ContextVar framework.

Ordering: arbitrary budget/receipt callbacks -> owner_guard -> final transport/operation and callback-free original-group proof. Physical send: before_attempt callbacks/charge -> owner_guard -> final proofs -> one send. Original response: budget.response quota/ban FIRST -> guard/proofs -> origin/body/schema. Recheck after stream/retry/close/parse/validator and at final return; no arbitrary callback after final group proof. Preserve quota-primary/cleanup/cancellation precedence and charged attempt when guard rejects before send. None default preserves accepted E1 callers.

Guard errors retain owner/type/code; unknown/cancellation propagate. Even known-code guard BybitReadError is not transport. Origin tracking stays route-local; no new public family/raw text/fallback/qualification. Test origin.

## 4. E2 lifetime, groups and complete capture

1. Private risk-pending marker precedes callbacks; overlap rejects. Snapshot exact _Current/handle/generation/client/environment/testing/E1 IDs. Start/ordinary failure preserves catalog. Refresh (same revision included)/retire/close/replacement/expiry/wrong loop invalidates. Keep marker until finally drains; release scratch, no registry.
2. RAW helper validates whole catalog/receipt/A02 links. Compare full eleven-field pin via pin_instrument_v2(member,catalog_revision=actual_view.catalog.revision)/closed payload, including raw row/receipt. No selected-only catalog/table-derived revision/SupportedInstrumentProjection/refetch. Full view/pin at entry/page completion/final; identity/all-source ages in each E2A guard.
3. History snapshots original current/generation too; keeps outer group through projection; checks its actual account binding before/after await and final return. Producer passes composite guard into E1. DEMO client sends no risk GET; PUBLIC response keeps public origin/environment. require_binding here proves raw session consistency, not actual observation/native permission.
4. Exact budget; reuse token/deadline/counters/guards. Snapshot budget._receipt ContextVar/exact _ReceiptBudget/token BEFORE callbacks. Recheck original identities/active=True and owner/current/generation callback-free. Adopt E1 private read seam, no budget mutation. No require_active_group after final proof; producer guard calls no budget checks (no recursion).
5. Budget/root/clock callbacks may revoke/replace state. After them prove original owner/current/group callback-free, including final History binding/freshness/projection. No callback/await until context reset/return. Original _Current/handle/generation rejects same-revision refresh.
6. Sequential E1 pages/exact cursor; existing decoder gives cursor/count. Before append bound64pages/1MiB/1,024rows/configured cap; group32GET/20s,parent200sends/60s includes setup. No remaining>=max_pages precondition (may end early). Exhaustion stops next send; nonterminal cap RESOURCE_LIMIT; cycles/missing terminal retain parser errors. All rows/lexemes/IDs; no pruning/64KiB cap increase.
7. Producer mints table operation ID distinct from page IDs, calls parse_bybit_risk_limit_table_v1(tuple(raw_pages),instrument_pin=exact_pin,observation_id=operation_id) ONCE. Byte replay is allowed, not another transport. History v2 calls project_bybit_tier_snapshot_v1 ONCE with original full instrument inside outer group. Revalidate current full catalog/pin/root binding/group and each page after parse/project; exact return types above.
8. Catalog own expiry remains. Each risk page: explicit max_age, age>=bound rejects; UTC response/receipt plus exact monotonic elapsed-age microseconds/as_integer_ratio math. Risk-typed helper, no instrument cast/aggregate-max shortcut/tolerance/newTTL/receipt-as-server. Transport generation is not catalog generation.

## 5. Errors, future evidence and rulings

Producer preserves E1 InstrumentSourceError and A0/pin/parser/projector RiskV2Rejected. New config failure uses source INPUT_INVALID/BUDGET_REQUIRED; cap uses Risk RESOURCE_LIMIT; wrong member pin uses Risk SOURCE_MISMATCH. Existing A02 CatalogRejected uses fixed catalog_error_code at source boundary. No text copied.

NEW History finite source-suffix -> Risk-suffix map: INPUT_INVALID/JSON_INVALID/RESPONSE_INVALID/ENCODING_UNSUPPORTED/BUDGET_REQUIRED/GROUP_REQUIRED/OVERLAP -> INPUT_INVALID; RESOURCE_LIMIT/BUDGET_EXHAUSTED -> RESOURCE_LIMIT; ORIGIN_MISMATCH -> SOURCE_MISMATCH; CLOCK_INVALID -> CAUSAL_ORDER_INVALID; EXPIRED -> EXPIRED; SOURCE_UNSUPPORTED/PROVIDER_REJECTED/HTTP_REJECTED/ACCESS_RESTRICTED/RATE_LIMITED/NETWORK/TIMEOUT/RETIRED/CLOSED/LOOP_REQUIRED/RECEIPT_REQUIRED/INJECTED_TRANSPORT/INCOMPLETE -> RISK_TIER_UNVERIFIED. Prefixes are INSTRUMENT_SOURCE_ and RISK_V2_. Unlisted/AccountBindingError/cancellation retain owners. No legacy catch-all/new enum/V1 behavior change.

Future fake RED/GREEN: genuine catalog/DEMO auth; exact two pages/lexemes/full pins/no instrument refetch; same-group counters; all caps; equal-revision/new-handle/unused-member change; retire/session loss at send/chunk/retry/quota/close/parse/project/final callback; replaced receipt/ContextVar with old token; clock reentrancy/expiry equality/old page; no send after owner loss; quota priority; guard error origin; final loss returns nothing; production rejects fixture. Preserve failures. Root separately adopts fixed-interpreter/selector-off fake/static commands; NONE run here.

Root rulings before code: R1 transfer/adopt E2A optional guard/error ordering and fresh E1 regressions/review first; R2 permit only RAW existing testing-lookup selector, keeping production strict; R3 transfer producer/history/two NEW tests and narrow callback-free private owner/group reads, unchanged returns/one outer receipt. These are engineering rulings, not user finance questions.

Actual provider/clock/Risk-registry/DEMO applicability/fees/settings/account/native/protection/BUY/SHORT/PC-off/full63 remain unproved. Task9/13 same-read current-owner association stays separate.

## 6. Actual byte pins

R-relative prefix aliases: A=src/multi_exchange_trader/account/; M=src/multi_exchange_trader/market_data/; X=src/multi_exchange_trader/execution/; TM=tests/market_data/; TX=tests/execution/; S=.superpowers/sdd/2026-11-03-bybit-risk-public-route/. Context pins are not a repo-wide audit/test rerun. Prior EX preflight remains29799B/SHA84c42a4d83d2123094f169e4bce3473182c8187d5682e8f37062b1d7dae3e41a. Final report hash external, no recursion.
TA=tests/account/. All15 pins rechecked against initial/current accepted inputs.
|Path|Bytes|SHA256|
|---|---:|---|
|A/bybit_readonly.py|45070|22afd5053d98b569c925af76f6a8d7a0343c1563a857794cf1d8223366425736|
|M/instrument_producer.py|19532|73923b8a4ff2c8b4555494ee54393dc18af4bca760990e8ea232de422af4cafd|
|X/history.py|28624|eb2432d5aaf9d26fe9defff455594c86e4b3f7e7a077e7b6664e53f6d46317fb|
|X/account_binding.py|34075|1f9356bf2fa5677cacd9d4873fc34bfbdb05c00b2007c52fbcd72141c1b281c1|
|X/monitoring_budget.py|6220|0575255097930ad0e4337a0fc6c56c1bb8b7675151cd9f17bce3d0a1672f13f5|
|A/instrument_envelope.py|14702|6fc195bb2a22a2d7afb8ab739d103ff94f685e563d8157d71d2054b4aee1c745|
|A/risk_limit_envelope.py|12485|3c388d22bb4e8f62f204752124a0bd8705c31a5064bb59b9b6dd502c8ee5789d|
|M/instrument_catalog.py|9196|23b8a8c4a7f155a2b7818969003ffc90cc82af3cfffc2ba722b6bc8a98137240|
|X/bybit_linear_model_inputs_v1.py|88711|86ba6ceb1770753b0fdacea96627639aa954c17bd2fb3d581432c779af897dd5|
|X/risk_v2_contracts.py|43844|2ff250f0a321befcf64cfe0104896a51d1b0063f513a05bdcaa32b506bd7cb35|
|TM/test_instrument_producer.py|41603|5f5be77caee499cf73feac0ca2f877cfdda61d32a4f5e82773857593a1a90b86|
|TX/test_history.py|20150|0a85daff917711195874817f5ace455b25c788a56f4cecfca2ccf5b7f8573bc5|
|TA/test_bybit_risk_limit_envelope.py|42091|c00931621c4966c18f23fccad9427d54e65cb1d4950680d03f592d37be55f0db|
|S/task-e1-report.md|15196|31c87320e2e1f115542a98f9b2b1a350b8b6675b63748f835a1bba5d8b4a1dd1|
|S/task-e1-group-fix-review.md|9162|811a26fbd9e0ef0352d95e1620520cdf548ba1ecb2404b50b27a40e6837a3e91|

Only this new report written; no source/test changes or executions. Final full read/hash then STOP writes.
