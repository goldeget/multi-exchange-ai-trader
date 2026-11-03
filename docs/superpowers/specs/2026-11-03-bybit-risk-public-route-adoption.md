# Adopted M2-E1 public raw risk-limit route — 2026-11-03

Root adopts sections3,5(E1),6(E1),7 of the independently inspected entry preflight as the binding E1 implementation contract. The full exact preflight follows this adoption. This is technical implementation authority, not actual source qualification or product readiness. E2 and Task9/13 remain deferred.

Write ownership: existing src/multi_exchange_trader/account/bybit_readonly.py and NEW tests/account/test_bybit_risk_limit_envelope.py only. No existing test, A0/parser, history, producer, generic risk, credentials, runtime, native, configuration, fixture or lock change. Existing factory gains only the explicitly described public risk-page operation. Reuse its physical client/retry/bounded stream and budget; no duplicate transport/decoder.

source_id/source_generation expressly describe the real public transport owner and its open lifetime; each is a separate UUID4hex sampled once at factory/open lifetime. They are not catalog/Risk-registry/current-receipt identity or account authority. Each logical page gets its own stable-across-retries observation_id. Close retires before awaiting. Return the existing raw value DTO only.

Use the finite InstrumentSourceError translations in section3. Root inspected its actual closed code set: RETIRED, CLOSED, ORIGIN_MISMATCH, SOURCE_UNSUPPORTED, INPUT_INVALID, CLOCK_INVALID and the listed resource/network/quota codes already exist. Do not extend that enum. Preserve A0 RiskV2Rejected errors and unknown programming/guard errors; no generic catch-all translations. Quota/ban precedes simultaneous owner/schema/cleanup rejection on the original response. Exact byte and identity checks never create source authority. Preserve instrument accepted_attempt zero-based; new risk accepted_attempt one-based.

No new TTL, clock tolerance, financial default, endpoint alias, host substitution or receipt synthesis. Explicit deadline is None or exact finite nonnegative float; use min of existing operation deadlines. E1 actual CLOCK_INVALID remains a later source gate and must not be relaxed.

Tests use the fixed existing Python interpreter and fake HTTPX owned transport only. Child selectors TRADER_CREDENTIAL_FILE, TRADER_ACCOUNT_PROFILE_FILE, BYBIT_ENV_FILE, AI_ENV_FILE, ANTHROPIC_ENV_FILE are literal off at launch. No secret/default DB/.env/vault/SDK/native/provider/API/network/order/AI/server/install/browser/Git or remote effects; no subagents. Root owns Git/CI/main and independent review.

Meaningful RED before implementation, targeted GREEN while developing, then ONE stable final command: new tests/account/test_bybit_risk_limit_envelope.py plus existing tests/account/test_bybit_readonly.py, tests/account/test_instrument_envelope.py, tests/execution/test_instrument_read_budget.py, tests/execution/test_history.py. Ruff and strict mypy on touched module/test. Do not repeat passing broad suites without changed code/failure. Do not execute the two unrelated long pure model resource cases locally. New tests must cover all six E1 groups in section6, including cancellation, close precedence, physical-budget charges, one bounded A0 decode, raw NUMBER identity, currentness around awaits and public/private family exclusions.

Freeze report/receipts with exact commands, PID/start/end/exit/stdout/stderr and source before/after pins, no incomplete command relabeled PASS. Preserve failures/historical artifacts; before copies are immutable. Final report names any unverified facts and limits E1 distinctly from full M2-E/canonical63. Stop writing after final freeze and hand off to root for commit, mandatory diff and independent review.

---

# E05 M2-E independent source-route entry preflight — 2026-11-03

Status: **READY TO CODE WITH THE EXPLICIT E1 SCOPE/CONTRACT FIX BELOW; NOT READY TO IMPLEMENT ALL THREE RETAINED SIGNATURES UNCHANGED.** This is a technical candidate for root adoption, not adoption, implementation evidence, actual source qualification, or product readiness. Full63 continues. No new end-user financial question is needed.

## 1. Authority and inspection boundary

