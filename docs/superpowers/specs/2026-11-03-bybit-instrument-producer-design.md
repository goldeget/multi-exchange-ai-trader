# Root adoption — Bybit instrument source producer

## Root HTTPX association clarification (binding)

Installed and locked HTTPX0.28.1 primary source shows AsyncClient._send_single_request assigns response.request=request; standard AsyncHTTPTransport returns a Response without an independent request association. Therefore response_origin means the original HTTPX response's application-associated origin, captured before this application's synthetic response, not an independently supplied transport/server origin. Validate configured/sent and HTTPX-associated method/origin/path/query, no redirects, and preserve standard verify=True host/TLS validation; do not add a transport wrapper solely to recover injected pre-normalization test metadata. A malformed original association can be tested at the send-return boundary with a clearly labeled fake; a MockTransport mismatch overwritten by HTTPX cannot be claimed as detectable production evidence. Preserve such test failures as dependency/harness characterization, not genuine product RED. No second transport stack or new TLS attestation is added.


## Root T3 interface closure (adoption clarification)

T3 remains fully independent of new T1 modules. It reuses frozen CatalogRejected and its fixed codes, not InstrumentSourceError. Builder propagates frozen helper fixed codes; wrong nonexact row -> CATALOG_INPUT_INVALID and wrong exact row symbol -> CATALOG_SCOPE_MISMATCH. SupportedInstrumentProjection is a required-seven-field frozen dataclass with constructor validation and public validate_supported_instrument_projection(value)->None; both use one nonrecursive exact validator, enumerate trusted class fields, and rederive raw/spec/digests from bounded canonical bytes. DTO malformed/inconsistent/forged/missing fields -> CATALOG_OBSERVATION_INVALID, retaining CATALOG_RESOURCE_LIMIT for actual bounded-resource rejection. Reuse frozen _decode_row for canonical raw revalidation and _validate_spec/_spec_payload as needed; no parser/decoder/hash duplication, no T1 dependency, no runtime source-file reads. This explicitly extends the documented private helper dependency, with regression pinning and future maintenance cost. T4 later maps these pure fixed errors at its owner boundary.


ADOPTED for implementation of T1, T2 and T3 only, after EXECUTABLE final independent preflight. T4/T5 below are explicit subsequent plans, not tasks whose completion is claimed by this plan. The historical candidate wording retained below records preparation ownership; this adoption supersedes PROPOSED status for T1–T3 only.

Fixed source baseline: 74b66c53c8c38299c03f48a99145dddbce38ec61; branch gpt/bybit-source-risk-producers-20261103; origin https://github.com/goldeget/multi-exchange-ai-trader.git. Fresh origin/main remains46d4d13de3f990e875c34bba3b4c34ffc3f81609. PR29 exact corrected-source CI remains a separate running gate, not accepted main yet. Frozen source blob pins: bybit_readonly.py54f73787093d3ec1c6f3ca87e3a2c15cba4ae2bd; monitoring_budget.pyae0ec5db788b503285860b9bb4253ddc96b50a11; instrument_definition.pyf8c0ac9d72e56a091da781ee6eac3a05b73c1203; instrument_catalog.py95e3c49e94ddaca757780696e2ba2cb3957f2772. Definition SHA2564c4e1c60ef0ff9c564478503cdce35198d1e2edabcd6730a20761aad8e31a6a5. No applicable AGENTS found at ancestors or repository search; tracked tree clean before adoption. Root serializes all Git and preserves original worktrees/evidence.

Binding spec is docs/superpowers/specs/2026-11-03-bybit-instrument-producer-design.md; execution plan is docs/superpowers/plans/2026-11-03-bybit-instrument-producer.md. External frozen originals and A02_TRUSTED_PRODUCER_FINAL_PREFLIGHT_20261103.md are preserved. Root read original documents, complete R2 replacement changes (recovering truncated middle explicitly), full R2 plan and final preflight.

Implementation clarifications from final preflight are binding: retain original fixed HTTP/quota cause despite parent terminal classification; quota hook runs before encoding/body rejection; retain safe decoder failure while terminalizing parent. T1/T2 are serial source collection: shared parent start/end counters are not per-read counters under interleaving; reject invalid interval without counter rewriting, and concurrent T4 evidence needs its own later binding design. No fixture or injected test route produces production require_current authority. All exact record enumeration uses trusted classes, never caller instance dataclass metadata.

