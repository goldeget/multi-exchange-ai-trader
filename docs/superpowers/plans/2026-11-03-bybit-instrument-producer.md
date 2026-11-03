# Root adoption — Bybit instrument source producer

## Root T3 interface closure (adoption clarification)

T3 remains fully independent of new T1 modules. It reuses frozen CatalogRejected and its fixed codes, not InstrumentSourceError. Builder propagates frozen helper fixed codes; wrong nonexact row -> CATALOG_INPUT_INVALID and wrong exact row symbol -> CATALOG_SCOPE_MISMATCH. SupportedInstrumentProjection is a required-seven-field frozen dataclass with constructor validation and public validate_supported_instrument_projection(value)->None; both use one nonrecursive exact validator, enumerate trusted class fields, and rederive raw/spec/digests from bounded canonical bytes. DTO malformed/inconsistent/forged/missing fields -> CATALOG_OBSERVATION_INVALID, retaining CATALOG_RESOURCE_LIMIT for actual bounded-resource rejection. Reuse frozen _decode_row for canonical raw revalidation and _validate_spec/_spec_payload as needed; no parser/decoder/hash duplication, no T1 dependency, no runtime source-file reads. This explicitly extends the documented private helper dependency, with regression pinning and future maintenance cost. T4 later maps these pure fixed errors at its owner boundary.


ADOPTED for implementation of T1, T2 and T3 only, after EXECUTABLE final independent preflight. T4/T5 below are explicit subsequent plans, not tasks whose completion is claimed by this plan. The historical candidate wording retained below records preparation ownership; this adoption supersedes PROPOSED status for T1–T3 only.

Fixed source baseline: 74b66c53c8c38299c03f48a99145dddbce38ec61; branch gpt/bybit-source-risk-producers-20261103; origin https://github.com/goldeget/multi-exchange-ai-trader.git. Fresh origin/main remains46d4d13de3f990e875c34bba3b4c34ffc3f81609. PR29 exact corrected-source CI remains a separate running gate, not accepted main yet. Frozen source blob pins: bybit_readonly.py54f73787093d3ec1c6f3ca87e3a2c15cba4ae2bd; monitoring_budget.pyae0ec5db788b503285860b9bb4253ddc96b50a11; instrument_definition.pyf8c0ac9d72e56a091da781ee6eac3a05b73c1203; instrument_catalog.py95e3c49e94ddaca757780696e2ba2cb3957f2772. Definition SHA2564c4e1c60ef0ff9c564478503cdce35198d1e2edabcd6730a20761aad8e31a6a5. No applicable AGENTS found at ancestors or repository search; tracked tree clean before adoption. Root serializes all Git and preserves original worktrees/evidence.

Binding spec is docs/superpowers/specs/2026-11-03-bybit-instrument-producer-design.md; execution plan is docs/superpowers/plans/2026-11-03-bybit-instrument-producer.md. External frozen originals and A02_TRUSTED_PRODUCER_FINAL_PREFLIGHT_20261103.md are preserved. Root read original documents, complete R2 replacement changes (recovering truncated middle explicitly), full R2 plan and final preflight.

Implementation clarifications from final preflight are binding: retain original fixed HTTP/quota cause despite parent terminal classification; quota hook runs before encoding/body rejection; retain safe decoder failure while terminalizing parent. T1/T2 are serial source collection: shared parent start/end counters are not per-read counters under interleaving; reject invalid interval without counter rewriting, and concurrent T4 evidence needs its own later binding design. No fixture or injected test route produces production require_current authority. All exact record enumeration uses trusted classes, never caller instance dataclass metadata.