E = `F:/자동매매프로그램/worktrees/bybit-linear-model-source-20261103`. External A = `F:/자동매매프로그램/final-completion-audit-20261103`. Root supplied HEAD `52b4abf4f6d08f906a5ffee77df27bc92e6a5876`, with D pin fix awaiting independent closure. HEAD/branch/status were not independently queried: Git calls were prohibited. Actual file-byte pins below are the independently inspected state.

Read FIRST the complete root notes (5,185 bytes; SHA256 `2be42e581807146d0692fd468f171c146992c49c5f6cbd8e33e5801960eb1f2d`), then retained M2 design section 9, full M2 root adoption and D adoption, and complete plan ownership map/Task M2-E lines 203–215. An initial batched display was truncated; required sections and both adoption documents were subsequently retrieved separately without truncation. Inspected actual client/history/account session/monitoring budget/readback/raw A0-A1 contract/parser/current table/parser/projection/pin/A02 catalog/public producer/supported projection. Existing test declarations and bytes are inspected/pinned, not reported as executed.

Only static reads/searches/hashes and official primary-document browsing were used. No app imports/tests/Git/browser interaction/provider API requests/credential or private/default-store/.env reads/DB/SDK/native/orders/AI/server/install/subagent/other-WIP actions. Reading config.py source executed none of its environment-loading functions. One initial search guessed account/ paths for execution modules and was corrected. One overlong report-authoring command was rejected before process creation (Windows error 206); no file was created by that attempt. The sole authored file is this new external report.

## 2. Confirmed entry findings

| Finding | Inspected fact | Required closure |
|---|---|---|
| Missing whole catalog | history.py414–423 stores only session/binding/scope/clock/max_age/max_pages; pin_instrument_v2(observation, *, catalog_revision) requires actual whole-catalog revision. | Instrument alone cannot identify current global catalog. Do not build a selected-only replacement or copy table revision as current evidence. |
| DEMO/public mismatch | account_binding.py204–252 owns/guards one DEMO client; read_client611–615 returns it. risk_limit_envelope.py185–198 admits PUBLIC_MAINNET/PUBLIC_TESTNET/FIXTURE, api.bybit.com/api-testnet.bybit.com only. | No DEMO relabeling/raw A0 weakening/cross-host credentials/client per page/public read() gate bypass. |
| Missing risk identities | Raw envelope requires observation_id/source_id/source_generation. Existing instrument client route has no risk identity metadata; producer source_id is per catalog refresh and its generation is opaque object. | Assign actual technical owner semantics explicitly. Supplied strings/copy labels are not authority. |
| Budget inputs absent | Exact MonitoringReadBudget and identity receipt tokens/reuse_or_open_receipt/require_active_group exist; monitoring_readback249–259 installs actual owner guards. New history signatures supply none. | New raw route accepts exact budget/active group only, opens neither. Later reader needs explicit public-owner/catalog/budget context. |
| Parser/projector cannot invent inputs | parse_bybit_risk_limit_table_v1(pages, *, instrument_pin, observation_id)1158–1202; project_bybit_tier_snapshot_v1(table, *, instrument)1725–1752 pins with table's own catalog revision. | Supplied consistency does not prove current whole catalog. Preserve D full-pin fix and its independent closure. |
| Current public owner is catalog-only | BybitInstrumentCatalogProducer owns one internally created unsigned public client, event loop, opaque generation and CurrentCatalogReceipt. refresh revokes current; require_current checks exact handle/generation/full view/dual clocks/expiry. | Smallest real owner to extend later. No inspected risk-capture/projection registry association API exists there. Catalog receipt is not a risk receipt. |

A0/A1 already retain complete pages/body hashes/NUMBER spellings/pointers/rows; no raw contract change is necessary. A02 catalog revision excludes changing raw/source receipt identity, so same semantic revision does not replace exact current receipt identity/full eleven-field pin. SupportedInstrumentProjection is a pure row/spec projection, not current source/catalog authority.

## 3. Recommended first slice E1: public raw page route only

Transfer write ownership for existing `src/multi_exchange_trader/account/bybit_readonly.py` and NEW `tests/account/test_bybit_risk_limit_envelope.py` only. Existing client owner reviews it. history.py, producer, A0/A1, generic risk/config/credentials/policy/native/runtime/fixtures remain protected. Defer both history methods and their new test file to E2. E1 fake completion does not complete original Task M2-E.