T1 ownership: src/multi_exchange_trader/account/bybit_readonly.py, new account/instrument_envelope.py, execution/monitoring_budget.py; tests/account/test_bybit_readonly.py, new tests/account/test_instrument_envelope.py, new tests/execution/test_instrument_read_budget.py. Relative module paths below are under src/multi_exchange_trader. Covering existing budget consumers: tests/execution/test_native_monitor_http.py, test_monitoring_readback.py, test_monitoring_receipts.py, test_restored_monitoring_worker.py. T3 ownership: new market_data/instrument_source_projection.py and new tests/market_data/test_instrument_source_projection.py only. T2 follows frozen/reviewed T1 and owns new market_data/instrument_producer.py and new test. No overlapping source edits.

Use existing fixed offline Python C:/Users/PC/.codex/tmp/trader-receipts-20261102-venv/Scripts/python.exe with PYTHONPATH=<R>/src and TRADER_CREDENTIAL_FILE,TRADER_ACCOUNT_PROFILE_FILE,BYBIT_ENV_FILE,AI_ENV_FILE,ANTHROPIC_ENV_FILE each literal off. Synthetic/offline meaningful RED, focused covering checks, separate Ruff/strict mypy, exact receipts and SHA freeze before independent task/whole review. Actual source acceptance, existing native36/restart6/restored12, exact whole hosted/main CI, actual DEMO and full product acceptance remain separate gates. No agent private/default-store/provider/network/native/WSL/order/paidAI/server effects or Git mutations. Root continues full63; this plan is not final program completion.

---

# A02 trusted producer — SPEC CANDIDATE R2

2026-11-03 KST. 상태 **PROPOSED / fresh independent preflight 필요 / 구현·시험·운용 수락 아님**.

R=`F:/자동매매프로그램/worktrees/engine-restart-recovery-20261102`. root 전달 최신 source 기준은 reviewed `74b66`에서 만든 `gpt/bybit-source-risk-producers-20261103`이며 수정된 definition hash의 전달 prefix는 `4c4e1c60`이다. field enumeration 두 줄 수정 외 GET client/budget는 unchanged라고 전달받았다. root가 채택 시 full SHA/hash와 frozen A02 및 unchanged client blob을 pin한다. 이 문서는 Git을 실행하거나 축약 SHA를 full SHA로 추정하지 않았다. PREPARATION 원본2개와149줄 PREFLIGHT를 보존하고 이 새 후보만 작성했다. 현재 GET client190–457, monitoring_budget 전체와 native arm/cache/connect를 읽었다. source/test/Git/private/env/key/vault/note/trial DB/native/WSL/API/auth/order/AI/server 작업 및 새 network 조회는 하지 않았다.

## 1. 채택할 구현 범위

T1은 기존 단일 SEND/retry machinery의 additive exact-envelope/public-only 확장과 narrow budget group API, T2는 bounded 실제 public collector/current receipt, T3는 독립 pure source-neutral projection이다. T1→T2와 병렬 T3를 먼저 구현할 수 있다. T4 LINEAR monitoring source/native receipt와 T5 E16 admission/실제 수락은 별도 다음 작업이며 T1–T3 구현 blocker가 아니다. frozen parser/catalog, A04, legacy LINEAR permission allowlist와 account/environment single writer를 수정하지 않는다.

source receipt는 실제 in-process issuer가 자기 GET으로 만든 현재 관찰을 검사하는 수단이다. DTO/hash/label/approved boolean이 auth, native compatibility, 주문 admission 또는 full63 완료를 만들지 않는다.

## 2. F01/F11 — public 환경과 mapping을 확정

| public source 인자(필수, default 없음) | 기존 client environment | exact HTTPS origin |
|---|---|---|
| PUBLIC_MAINNET | mainnet | https://api.bybit.com:443 |
| PUBLIC_TESTNET | testnet | https://api-testnet.bybit.com:443 |

public factory는 `httpx.AsyncClient(trust_env=False, verify=True, ...)`로 HTTPX standard TLS 검증과 dependency의 certifi trust bundle을 사용한다. 이를 OS system roots라고 부르지 않는다. SSL_CERT_FILE/SSL_CERT_DIR 및 proxy/env CA overrides를 사용하지 않고 arbitrary CA path를 받지 않는다. 표준 HTTPS의 dependency CA bundle 읽기는 허용되며 credential/settings/vault/note/private file 읽기와 구분한다. env/proxy/CA override/resolver/credential-file/headers/auth/cookie/hooks/URL 입력을 받지 않는다. HTTPX 인증 기본값을 우회하는 dummy key/secret를 만들지 않는다. legacy signed constructor의 trust_env/proxy/TLS/credential/host/decoder/반환은 그대로 둔다. public source label과 legacy environment는 별개 필드이며 위 명시 매핑만 허용한다. regional host/fallback/VPN/proxy 자동 설정이 없다.