T1 ownership: src/multi_exchange_trader/account/bybit_readonly.py, new account/instrument_envelope.py, execution/monitoring_budget.py; tests/account/test_bybit_readonly.py, new tests/account/test_instrument_envelope.py, new tests/execution/test_instrument_read_budget.py. Relative module paths below are under src/multi_exchange_trader. Covering existing budget consumers: tests/execution/test_native_monitor_http.py, test_monitoring_readback.py, test_monitoring_receipts.py, test_restored_monitoring_worker.py. T3 ownership: new market_data/instrument_source_projection.py and new tests/market_data/test_instrument_source_projection.py only. T2 follows frozen/reviewed T1 and owns new market_data/instrument_producer.py and new test. No overlapping source edits.

Use existing fixed offline Python C:/Users/PC/.codex/tmp/trader-receipts-20261102-venv/Scripts/python.exe with PYTHONPATH=<R>/src and TRADER_CREDENTIAL_FILE,TRADER_ACCOUNT_PROFILE_FILE,BYBIT_ENV_FILE,AI_ENV_FILE,ANTHROPIC_ENV_FILE each literal off. Synthetic/offline meaningful RED, focused covering checks, separate Ruff/strict mypy, exact receipts and SHA freeze before independent task/whole review. Actual source acceptance, existing native36/restart6/restored12, exact whole hosted/main CI, actual DEMO and full product acceptance remain separate gates. No agent private/default-store/provider/network/native/WSL/order/paidAI/server effects or Git mutations. Root continues full63; this plan is not final program completion.

---

# A02 trusted producer — PLAN CANDIDATE R2

2026-11-03 KST. **PROPOSED / fresh preflight 필요 / 아직 채택·구현·시험·실제 수락 아님**.

입력은 R2 SPEC, 원본 PREPARATION spec/plan 및149줄 independent PREFLIGHT다. root 최신 전달은 reviewed74b66에서 만든 gpt/bybit-source-risk-producers-20261103, definition 수정 hash prefix4c4e1c60와 GET client/budget unchanged다. 이는 context이며 root가 adoption full SHA/frozen bytes/unchanged GET client blob을 pin한다. 이 작성자는 Git을 실행하지 않았다. 기존 산출물은 보존하고 이 새 계획만 작성했다. source/test/env/private/key/vault/note/trial DB/SDK/native/WSL/API/auth/order/AI/server 효과는 없다.

## 1. 시작할 수 있는 실제 구현 단위

T1 existing GET client+budget additive API를 먼저 동결하고 T2 producer가 소비한다. T3 pure projection은 새 파일만 쓰므로 T1과 독립 진행할 수 있다. native T4의 별도 binding design/검토와 E16 T5는 그 다음 작업이며 T1–T3가 이것 때문에 멈추지 않는다. 구현 담당자는 아래 고정 결정을 다른 transport/clock/group/qualification 방식으로 임의 교체하지 않는다.

root의 착수 gate는 actual checkout/AGENTS/origin/branch/HEAD/dirty 소유 확인, 실제 adoption full SHA와 bytes pinning, 새 independent R2 preflight다. 이번 문서는 그 증거를 대체하지 않는다. 기존 frozen definition/catalog, A04 소스, legacy LINEAR permissions, account/environment writer lock을 보호한다.

## 2. preflight 변경 지도

