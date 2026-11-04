# U4 실제 설치의 private 준비와 단일 SDK 인계 — root 기술 채택

CI149 eedaea0ce8212277833569606514f5fe195ed6cc에서 기존36/6/12/8 및 새 실제 feasibility2가 모두 실패·skip0으로 통과했다. root PID12496/helper11516이 원XML과 actual workflow validator를 독립 대조했다(CI149_SIX_NATIVE_RECEIPTS_INDEPENDENT_VERIFICATION_20261104.json). ABI 선행만 충족했으며 금융 source/UID/model/U2-B/계좌·보호·실거래 권한은 아니다.

root는 기존 설계23583B/61e12404ffc81bec1d5ebea98169bbba739f169a880c51d1b9614021fe39fc4c 및 다른 정적 검토17931B/ba635281df039dd0c07e6ea460d1bca15ee0f691b6c928594a84863b86688b50를 읽었다. 검토의 M1 역사 표현은 다음으로 정정한다: 옛 combined invalid-fill construction/capture 예외검사가 통과했지만 capture-alone attribution은 UNKNOWN이었고, 이후 actual8 전체 PASS는 별도로 확인했다. 원래 U1blocked/postclaim부재는 c963 당시 문맥이며 지금의 root CODE 채택과 다르다. 이번에는 아래 native-only 비활성 범위만 구현한다. 완전 U2-B/actual 금융 소비자/최종 native6는 아직 채택·완료하지 않는다.

## 소유 범위

새 isolated W는 eeda 기준이다. 세 source와 세 검증 파일만 소유한다.

- src/multi_exchange_trader/execution/bybit_native.py
- src/multi_exchange_trader/execution/native_node.py
- src/multi_exchange_trader/execution/native_restart.py (실제 restore의 첫 resolver/SDK 변경 이전 철회 hook만)
- 새 tests/execution/test_native_prepared_handoff_v2.py (portable 구조·거부·구 API 회귀)
- 새 tests/execution/test_native_prepared_owner_linux.py (아래 실제 native-only4)
- .github/workflows/ci.yml (기존 관문 뒤 별도4/strict validator/별도 artifact만 추가)

worker/U1/J/모델/수학/codec/설정/qualification/기본monitor/README·기존테스트는 소유하지 않는다. rootplan은 별도 seed 문서다. 기존 public signature·import laziness·legacy/V2 cache/idempotency/UNKNOWN/cancel 동작을 보존한다. 기본 worker monitor_only=True와 production_supported=False, 금융 차단은 유지한다.

## 원래 실제 설치와 경로

actual build_native_node의 같은 설치에서만 private installation 관계를 만든다: 등록한 정확한 SDK base Strategy·원래 OrderFactory·node/kernel/trader/cache/bus·각 원래 clock·실제 한 client/channel/bridge/assembly·captured SDK base submit/limit wrappers. 별도 client/직접 HTTP writer/공개 발급자·권한 registry·승인 DTO/활성화 flag를 만들지 않는다. loader.factories는 기존 Bybit adapter용으로 보존하고 common.factories에는 distinct order_factories key를 쓴다. 실제 Strategy.clock은 kernel.clock과 다른 component LiveClock이므로 각각의 원래 참조를 보존하며 둘이 같다는 조건을 만들지 않는다. factory의 private C _clock/_cache Python 속성을 발명하지 않는다.

기록 자체는 native 설치 provenance다. actual Session/source/UID/전략/lease/금융 권한을 증명하지 않는다. copied/forged/injected assembly·bridge·installation/data는 원래 관계/현재 slot is 검사에서 거부한다. 실제 monitor-only 설치는 준비 발급/인계가 거부된다. 후속 금융 소비자는 원래 실제 worker/session/source와 전체 선행 권한을 별도로 보존해야 한다.

## 준비·seal·인계

private _prepare_fixed_limit_handoff_v2(original_request)->ContextManager[_PreparedFixedLimitDataV2], _seal_prepared_fixed_limit_v2(original_data)->None, _handoff_prepared_fixed_limit_v2(original_data)->NativeSubmission 경계를 둔다. seal은 구체적인 pre-clock full recipe 단계를 표현하며 새 권한 객체를 반환하지 않는다. private data/params/order/raw config는 공개하지 않고 repr에 실어 내보내지 않는다.