Retain the exact page signature:

```python
async def read_risk_limit_envelope(
    self, key: InstrumentKey, *, cursor: str,
    budget: MonitoringReadBudget, group: ReadGroupToken,
    deadline: float | None,
) -> BybitRiskLimitEnvelopeReadV1: ...
```

The existing for_public_instruments instance gains only this unsigned public-market operation. read()/anchor_server_time remain denied; trust_env=False and credential-free construction remain. Reject authenticated mainnet/testnet/DEMO instances even if unsigned market traffic could technically be sent.

### E1 metadata/lifetime amendment

The actual public client owns technical raw transport lifetime, not Risk registry qualification. Add private risk-only source_id and source_generation (separate UUID4 hex values, once per actual client/open lifetime) and the original underlying HTTP client identity to this existing public factory instance. No new factory/HTTP client/credential resolver/environment file/public lookup/global store. UUID32 already satisfies the existing identifier validator.

source_id names this public transport owner; source_generation names its open lifetime. Neither is the catalog producer's per-refresh source_id/opaque generation, account UID, or current Risk registry generation. observation_id is a new UUID4 hex for each logical page read before first send, stable across its retries. Sequential pages on this client share source_id/generation and have distinct observation IDs. Later capture owner mints its own separate table operation ID.

Snapshot/recheck exact original underlying client identity, still-open state, original public environment/base URL, empty credential fields and risk generation. Close invalidates route generation synchronously before its await; an in-flight route cannot return after close/replacement. Returned raw DTO remains a value, never a current handle. Injected status remains on the existing client; PUBLIC_* label alone proves no real network. Do not admit injected ownership through production require_current.

This permits the retained page signature to obtain identities from a real owner without inventing a registry. If root requires source_generation to mean catalog/current Risk generation, E1 must wait for E2/Task9 actual owner context instead. Do not silently treat client-lifetime generation as stronger registry generation.

### E1 exact transport/capture contract

1. Validate exact key/LINEAR product/cursor/optional absolute deadline and exact MonitoringReadBudget. Cursor is unmodified empty/nonempty exact str within A0's 1,024-byte bound; do not substitute history's 512-byte ASCII rule. Deadline is None or exact finite nonnegative float. Effective deadline is minimum of caller/budget/group deadline. Missing/foreign/subclass/forged/ended group fails before send. Active client operation is absent or this exact budget; preserve overlap. Open no receipt or budget here.
2. Envelope params are `(("category","linear"),("symbol",key.symbol))` plus `("cursor",cursor)` only when nonempty. Physical URL uses existing _query: later pages sort category/cursor/symbol. Compare wire URL bytes to that exact encoding of canonical params; do not demand metadata order in sorted query or change _query globally. GET/path/fragment/userinfo/port/scheme/origins are exact; public environment selects fixed public host. No limit/extra query/X-BAPI credential or signature headers. Accept-Encoding identity; no redirects/regional host substitution.
3. Existing begin_read runs once per page; its deadline bounds caller deadline. Capture actual group logical ordinal after it. physical_start/end are actual shared-budget snapshots, not invented per-page counts. Immediately before each send, after final group proof, charge before_attempt. Internal retries remain 0/1/2; risk accepted_attempt=index+1 only, instrument stays zero-based. In isolated sequential tests physical difference equals accepted_attempt; that equality alone proves no concurrency properties.
4. Reuse same self._client.send, bounded response path, existing retry waits/status policy and finally-close path through a closed new risk branch. No legacy _parse_json/_accepted on risk path, duplicate GET, second instrument-info GET, body reconstruction or new client. Keep instrument/legacy behavior unchanged. Private route dispatch changes remain explicit, not a new generic framework.
5. On ORIGINAL response, budget.response observes quota/ban before owner/group/currentness/origin/encoding/length/body/schema rejection. Simultaneous owner change cannot conceal terminal403/429/451 or zero/invalid quota. Preserve current narrow long/non-ASCII integer conversion handling; unexpected callback faults propagate. Then recheck lifecycle/group. Validate actual response.request and response.history, not a reconstructed response as original provenance. HTTPX association is application evidence, not signed server evidence.
6. Require one supported identity content encoding, bounded valid content-length when supplied, and at most65,536 application-body bytes. Check group/lifecycle/deadline before/after send/body-iteration/retry-sleep/close awaits and after arbitrary guards/validators. Every attempted send remains charged, including rejected body; no refund. Sample actual UTC/monotonic receipt after complete body. A0 rejects response_time>receipt/impossible time order; no clock tolerance/receipt-as-server/fake clock. E1 has no new TTL: raw causality plus active operation deadline only.
7. Use ONE existing bounded A0 lexer/token parser, original UTF-8 bytes and SHA. Before successful envelope, inspect bounded retCode/retMsg for fixed errors:10006→RATE_LIMITED; other nonzero exact integer retCode within -(2**31)..2**31-1→PROVIDER_REJECTED; bounded region message→ACCESS_RESTRICTED. Successful closed body/time/integer grammar stays A0-owned. Derive response_time from exact time token, never float. Construct existing raw DTO with actual metadata, SHA, accepted_attempt=index+1. Import existing private decoder/validators; A0/A1 bodies remain unchanged, no second parser.
8. Preserve successful budget.validating_response/validate_response. New raw branch result retains JsonNumberTokenV1 and exact integer stamp; never provide fabricated empty result or legacy float-reparsed body. Existing hook behavior outside risk is unchanged. Recheck group/lifecycle/deadline after parsing/DTO/callback/original close, immediately before return. Any known close/currentness/deadline failure prevents success.
9. Close original stream for success/rejection/retry/timeout/cancellation. Risk branch explicitly preserves an already fixed primary quota/ban/rejection when known network cleanup error also occurs. Without primary rejection, known close failure is NETWORK/TIMEOUT. Unexpected faults and cancellation never become success/retry. Translated errors contain no raw provider text/context. Verify this risk-only precedence without changing existing legacy close semantics.