DEMO account source는 실제 `https://api-demo.bybit.com:443`/environment demo다. CatalogSource에는 DEMO가 없으므로 DEMO row를 PUBLIC_MAINNET/FIXTURE로 라벨링하지 않는다. 정의할 mapping profile은 **MATCH_SUPPORTED_FIELDS_V1**이며 대상은 `DEMO -> PUBLIC_MAINNET`뿐이다. 이는 T3 supported spec fields/raw spelling/scale 및 source rule revision을 현재 양쪽 관찰로 exact 대조하고 mismatch는 fail하는 비교 규칙이다. REST origin/source equivalence 보증이 아니다. 운영 policy가 profile을 명시 선택하기 전 binding 활성화가 없다. TESTNET을 DEMO에 자동 매핑하지 않는다. 실운용 universe/TTL/policy selection은 후속 explicit input이다.

## 3. F03 — 같은 parent와 하나의 실제 group

기존 MonitoringReadBudget의 exact 객체가 parent200 physical attempts/60초를 소유한다. 새 exact route에는 이 객체와 active group의 identity proof가 필수다. Protocol 흉내/subclass/다른 budget/그룹 없는 경로는 send0 거절한다. account client와 public client의 `_operation`에 같은 parent 객체를 연결한다. parent를 collector/key/retry/phase별로 생성하지 않는다. public standalone source owner도 명시 생성한 budget 한 개를 전달하며 budget 자체는 account auth가 아니다.

허용 narrow additive API를 다음으로 고정한다(legacy receipt()의 기존 semantics는 바꾸지 않음):

```python
# execution/monitoring_budget.py
@contextmanager
def reuse_or_open_receipt(self) -> Iterator[ReadGroupToken]: ...
def require_active_group(self, token: ReadGroupToken) -> ReadGroupView: ...
```

ReadGroupToken은 caller construct 불가이며 exact `_ReceiptBudget` 한 개의 token identity다. legacy receipt()가 만든 group도 내부 token 및 active=True marker를 갖도록 additive default fields를 둔다. receipt() finally는 marker를 False로 닫고 ContextVar를 복구하되 legacy read의 limit/validation 의미는 바꾸지 않는다. ReadGroupView는 frozen exact `deadline: float`, `logical_gets: int`, `remaining_gets: int`만 갖는다. token/view에 reset/수정 메서드가 없다.

reuse_or_open_receipt는 (a) active group이 있으면 같은 token/deadline/logical counters/guard/owner_guard를 보존하고 그대로 yield, (b) 없으면 기존 receipt()를 정확히 한 번 열어32 logical GET/20초 group을 만들고 종료 시 닫는다. active group을 중첩 receipt()로 교체하지 않는다. 새 producer generation guard는 각 await 전후 직접 검사하며 기존 group guards를 교체하지 않는다. require_active_group은 exact self/current group/token identity, group active=True marker와 기존 budget.check를 검사한다. 복제 group/token 또는 부모가 다른 token은 실패다. native child task가 복사한 ContextVar로 같은 실제 active group을 공유하는 것은 허용하지만 원래 group이 종료되면 shared active=False marker 때문에 child context의 낡은 참조도 실패한다. 새 route는 send 직전과 await 복귀마다 같은 budget/group proof를 다시 확인한다. legacy broader Protocol callers는 유지한다.

public refresh 전체 N=1..16은 group 한 개를 소비한다. begin_read는 logical1을, before_attempt는 실제 send1을 계수하며 retry는 group을 재진입하지 않는다. 전체 key 수만큼 남은 logical slots가 없으면 I/O 전 실패한다. remaining physical/deadline는 기존 budget이 매 attempt 검사하며 reset하지 않는다.

T4 native provider startup은 **fee+instrument2N와 wallet1을 같은 현재 group**에서 소비한다. `2*N+1 <= remaining_gets`를 native initialize 전 preflight한다. 새 빈 group에서도 native N<=15이며 N16은 send0 reject다. 기존 group에 public refresh가 이미 소비한 N개가 있으면 남은 slots로 다시 판단한다(같은 N이 모두 연결될 경우3N+1<=32, 최대10). group 자동 분할/reset/limit 증가로 우회하지 않는다. PRE/POST 등 별도 group은 기존 phase가 명시 소유할 때만 허용하며 동일 parent200/60은 유지한다.

## 4. F02/F07 — 단일 SEND와 original response capture

기존 `BybitReadOnlyClient.read()`는 `(result, server_ms)`를 그대로 반환한다. 공통 send/retry 루프와 body reader에 route policy만 추가하며 새 HTTP stack이나 중복 SEND를 만들지 않는다. 새 API:

```python
BybitReadOnlyClient.for_public_instruments(
    *, source_environment: Literal['PUBLIC_MAINNET', 'PUBLIC_TESTNET'],
    transport: httpx.AsyncBaseTransport | None = None,
) -> BybitReadOnlyClient
async def read_instrument_envelope(
    self, key: InstrumentKey, *, budget: MonitoringReadBudget,
    group: ReadGroupToken,
) -> InstrumentEnvelopeRead: ...
```

public factory는 internal public-only role을 만들고 새 exact envelope API의 instruments GET/unsigned/category+symbol만 허용한다. public-only instance에서 legacy read/read_pages/anchor_server_time으로 우회하는 호출은 send0 고정 거절하며 기존 legacy instance의 해당 메서드 의미는 보존한다. 기존 signed client도 이 새 unsigned route를 쓸 수 있으나 actual demo origin을 그대로 기록한다. route의 budget class는 method 내부 lazy import로 검사해 account module import에 새 native/session dependency를 도입하지 않는다. arbitrary client/factory/receipt를 collector production constructor에 주입하지 않는다. transport 인자는 offline testing용이며 주입되면 내부 provenance가 nonproduction으로 고정된다. caller가 production bool을 공급할 수 없다.

새 route만 `Accept-Encoding: identity`를 송신한다. original response의 Content-Encoding이 없거나 empty/identity이면 허용하고 그 밖의 비어 있지 않은 값은 decode 전 실패한다. comma list/multiple encoding/gzip/br/deflate/비정규값을 허용하지 않는다. Content-Length가 있으면 단일 비음수 ASCII decimal 값으로 검사하고65536 초과면 stream 소비 전 거절한다. 실제 stream byte count도 항상65536을 적용한다. legacy read는 기존1MB/decompression/number/error 의미를 보존한다.

original `response`를 가진 `_bounded_response` 내부에서 synthetic response 생성 **전에** 송신 request와 original response.request의 scheme/host/effective port443/userinfo 없음/path/정렬 query/category/symbol/method, redirect history 없음/status를 검사한다. exact request path는 `/v5/market/instruments-info`이고 params는 sorted tuple(category,symbol), 다른 query는 금지다. 마지막 body byte 직후 내부 UTC wall/mono receive stamp를 찍고 나서 immutable metadata를 구성한다. synthetic request=request의 origin을 actual response 증거라고 사용하지 않는다.

status는 원래 quota guard를 반드시 통과한다.403/429/451 및 quota exhaustion은 terminal이다. 기존 retry 대상으로 최대3 sends/read이며 retry/timeout/parse/limit/cancel 등 모든 exit에서 original stream/response는 finally close한다. accepted envelope는 원래 full bytes를 한 번 strict decode하며 legacy tuple/new immutable evidence는 같은 내부 결과의 projections다. 새 `retCode=0/result/time` envelope 합성이 없다.

origin 증거는 application request/response metadata 검사다. TLS peer attestation 또는 cryptographic 서버 신원 증거가 아니다. identity stream도 HTTPX 내부 chunk/allocation과 JSON tree가 생길 수 있으므로 절대 process-memory/preallocation 상한을 주장하지 않는다.

## 5. F06 — exact immutable DTO/handle/errors

새 순수 DTO module `account/instrument_envelope.py`의 모든 값은 exact frozen dataclass다. subclasses/extra coerced 값/unsafe repr hooks는 거절하며 bytes는 repr=False다. 단순 constructor 성공은 source authority가 아니다.

- `HttpsOrigin(scheme: str, host: str, port: int)`: scheme exact https, port exact int443, host는 위3개 literal만. actual/sent origin은 각각 독립 포착하여 동일성 검사.
- `InstrumentEnvelopeRead(key: InstrumentKey, method: str, endpoint: str, params: tuple[tuple[str,str],...], sent_origin: HttpsOrigin, response_origin: HttpsOrigin, response_status: int, request_started_at: datetime, received_at: datetime, started_mono: float, received_mono: float, response_time: datetime, decoded_body_bytes: bytes, decoded_body_sha256: str, canonical_envelope_bytes: bytes, accepted_attempt: int, physical_start: int, physical_end: int, logical_ordinal: int)`.
- method GET/path fixed/params(category,symbol) exact helper agreement/status int200. times exact datetime with exact datetime.timezone UTC normalized, response<=receive, start<=receive. mono exact finite float>=0, start<=receive. digest lowercase64hex, attempt int0..2, counters exact ints0..200 with1<=end-start<=3, logical ordinal1..32. original body nonempty<=65536, canonical envelope<=65536. raw body hash is application content bytes hash, not TLS wire signature. no mutable tree/HTTP response/session/budget/native/cache object is in DTO.
- `CatalogReceiptView(catalog: InstrumentCatalog, source_environment: SourceEnvironment, source_id: str, envelopes: tuple[InstrumentEnvelopeRead,...], issued_at: datetime, issued_mono: float)`: exact catalog consistency/canonical key order/1..16 envelopes/source+origins+key+time+hash correspondence and fixed source ID grammar. source_id is one UUID hex generated per accepted refresh and never authority by itself.
- `CurrentCatalogReceipt`: exact opaque handle, public constructor raises fixed error, repr contains fixed class name only. registry lookup uses `is`, not caller equality/hash/fields. view has no current/qualified boolean.