| Finding | R2 고정 해결 | 구현 소유 |
|---|---|---|
| F01 | public trust_env=False+verify=True/certifi bundle/no env·proxy·CA override inputs, legacy unchanged; source label→mainnet/testnet 명시 매핑 | T1 |
| F02 | original response 내부에서 synthetic 이전 origin/path/query/status와 last-byte wall/mono capture; app metadata 증거 한정 | T1 |
| F03 | exact same parent200/60+active token, reuse-or-one-open32/20, original guards/counters/deadline 보존, native2N+wallet1 capacity preflight | T1/T2; T4 wiring |
| F04 | caller now 제거, internal UTC+mono, exact positive timedelta/overflow, response-age+mono strict expiry, no clamp/offset/time GET | T1/T2 |
| F05 | loop/open/overlap 먼저, accepted refresh invalidation-before-validation, retire marker drain, closed-before-await/no late publish/cancel propagation | T2 |
| F06 | exact frozen DTO fields/opaque actual issuer handle, fixed code whitelist/classification, raw context 없는 handler-outside raise | T1/T2 |
| F07 | exact route identity encoding only/length+stream64KiB/retained3*65536*N, no total process-memory claim; legacy1MB/numbers/errors 유지 | T1/T2 |
| F08 | existing private helper signature/known-source semantic regression pin, source-neutral adapter only, no runtime file SHA I/O | parallel T3 |
| F09 | private real target witness arm, pending1/issued1<=15keys registry, exact publish/revoke map; separate binding design before T4 | T4 |
| F10 | native comparison whitelist+source-side market/leverage; same-session signed fee attribution; timestamp/info exclusions | T4 |
| F11 | public MAINNET/TESTNET both explicit; MATCH_SUPPORTED_FIELDS_V1 DEMO→PUBLIC_MAINNET comparison, policy selection later, mismatches fail | T1/T2 definitions; T4/T5 activation |

## 3. T1 — exact envelope와 narrow budget group API

**단독 편집 소유:** `account/bybit_readonly.py`, `execution/monitoring_budget.py`; 신규 pure `account/instrument_envelope.py`; 관련 account/budget focused test. T2/T3는 이 파일을 동시에 수정하지 않는다.

순서:

1. 기존 constructor/read/signing/host/time/retry/decoder/budget hooks 호출과 반환을 baseline으로 보존한다. 공통 HTTP 초기화/SEND/body reader를 route policy로 확장하며 새 REST stack을 만들지 않는다.
2. budget에 opaque ReadGroupToken과 exact frozen ReadGroupView, reuse_or_open_receipt/require_active_group을 additive 제공한다. 기존 receipt()의 limit/guards/진입 의미는 그대로다. active group에 join하면 original deadline/counter/guards를 유지하며 nested reset이 없다. ended group은 shared active=False marker로 child task에 남은 ContextVar 참조에서도 거절한다. 같은 actual active group을 native child가 공유하는 것은 허용한다.
3. public-only factory에 explicit PUBLIC_MAINNET→mainnet/api.bybit.com 및 PUBLIC_TESTNET→testnet/api-testnet.bybit.com mapping, trust_env=False+verify=True와 HTTPX standard certifi bundle을 적용한다. OS system roots라고 잘못 부르지 않는다. SSL_CERT_* /proxy/env CA override 및 arbitrary CA path/env/resolver/custom URL/default headers/auth/cookie 입력 금지, dummy credentials 없음. 표준 dependency TLS CA bundle 읽기는 허용하되 credential/settings/vault/note/private 파일은 읽지 않는다. legacy init의 trust_env는 변경하지 않는다.
4. exact key/budget/group route를 제공한다. actual original response metadata와 마지막 identity body byte 직후 UTC/mono를 synthetic response 생성 전에 capture한다. one accepted internal result에서 legacy tuple/new evidence를 투영한다. transport injection은 nonproduction provenance다.
5. exact route만 Accept-Encoding identity; nonidentity encoding reject beforedecode; declaredContentLength>65536 또는 실제 bytes>65536 reject. nested duplicate/int/float/constants/tree bounds를 확인하고 immutable original/canonical bytes를 보존한다. expected exception을 고정 코드로 분류하여 handler 밖에서 raise한다.

**필수 focused 검증(실제 network0):**