### E1 return/error owners

Return only existing BybitRiskLimitEnvelopeReadV1, never Capture/Table/snapshot/CurrentCatalogReceipt/qualified evidence. No registry/cache side effect.

Explicit amendment: reuse existing InstrumentSourceError fixed public-source family despite historical prefix; no second error family or A0 edit. Finite known BybitReadError translations: timeout→TIMEOUT; proxy/dns/network→NETWORK; response_too_large→RESOURCE_LIMIT; invalid_json→JSON_INVALID; redirect→ORIGIN_MISMATCH; http_403/http_451→ACCESS_RESTRICTED; ret_10006→RATE_LIMITED; http_status→HTTP_REJECTED; monitoring_operation_overlap→OVERLAP. BybitBlocked→ACCESS_RESTRICTED. Unknown BybitReadError propagates, no catch-all .get fallback.

Exact MonitoringBudgetError MONITORING_GROUP_REQUIRED→GROUP_REQUIRED. Known MONITORING_DEADLINE_OR_CLOCK/MONITORING_LOGICAL_GET_LIMIT/MONITORING_PHYSICAL_ATTEMPTS/MONITORING_READ_FAILED→BUDGET_EXHAUSTED. Quota hook supplies RATE_LIMITED/ACCESS_RESTRICTED before schema. Wrong budget exact type→BUDGET_REQUIRED; wrong public family→SOURCE_UNSUPPORTED; actual wrong origin/lifetime/client→ORIGIN_MISMATCH/RETIRED/CLOSED; route nonfinite/backward clock→CLOCK_INVALID. Preserve A0 RiskV2Rejected input/resource/unsupported/source/causal errors unchanged, fixed code/from None, never malformed input→tier-unverified. Known failures terminalize shared parent with existing read_failed pattern without replacing primary error. Unlisted owner/guard errors and cancellation retain their owners after cleanup. No new enum/legacy error change.

## 4. Concrete E2 owner/API amendment for both readers — deferred