기존 RLock 안에서 첫 callback/getter/등록 전에 한 active frame을 둔다. 원래 bridge/installation/assembly·maps/context/bindings/request·key·closed generation·frame/data/current phase를 고정한다. 준비 중 같은 키/다른 키의 public submit, submit_fixed_limit_v2, cancel_owned, register_owned, register_fixed_limit_v2 및 중첩 준비는 첫 locked 진입에서 outer를 먼저 taint한 뒤 고정 진단으로 거부한다. 이 검사 전에 caller request의 getter/hash/키를 읽지 않는다. inner 거부를 삼켜도 outer는 살아나지 않는다. active marker는 원래 finally까지 유지하고 다른 attempt를 지우지 않는다. private 원래 mapping routine만 분리해 준비가 쓰며 allow_preparing flag나 외부 bypass callback을 만들지 않는다. active scope 밖 기존 public 동작은 원형을 유지한다.

원래 실제 factory와 accepted verifier로 detached LIMIT/IOC·원래 Q/price/seed/protection/params/정밀도를 검증한다. 공개 cache/event에 넣거나 Strategy.submit을 호출하지 않는다. 이미 submitted/UNKNOWN/cached key는 private 준비가 거부되고 관찰/복구 대상이다. 원래 지원되는 client ID, 모든 금지 옵션, 원래 frozen graph·known record association·실제 cache 부재/Trader 등록·SDK type/member 검사와 converter/getter는 context yield 이전에 마친다. 캡처한 actual base Strategy.__dict__['submit_order']와 명시적 원래 self/order/None/None/params의 tuple을 보존한다. 임의 bound callable·__func__ fallback은 없다.

seal은 callbackful native 검사를 반복하지 않는다. 긴 외부 정책/J 작업 뒤, final sampler BEFORE 순수 전체 request/known-record/params recipe와 원래 field/immutable-leaf/primitive owner 참조를 다시 대조한다. source/model/실제 clock·claim은 이 native-only slice가 만들지 않는다. 원래 public _association_fence_v2는 envelope hash를 호출하므로 post-clock helper로 재사용하지 않는다.

handoff의 SDK 호출 전 suffix는 exact internal primitive/is/closed/tainted/generation/phase·원래 built-in dict/str-key 및 이미 보존한 original links 검사만 수행한다. SQL/file/hash/codec/Decimal·Fraction/native raw·precision·as_decimal/string conversion/getter/descriptor resolution/owner callback/await/새 lock 획득/일반 guard 호출을 두지 않는다. 이 함수 구현과 실제 SDK wrapper reference는 future caller가 sampler 전에 캡처할 수 있다. private detached native 객체는 원래 identity로만 본다. 전체 graph 검증·private 상태·participating owner 수명이 근거이며 단순 scalar snapshot이 권한은 아니다.

준비를 consumed로 바꾼 뒤 prebuilt SUBMITTING을 원래 map에 기록하고 captured actual SDK base wrapper를 딱 한 번 호출한다. SDK 호출이 첫 native effect이며 그 안의 clock/cache/bus는 effect 자체다. 그 동안도 all-key guard를 유지한다. SDK subscriber가 inner 거부를 잡아도 outer taint는 남고 호출이 이미 시작됐으면 prebuilt UNKNOWN을 보존한다. 일반 SDK 예외는 원래 고정 diagnostics/UNKNOWN 의미를 따르고 cancellation/control BaseException은 UNKNOWN을 먼저 보존한 뒤 소유 방식대로 전파한다. 실패/불명 시 원래 key/등록/SENDING/full holds를 되돌리거나 재가격·재수량·재전송·release하지 않는다. marker 기록 전 메모리/owner 오류도 future 실제 J의 SENDING을 되돌리는 권한을 만들지 않는다. 불확실 설치를 private reuse 가능 상태로 복구하지 않는다.

## 실제 native 수명 철회

close_event_channel/dispose_local 및 native_restart.restore의 첫 resolver/SDK 변경 BEFORE 준비 generation/frame을 철회한다. active 원래 frame을 먼저 taint하고 mutation을 거부해 outer가 정상 통과하지 못한다. closed/disposed 설치는 새 준비를 할 수 없다. restore가 실제로 시작되기 전 이전 준비를 무효화하고, 다음 준비는 원래 설치의 전체 새로운 preflight를 거쳐야 한다. 철회 epoch 변경은 금융 재무장이 아니다. 정상 restore·monitor·shutdown과 기존 independent finally/리소스 보존은 유지한다. missing direct SDK/session/catalog/runtime 참여는 full U2의 남은 관문이며 이 slice가 대신 보장하지 않는다.