new `InstrumentSourceError(ValueError)` exposes only whitelist `.code`, str/args=(code,), rejects unknown/nonexact code with fixed ValueError('INVALID_INSTRUMENT_SOURCE_ERROR_CODE'). Prefix below is `INSTRUMENT_SOURCE_`; whitelist exactly:

```
INPUT_INVALID RESOURCE_LIMIT SOURCE_UNSUPPORTED ORIGIN_MISMATCH RESPONSE_INVALID
JSON_INVALID ENCODING_UNSUPPORTED PROVIDER_REJECTED HTTP_REJECTED ACCESS_RESTRICTED
RATE_LIMITED NETWORK TIMEOUT BUDGET_REQUIRED BUDGET_EXHAUSTED GROUP_REQUIRED
CLOCK_INVALID EXPIRED RETIRED CLOSED OVERLAP LOOP_REQUIRED RECEIPT_REQUIRED
INJECTED_TRANSPORT SELECTION_INVALID NATIVE_MISMATCH NATIVE_SCOPE NOT_ARMED
INCOMPLETE NATIVE_CAPACITY
```

classification: legacy timeout→TIMEOUT; proxy/dns/network→NETWORK; response_too_large→RESOURCE_LIMIT; invalid_json→JSON_INVALID; redirect→ORIGIN_MISMATCH; BybitBlocked/http403/http451→ACCESS_RESTRICTED; ret10006/http429/quota→RATE_LIMITED; any other integer nonzero provider retCode→PROVIDER_REJECTED; other non200→HTTP_REJECTED; operation overlap→OVERLAP; clock/deadline or attempt/logical-budget fixed terminal error→BUDGET_EXHAUSTED (producer own clock failures→CLOCK_INVALID). none/foreign/subclass budget→BUDGET_REQUIRED; wrong/absent group→GROUP_REQUIRED. frozen CatalogRejected input/selection/source/time/expired/resource errors map respectively INPUT_INVALID/SELECTION_INVALID/ORIGIN_MISMATCH/CLOCK_INVALID/EXPIRED/RESOURCE_LIMIT; remaining frozen structural/spec/provider failures map RESPONSE_INVALID except provider-rejected→PROVIDER_REJECTED. no raw code interpolation.

새 route/producer는 expected exception만 fixed code로 분류한다. sanitized failure code만 handler 밖으로 전달하고 handler를 벗어난 뒤 새 error를 raise한다. `raise from None`만으로 raw `__context__`가 없어진다고 하지 않는다. raw-bearing exception/traceback/request/body/retMsg를 slot/result/log에 보관하지 않는다. CancelledError는 그대로 재전파한다. programming errors는 성공/retry로 바꾸지 않으며 expected transport failures와 구분한다. legacy read의 기존 dynamic ret_* code, decoder/return/signing 의미는 변경하지 않는다.

## 6. strict decoder와 retained resource(F07)

새 route에서만 raw size 확인 뒤 json.loads duplicate object_pairs_hook, bounded integer text<=20/abs<=2**63-1, reject float/constants를 적용한다. 모든 depth duplicate를 dict 생성 전 거절한다. then frozen canonical helper가 root depth8/nodes4096/container128/string128/surrogates/canonical65536을 확인한다. canonical row 전용 `_decode_row`를 arbitrary raw-envelope decoder로 오용하지 않는다. full original body와 canonical bytes는 구분한다. JSON tree는 즉시 소비 후 release하고 반환/진단 history로 저장하지 않는다.

한 issuer에는 current1 또는 pending1의 generation만 retained된다. accepted refresh는 old current raw refs를 먼저 버린다. per key body<=65536 + canonical envelope<=65536 + catalog canonical row<=65536, N<=16이므로 해당 retained bytes 합<=3*65536*16=3,145,728 bytes. 같은 row/projection은 immutable bytes 참조를 공유한다. 구 generation을 caller가 외부 view로 보유하면 그 view도 개별 bound를 지키지만 caller 전체/process memory 총합은 issuer가 보장하지 않는다. 실패/retire/close/replacement에 old raw refs를 해제하고 무제한 receipt/fee/diagnostic history를 만들지 않는다. transient JSON tree/Python object overhead/HTTPX chunk는 이 bytes 합에서 제외됨을 명시한다.