Do not implement old instrument-only methods with DEMO traffic, invented catalog, arbitrary protocol-shaped owner or copied labels. Select E2 after D closure/fresh E1 review and source/history ownership transfer. Smallest concrete actual public owner is existing BybitInstrumentCatalogProducer, which already owns same public client/current catalog. It needs explicit additional producer write transfer absent from the old four-file M2-E map.

Keep HistoryReader constructor and legacy methods unchanged. Amend only its NEW methods, no guessed defaults:

```python
async def read_risk_limit_capture_v1(
    self, instrument: InstrumentObservation, *,
    public_owner: BybitInstrumentCatalogProducer,
    catalog_receipt: CurrentCatalogReceipt,
    expected_catalog_revision: str,
    budget: MonitoringReadBudget,
) -> BybitRiskLimitCaptureV1: ...

async def read_risk_limits_v2(
    self, instrument: InstrumentObservation, *,
    public_owner: BybitInstrumentCatalogProducer,
    catalog_receipt: CurrentCatalogReceipt,
    expected_catalog_revision: str,
    budget: MonitoringReadBudget,
) -> LinearTierSnapshotV2: ...
```

Concrete public-owner primitive candidate, NOT present/implemented:

```python
async def capture_linear_risk_limit_v1(
    self, instrument: InstrumentObservation, *,
    catalog_receipt: CurrentCatalogReceipt,
    expected_catalog_revision: str,
    budget: MonitoringReadBudget,
    max_age: timedelta,
    max_pages: int,
) -> BybitRiskLimitCaptureV1: ...
```

History wrapper checks existing DEMO session/binding/scope before/after await and before return. It passes explicitly configured self._max_age/self._max_pages; no new defaultTTL/financial value. Public owner owns all risk GETs; DEMO client does not. E2 exact obligations requiring adoption:

- Exact actual owner/open loop/client/current catalog receipt with expected revision. Capture original current handle AND opaque generation identity; recheck both after every callback/await, including refresh with equal semantic revision. Refresh/retire/close/replacement/expiry invalidates operation. Production require_current continues refusing injected transport; offline _for_testing verification is distinct and never qualification.
- Resolve member from exact full catalog, validate A02 original raw/spec/source links and compare complete supplied eleven-field pin with pin_instrument_v2(member, catalog_revision=view.catalog.revision). Match original receipt/raw row as well as spec/env/key. Before request/after awaits retain full catalog/member. No instrument-info GET, selected-only catalog, SupportedInstrumentProjection-as-receipt or table-derived current revision.
- Exact caller budget, reuse_or_open_receipt ONCE over entire capture, preserve existing token/deadline/counters/guards. Owner performs its own current-handle/generation/dual-clock checks before/after all page awaits/callbacks, even with caller budget default no-op guard. New receipt is raw accounting, not qualification. No receipt per page or parent allowance reset. Pass actual active group deadline to E1; quota can reject below theoretical A0 page max.
- Sequential pages on exact owned public client. Existing decode_bybit_risk_limit_page_v1(envelope, *, page_ordinal) yields exact next cursor. Enforce configured max_pages plus A0 independent64pages/1MiB/1,024rows; shared receipt32logicalGET/20s and parent200sends/60s unchanged. No cap increase/pruning/joint-fit promise with64KiB canonical. Nonterminal beyond configured/structural budget→RESOURCE_LIMIT; cursor cycles/missing terminal retain A0 input/source owners. Do not synthesize terminal or omit unused rows.
- Mint distinct table operation observation_id; call existing parse_bybit_risk_limit_table_v1(tuple(raw_pages), instrument_pin=exact_pin, observation_id=operation_id) once for product. Preserve every byte/hash/time/query/cursor/sourceID/generation/full row. Replay may parse bytes again, never transport. Every page response/receipt plus monotonic elapsed age must meet explicit max_age. Recheck exact catalog currentness/group/individual page age after parse/project and before return. No aggregate max hides old children; catalog.max_age does not invent risk TTL, explicit configured reader max_age bounds pages separately.
- read_risk_limits_v2 invokes capture exactly once, returns project_bybit_tier_snapshot_v1(capture.table, instrument=instrument) with same currentness checks, exact LinearTierSnapshotV2. Caller needing both retains Capture and applies pure projector. Calling both readers is two operations; no hidden cache. One page set/capture/table/projection, no duplicate/instrument GET.

