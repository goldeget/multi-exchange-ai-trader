# M3-A account/info 원문 출처 보존 — root 기술 채택

2026-11-04. Candidate dc064f2834f901ffd44393aa68a726179455c870fd77544f72582e7c71807b3c 및 DIFFERENT static review 3a9813e413a1d6000ac626e7a72a057f7757abb4439a6a024566b3a19d5ab125 전체를 읽었다. C0/I0/M1 중 M1을 아래 필수 original-owner guard 인자로 구체화하여 raw-only inactive 구현만 채택한다. 실제 필드 매핑·모델·계좌 위험/일별 장부·승인 PLAN·금융 qualification·U2-B·주문 활성화는 채택하지 않는다.

## 소유와 재사용

새 isolated W는 CI149 source eedaea0ce8212277833569606514f5fe195ed6cc를 기준으로 한다. 기존 원본과 Nf0b/I29bd/I3eeda는 보존한다. 소유는 네 파일뿐이다.

- src/multi_exchange_trader/account/bybit_readonly.py
- src/multi_exchange_trader/execution/account_binding.py
- 새 tests/account/test_linear_account_info_capture_v1.py
- 새 tests/execution/test_linear_account_info_source_v1.py

기존 Session의 원래 credential/client/binding·scope·loop/freshness와 기존 signed GET/budget/retry/response-size/cleanup을 재사용한다. public read() signature/tuple 의미, 기존 인증/history/monitoring/public instrument/risk-limit route와 세대는 유지한다. 낮은 account transport 계층에서 execution model이나 Session을 import하지 않는다. Session에서 기존 BybitModelSourceRefV1을 만들 때는 TYPE_CHECKING/lazy import 등 순환·시작 부하를 고려한다. 새 generic serializer/parser/model math/registry/권한 DTO/qualified flag/설정/SDK/공개 API/worker 금융 소비자를 만들지 않는다.

## 필수 API와 원래 소유자 경계

async Session._capture_linear_account_info_v1(self,binding,*,scope,budget,owner_guard:Callable[[],None])->BybitModelSourceRefV1. owner_guard는 필수 인자이며 default/no-op fallback을 두지 않는다. Session._require_linear_account_info_v1(self,source,*,binding,scope)->None은 그 출처에 보존한 정확한 guard를 재사용한다. guard callable 자체는 권한이 아니다. 후속 trusted 소비자가 실제 original worker/resources를 보존한 guard를 제공해야 하며 이번 단계는 그 소비자나 금융 권한을 만들지 않는다.

Session에 원래 issuer/self, binding/identity, client/원래 HTTP client, source object, raw capture·preimages, guard, generation과 pending/issued phase를 묶은 endpoint-specific 단일 bounded association만 둔다. copied Session/client 또는 재구성한 SourceRef는 원래 issuer가 아니다. 일반 DTO equality/hash만으로 require하지 않는다. private constructor self anchor 등 직접적인 원래 관계를 사용할 수 있으나 범용 발급자/등록 체계로 확대하지 않는다.

capture 시작에서 callbackful 검사나 첫 await 전에 이전 source를 철회하고 원래 pending frame을 둔다. overlap/재진입은 이전 frame을 taint하고 실제 새 I/O 전에 거부한다. inner error가 잡혀도 outer frame이 살아나지 않으며 marker는 원래 finally까지 유지한다. 동일 자료 재발행도 이전 generation을 복원하지 않는다. authenticate 시작/close/freshness withdrawal/owner·client 교체는 기존 source를 철회한다. 원래 binding/loop/session/client/HTTP client/phase를 callback 전후 및 모든 send/body/retry/cleanup await 전후, publication 직전에 확인한다. guard가 객체나 세대를 바꾼 뒤 정상 반환해도 거부한다.

성공한 전체 수신·acceptance·response cleanup 뒤에만 issued source를 한 번 설치한다. cancellation/BaseException/오류/timeout/quota/owner변경은 이전 source나 pending을 복원하지 않는다. 원래 pending의 finally가 다른 시도의 marker/issued source를 지우지 않게 한다. key·secret·signed header·raw body·원래 request/response는 Session/transport 내부에만 보존하고 repr/log/error/public JSON/artifact로 내보내지 않는다. private data repr를 감춘다. SourceRef만 안전한 integrity data로 반환한다.

HTTP receipt context는 성공한 read 뒤 정상 닫힌다. issued source의 require가 이미 종료된 receipt의 active=True를 요구하는 순환 계약을 만들지 않는다. 캡처 당시 실제 budget/group/attempt의 원래 증거는 보존하되, 이후 require는 issued association과 현재 원래 소유자·binding을 검증한다. 원래 source 시각은 보존하고 새 valid_until·가상 server time·추가 TTL·허용 오차를 발명하지 않는다. 이후 실제 model 소비자의 freshness/causal/clock 규칙은 별도다. 이 require는 callbackful 작업이며 미래 final sampler 전에만 사용한다. U2-A 동기 no-await section을 HTTP await에 걸쳐 유지하지 않는다.

## endpoint-specific 수신 경계