## 7. F04/F05 — producer API, freshness, state

```python
class BybitInstrumentCatalogProducer:
    # production constructor: source_environment mandatory; no arbitrary clocks/client/factory
    async def refresh(self, *, declared_universe: tuple[InstrumentKey,...],
                      max_age: timedelta, budget: MonitoringReadBudget) -> CurrentCatalogReceipt: ...
    def require_current(self, receipt: CurrentCatalogReceipt, *,
                        expected_revision: str) -> CatalogReceiptView: ...
    def retire(self) -> None: ...
    async def aclose(self) -> None: ...
```

public caller now는 없다. production owner는 `datetime.now(UTC)`와 `time.monotonic()`를 사용하고 모든 timestamp/mono/deadline type/finite/order/rollback를 검사한다. 내부 test-only factory만 fake clocks/transport를 허용하고 그 instance는 production require_current를 INJECTED_TRANSPORT로 거절한다. 테스트 내부 verifier가 같은 state machine을 검증할 수 있으나 production handle로 내보내지 않는다.

TTL은 exact timedelta>0이고 arithmetic overflow를 거절한다. 새 임의 금융 TTL 상한/기본값은 없다. datetime arithmetic과 budget deadline가 해당 입력을 안전하게 처리하지 못하면 fail한다. operational TTL은 policy 입력이다. 각 row의 server response<=received wall이며 future/skew를 clamp/offset하지 않는다. 새 market/time GET도 없다.

freshness는 frozen catalog의 wall-clock strict now<earliest(response,receive)+TTL 검사와 각 row의 monotonic effective age를 **둘 다** 통과해야 한다. row별 `age_at_receive_us=(received_at-response_time)`의 exact integer microseconds, `elapsed=mono_now-received_mono`를 사용하여 `age_at_receive + elapsed < TTL`을 요구한다. equality fail. float elapsed 비교는 두 finite float의 as_integer_ratio로 exact rational 차를 구하고 TTL integer microseconds와 cross multiply하여 반올림/clamp/재기산하지 않는다. publish에서 TTL 전체를 새로 주지 않는다. every await 복귀/send 전후/publish/require에서 clocks와 generation을 확인한다. rollback/NaN/Inf/overflow/expiry는 revoke/fail이며 stale fallback 없음.

정확한 issuer internal state: `_owner_loop`, `_closed: bool`, `_generation: object`, `_current: (handle,view,generation)|None`, `_pending: PendingSlot|None`, `_last_wall`, `_last_mono`, `_client` exact owned public client, `_close_task` owner-created cleanup task 또는 None. PendingSlot은 exact operation marker/generation/phase(VALIDATING/COLLECTING/REVOKED)/key tuple/TTL/source_id/budget/group/start clocks/현재 bounded envelope tuple만 갖고 caller task/exception/history를 보관하지 않는다. accepted-before-validation 단계에는 keys=(), TTL/budget/group=None이며 raw caller args는 slot에 보관하지 않는다. exact 검증 뒤에만 해당 typed fields를 채우고 COLLECTING으로 전환한다.

transition:

1. refresh 진입에서 owner loop→open→pending/concurrency를 먼저 검사한다. contender는 OVERLAP이고 active generation/current/pending을 변경하지 않는다.
2. accepted refresh는 **입력 검증 전** old current를 폐기하고 generation을 교체한 pending slot을 점유한다. invalid universe/TTL/budget이면 current 없음으로 끝난다. accepted operation만 old receipt를 revoke한다.
3. exact1..16 distinct keys, explicit source/TTL 검증 후 같은 parent/group에 join/open하여 전체 logical capacity를 preflight하고 canonical key order로 한 symbol씩 조회한다. partial publish 없음.
4. every await 후 closed=false, exact same pending object, exact generation, source/client/budget/group/clock를 확인한다. retire/close된 coroutine은 current를 게시할 수 없다. all responses를 frozen parser→catalog builder로 검증하고 await 없는 동기 구간에서 current 하나를 게시한다.
5. finally는 **자기 pending slot identity가 아직 같을 때만** pending을 비운다. revoke된 slot도 old coroutine이 자연히 bounded drain할 때까지 retained marker를 남기므로 새 refresh는 OVERLAP이다. unrelated slot을 지우지 않는다.
6. retire는 open이면 generation 교체/current revoke/pending raw refs release+revoked marker 유지. terminal close가 아니므로 old coroutine drain 후 새 explicit refresh를 허용한다. network/새 budget/자동 refresh 없음.
7. aclose는 first await 전에 closed=true/generation revoke/current clear/pending revoked marker를 설정한다. public HTTP client close를 owner-created 단일 cleanup task에서 실행하고 기술 cleanup timeout10초로 bound한다. aclose는 그 own task를 shield await하며 소유하지 않는 refresh caller task를 cancel/join하지 않는다. caller CancelledError는 그대로 재전파하되 own cleanup은 bounded 종료까지 진행한다. repeated close는 같은 cleanup task만 await하며 새 task/client를 만들지 않는다. in-flight request는 기존 deadline/HTTP close로 자연히 drain하고 게시 금지다. close/cancel/cleanup timeout 실패도 reopen 없음. HTTP close가 실패한 경우 resource release 성공을 주장하지 않는다.
8. require_current는 owner loop/open/production provenance→exact handle/current/generation→internal clocks/entire frozen catalog/envelopes/revision/source/TTL 순으로 검사한다. source_id/hash/semantic revision이 같은 copied old handle은 거절한다. lookup은 GET/DB/file0이다.