- public secret env/credential/proxy/resolver/settings/vault/note/private-file/native access가 있으면 test 실패. dependency의 표준 TLS CA bundle 읽기까지 전역 차단하지 않는다. mainnet/testnet explicit mapping·legacy demo unchanged; public signed/private/path/URL/default product/no key 입력은 send0.
- original response.request origin/path/query가 송신과 다른 fake response를 반환하면 실패. synthetic response가 송신 request를 재사용해 mismatch를 숨기지 못함. redirects/history/userinfo/non443 reject. accepted metadata는 original last-byte stamp이며 consumer가 늦게 읽어도 receive 재기록 없음.
- active group exact same object/token, none/open/reuse/ended/foreign/subclass/protocol-mock, nested-reset 금지, already-used logical capacity/deadline/guards 유지. logical1당 retry physical1..3이며 early send permission denial은 physical 증가 없음.
- literal wire duplicate를 retCode/result/time/category/symbol/filter 각 depth에 넣어 실패. malformed utf8/NaN/Infinity/float/int overflow/string/node/depth/container/canonical-size/surrogate boundary. canonical decoder가 original body를 합성 원문으로 바꾸지 않음.
- identity/empty/absent encoding만 허용; gzip/br/deflate/comma list/oversized Content-Length reject before decode. length 거짓/누락이어도 streamed65537번째 byte reject. success/retry/quota/error/limit/cancel 모두 response close.
- 403/429/451/quota terminal, retry statuses 최대3와 deadline 중단, fixed code whitelist 모든 mapping, invalid error code constructor reject. raw body/retMsg/URL/key/provider exception이 DTO repr/errors/args/context chain에 없음. CancelledError 그대로.
- legacy read tuple/dynamic ret_* errors/1MB JSON decimal numbers/signing/host/server anchor/monitoring hooks behavior regression. 새 strict route가 old instrument/risk/history reads를 전역 강화하지 않음.

**완료 gate:** 실제 source bytes 동결, focused GREEN+Ruff/strict mypy, 독립 task review. 예상 public evidence는 provenance view일 뿐 current catalog authority가 아니며 T2가 owner다. T1 source-only checks는 실제 public TLS/응답 수락 증거가 아니다.

## 4. T2 — bounded public collector와 현재 receipt

**단독 소유:** 신규 `market_data/instrument_producer.py`, 신규 focused test. 기존 adapter/base/definition/catalog 무수정. 실제 T1 API 동결 후 소비한다.

production constructor는 explicit source environment와 internal actual public factory만 사용하고 caller clocks/client/factory/receipt 입력은 받지 않는다. 테스트 전용 private route는 fake transport/clocks를 허용하되 production require_current가 거절한다. no invented caller trusted flag.

refresh 구현 순서는 SPEC transition 그대로다: owner loop/open/overlap 검사→accepted generation을 먼저 retire/새 pending marker→explicit keys1..16/TTL/budget 검증→같은 group reuse-or-one-open와 remaining N logical preflight→canonical key order sequential exact GET→각 await 후 exact slot/gen/open/source/clocks/group 확인→frozen parser/catalog→동기 publication. invalid accepted refresh도 old current를 부활시키지 않는다.

internal UTC wall/mono가 freshness authority이며 require_current public now 인자는 없다. TTL exactpositive timedelta/overflow 검사. row response-age-at-receive와 elapsed mono를 exact rational로 계산하여 strict expiry를 보존한다. new GET/time anchor/skew offset/clamp/TTL 자동 증가가 없다. generation을 폐기해도 old pending coroutine이 drain할 때까지 slot marker를 유지한다. close는 first await 전에 closed/revoke, 단일 owner cleanup task에서 HTTP close를10초 bound로 수행하고 shield await한다. caller 취소는 전파하되 own cleanup은 진행하며 외부 refresh task cancel/join을 하지 않는다. repeated close는 같은 task만 사용한다.

**필수 focused 검증:**