constant signed GET /v5/account/info, exact empty query와 원래 DEMO origin만 허용한다. 원래 Session binding의 scope/UID로 결속하며 account/info가 UID를 echo한다고 가정하지 않는다. public/risk route의 instrument key/envelope를 계좌 조회에 억지로 재사용하지 않는다. 기존 _bounded_response가 응답을 재구성하기 BEFORE 원래 실제 request/response association, sent/response origin·status, 제한된 완전 raw bytes/hash, 실제 start/received UTC·monotonic, 실제 accepted attempt를 endpoint-specific private 경로로 보존한다. global 모든 응답 수집 hook이나 public raw-body service를 만들지 않는다.

가능하면 원래 _build_request/_read/_bounded_response의 좁은 private 인자/내부 data를 확장해 기존 signing·retry·deadline·physical charging을 한 번만 수행한다. 기존 public/risk route는 byte/AST상 가능한 한 보존하며 별도 회귀로 확인한다. 새 transport 경로는 원래 요청의 GET/path/raw query/origin 및 원래 response.request/history를 검사하고 redirects/다른 계좌·환경/인코딩 모호성/초과·부분 body를 거부한다. 기존 body bounds를 보존하며 새 경로는 65536B 이하로 제한한다. identity encoding과 header 검사는 실제 raw hash 의미를 명확히 하고 quota/ban 관찰을 우회하지 않는다. accepted_attempt는 실제 index+1, page_ordinal=1이다. 예산은 원래 shared operation/receipt와 모든 retry를 통해 계수하며 새 attempt마다 재설정하지 않는다.

기존 _accepted와 표준 JSON 수락을 재사용하되 계좌 field projection은 하지 않는다. raw capture primitive는 low-level 내부 데이터일 뿐이다. 기존 SourceRef의 14개 closed fields/canonical 검증을 Session 쪽에서 재사용하고, source is·별도 원래 preimages/raw hash/current issuer를 require에서 다시 대조한다. SourceRef가 constructible하다는 사실을 출처 권한으로 바꾸지 않는다. 실제 mono와 original raw preimages는 private에 보존하고 시각을 반올림·새 시각으로 교체하지 않는다.

공식 account-info 문서는 GET/no-query와 envelope time 생략을 허용하고 DEMO API 목록에 endpoint가 있다. stamp=None은 기존 budget/SourceRef에도 합법이므로 provider time을 만들지 않는다. isMasterTrader는 copytrading leader이고 main/sub master 관계가 아니다. unified mode/flags/fee/권한/wallet/금융값을 여기서 도출하지 않는다.

## 검증과 전달

genuine existing Session/client 코드와 bounded HTTPX fixtures로 행동 RED를 먼저 확보한다. 모든 mock/intercepted HTTP·synthetic account/clock은 offline 구조 검증이며 실제 인증/자료·금융 권한이 아니다. 원래 require_observation_binding의 injected transport 거부를 바꾸거나 production flag를 조작해 자격을 얻지 않는다. 정상 capture의 구조 경로를 intercepted original HTTPX로 시험한다면 그 사실과 비자격을 명시하고 원래 Session/closed/ref/guard 검사 자체는 교체하지 않는다. 실제 endpoint·SDK/주문은 실행하지 않는다.

정상 raw bytes/metadata/attempt/None-time/read tuple 호환, 악성 route/query/origin/response association·body/header·retry/partial/clock/budget·owner callback 변화, exact source와 copy/foreign/replaced client·Session, equal republish/overlap/inner-caught reentry, authenticate/close/취소와 cleanup 실패·old source 부활 금지를 확인한다. 실제 Session 및 read/auth/history/monitor/budget/public-risk capture의 영향 받는 기존 완전 모듈을 선택해 회귀하고 source/module 범위와 counts를 명시한다. 단순 counter/가짜 성공 validator로 대체하지 않는다.

최종 owned Ruff/strict mypy, source before/after·미소유 보존, 실제 PID/argv/UTC/환경·streams·exit를 남긴다. 기존 실패/부분 세대는 보존한다. 고정 read-only Python3.12.10 -B/-Xutf8/currentNEWsrcFIRST/importlib, five file env literal off, 모든 새 temp/cache/fixture/evidence는 NEW F다. SDK·실제 외부 HTTP/socket·앱/server·private env/vault·운영 DB·키·주문·유료 AI·설치·Git/CI는 child가 실행하지 않는다. 기존 async 정리 stdlib127 IPC만 caller-proven 예외로 별도 계수한다. root만 Git/CI/main을 처리한다.

EX/TASK13_M3_A_ACCOUNT_INFO_SOURCE_IMPLEMENTATION_20261104.md(16KB 이하)+SOURCE_PINS_20261104.json+RECEIPT_20261104.json을 동결하고 별도 읽기 검증 뒤 STOP WRITES. 다른 CODE 검토·root 최종 검사·정확한 전체 CI·main을 거친다. M3-A는 미완성 금융 producer의 실제 원문 보존 선행 단계이며 전체 source/model/U2-B/U3/보호/DEMO/서버/PC-off/canonical63 완료를 의미하지 않는다.