## 8. F08 — T3 pure adapter 확정

신규 `market_data/instrument_source_projection.py`:

```python
project_supported_instrument_row(row: object, *, expected: InstrumentKey) -> SupportedInstrumentProjection
```

순서: `_key_payload(expected)`→exact dict→exact str row.symbol==expected.symbol→`_canonical_json_bytes(row)` 전체 bounds→`_parse_spec(row, expected)` 위치 인자→`_spec_revision(expected, spec)` 위치 인자. 반환 frozen exact `SupportedInstrumentProjection(key, native_instrument_id, parser_revision, source_rule_revision, specification_revision, raw_row_canonical, spec)`이며 기존 exact Linear/SpotSpec을 사용한다. parser/rule/native helpers를 소비하고 새 parsing/revision 알고리즘을 작성하지 않는다. source/time/auth/account/current authority 없음. raw canonical bytes는 repr=False/<=65536이며 caller row dict는 보유하지 않는다.

현재 signatures/source dependency를 문서화하고 focused import/signature/known fixture semantic payload/revision regression으로 pin한다. runtime 파일 SHA 검사/파일 I/O/native import는 없다. helper 변경은 review/test failure로 발견하고 fallback parser/새 rule 자동 채택이 없다. frozen 파일 무수정.

## 9. F09/F10 — T4 실제 owner/registry/비교 계약

T4는 **별도 binding design preflight를 마친 다음 구현**한다. 아래 exact owner/상태/비교 계약은 그 설계의 필수 입력이며 T1–T3에 미구현 capability를 끼워 넣지 않는다.

실제 factory는 native_node의 local BoundBybitFactory.create다. monitoring에서만 create_monitor_client가 existing MonitoringHttpAdapter→pinned BybitInstrumentProvider(LINEAR,load_all=False,exact load_ids)를 설치한다. normal pinned SDK factory는 이 capture에 참여하지 않는다. constructor 시 완성 assembly가 없으므로 adapter는 UNARMED로 생성하고 receipt 발급 불가다.

arm wiring: existing `MonitoringBybitExecutionClient.arm_monitoring(target)`의 `require_current_target`, exact target witness, witness._assembly.execution_client is self, current worker binding 검사 후 session의 private `_arm_instrument_capture`를 호출한다. witness._assembly/channel/provider/adapter/binding/target를 그대로 캡처한다. arbitrary public register(assembly,channel)/caller attestation은 없다. old assembly/client rearm 또는 provider repeat initialize는 실패+revoke한다.

session registry는 assembly 한 개에 pending1 또는 issued1 handle, LINEAR keys1..15만 보유한다. exact internal Slot fields: session generation, assembly generation, current binding identity/attested tuple, exact scope, exact assembly/channel/client/provider/adapter identities, target identity/fingerprint, keys, budget/group token, armed_at/wall+mono, phase(ARMED/CAPTURING/ISSUED/REVOKED), immutable per-key source projection/envelope, same-session fee capture, converter revision/constants, provider/cache semantic snapshots, optional issued handle. public view에는 native/session/cache/response 객체가 없다. refs는 registry 내부에서만 identity 검사에 사용하고 raw provider exceptions/history는 보관하지 않는다.

fee capture는 기존 signed fee GET의 **같은 accepted response**에서 scope/category/symbol, original origin/status/body digest, response/receive stamps, maker/taker exact raw rates, physical counters를 owner가 포착한다. public fee를 만들거나 untrusted dict를 signed receipt로 등록하지 않는다. fee body 전체는 slot에 보관하지 않고 validated exact selected fields+digest만 보관한다. fee query 자체의 기존 bounds/decoder 의미는 보존한다. signed fee metadata hook의 exact integration은 T4 binding design에서 old read tuple을 유지하는 형태로 고정한다.