- explicit SPOT/LINEAR/mixed same symbol distinct keys; invalid/duplicate/empty/>16 inputs send0. source/TTL defaults 없음. N logical/max3N physical exact, query category+symbol only, no page/time/auth/fee/hidden refresh.
- current complete all-or-nothing, any key failure/missing/multirow/cursor/status/quote/settle/filter/prelisting future/expiry reject. 마지막 key 완료 시 earliest row가 만료되면 no current.
- 같은 revision 새 refresh는 old handle reject. copied/foreign/forged/serialized/source_id/hash-only handle reject. full view bounds/type/tuple correspondence와 all frozen catalog 재검증.
- successful refresh 뒤 invalid accepted refresh가 old current를 retire. active refresh 중 invalid competing caller는 OVERLAP이며 기존 operation/generation을 건드리지 않음.
- receive await 중 retire→old coroutine no publish; pending marker drain 전 새 refresh OVERLAP; drain 뒤 explicit refresh 성공 가능. close→영구 closed before await; cancel/HTTP close failure/repeat close에서도 reopen/late publish 없음. caller CancelledError 전파.
- wall/mono rollback/NaN/Inf/response>receive, TTL bool/subclass/zero/overflow, strict equality expiry, response-age-at-receive가 publish에서 초기화되지 않음. backward caller now로 우회할 public API 없음.
- existing active group original logical/deadline/guards를 보존. N slots 부족이면 I/O0; terminal budgets를 새 parent/group으로 회복하지 않음. source and account clients가 same parent에 join할 수 있어도 account auth를 collector가 시작하지 않음.
- retained current1 또는 pending1, per generation body+canonical+row<=3,145,728 bytes, replaced/failed refs 정리. no unbounded diagnostic/history registry; external held views와 transient object overhead는 별도라 total process-memory proof를 주장하지 않음.
- require/retire는 network/file/DB0. nonproduction test instance가 production receipt authority를 발급/검증하지 못함.

**완료 gate:** T1+T2 focused covering checks/독립 review/whole review/필수 CI. 실제 운영 source 수락과 E16 activation은 별도 미입증으로 보고한다.

## 5. T3 — T1과 독립 pure source-neutral projection

**신규 파일만 소유:** `market_data/instrument_source_projection.py` 및 focused test. frozen definition/catalog 수정 없음. parser/rule/type/semantic payload를 재작성하지 않는다.

exact key→exact dict/symbol→entire canonical bounds→`_parse_spec(row,expected)`→`_spec_revision(expected,spec)`의 실제 위치 인자 호출을 고정한다. exact frozen SupportedInstrumentProjection은 기존 spec/native ID/parser+rule+semantic revisions/raw canonical bytes를 담고 mutable row/source/time/auth/current 권한은 없다.

검증은 실제 helper signatures 및 fixed known rows/revisions/scale를 pin한다. key/row identity/unknown product/subclass/oversize/malformedcaps/prelisting/low Decimal context cases를 포함한다. helper의 새 signature나 의미가 바뀌면 review/regression failure가 나고 fallback parser를 자동 선택하지 않는다. runtime file SHA read/clock/native/HTTP/file side effect 없음. T3 결과는 DEMO source label을 발급하지 않으며 T4가 origin/account를 별도로 입증한다.

**완료 gate:** focused pure checks/Ruff/mypy/독립 review. T1/T2와 independent new-file ownership이며 실제 adopter는 source SHA와 dependency를 review 시 pin한다.

## 6. T4 — 별도 binding design 후 실제 LINEAR capture

T4 시작 전 별도 작은 binding 설계에서 T1 API와 기존 signed fee accepted metadata hook, actual `_cache_instruments`/superclass await/wallet ACK 위치의 exact call graph를 고정하고 독립 review한다. 이 준비 작업은 T1–T3의 착수 blocker가 아니다. R2 SPEC의 registry fields/arm/revoke/whitelist를 약화하지 않는다.

**예정 편집 소유:** account_binding/native_monitor_http/native_monitor_client, 신규 pure instrument_receipts, 최소 actual assembly arm wiring와 관련 tests. worker/native_node는 actual target witness 연결에 꼭 필요한 지점만. 기존 history/risk/permissions/lock 변경 없음.