## 검증

현재 실제 ABI2는 기계 경로 선행일 뿐 새 private 제품 API의 검증을 대체하지 않는다. portable behavioral RED → 실제 구현 → 의미 있는 전체 old native/fixed-limit/recovery/restore portable 모듈 회귀, changed Ruff/strict mypy, scoped whole-AST·미소유 보존을 한다. fake modules/HTTP/session/clock은 비자격 구조 시험으로 명시한다. resolver/factory를 가짜 금융 허가로 교체하거나 공개 testing flag로 발급자를 만들지 않는다.

추가 actual Linux native-only4의 고유 이름은 다음과 같이 고정한다.

1. test_actual_entry_installation_prepares_a_private_order
2. test_actual_prepared_handoff_uses_one_original_sdk_route
3. test_actual_prepared_scope_rejects_cross_key_callback_reentry
4. test_actual_prepared_owner_retirement_and_monitor_denial

고정 실제 SDK/원래 builder·factory·Strategy·cache/bus/한 client/원 wrapper를 사용하고 node는 미실행, RiskEngine 원래 local route만 관찰한다. source/account fixture는 합성·금융 비자격이다. 실제 HTTP/주문은 없다. 필요한 unstarted native-only entry 설치 fixture는 실제 builder를 사용하고 구성 자격의 합성 부분을 명확히 기록한다. monitor 설치는 거부해야 한다. private cache/event0→원 SDK1호출/원래 Risk handler1회·queue1/처리0, same/cross-key callback taint·원 결과 유지, copy/escape/consume/lifecycle/monitor 거부를 실제 객체로 확인한다. 원 SDK callable/factory/owner resolver를 positive 경계에서 patch하지 않는다. forwarding observation만 원래 SDK 경로를 호출한다. fixture-only unjournaled native cache 정리는 사용자 복구 증거로 세지 않는다.

기존36/6/12/8 및 feasibility2의 commands/names/validators/uploads/timeout과 필수 Linux/netns/loopback/version fixture를 그대로 보존한다. 뒤에 OS isolation의 새 actual4/zero-failure-error-skip·exact module/names/individual bounded ASCII counters 검증과 별도 always artifact만 추가한다. artifact는 PR head SHA를 명확히 표기한다. 기존 meta extractor는 first closing이므로 새 인접 heredoc을 다시 먹지 않는지 기존 전체30으로 확인한다. 이4는 native-only 기계 계약이며 full J/source/clock/U3를 포함하는 미래 final native6와 다르다. final6를 실행·완료했다고 쓰지 않는다.

모든 소유·copy/closed·중첩/다른 키/legacy 경로·getter/factory/register/SDK subscriber·known graph 변화·opaque command 원본·UNKNOWN·cleanup을 검증한다. 미래 true-J claim/postclaim·최종 UTC/mono 전체 vector·금융 source/권한·보호/실제 거래 수락은 별도다.

고정 readonly Py3.12.10 -B/-Xutf8/currentNEWsrcFIRST/importlib/five file env literal off/allNEWF. 로컬 SDK import·nativepytest·앱/server·외부HTTP/socket·private env/vault·운영DB·키·주문·유료 AI·설치·Git/CI는 child 금지. actual SDK Linux 실행은 root의 전체 CI가 수행한다. 기존 stdlib127 async cleanup/검증용 정확한 offline snapshot child가 필요한 경우 원래 명령/NEW F 경계를 증명하고 별도 계수한다. root만 Git/CI/main. 실패·부분·이전 세대는 보존한다.

EX/TASK13_U4_NATIVE_PREPARED_IMPLEMENTATION_20261104.md<=16KB +SOURCE_PINS_20261104.json+RECEIPT_20261104.json을 동결하고 별도 읽기 검증 뒤 STOP WRITES. 다른 CODE 검토·root 최종 확인·정확 전체 CI/새 actual4 XML 검증을 통과한 변경만 main에 병합한다. 금융 소비자·source/model/승인 PLAN/full U2-B/native6/전체63·실제DEMO/기존서버/PCoff 완료로 표시하지 않는다.