E2 Risk-facing error amendment: source INPUT_INVALID/JSON_INVALID/RESPONSE_INVALID/ENCODING_UNSUPPORTED/BUDGET_REQUIRED/GROUP_REQUIRED/OVERLAP→RISK_V2_INPUT_INVALID; RESOURCE_LIMIT/BUDGET_EXHAUSTED→RISK_V2_RESOURCE_LIMIT; ORIGIN_MISMATCH→RISK_V2_SOURCE_MISMATCH; CLOCK_INVALID→RISK_V2_CAUSAL_ORDER_INVALID; EXPIRED→RISK_V2_EXPIRED; SOURCE_UNSUPPORTED/PROVIDER_REJECTED/HTTP_REJECTED/ACCESS_RESTRICTED/RATE_LIMITED/NETWORK/TIMEOUT/RETIRED/CLOSED/LOOP_REQUIRED/RECEIPT_REQUIRED/INJECTED_TRANSPORT/INCOMPLETE→RISK_V2_RISK_TIER_UNVERIFIED. Unlisted codes propagate, not fallback. Raw/parser/projector RiskV2Rejected unchanged. A02 CatalogRejected follows D's fixed RISK_V2_INPUT_INVALID boundary; directly imposed new technical bound is RESOURCE_LIMIT. AccountBindingError retains actual account owner, not legacy catch-all. No change to read_history/read_risk_limits/amount()/V1 result/qualification/error behavior.

E2 write scope requires producer/history and expressly transferred new tests. E1/legacy/A0-A1/generic dependencies stay protected unless concrete composition defect is returned to owner first. E2 returns raw/pure consistency products; obtaining current A02 receipt is not actual Risk registry association.

## 5. Task9/13 and actual-source boundary still open

A02 current catalog issuer and copied IDs do not associate risk Capture/projection with actual current Risk source registry. Task9/13/current-source owner separately specifies/implements atomic association of exact capture/full pin/raw manifest/full table/source identities/generic projection under real current owner/generation/lease before issuing qualified downstream evidence. Adapter must validate retained raw preimages/same operation, revoke on owner/generation/session change, retain fake/public-label boundary. No public label-to-authority lookup/fake handle/global qualified flag/assumed record-hash equality/temporary registry stand-in.

DEMO UID/credentials/host capabilities and public-market applicability mapping remain separate. Actual PUBLIC_MAINNET GET would not prove DEMO settings/fees/AMR/leverage, whole liabilities/held/order/reservation classification, fee applicability, native writer/protection/cleanup, BUY/SHORT qualification/current issuer/paidAI/server/PC-off/full63. domain.source_record_sha256s remain opaque registry-record values, not raw Bybit ref hashes. D pin-fix independent closure and earlier actual CLOCK_INVALID/NONPASS remain separate; no actual read/OS-clock action here.