- constructor UNARMED; existing trusted arm_monitoring의 target witness/current binding/exact assembly-client 검사를 통과한 private wiring으로만 session registry 등록. public arbitrary assembly registration 금지.
- single current assembly의 pending1 또는 issued1, LINEAR<=15 keys. current group remaining_gets>=2N+1와 sameparent check 후 기존 fee+instrument2N 및 wallet1 실행. provider repeat initialize/재arm은 reject+revoke, group reset/split 없음.
- same signed fee accepted response의 origin/time/bodydigest/rates/counters와 exact instrument envelope/T3 projection을 capture하고 existing converter1회만 호출. extra GET0. public fee fiction 없음.
- baseline cache↔fresh provider 기존 drift 검사 보존→source whitelist↔native 비교→super cache await→actual updated entries/currentowner recheck→wallet/account ACK→sync issuance. timestamps/info 제외 이유와 source-side marketcap/leverage 비교를 고정.
- auth refresh/close/credential/client mismatch/binding expiry/clock failure/native fault/retire/disconnect/assembly-channel retirement/adapter cancel/provider repeatinit/public current retirement/target/mapping-policy invalidation에 slot revoke. session 살아 있음만으로 channel retirement를 무시하지 않음.

검증에는 exact existing2N+wallet1/extra0 비용, N15/newgroup31 logical 통과와 N16 send0 실패, already-used group capacity, falseoriginalorigin/fee mismatch/precision drift/marketcap-only or leverage-only drift/native constants vs venue rule 구분, originalbaselinecache와 actualupdatedcache 혼동, pending before arm/no issue before finalACK, all revoke paths, clone/currentgeneration mismatch가 포함된다. 실제 pinned native Linux 검사는 root의 허용된 exact CI에서 수행한다. normal writer factory/SPOT source provenance는 이 범위 수락이 아니다.

## 7. T5 — product와 실제 사용 연결

E16 실제 coordinator/worker가 product/scope/policy/permission/strategy/mapping/current source/native receipt와 account/environment lease/channel generation을 소유하고 PLAN/SUBMIT 및 모든 await 후 require한다. 미구현 coordinator를 qualification boolean/seal로 대체하지 않는다.

운영 입력은 explicit universe, TTL, mapping profile policy selection, capital/loss/fee/caps/approved strategy다. 문서 fixture 숫자를 기본 운용값으로 승격하지 않는다. native SPOT converter/CASH/fee/debt/owned inventory/history/protection/restart와 normal writer source capture는 별도 exact tasks로 완료한다. same account/environment 두 product writer 금지와 legacy LINEAR allowlist를 보존한다.

root가 source independent task/whole review→exact hosted CI와 기존 native36/restart6/restored12 unskipped→merge/readback/mainCI→frozen source/import pinning을 검증한 뒤, 기존 권한 범위에서 actual public/DEMO/native/UI/server acceptance를 수행한다. 이 후보는 external effects를 추가 승인하지 않는다. full63 완료와 현재 runtime activation은 계속 별도 gate다.

## 8. 비용·증거와 인계

public collector N<=16: N logical/max3N sends. native startup N<=15:2N+wallet1 logical/max6N+3 sends. receipt capture/compare/currentlookup/retire extraGET0. same group public+native sameN은3N+1<=32(최대10)이며 이미 소비한 slots/parent200/60/20초 group deadline를 모두 검사한다. authrefresh2/max6, PRE/POST/history/stream 기존 비용은 별도다. 역사적153 sends 합은 full-budget success proof가 아니다.

handoff는 actual adopted full SHA/tree/clientblob/frozen bytes, step별 changedfile ownership, exact API/constants/error whitelist/group semantics, focused and whole CI receipts, source/clock/budget/generation/retirement tests, actual vs fixture provenance, 운영입력 여부와 T4/T5 blockers를 기록한다. 새 independent R2 preflight가 끝나기 전 상태는 PROPOSED다. 이 문서 작성 시 구현/시험/CI/실제 source/native 수락은 모두 NOT_IMPLEMENTED/NOT_RUN이다.