기존 request_instruments의 fee GET→instrument GET→기존 convert_instrument1회 순서를 유지한다. 각 await 복귀와 converter 직전/후에 binding/generation/group을 require한다. T3 public/native supported spec 비교는 spelling/scale/specification revision exact이며 unknown raw-only fields 전체 bytes equality를 요구하지 않는다. fee는 public spec에 없으므로 public-vs-DEMO fee equality를 요구하지 않는다.

_cache_instruments phase: (1) fresh provider exact keys와 source capture keys 일치, (2) 원래 baseline cache와 fresh provider의 기존 semantic drift 검사 유지, (3) source/native whitelist 비교, (4) 기존 superclass cache 업데이트 await, (5) current owner/generation 및 실제 updated cache/provider entries를 재확인, (6) wallet/account ACK까지 동일 startup group 통과 후 await 없는 최종 issuance. 이전 cache의 metadata만으로 fresh receipt를 발급하지 않는다. 어느 phase 실패도 issued 없음이다.

native/source profile whitelist:

- instrument ID/product/native exact type/raw symbol;
- base/quote/settlement currency code, precision, iso4217, name, currency_type; is_inverse=False;
- price_increment/size_increment exact Decimal 값과 precision, price_precision/size_precision, lot_size;
- min_quantity/max_quantity=source min/max **Limit** qty; min_price/max_price; min_notional currency+amount;
- maker_fee/taker_fee는 그 same-session signed fee capture와 exact attribution;
- converter representation constants multiplier1, margin_init0.1, margin_maint0.1, max_notionalNone, tick_schemeNone은 pinned converter agreement로만 검사. 실제 venue leverage/margin/notional permission으로 해석하지 않음.

native timestamps ts_event/ts_init은 source receive/constructor 수명 차이로 freshness를 대신하지 않아 semantic whitelist에서 제외하고 실제 evidence clocks로 별도 검증한다. info는 provider/cache 부가 metadata이고 caller가 채울 수 있어 authority/semantic whitelist에서 제외한다. raw market max quantity/source_priceScale/leverage/known-not-published 필드는 T3 side-source exact comparison으로 보존하며 이 필드만 달라도 source binding 실패다. Native max_quantity에 Market cap을 덮어쓰지 않는다.

revoke map: auth refresh/_revoke_history_receipts/close/_guard credential-client mismatch→session registry 전체 revoke; binding expiry/rollback→그 binding slots revoke; native _fault/retire_monitoring/disconnect/assembly-channel retirement/adapter cancel/provider repeat initialization/target invalidation→해당 slot revoke; public refresh/retire/expiry/mapping-policy change→연결 binding revoke. revoke는 immutable view/hash를 authority로 재생하지 않는다. budget/context ended 이후 새 reads는 금지하되 issued observation의 lookup freshness와 E16 runtime lifetime은 별도 owner 검사한다.

SPOT converter/CASH/account/native writer provenance는 T4 범위 밖이며 native SPOT compatible 표시는 없다. normal SDK writer의 source capture는 별도 설계다. T5 실제 E16 owner가 product/policy/strategy/account lease/channel/current receipts를 PLAN/SUBMIT 및 await 후 재검사해야 한다. 미구현 owner 대신 invented qualification seal/boolean을 만들지 않는다.

## 10. 정확한 비용과 남은 수락

public N<=16: N logical/max3N sends, no auth/time/fee/page/hidden refresh. native startup N<=15:2N+wallet1 logical/max6N+3 sends, same active group capacity를 preflight. capture/projection/require/retire는 extra GET0. existing account refresh2 logical/max6 sends는 별도 기존 phase 비용이다.

같은 group에 public N와 native2N+1을 이어 붙이면3N+1<=32와 남은 parent budget/deadline가 모두 필요하다. N10이면31 logical/max93 sends, auth refresh까지 포함한 열거 상한99 sends이나 전체 resume 성공 보장은 아니다. 역사적153 합산은 서로 다른 상한의 산술이며 group/native N16 실패와 PRE/POST/history 비용을 숨겨서는 안 된다. 부족하면 fail closed, budget reset/자동 group split/TTL 증가/partial universe 없음.

source implementation/offline tests/whole CI/frozen adoption source 검증/실제 공개 응답 수락/DEMO-native 비교/E16 금융 자격/full63 제품 완료를 구분한다. 실운용 universe/TTL/선택 mapping/policy, native SPOT, A07 account fees/tiers/dynamic constraints, 실제 UI/server/DEMO acceptance는 후속 필수다. root가 adoption full SHA를 pin하고 새 independent preflight를 수행하기 전 이 후보는 PROPOSED다.