Official primary docs read 2026-11-03: [Demo Trading Service](https://bybit-exchange.github.io/docs/v5/demo) names api-demo.bybit.com, independent DEMO account/keys and all market endpoints. Its public-data equivalence wording concerns WebSocket data, not authenticated REST raw-origin substitution. [Integration Guidance](https://bybit-exchange.github.io/docs/v5/guide) lists api.bybit.com/api-testnet.bybit.com; preserve stricter reviewed code allowlist despite other documented regional hosts. [Get Risk Limit](https://bybit-exchange.github.io/docs/v5/market/risk-limit) documents GET/category/symbol/cursor and NUMBER margin rates, but its inverse example spells rates as strings and omits nextPageCursor. Those examples do not amend frozen LINEAR NUMBER/closed result contract or qualify actual response. These are DOCUMENTATION observations, not API observations; no explorer/provider request occurred.

## 6. Meaningful acceptance requirements — planned, not run

E1 genuine behavioral RED/GREEN with fake owned transport, independent byte literals/assertions:

1. Accepted page exactly one actual send; NUMBER spellings0.0050/5e-3 retain exact raw field pointers, equal economics/different source identity. Force failure if new path invokes legacy decoder. No instrument-info request.
2. Two pages share actual client/source ID/generation with unique page IDs, exact canonical metadata versus sorted encoded wire cursor.500→200 retry shows risk attempt1-based, physical charges/one begin_read; instrument0-based stays unchanged. Key/cursor/query/origin/header forgery fails at proper boundary.
3. Quota/ban precedes malformed body/encoding/huge-invalid length/origin/stale owner/closed group; original closes, attempted charges stay, zero/invalid/long/non-ASCII quota terminal.403/429/451 gain no retry under existing quota policy. Terminal observation cannot be hidden by schema or cleanup failure.
4. At 65,536/+1 stream cap; A0 UTF8/depth/nodes/keys/token/exponent/escaped duplicate/closed-schema bounds retain owner errors. Provider/region codes omit raw text/context. Unknown transport/guard/programmer fault does not become generic success/fallback source error.
5. Missing/foreign/subclass/forged/ended groups; exact-but-unguarded budget is raw-only and never qualification; overlap; deadline before send/during stream/parse/close; backward/future clocks; client close/replacement at send/chunk/retry/close await; cancellation. No envelope after failed close/currentness; close once per original physical response, preserve primary quota priority.
6. Public read()/anchor denied; authenticated DEMO/mainnet/testnet refused by new public route; trust_envFalse/empty credentials unchanged. Legacy read()/amount()/history/V1/client behavior unchanged.

Once stable E1, run new test_bybit_risk_limit_envelope plus existing test_bybit_readonly/test_instrument_envelope/test_instrument_read_budget/test_history once and touched Ruff/strict mypy. Use root's existing fixed interpreter/selector-off environment. None executed in this preflight. Root must prescribe exact future commands/env/receipts. Broaden/repeat only for changed dependencies/failure/unresolved concern; no actual network/native/DB/PAPER/runtime suite here.

E2 adds complete synthetic catalog/member pins and two-page capture/projection. Mutate current receipt expiry/retire/refresh with unchanged semantic revision or changed raw/member receipt; old child despite fresh aggregate; parent group reuse/remaining quota; cursor cycle/nonterminal cap; catalog change during parse/project; DEMO binding closed before return; exact one capture for snapshot; wrong full pin/catalog env; production rejection of fake owner/current receipt; known/unlisted error owners. Assertions use actual captured synthetic bytes/current handle identities, never fake registry. Actual Task9 association needs its own independent tests/review and cannot pass by E1/E2 fake success.

## 7. Root adoption/dispatch gates

- Read full report; explicitly adopt E1 scope, client-lifetime metadata meaning and finite existing source-error reuse. Preserve/re-freeze owned before paths/dependencies before code. If catalog/registry generation is mandatory at E1, select E2/Task9-first with actual operation context, not an implicit label substitution.
- Transfer client ownership; implement/freeze exact E1 with new test, fresh independent source review; root owns Git/adoption/CI. No actual API authority follows implementation selection.
- Close D full-pin fix independently before whole E integration. E1 cannot make D/current source issuable.
- E2 requires later producer/history transfer and explicit API amendment above; actual Task9/13 association/mapping are later gates. Do not claim all three original methods complete if only E1 exists.

## 8. Inspected byte freeze

Paths relative to E. Source/test entries pin inspected dependency bytes, not repo-wide correctness; document entries are contextual root-owned snapshots. Hashing a file does not claim every unrelated declaration/test was fully reviewed. Final recheck after authoring must match these initial pins. Final report bytes/SHA are returned externally, avoiding self-hash recursion. No writes after final freeze.

| Path | Bytes | SHA256 |
|---|---:|---|
| src/multi_exchange_trader/account/bybit_readonly.py | 30577 | 48cca753e18ac48d6c31ed47d7854c084647c6b20d6c562822b9ac3baa11398c |
| src/multi_exchange_trader/execution/history.py | 28048 | da82a893b2e0bbf8a691f0d30c60799b429ce16ee87e02e095a1240d8d68f41c |
| src/multi_exchange_trader/execution/account_binding.py | 33428 | de3100f8079ce9867acd9515f15aba03ee9d861bb82b9b2028f4223a10e447ea |
| src/multi_exchange_trader/execution/monitoring_budget.py | 6050 | e3fd7c5bedf4d9b44dc7b9ae1a5088684301aad671a283fdb510da1d1fb10675 |
| src/multi_exchange_trader/execution/monitoring_readback.py | 16186 | 0d2fe48a955492f1146c616a7683293844f4b58fe7d9c79ed59aadc6bf9b1463 |
| src/multi_exchange_trader/account/risk_limit_envelope.py | 12148 | 77a47a779279fa95fdac08eff26c0114994863e7525a3e347391eb2f844c24e6 |
| src/multi_exchange_trader/account/instrument_envelope.py | 14332 | 5b61e0b474981f99e33949f15333824efd5247782fb0f1bbb462de6dd0ea0e05 |
| src/multi_exchange_trader/account/config.py | 5731 | 47af88d79d2e8df96daa0d0a81a5f737a4a8f7f9db33653201417d12eb8fd89d |
| src/multi_exchange_trader/market_data/instrument_catalog.py | 8943 | 755b7beb5dd77db307ef3421eaa0129520136668e007e17a1a56c666039c85a4 |
| src/multi_exchange_trader/market_data/instrument_definition.py | 21273 | 4c4e1c60ef0ff9c564478503cdce35198d1e2edabcd6730a20761aad8e31a6a5 |
| src/multi_exchange_trader/market_data/instrument_producer.py | 19024 | 9dd37bcc2a07756b7c03b6b54c2631ab78cc459c5396b1a95b4e3e59e6aae42e |
| src/multi_exchange_trader/market_data/instrument_source_projection.py | 4911 | bac5c9a685d3eb41fe2cb35a97c460a3f4348728bcf4330b1ac74761c4a741e7 |
| src/multi_exchange_trader/execution/bybit_linear_model_inputs_v1.py | 86504 | f0d840386c0328fc118a8a5b911b1e1146405b688d3508ca6624fe0f6764aea9 |
| src/multi_exchange_trader/execution/risk_v2_contracts.py | 42681 | 7128ab957cc2536f87eb85e4c4bff8c32913b97a745fe6fa01b349a15ba74faa |
| src/multi_exchange_trader/execution/risk_v2_math.py | 10427 | 1bfb183b310f5a4c086fa1b74b0a3018829539a26ce0a64fcea150eaefd0fe70 |
| tests/account/test_bybit_readonly.py | 64996 | 8aeea3ad62e41459a752dc672c92b9550893e842b8cf0cb3fbe9ad4ff113b3be |
| tests/account/test_instrument_envelope.py | 7707 | db18dd98795e31da574933249d86a1655dd4337ce6dcb26f0c0da8522f533f8b |
| tests/execution/test_history.py | 19757 | 064f36f9d605793c627e117ec3f912141d46f67170bf7dd52bc39eae2d09d3f9 |
| tests/execution/test_instrument_read_budget.py | 2399 | 82eac4c0f63a5b02b575fcf772006de801eb1fdac4ab3523b18fa804ebd4eef6 |
| docs/superpowers/specs/2026-11-03-bybit-linear-model-m2-design.md | 95190 | 4a63d0565761abc1546dd84fbf902906927638e3a5c1b96ddb1f78dbaa561260 |
| docs/superpowers/specs/2026-11-03-bybit-linear-model-m2-root-adoption.md | 7725 | 37d7b1db53f8a5f89a2405c02f09f0e725060cdbd4a988548bc9d685541c5016 |
| docs/superpowers/specs/2026-11-03-bybit-linear-model-m2-d-adoption.md | 11937 | 720d6e2137f9611e8e61018846ab8a3115ddeca2f0adb06c92cd51471fb876ef |
| docs/superpowers/plans/2026-11-03-bybit-linear-model-m2.md | 49227 | e17cb3fbda3240aeec58aa7be341cf1185b9adad83fb6db6b66887b338c4da19 |

Final authoring-time recheck: all 23 listed file lengths/SHA256s match the initial inspection pins. The two proposed new test paths are absent at this check. Root notes remain separately pinned above. No other source/test/doc/Git/default-store changes were made. This report is frozen after the final full read and external byte/SHA calculation. STOP.
