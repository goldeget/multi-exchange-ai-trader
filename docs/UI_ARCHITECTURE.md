# 목업 일치 UI 및 매매 프로그램 최종 아키텍처 · Implementation Plan

> GPT Work/Codex 직접 실행용. 총괄·구현·독립 검토·브라우저 검수·원격 저장은 이 대화의 GPT가 책임진다. 이 문서는 설계·패치 계획이며 구현 완료 보고서가 아니다. 실제 매매 주문 활성화 지시가 아니다.
>
> For agentic workers: 지원되는 경우 superpowers:subagent-driven-development 또는 superpowers:executing-plans로 작업 단위를 실행한다. 총괄 GPT가 하위 작업·검토·통합을 관리한다. 사용자에게 에이전트나 도구를 따로 지휘하도록 요구하지 않는다.

**Goal:** 최종 PC 12장·모바일 12장을 기준으로 한국인용 실제 UI를 재현하고, 화면의 문구·입력·버튼을 실제 기능 계약과 일치시킨다.

**Architecture:** 기존 React·TypeScript·Vite와 FastAPI·NautilusTrader·SQLite를 유지한다. 공통 디자인 계약 → 12개 페이지 → 조회 모델 → 기존 도메인 서비스의 구조로 연결한다. 외형 비교용 고정 예시와 실제 데이터 경로는 명확히 분리하고, 거래·원장·위험 판단은 서버가 소유한다.

**Tech Stack:** 현재 원격 main에서 React/React DOM 19.2.0, TypeScript 5.9.3, Vite 7.3.7, Lightweight Charts 5.2.1, NautilusTrader 1.231.0을 확인했다. 버전은 이번 UI 작업 때문에 변경하지 않는다.

**Spec:** 이 문서 1–13절을 구현 명세로 사용한다. 14–17절은 패치·검증·인계 계약이다.

- 작성일: 2026-11-01 KST.
- 저장소: goldeget/multi-exchange-ai-trader.
- 원격 조사 기준 main: 681d4d67dbabd9a3846864f7f60ad10ecf05242b.
- 최종 목업: TRADING_UI_MOCKUPS_PC_MOBILE_24_20261101.zip.
- 목업 ZIP SHA-256: a3d1d29b0bed47149a2cf93c09ff52e2328d56798e8ebd98d22959b9c10624b9.

## Global Constraints

- 기존 저장소·미커밋 작업·원장·미확정 명령·수동 포지션을 보존한다.
- 사진을 배경으로 깔고 투명 버튼을 얹는 구현은 사용하지 않는다. 글자·표·폼은 실제 DOM, 차트는 기존 차트 컴포넌트로 만든다.
- UI는 본문 16px, 보조 14px 이상을 기본으로 한다. 화면에 맞추려고 글씨를 다시 10–12px로 줄이지 않는다.
- 실제 주문은 기본 꺼짐을 유지하며, 이번 작업으로 실계좌 주문·외부 포지션 변경·추가 결제·외부 배포·자동 머지를 활성화하지 않는다.
- 현재 계좌 읽기와 가상매매는 서로 다른 계좌·장부다. 독립된 가상 장부도 합산하지 않는다.
- 실제 가격·수수료·수량·시각·손익·모델·권한·상태는 서버 데이터에서 표시한다. 목업 숫자는 예시 전용이다.
- 미지원 기능은 서버 capabilities로 차단한다. UI에서만 disabled 처리하고 서버에서 허용하는 구조는 금지한다.
- 기존 안전 패치와 테스트를 보존한다. 이미 구현된 예전 R1–R6를 새 미해결 결함으로 다시 작성하지 않는다.
- 모바일은 같은 앱·같은 API·같은 상태를 사용한다. 화면 표현과 탐색만 반응형으로 바꾼다.
- 모바일 반응형과 실제 휴대전화 원격 접속은 별도 기능이다. 현재 loopback·Origin·Host 제한을 무조건 해제하지 않는다.

## Review Focus

1. 추천 승인 대기 중 선택 종목을 바꿨을 때 이전 응답이 현재 카드처럼 표시되지 않아야 한다. Task 6의 경합 시험으로 검증한다.
2. 원장 응답 유실·새로고침·재시작 뒤에도 같은 멱등 키와 명령을 유지해야 한다. Task 5·7의 실제 서비스 시험으로 검증한다.
3. 서버는 살아 있지만 자동 작업이 중단되면 정상·거래 가능으로 표시하지 않아야 한다. Task 8의 부분 장애 시험으로 검증한다.
4. 긴 한국어·작은 코인 수량·큰 금액·화면 확대에서도 주요 값·단위·버튼이 잘리지 않아야 한다. Task 2·9의 브라우저 시험으로 검증한다.
5. 휴대전화 접속을 위해 기존 로컬 보안 경계를 완화하거나 브라우저에 키를 보내지 않아야 한다. Task 10의 접근 제어 시험으로 검증한다.

---

## 1. 사진 수준으로 구현하기 위한 확정 방식

사용자가 원하는 것은 사진과 같은 배치·글씨·밀도·숫자·버튼을 가진 작동하는 프로그램이다. 가장 확실한 실행 순서는 다음과 같다.

1. 24장 원본을 변경하지 않고 디자인 참조로 보관한다.
2. 서로 다른 메뉴·아이콘·문구를 공통 디자인 계약으로 정리한다.
3. 같은 예시 상태를 사용하는 실제 React 화면을 만든다.
4. 같은 크기의 브라우저 캡처를 목업과 겹쳐 비교하고 차이를 수정한다.
5. 기존 서비스와 새 기능 계약에 연결한다.
6. 검수한 실제 구현 화면을 회귀 기준 이미지로 고정한다.

생성 이미지의 글자는 실제 폰트 파일로 정의되어 있지 않고, 화면 사이에 메뉴 개수도 다르다. 24장 각각의 모든 픽셀과 동시에 일치하면서 공통 메뉴까지 일치한다는 조건은 서로 충돌한다. 따라서 아래 기준을 적용한다.

- 페이지 내용·위계·배치는 해당 목업을 따른다.
- 공통 메뉴·아이콘·글꼴·여백은 이 문서를 따른다.
- 돈·시각·권한·버튼 의미는 실제 도메인 계약이 우선한다.
- 이러한 정규화 차이는 visual-exceptions.json에 이유와 영역을 기록한다. 차이를 숨겨서 100%라고 보고하지 않는다.
- 원본 목업과의 첫 구현 검수와, 승인된 실제 구현의 이후 픽셀 회귀 검수는 별개다.

이 계약은 목업을 대충 비슷한 화면으로 대체해도 된다는 뜻이 아니다. 위치·크기·타이포·배치 차이는 캡처와 겹침 이미지로 수정해야 한다.

## 2. 현재 상태 — 기존 문서 이후 달라진 사실

### 2.1 이번에 다시 확인한 원격 상태

| 항목 | 관측 |
|---|---|
| main | 681d4d67dbabd9a3846864f7f60ad10ecf05242b |
| PR #2·#3·#4 | 머지됨 |
| PR #5 | 계좌 신선도·작업 정지 감시 패치, 머지됨 |
| PR #6 | 규칙 기반 가상 주문 추천·승인, 머지됨 |
| 열린 PR | 조회 시점 0개 |
| 해당 main CI | 41258880582, completed/success |
| 실제 로컬 앱·미커밋 파일 | 이 조사에서는 미검증 |
| 실제 계좌·AI 권한·현재 잔액 | 이 조사에서는 미검증 |

이 문서는 코드 정적 검토와 원격 메타데이터를 기반으로 한다. 이 환경에서 앱을 실행하거나 24개 실제 구현 화면의 시각 일치를 시험한 것은 아니다. 총괄 GPT는 구현을 시작할 때 최신 원격·작업 환경·기존 변경 상태를 다시 확인한다.

### 2.2 그대로 유지할 안정성 기능

- 계좌 요청 완료 후 재예약, 응답 generation 검사, 전체 20초 deadline.
- 서버 single-flight와 8초 공유 캐시, 45초 지연·120초 확인 불가 표시.
- 중첩 응답 검사와 오류를 0원·포지션 없음으로 바꾸지 않는 처리.
- account/info 기반 마진 조회와 10010·10002·403 등 오류 구분.
- 스케줄러 watchdog, heartbeat와 실제 진행 구분, 중단 감지·종료 정산.
- health와 ready 구분. ready의 최상위 status=ok도 실거래 준비를 뜻하지 않는다.
- 차트 인스턴스 정리·리사이즈·logical range 보존.
- 원장·명령 멱등성·미확정 요청 복구·재시작 후 명시 재개.

### 2.3 확인된 UI 개선 대상

| 현재 코드 관측 | 필요한 패치 |
|---|---|
| styles.css에 10px small/time, 11px 보조, 12px label/표 셀 | 공통 16/14px 타이포 계약 적용 |
| 표에 overflow-wrap:anywhere 적용 | 한국어 일반 문장과 긴 식별자 줄바꿈 분리 |
| 전역 header/main/label/small/time 선택자와 중복 스타일 | reset·토큰·공통 컴포넌트·feature CSS 책임 분리 |
| 계좌 표 nowrap·가로 스크롤·왼쪽 숫자 정렬 | PC 숫자 오른쪽 정렬, 모바일 PositionCard |
| 현재 App에 공통 사이드바·하단 메뉴가 없음 | AppShell·navigation·router 도입 |
| 5개 query view, replaceState 위주, popstate 처리 없음 | 기존 URL 호환 + 12페이지 탐색·뒤로 가기 |
| 한글 폰트 로딩 선언을 확인하지 못함 | 자체 호스팅 폰트와 로딩 완료 후 캡처 |
| 차트 상승 초록·하락 빨강 | 한국식 상승 빨강·하락 파랑, 꼬리·거래량까지 통일 |

overflow-wrap은 줄바꿈 원인 후보를 코드에서 확인한 것이다. 실제 렌더링 원인은 브라우저에서 다시 검증한다.

## 3. 디자인보다 먼저 해결해야 하는 기능 불일치

아래를 해결하지 않고 목업의 버튼·입력을 기존 API에 이름만 바꿔 연결하면 잘못된 프로그램이 된다.

| 목업 | 현재 계약 | 최종 결정 |
|---|---|---|
| 수동 화면: 현물·지정가·매수·매도 | 합성 선물 CryptoPerpetual의 enter/close/protect/tick | 현물 PAPER 시장가·지정가 기능을 별도 검증하고 같은 실행 경로로 추가. 미지원 동안 원인을 표시하고 비활성 |
| 신규 진입 일시정지 | 현재 pause는 가격·봉 처리도 중지 | ENTRY_PAUSED를 새 계약으로 추가. 기존 PAUSED를 이름만 바꿔 연결하지 않음 |
| 레버리지 3배 입력 | 자동실행은 leverage 필드 없음·진입 10배 고정 | 새 설정 버전에서 레버리지 파라미터화. 기존 실행은 원래 설정으로 보존 |
| 백테스트 기간·20/60·비용 편집 | 현재 POST는 dataset_id·idempotency_key만 받음 | 평가 구성 버전·구간·비용 계약을 서버까지 확장 |
| DeepSeek 분석 중 표시 | 현재 추천은 외부 AI 없이 SMA 5/20·15분·2배 규칙 | source=RULE/LLM 구분. 실제 연결 전에는 규칙 추천이라고 표시 |
| 예시 수수료 0.10% | 기존 PAPER-v1 FEE=0.0004, 즉 0.04% | 실제 비용은 서버 계산. 목업 예시를 전역 상수로 복사하지 않음 |
| AI 예산·예약·연결 상태 | 해당 원장·제공자 연결 API는 현재 확인되지 않음 | 실제 설정·예약·정산 서비스를 만들기 전에는 미설정/꺼짐 표시 |
| 종합 현황 하나의 자산 숫자 | 계좌·수동 PAPER·자동 PAPER는 독립 장부 | 선택 장부를 명시. 독립 자금·성과 합산 금지 |

최종 UI는 목업을 구현하되 capabilities가 false인 기능을 실제 지원처럼 꾸미지 않는다. 신규 기능 패치가 검증되면 같은 컴포넌트가 활성화된다. 별도의 가짜 운영 화면을 만들지 않는다.

## 4. 최종 시스템 구조

~~~mermaid
flowchart TD
  UI["PC·모바일 React UI"] --> VM["페이지 조회 모델·명령 상태"]
  VM --> API["FastAPI · 계약·권한 검사"]
  API --> READ["시세·계좌 조회"]
  API --> CMD["가상 명령·위험 검사"]
  API --> ANA["규칙·선택적 AI 분석"]
  CMD --> ENG["기존 Nautilus 실행 경로"]
  ENG --> DB["원장·명령·감사 저장"]
  ANA --> CAND["검토할 후보"]
  CAND --> CMD
  READ --> EX["거래소 읽기 전용"]
~~~

### 4.1 책임 경계

| 계층 | 소유하는 것 | 소유하지 않는 것 |
|---|---|---|
| 디자인·UI | 배치·글꼴·입력 초안·선택·열림·접근성 | 체결·잔고·실제 실행 상태 계산 |
| 조회 모델 | API 응답 검사·보기용 변환·freshness·요청 수명주기 | 장부 생성·거래 승인 우회 |
| 명령 서비스 | 검토·확정·멱등성·권한·재검사 | 브라우저 응답 성공을 체결 완료로 간주 |
| 도메인·엔진 | 위험·수량·비용·체결·포지션·정산 | AI 문장을 직접 실행 |
| AI 서비스 | 제한된 입력의 분석 후보·근거·사용량 | 수량·레버리지·위험 한도 임의 상향 |
| 저장소 | 이벤트·명령·결과·설정·버전·비용 예약 | UI에 맞춰 과거 데이터 재작성 |

기존 단일 FastAPI와 기존 네이티브 worker/sandbox를 사용한다. UI 개편을 위해 Redis·Kafka·대형 데이터베이스·마이크로서비스·새 거래 엔진을 추가하지 않는다. 저사양 PC에서는 전체 빌드·네이티브 시험·브라우저 시험을 순차 실행한다.

### 4.2 재사용할 API

모든 경로는 /api/v1 접두사를 가진다. 아래는 현존 경로이며 설치된 최신 소스에서 요청·응답 필드를 다시 확인한다.

| 영역 | 경로 |
|---|---|
| 시세 | GET /market/ticker, /market/candles |
| Bybit 읽기 | GET /account/bybit/view |
| 수동 PAPER | GET /paper/scenarios, /paper/scenarios/{id}, /paper/scenarios/{id}/audit; POST /paper/scenarios/{id}/commands |
| 데이터 | GET /research/datasets, /research/datasets/{id} |
| 자동실행 | GET /research/runs, /research/runs/{id}, /research/runs/{id}/audit; POST /research/runs/{id}/commands |
| 평가 | GET /research/evaluations, /research/evaluations/{id}; POST /research/evaluations |
| 규칙 추천 | POST /recommendations/preview, /recommendations/{id}/approve, /recommendations/{id}/reject |
| 기본 진단 | GET /health, /ready; /research/runs의 scheduler 정보 |

### 4.3 추가할 조회·설정 계약 — 아직 현존 API가 아님

| 제안 경로 | 역할 |
|---|---|
| GET /capabilities | 상품·주문 방식·설정 범위·권한·지원 이유. 실패 시 신규·위험 증가 명령 차단. 기존 가상 포지션 감축·정리·보호는 서버가 권한·원장 상태로 별도 판정 |
| GET /overview | 기존 서비스를 읽어 조합한 현황. 새로운 공유 원장 아님 |
| POST /paper/order-previews | 상품·수량·가격·비용·가용성 재검사 후 검토 토큰 생성 |
| POST /paper/order-confirmations | 검토 토큰·멱등 키·버전으로 재검사 후 기존 실행 경로 호출 |
| GET /recommendations, /recommendations/{id} | 저장된 추천 목록·상세. 기존에 있다고 가정하지 않기 |
| GET /connections/status | 등록/권한/최근 확인 상태. 키 값·서명·인증 원문 반환 금지 |
| GET·PUT /settings/ai | 모드·허용 모델·예산 설정과 revision. 비밀키 전달 경로 아님 |
| GET /ai/budget | 사용·예약·남은 한도·불확실 비용. 제공자 실제 잔액과 구분 |
| GET /diagnostics | 준비 상태·작업 진행·미완료 요청·안전한 오류 코드 |

추가 API는 기존 사용자 데이터를 변경하지 않는 읽기 모델부터 붙인다. 기존 API 응답은 갑자기 교체하지 않고 프런트 adapter 또는 명시적 새 계약으로 이행한다.

## 5. 공통 디자인 계약

### 5.1 원본 픽셀과 CSS 화면 크기

검사한 최종 ZIP에는 PNG 24장과 README 1개가 있다. 모든 PC 원본은 1487×1058px, 모바일은 853×1844px이다.

| 용도 | 기준 |
|---|---|
| 원본 보관 | PNG 원본과 SHA-256 보존 |
| PC 구현 기준 viewport | 1440×1024 CSS px |
| 모바일 구현 기준 viewport | 390×844 CSS px |
| 보조 반응형 검수 | 1280×800, 768×1024, 360×800, 320px 폭, 200% 확대 |
| PC 참조 환산 | 폭 1440 등비 환산 시 높이 약 1024.56 |
| 모바일 참조 환산 | 폭 390 등비 환산 시 높이 약 843.09 |

비교용 참조를 만들 때만 등비 환산하고 1px 안팎의 crop/pad 정책을 기록한다. 원본은 변경하지 않는다. 실제 앱에 transform:scale()을 걸거나 모바일 viewport를 853px로 지정해 축소된 데스크톱을 만드는 방법은 금지한다.

### 5.2 단일 AppShell

- PC: 01 종합 현황의 7개 메뉴를 모든 화면의 공통 기준으로 사용한다.
- 메뉴: 종합 현황 / 시장·차트 / 계좌·포지션 / 분석·추천 / 매매·자동실행 / 전략·검증 / 설정·진단.
- PC 원본 중 메뉴가 5개·6개인 화면은 본문 배치만 참고하고 메뉴 생략을 복제하지 않는다.
- 모바일: 홈 / 시장 / 매매 / 계좌 / 더보기의 5개 고정 탭.
- 매매 아이콘은 모든 화면에서 같은 SVG를 사용한다. 같은 탭이 화면마다 막대·재생 아이콘으로 바뀌지 않게 한다.
- 분석·전략·설정은 더보기에서 같은 URL 체계로 이동한다.
- 사이드바·탭·페이지 제목은 navigation.ts 한 곳에서 공급한다.
- 환경 배지는 데이터 범위별로 표시한다. 계좌·포지션에는 실계좌 조회 전용을 표시하고 전역 PAPER를 덮어씌우지 않는다.

### 5.3 구현용 확정 토큰

아래는 생성 이미지에서 정확한 CSS를 추출한 값이 아니라, 공통 구현을 위한 정규화 설계값이다.

| 토큰 | 값 |
|---|---|
| 배경 | #101721 |
| 사이드바·기본 표면 | #141E2A |
| 보조 표면 | #18222E |
| 주 글자 | #E6EDF6 |
| 보조 글자 | #AEBACB |
| 기본 버튼 | #3665EA, 글자 #FFFFFF |
| 상승·이익 | #FF6B7A, 부호·상승 문구 함께 |
| 하락·손실 | #76A1FF, 부호·하락 문구 함께 |
| 정상 상태 | #48D8B1, 상태 문구 함께 |
| 주의 | #F6BD4F, 주의 문구 함께 |
| 구분선 | #2B394C, 장식용 |
| 입력 배경·테두리 | #121C28 / #697E9A |
| PC 사이드바 | 200px |
| PC 본문·그리드 간격 | 24px |
| PC 패널 패딩 | 24px |
| 모바일 좌우 여백·패널 패딩 | 16px |
| 패널 모서리 | 12px |
| 입력·주요 버튼 높이 | 48px |
| PC 제목 / 모바일 제목 | 28px / 24px, 700 |
| PC 주요 금액 / 모바일 주요 금액 | 32px / 28px, 700 |
| 본문 / 보조 | 16px / 14px, 행간 1.5 |
| 간격 단계 | 4 / 8 / 12 / 16 / 24 / 32px |

단색 조합을 계산했을 때 주 글자/배경은 약 15.27:1, 보조/보조 표면은 8.18:1, 흰 글자/기본 버튼은 4.99:1이었다. 이는 해당 토큰 조합 계산이며 전체 화면 접근성 검증 완료를 뜻하지 않는다. disabled·hover·focus·오류·투명도가 적용된 최종 화면도 별도 확인한다.

### 5.4 글꼴·아이콘·배경

- Pretendard 한글 웹폰트를 자체 호스팅하고 라이선스·버전을 함께 보관한다. 폰트 CDN을 필수 실행 의존성으로 두지 않는다.
- 실제 weight 400/500/600/700을 포함하고 가짜 bold에 의존하지 않는다.
- 숫자는 tabular-nums를 적용한다. 일반 본문 전체를 고정폭 글꼴로 만들지 않는다.
- 시각 캡처는 document.fonts.ready 이후 수행하고 폰트 실패를 감지한다.
- 아이콘은 일관된 SVG 집합 하나만 사용한다. 기본 20px·선 1.75px, 주요 제어 24px. 상표 로고는 공식 자산·사용 조건 확인 후 사용한다.
- 생성 이미지의 미세 배경 질감·가짜 글자 윤곽을 그대로 재현하려고 글자나 금융 숫자를 이미지로 굽지 않는다.

### 5.5 한국어·숫자 규칙

~~~css
.body-copy {
  word-break: keep-all;
  overflow-wrap: break-word;
  line-height: 1.5;
}
.long-identifier { overflow-wrap: anywhere; }
.financial-value {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
~~~

- 긴 식별자에만 anywhere를 사용한다. 일반 문장을 좁은 표 셀에 몰아넣지 않는다.
- 일반 안내는 요약 1–2문장, 상세는 disclosure로 분리한다. 경고·중요 위험·미확정 상태는 숨기지 않는다.
- 금액은 USDT·USD·BTC를 구분한다. 숫자와 단위가 따로 잘리지 않게 한다.
- 금액·수량은 서버의 Decimal 문자열을 보존한다. 표시를 위해 반올림해도 계산·요청 원본은 바꾸지 않는다.
- 작은 코인 잔고를 0으로 만들지 않는다. 필요하면 전체 정밀도 확인 기능을 제공한다.
- 가격·금액은 천 단위 구분, 표의 숫자는 오른쪽 정렬. 수량은 상품 lot/tick 기준.
- 프런트 Number/parseFloat로 거래 계산을 하지 않는다. 차트 좌표용 숫자 변환은 표시용으로만 제한한다.
- 손실 한도 입력 5 %는 제출 경계에서 정확한 Decimal 비율 0.05로 변환한다.
- 사용자 시각은 KST, 저장·전송 기준 시각은 UTC. 상세 원본 UTC와 거래소 갱신 시각은 구분한다.
- null·미조회·실패·0·빈 배열은 다른 상태다. 값이 없다고 0을 보여주지 않는다.

### 5.6 반응형 기준

- 1200px 이상: 사이드바 200px, 차트·보조 패널 2열.
- 768–1199px: 같은 사이드바·탐색을 유지하되 본문은 필요한 영역부터 1열로 전환한다. 고정 폭 2열을 억지로 유지하지 않는다.
- 767px 이하: 사이드바 제거, 모바일 상단 제목·하단 5탭, 1열 구성.
- 포지션·기록은 모바일 카드 표현을 사용한다. fetch hook과 명령 서비스는 PC와 공유한다.
- 차트만 그 목적에 맞는 가로 제스처를 허용하고, 일반 문단·입력 페이지에는 가로 넘침이 없어야 한다.
- 하단 메뉴·CTA·safe-area·가상 키보드가 콘텐츠를 가리지 않게 scroll-padding과 하단 공간을 확보한다.
- 내용이 길면 세로로 스크롤한다. 사진 한 장에 모두 들어가 보이도록 폰트와 조작 영역을 줄이지 않는다.
- 버튼의 한국어 문구가 길면 라벨을 개선하거나 높이를 늘린다. 중요한 의미를 생략 부호로 자르지 않는다.
- 확대·핀치 줌을 막지 않는다. 320px reflow·200% 확대·가로 방향도 검사한다.

## 6. 12페이지·24목업과 URL·컴포넌트 연결

PC/01_종합현황_PC.png와 Mobile/01_종합현황_Mobile.png처럼 같은 번호를 같은 페이지로 연결한다. 모두 최종 ZIP의 파일명이다.

| 번호·참조 이름 | 제안 URL | 페이지 컴포넌트 | 핵심 |
|---|---|---|---|
| 01 종합현황 | ?view=overview | overview/OverviewPage | 선택 장부·연결·조치 항목 |
| 02 시장차트 | ?view=market | market/MarketPage | 거래소·상품·종목·시간봉·출처 |
| 03 계좌포지션 | ?view=account | account/AccountPage | 실계좌 읽기·외부 포지션·주문 |
| 04 AI분석추천 | ?view=recommend | recommend/RecommendPage | 실제 source·근거·반대·유효기간 |
| 05 수동가상매매 | ?view=paper | paper/PaperPage | 상품별 주문 입력·검토·가상 확정 |
| 06 자동실행모니터 | ?view=research&tab=run&run_id=… | research/RunMonitorPage | 실제 작업·고정 설정·제어 |
| 07 새자동실행설정 | ?view=research&tab=create | research/RunCreatePage | 신규 설정·검토·생성, 자동 시작 기본 꺼짐 |
| 08 실행기록 | ?view=research&tab=history | research/RunHistoryPage | 완료·실패·미확정·과거 설정 |
| 09 백테스트설정 | ?view=research&tab=backtest | research/BacktestPage | 데이터·구간·비용·검증 방식 |
| 10 전략검증결과 | ?view=research&tab=report&report_id=… | research/ReportPage | 실제 구성·순손익·낙폭·한계 |
| 11 연결예산설정 | ?view=settings&tab=connections | settings/ConnectionsPage | 등록·권한·모드·예산 예약 |
| 12 진단복구 | ?view=settings&tab=diagnostics | settings/DiagnosticsPage | 기능별 상태·미완료·복구 |

### 6.1 라우팅 계약

- 기존 market/paper/research/account/recommend query view를 계속 해석한다.
- view가 없으면 새 종합 현황을 연다. 기존 명시적 ?view=market 링크는 유지한다.
- 기존 ?view=research는 백테스트 탭으로 연결하고 자동실행·기록 링크를 같은 메뉴에 제공한다.
- 사용자 페이지 이동은 pushState, 같은 페이지의 필터 정리는 replaceState를 사용한다.
- popstate를 구독해 뒤로·앞으로 가기를 반영한다. 직접 URL·새로고침도 동일하게 동작해야 한다.
- 현재 dependencies에 라우터가 없으므로 작은 query router를 먼저 사용한다. 필요 이상으로 프레임워크를 교체하지 않는다.
- route 상태는 URL이 소유하고, 폼 초안은 페이지가 소유하며, 미확정 명령은 별도 복구 저장소가 소유한다.
- run/report ID가 없거나 삭제·미지원 상태이면 빈 상태와 다음 행동을 표시한다. 임의의 최근 실행을 변경 대상으로 선택하지 않는다.

### 6.2 공통 컴포넌트

AppShell, Sidebar, BottomNav, PageHeader, EnvironmentBadge, FreshnessBadge, StatusBadge, Button, Field, SelectField, PercentField, MoneyInput, DefinitionList, MetricStrip, EmptyState, ErrorState, ConfirmDialog, DataTable, Disclosure만 먼저 만든다.

PositionTable/PositionCard, MarketChart, RunStatusPanel, OrderReview, BudgetSummary는 기능 컴포넌트다. 공통 UI가 거래 API를 직접 호출하지 않는다. PC·모바일에 별도 폴링 hook을 두지 않는다.

## 7. 프런트 파일 구조와 의존성

아래는 제안 구조다. 기존 테스트와 import를 단계적으로 이동하며 한 번에 전면 삭제하지 않는다.

~~~text
apps/web/src/app/
  AppShell.tsx, navigation.ts, router.ts
apps/web/src/design/
  tokens.css, reset.css, typography.css, layout.css
apps/web/src/ui/
  Button.tsx, Field.tsx, StatusBadge.tsx, Dialog.tsx, EmptyState.tsx
apps/web/src/shared/api/
  client.ts, errors.ts, capabilities.ts
apps/web/src/shared/format/
  money.ts, quantity.ts, percent.ts, time.ts
apps/web/src/shared/commands/
  pending.ts, commandState.ts
apps/web/src/overview/
  OverviewPage.tsx, api.ts, contracts.ts
apps/web/src/market/
  MarketPage.tsx, MarketQuotes.tsx, useMarketData.ts
apps/web/src/account/
  AccountPage.tsx, useAccountView.ts, contracts.ts
  AccountSummary.tsx, PositionTable.tsx, PositionCard.tsx, OpenOrders.tsx
apps/web/src/paper/
  PaperPage.tsx, PaperOrderForm.tsx, OrderReview.tsx, usePaperCommands.ts
apps/web/src/recommend/
  RecommendPage.tsx, RecommendationCard.tsx, useRecommendation.ts
apps/web/src/research/
  RunMonitorPage.tsx, RunCreatePage.tsx, RunHistoryPage.tsx
  BacktestPage.tsx, ReportPage.tsx, api.ts, contracts.ts, useResearchCommands.ts
apps/web/src/settings/
  ConnectionsPage.tsx, DiagnosticsPage.tsx, api.ts, contracts.ts
apps/web/src/chart/
  CandleChart.tsx
apps/web/src/visual/
  fixtureRoutes.ts, scenarios.ts
apps/web/public/fonts/
  PretendardVariable.woff2, OFL.txt
apps/web/tests/visual/
  pages.spec.ts, states.spec.ts
~~~

- App.tsx의 시장 표시와 탐색을 위 책임에 맞춰 분리한다.
- styles.css는 reset·토큰 진입점으로 축소하고 feature 스타일은 CSS Modules로 격리한다. 전역 태그 선택자의 화면별 덮어쓰기를 없앤다.
- Account.tsx를 분리할 때 single-flight·generation·freshness·AbortController를 보기 컴포넌트에 흩뜨리지 않는다.
- Research.tsx의 압축 JSX를 5개 페이지로 나누되 기존 저장·응답 유실 복구 기능을 보존한다.
- 기존 CandleChart는 새 디자인 옵션을 받도록 확장하고 수명주기·줌·범위 보존 코드를 재사용한다.
- 런타임에 큰 상태·UI 프레임워크는 필수가 아니다. Playwright Test는 시각 시험용 개발 의존성으로 추가·고정한다. 외부 아이콘 패키지를 추가하면 한 종류·버전·라이선스를 고정한다.

## 8. 데이터·상태·capabilities 계약

### 8.1 공통 값

~~~typescript
type SourceKind = 'REAL_MARKET' | 'ACCOUNT_READONLY' | 'SYNTHETIC' | 'FIXTURE';
type ExecutionEnvironment = 'PAPER' | 'DEMO' | 'LIVE' | 'NONE';
type Freshness = 'FRESH' | 'STALE' | 'UNAVAILABLE' | 'UNKNOWN';
type AnalysisSource = 'RULE' | 'LLM' | 'FIXTURE';

type AssetCode = string; // 서버 자산 메타데이터로 검증하며 네 개 자산으로 제한하지 않음
type Money = { amount: string; currency: AssetCode };
type AssetBalance = {
  assetCode: AssetCode;
  quantity: string;
  displayPrecision: number | null;
  valuation: Money | null; // 평가 통화·근거가 확인된 경우만 제공
};
type FreshnessMeta = {
  collectionStartedAt: string | null;
  collectionCompletedAt: string | null;
  lastSuccessAt: string | null;
  sourceUpdatedAt: string | null;
  ageSeconds: number | null;
  state: Freshness;
};
~~~

위 타입은 신규 프런트 표시 모델이며, 기존 API에 같은 이름의 필드가 있다는 뜻이 아니다. 기존 응답은 adapter에서 검증·변환한다. EUR·XAUT 등 다른 자산도 보존하며, 코인 수량·표시 정밀도·평가 통화를 분리한다. equity 같은 필드의 통화 단위를 확인하지 않고 USDT 평가금액으로 이름만 바꾸지 않는다.

### 8.2 환경·권한 분리

- 계좌 페이지: source=ACCOUNT_READONLY, execution=NONE, liveOrderEnabled=false.
- 기존 자동 가상실행: source=SYNTHETIC, execution=PAPER.
- 시장 페이지: 거래소 실제 조회라면 REAL_MARKET, 고정 시각 검수는 FIXTURE.
- 화면 전체에 하나의 PAPER 배지를 무조건 붙이지 않는다. 해당 데이터와 명령 대상 범위를 구분한다.
- capability는 source·product·command·environment·configRevision에 대해 판정한다.
- 서버는 product/orderType/side/leverage/configRevision을 다시 확인한다. 브라우저가 숨긴 값을 조작해도 허용되지 않아야 한다.

### 8.3 capability 조회 결과 — 제안 예시

~~~json
{
  "schema_version": 1,
  "live_order_enabled": false,
  "account_readonly": {"bybit": true},
  "manual_paper": {
    "synthetic_perpetual": {"enter": true, "close": true, "protect": true},
    "spot_limit": {"supported": false, "reason": "not_qualified"}
  },
  "automation": {
    "contract": "SYNTHETIC_AUTO_V1",
    "fixed_leverage": 10,
    "entry_pause": false
  },
  "analysis": {"sources": ["RULE"], "llm_enabled": false},
  "evaluation": {"editable_config": false}
}
~~~

이는 현재 코드 조사로 정리한 예시일 뿐 현재 서버의 실제 JSON이 아니다. 패치 후에는 서버가 검증된 지원 상태를 반환한다. true를 목업에 맞추기 위해 하드코딩하지 않는다.

### 8.4 화면 상태

모든 페이지는 최소 loading / empty / ready / stale / partial-error / error / recovering를 표현한다. 거래 명령은 idle / reviewing / submitting / confirmed / rejected / uncertain를 별도로 가진다.

network error와 rejected는 다르다. 응답을 못 받았다고 서버에 명령이 적용되지 않았다고 가정하지 않는다. uncertain에서는 새 키로 다시 보내지 않고 기존 요청을 조회·대조한다.

## 9. 거래·실행 동작을 목업과 맞추는 패치

### 9.1 수동 가상 주문 검토

~~~mermaid
stateDiagram-v2
  [*] --> Draft
  Draft --> Reviewing: 검토 요청
  Reviewing --> Ready: 검토 토큰 발급
  Ready --> Reviewing: 입력 변경·만료
  Ready --> Submitting: 가상 주문 확정
  Submitting --> Confirmed: 결과 확인
  Submitting --> Rejected: 명시 거절
  Submitting --> Uncertain: 응답 불명
  Uncertain --> Confirmed: 동일 요청 대조
  Uncertain --> Rejected: 미적용 확인
~~~

- preview에는 상품·환경·수량·지정가·비용·잔고 snapshot·설정 revision·유효기간을 포함한다.
- preview token은 서버가 소유하며 사용자 입력 hash·대상 장부·허용 명령에 바인딩한다.
- confirm은 token과 idempotency_key를 받는다. 최신 데이터·잔고·범위·비용을 재검사하고 기존 엔진 명령 경로로 실행한다.
- 입력·종목·환경이 바뀌면 기존 검토 토큰을 무효화한다. UI의 과거 검토 값을 최신 상태처럼 보여주지 않는다.
- 현물 매도는 보유 수량 내에서만 허용한다. 숏·레버리지·선물 청산 의미와 섞지 않는다.
- 지정가 PAPER는 미체결·체결·취소 상태를 갖춰야 한다. 단순히 마지막 가격에 즉시 체결시키고 지정가라고 표시하지 않는다.
- 기존 /commands는 V1에서 허용한 명령만 처리한다. 새 V2 현물 명령이 이 경로로 검토 토큰·확정 검사를 우회하지 못하도록 서버에서 거절하고 시험한다.
- 이미 처리된 동일 요청의 결과 조회는 검토 토큰이 나중에 만료돼도 기존 결과를 반환한다. 만료·재검사는 최초 적용 전 판정이며, 결과 재확인 때문에 중복 실행하지 않는다.
- 가상 체결 정책·슬리피지·부분 체결 가정을 버전으로 기록하고, 실제 거래소 체결을 재현했다고 주장하지 않는다.
- 기존 PAPER-v1 비용·가상 선물 데이터·과거 명령은 그대로 보존한다. 새 현물 지원은 qualification을 통과한 같은 Nautilus 경로의 별도 상품 계약이다.

### 9.2 일시정지·종료 상태

| 상태/명령 | 신규 진입 | 가격·봉 처리 | 기존 가상 포지션 관리 | 화면 문구 |
|---|---|---|---|---|
| RUNNING | 범위 안 허용 | 진행 | 진행 | 가상 자동실행 중 |
| ENTRY_PAUSED, 신규 추가 | 차단 | 계속 | 감축·기존 보호·청산 규칙 진행 | 신규 진입 일시정지 |
| PAUSED, 기존 계약 | 차단 | 중단 | 새 봉에 따른 처리는 중단 | 가상 실행 전체 일시정지 |
| PAUSED_RECOVERY | 차단 | 명시 재개 전 중단 | 저장 결과 대조 | 복구 확인 필요 |
| STALLED/FAIL_CLOSED | 차단 | 상태에 따라 중단 | 보장하지 않음, 실제 상태 표시 | 작업 중단·확인 필요 |
| STOPPING | 차단 | 진행 중 명령 정산 | 대상 가상 포지션 정리 | 정리 결과 확인 중 |
| STOPPED | 차단 | 중단 | 종료 결과 보존 | 종료 |

- ENTRY_PAUSED에서도 처리 봉·기간·위험 한도는 계속 적용한다. 재개 때 과거 봉의 놓친 진입을 소급 실행하지 않는다.
- ENTRY_PAUSED를 지원하지 않는 동안 현재 pause 버튼 문구는 전체 일시정지로 표시한다. 다른 동작인 척하지 않는다.
- stop은 기존 flatten 계약을 유지하고 가상 포지션 정리 후 종료로 표시한다. 실제 외부 포지션에는 적용하지 않는다.
- 전체 pause와 신규 진입 pause는 별도 명령·상태·감사 이벤트를 가진다.
- 프로세스 재시작으로 RUNNING이나 ENTRY_PAUSED를 자동 재개하지 않는다. 진행 결과 대조 후 명시 재개한다.

### 9.3 새 자동실행·평가 구성

- 자동실행 설정은 config_version·config_hash·실제 leverage·전략 기간·수량·한도·데이터 hash를 저장한다.
- 새로운 레버리지 설정은 서버 검증·native 진입·증거금·수수료·손익·정리·복구 경로까지 전달한다.
- 저장된 V1 실행은 fast/slow/10배 등 생성 당시 의미로 재생한다. 새 설정으로 과거 실행을 재해석하지 않는다.
- 백테스트는 dataset-only 기존 계약을 보존하고 새 버전에서 구간·평균·비용·검증 분리 설정을 받는다.
- 결과에는 요청한 설정과 실제 적용된 설정을 함께 hash로 대조할 수 있어야 한다.
- 전체·학습·미사용 구간의 기간과 성과를 구분한다. 포함 수수료·슬리피지·funding·AI 비용을 다른 항목으로 기록한다.
- 계산 완료는 실거래 검증·수익성 통과가 아니다. 짧은 예시 결과로 자동 승격하지 않는다.

## 10. 추천·AI·예산

### 10.1 기존 규칙 추천을 먼저 안전하게 연결

- 현재 rules.py는 SMA 5/20·15분·2배 고정이며 외부 LLM 호출이 아니다.
- 실제 source=RULE이면 규칙 추천·규칙 설명으로 표시한다. DeepSeek/Haiku 로고나 연결 중 배지를 붙이지 않는다.
- 추천 카드의 만료는 서버 기준으로 판정하고 화면에서도 시간이 흐르면 만료 표시를 갱신한다.
- approve/reject 대기 중 선택 변경을 막거나, 카드 ID·선택 generation을 검증해 이전 응답을 현재 카드에 반영하지 않는다.
- 서버 카드 승인·응답 유실 복구는 보존한다. 단순 버튼 busy만으로 중복 실행이 해결됐다고 보지 않는다.

### 10.2 외부 AI 연결의 후속 계약

- 기존 RULE 경로는 AI가 없어도 동작한다.
- LLM은 분석 후보·근거·반대 조건·무효 조건·누락 정보·유효기간을 반환한다.
- 위험 한도·수량·레버리지·비용·주문 적합성은 결정적 코드가 계산한다.
- 모델·제공자·정확한 ID·가격·권한은 연결 시 현재 공식 자료와 실제 계정으로 확인한다. 목업의 DeepSeek 글자를 모델 사용 증거로 취급하지 않는다.
- OFFLINE_TEST 기본, 명시적 NVIDIA_EVAL 또는 OPENROUTER_PAPER만 외부 호출한다.
- NVIDIA 실패를 유료 OpenRouter로 자동 전환하지 않는다. 무료 엔드포인트의 무제한 운영을 가정하지 않는다.
- 외부 AI에는 공개 시장 자료와 합성 PAPER 상태를 우선 제공한다. 거래소 키·실계좌 식별자·개인 잔고·외부 포지션은 보내지 않는다.
- 보유 약 $10은 사용자가 과거 충전했다고 한 정보다. 현재 잔액을 이 금액으로 하드코딩하지 않는다.

### 10.3 예산·비밀값

- 최초 연결 시험 누적 $1 이하, 초기 전체 실험 최대 $8 또는 확인 잔액보다 작은 한도.
- 사용자 월 총 AI 예산 최대 $100. 초기 한도를 자동 $100으로 올리지 않는다.
- 사용·예약·남은 내부 한도·불확실 비용을 구분한다. 제공자 잔액은 별도 검증한 경우만 표시한다.
- 요청 전 최대 비용을 transaction으로 예약하고 완료 후 실제 비용으로 정산한다.
- timeout 과금 불확실분은 예약을 유지해 대조한다. 재시작 시 예약·정산을 복구한다.
- 단가 불명·최대 과금 산정 불가·가격 변경 시 유료 호출 전에 차단·재확인한다. 실제 사용량이 반환되지 않으면 임의로 비용 0으로 정산하지 않고 예약을 유지한다.
- 같은 OpenRouter 계정을 다른 앱도 사용한다면 프로그램의 내부 원장만으로 계정 전체 $100 한도를 보장할 수 없다. 제공자 한도와 실제 계정 지출을 별도로 확인하고, 내부 한도와 구분해 표시한다.
- 자동 충전·추가 결제·비싼 모델 자동 승격은 하지 않는다.
- 키는 서버의 로컬 비밀 설정에서만 읽는다. 프런트·mockup fixture·응답·로그·프롬프트·커밋에 키 값을 넣지 않는다.
- 연결 UI는 등록됨·권한 확인 전·확인 실패·꺼짐을 구분한다. 등록됨이 실제 호출 권한 확인을 뜻하지 않는다.

## 11. 저장·복구·조회 상태

### 11.1 기존 저장 경계

| 대상 | 현재 기본 경로 |
|---|---|
| 수동 PAPER | PAPER_DB_PATH, work/phase3-data/paper.sqlite3 |
| 자동·연구 | PHASE4_DB_PATH, work/phase4-data/research.sqlite3 |
| 연구 데이터 | PHASE4_DATA_ROOT, work/phase4-data/datasets |
| 추천 | RECOMMENDATION_DB_PATH, work/recommendations.sqlite3 |

UI는 DB에 직접 쓰지 않는다. read model과 도메인 명령을 거친다. 종합 현황은 이 자료를 읽어 조합하며 새로운 공유 자금을 만들지 않는다.

### 11.2 이행 규칙

- 새 계약 전에 일관된 SQLite 백업을 만들고 다른 경로에서 읽기·원장 대조를 확인한다.
- 실행 중 DB/WAL/SHM을 서로 다른 순간에 복사한 것으로 일관성을 주장하지 않는다.
- schema·engine·automation metadata·이벤트 hash·append-only trigger를 확인한다.
- 새 V2는 V1 검증·재생을 계속 지원한다. 기존 immutable 이벤트·명령·보고서를 수정하지 않는다.
- V1 binary가 V2 DB를 안전하게 읽지 못하면 단순 git rollback으로 재가동하지 않는다. 호환성 검사 후 안전하게 중지·읽기 전용 대조한다.
- 백업 복원으로 백업 이후의 명령·거래 기록을 지우지 않는다. 복원 시험은 사본에서 한다.
- 브라우저의 paper-pending-command-v1 등 기존 미확정 요청 복구 키도 보존한다. 이름 변경 시 명시적 이행을 한다.

### 11.3 계좌·진단

- 계좌 기존 15초 재예약·20초 deadline·8초 공유·45/120초 기준을 보존한다. 이 값은 검증된 최적 매매 주기가 아니다.
- 화면의 시간 흐름만으로도 freshness를 갱신한다. 응답이 안 오면 이전 정상 배지가 유지돼선 안 된다.
- 과거 조회값을 남기면 나이·마지막 성공·현재 확인 불가를 함께 표시한다.
- 서버 응답, 거래소 조회, 작업 진행, DB 읽기, 미완료 요청은 서로 다른 상태다.
- 실제 포지션은 외부 포지션·프로그램 관리 안 함을 표시한다. 위험 감시가 구현되지 않은데 계속 보호된다고 표시하지 않는다.
- 진단에는 안전한 코드·영향·마지막 정상 시각·다음 행동을 표시한다. 키·인증 원문·stack trace를 일반 화면에 쏟지 않는다.

## 12. 실제 휴대전화 접속 아키텍처

모바일 CSS가 완성되어도 휴대전화의 localhost는 PC가 아니다. 현재 보호된 계좌·PAPER·연구·추천 API는 loopback Host·Origin 등 제한이 있으므로, 단순 LAN IP 입력이나 host=0.0.0.0만으로 지원됐다고 보고하면 안 된다.

단계를 분리한다.

1. **기본 모드:** 현재 로컬 PC loopback 유지. 모바일 viewport·터치·가상 키보드로 UI를 검수한다.
2. **사설 접속 모드:** 필요하면 같은 앱 앞에 인증된 HTTPS 진입점을 설계한다. PC와 휴대전화는 동일 origin의 UI/API를 사용한다. public 인터넷에 바로 공개하지 않는다.
3. **접근 제어:** 인증·기기 등록/폐기·세션 만료·CSRF·Origin·허용 Host·요청 크기·속도 제한을 갖춘 전용 진입 경로를 추가한다. 기존 loopback 보호를 전역 와일드카드로 해제하지 않는다.
4. **키 위치:** 거래소·AI 키와 거래 엔진은 PC/백엔드에 남는다. 모바일은 화면과 허용된 PAPER 제어만 사용한다.
5. **동작:** 모바일 앱/브라우저가 닫혀도 실행은 백엔드 소유다. PC 전원·네트워크가 끊기면 로컬 실행·감시도 멈출 수 있음을 실제 상태로 표시한다.

사설 VPN·LAN HTTPS·프록시 중 환경에 맞는 방법을 선택하되 계정·인증·허용 호스트를 확인하고 별도 접근 시험을 통과한다. 이 문서는 서버 구매·외부 배포·네트워크 설정 변경을 자동 승인하지 않는다.

## 13. 목업 일치·시각 검수의 완료 기준

### 13.1 참조 자산

- docs/ui-reference/original/{PC,Mobile}/에 최종 PNG 24장 보관.
- docs/ui-reference/manifest.json에 파일·hash·원본 크기·CSS 기준 크기·fixture ID·라우트 기록.
- docs/ui-reference/visual-exceptions.json에 공통 7메뉴, 고정 아이콘, 사실 계약 우선 등 정규화 차이 기록.
- docs/ui-reference/canonical/에 비교용 참조를 생성할 경우 원본 hash와 등비 환산·crop/pad 기록을 연결한다.
- 비교 도구에서는 원본을 등비로 표시한다. 참조 파일을 별도로 정규화하면 원본과 분리하고 변환 방법을 기록한다. 원본을 덮어쓰거나 정규화 과정에서 내용·비율을 바꾸지 않는다.

### 13.2 고정 시각 시험 모드

- dev/test 전용 fixture 서버·라우트를 사용하고 화면에 예시 데이터 배지를 표시한다.
- 운영 빌드에서 fixture 모드·데이터·키 없는 가짜 정상 응답이 기본 활성화되지 않게 한다.
- 고정 clock=2026-11-01T00:10:03Z, timezone=Asia/Seoul, locale=ko-KR.
- 같은 브라우저 버전·OS·폰트·viewport·deviceScaleFactor를 고정한다.
- 폰트 로딩 완료·UI 준비·차트 렌더 완료를 기다리고 애니메이션·caret를 고정한다.
- 네트워크는 정해진 fixture 응답만 허용해 실제 시세·계좌·유료 AI 호출이 일어나지 않게 한다.
- fixture에서만 목업 숫자를 사용한다. 실제 모드와 같은 컴포넌트를 렌더링하되 실제 원장은 쓰지 않는다.

### 13.3 2단계 비교

**A. 목업 → 첫 구현**

- 같은 CSS 기준 크기로 원본과 실제 캡처를 나란히 보고, 50% 겹침·차이 이미지로 위치·크기·정렬을 검사한다.
- 메뉴·제목·숫자·단위·경고·CTA·표 열·문단 줄바꿈·차트 비율을 영역별로 비교한다.
- 첫 기준은 PC 종합 현황과 모바일 자동실행의 공통 shell부터 맞춘다. 12개 페이지를 먼저 각각 임의로 만들지 않는다.
- 주요 경계·패딩은 정규화 명세 대비 2 CSS px 이내를 초기 목표로 삼되 이는 제안 검수 기준이다. 실제 측정 결과를 남긴다.
- 생성 글자 윤곽·안티앨리어싱·그라데이션 때문에 원본과 모든 픽셀이 같다고 단정하지 않는다.

**B. 검수된 구현 → 이후 회귀**

- 실제 검수한 DOM 화면을 Playwright baseline으로 고정한다.
- 동일 환경에서는 우선 maxDiffPixels=0, threshold=0.2의 엄격한 비교로 시작한다. threshold=0.2는 작은 색 차이 판정 기준이며 원본 목업과 100% 같다는 뜻이 아니다.
- 반복되는 렌더 노이즈가 관측되면 원인을 먼저 제거한다. 불가피하면 영역별 작은 예외·근거·수치를 기록한다. 임계값을 넓혀 통과시키지 않는다.
- 차트·주요 숫자·CTA·경고·메뉴 전체를 마스킹하지 않는다. 동적 데이터는 마스크 대신 clock·fixture로 고정한다.
- baseline 자동 갱신으로 실패를 없애지 않는다. 변경 전후·차이·변경 이유를 리뷰 자료로 남긴다.

### 13.4 실제 기능·접근성 검수

| 검사 | 통과 조건 |
|---|---|
| 24개 기본 화면 | 최종 참조·fixture·라우트·캡처가 1:1 연결 |
| 모바일 긴 페이지 | viewport + fullPage 캡처, CTA·메뉴·키보드 겹침 없음 |
| 한글 | 16/14px 기본, 문장 낱글자 끊김·붙은 문단·과한 줄 길이 없음 |
| 숫자 | 작은 수량·큰 금액·부호·통화·정렬·null/0 구분 |
| 입력 | label·단위·오류 가까이, 조작 44×44px 이상, 주 버튼 48px |
| 탐색 | 직접 URL·새로고침·뒤로·앞으로·활성 메뉴 일치 |
| 차트 | 확대·이동·리사이즈·갱신 후 범위 유지·형성 중 봉 구분 |
| 상태 | 로딩·빈 값·지연·부분 실패·전체 실패·복구를 정상과 구분 |
| 명령 | 검토·중복 클릭·응답 유실·동일 키 복구·만료 재검사 |
| 접근성 | 키보드·보이는 focus·dialog focus 복귀·명도 대비·확대·320px reflow |
| 실제 연결 | 실제 API/fixture/미실행/차단 결과를 구분해서 보고 |

브라우저·OS마다 글자 렌더링이 달라질 수 있으므로 다른 플랫폼의 픽셀 baseline을 섞지 않는다. 같은 환경에서의 회귀와 실제 Windows·휴대전화 조작 시험을 둘 다 수행한다.

---

## 14. 패치 실행 계획

각 Task는 독립 검수 가능한 변경 단위다. 공통 계약 확정 → UI·백엔드 병렬 → 통합 검수를 따른다. 저사양 PC에서 무거운 작업은 순차 실행한다. 작업 브랜치를 사용하고 자동 머지하지 않는다.

### Task 1. 기준점·자산·기능 계약 고정

**Files:** docs/ui-reference/manifest.json, docs/ui-reference/visual-exceptions.json, docs/UI_ARCHITECTURE.md. 기존 docs/WORK_LOG.md 갱신.

**Interfaces:** 입력=최신 로컬·원격 SHA와 최종 ZIP. 출력=24개 PageReference, 기능 차이 목록, 보존 대상 DB·미확정 키 목록.

- [ ] git status·worktree·실행 프로세스·DB 실제 경로와 최신 원격 SHA를 기록한다.
- [ ] ZIP hash·24장·수정본 3개·PC/모바일 치수를 확인하고 원본을 보관한다.
- [ ] 7개 메뉴·5개 탭·타이포·실제 의미 우선이라는 차이 계약을 저장한다.
- [ ] 실행 중 DB의 일관된 백업을 사본에서 읽어 검증한다.
- [ ] 보존 자료와 manifest를 검토하고 기준 커밋을 남긴다. 실제 키·계좌 캡처는 커밋하지 않는다.

### Task 2. 디자인 토큰·공통 UI·AppShell

**Files:** apps/web/src/design/*, ui/*, app/AppShell.tsx, navigation.ts, 기존 styles.css·paper.css·research.css.

**Interfaces:** AppShell({route, context, children}); Button/Field/StatusBadge는 서버 호출이 없는 표현 컴포넌트. 토큰은 5절의 값.

- [ ] 공통 shell을 먼저 PC·모바일 고정 fixture로 렌더링한다.
- [ ] 전역 small/time 10px 등 기존 선택자 간섭을 제거한다.
- [ ] 폰트·아이콘·색·숫자 정렬·keep-all·48px 입력을 공통 적용한다.
- [ ] 긴 한국어·긴 ID·작은 잔고·큰 금액에서 넘침/잘림을 검사한다.
- [ ] 키보드·focus·dialog 복귀·확대·하단 safe-area를 브라우저로 검수한다.
- [ ] 첫 PC/모바일 shell 겹침 자료를 남기고 커밋한다. 저영향 CSS를 그대로 복제하는 단위 테스트는 만들지 않는다.

### Task 3. URL·API adapter·기존 조회 화면 이행

**Files:** app/router.ts, shared/api/*, market/*, account/*, chart/CandleChart.tsx, 기존 App.tsx·Account.tsx.

**Interfaces:** parseLocation(url: URL): Route; navigate(route: Route, options?: {replace?: boolean}): void; subscribeRoute(listener: () => void): () => void. 조회 hook은 검증된 PageViewModel을 반환한다.

- [ ] 기존 query view와 12페이지를 파싱하고 push/replace/popstate를 구현한다.
- [ ] 뒤로 가기·직접 URL·새로고침·잘못된 ID를 실제 브라우저로 확인한다.
- [ ] 기존 계좌 single-flight·deadline·generation·freshness를 보존해 hook으로 분리한다.
- [ ] 시세·계좌 PC/모바일 표현을 연결하고 숫자·데이터 환경을 구분한다.
- [ ] 차트 색만 계약에 맞춰 변경하고 범위 보존·재마운트·리사이즈 회귀를 실행한다.
- [ ] 최종 UI 캡처와 기존 조회 안전성 검사 통과를 남기고 커밋한다.

### Task 4. capabilities·overview·diagnostics 조회 모델

**Files:** src/multi_exchange_trader/capabilities.py, overview/api.py·service.py, diagnostics/api.py·service.py, 기존 api.py. 제안 파일이며 구현 전 현재 구조에 맞춰 확정한다.

**Interfaces:** capabilities는 8절의 기능·상품·명령 계약을 반환한다. overview는 장부별 범위를 유지한 read model, diagnostics는 기능별 상태·미완료 수·안전한 오류 코드다.

- [ ] 실제 엔진이 지원하지 않는 현물 지정가·entry_pause·가변 레버리지를 false로 반환하는 계약 시험을 먼저 작성한다.
- [ ] 기존 서비스만 읽어 조회 모델을 만들고 새 공유 장부를 만들지 않는다.
- [ ] 일부 거래소/AI 실패가 다른 읽기 화면을 함께 막지 않는지 확인한다.
- [ ] 준비 상태의 최상위 ok를 거래 가능으로 표시하지 않는지 확인한다.
- [ ] 01 종합·12 진단의 fixture와 실제 데이터 경로를 구분해 검수 후 커밋한다.

### Task 5. 수동 주문 검토·현물 PAPER qualification

**Files:** paper/api.py·contract.py·native.py·store.py, 새 paper/order_preview.py, 프런트 paper/*.

**Interfaces:** create_order_preview(request)→preview_id/계약 hash/비용/만료; confirm_order(preview_id,idempotency_key)→command_id/상태. 기존 /commands와 같은 엔진·원장을 사용한다.

- [ ] 기존 합성 선물 enter/close/protect와 새 현물 주문을 상품별로 명시 구분한다.
- [ ] 현물 시장가·지정가·취소·미체결·잔고 예약·수수료·숏 금지 qualification을 수행한다.
- [ ] 미지원 입력은 UI와 서버에서 거절한다. 준비되지 않았는데 capability를 true로 바꾸지 않는다.
- [ ] 새 현물 명령을 기존 V1 /commands에 직접 보내 검토 단계를 우회하려는 요청을 서버가 거절하는지 시험한다.
- [ ] 검토 후 가격·잔고·설정 변경, 만료, 이중 클릭, 처리 후 응답 유실, 새로고침 후 동일 키 복구를 시험한다.
- [ ] 기존 PAPER-v1 원장·수수료·보호·부분 정리·재시작 회귀를 통과한다.
- [ ] 05 화면과 검토 dialog를 실기능에 연결하고 캡처·실행 증거 후 커밋한다.

### Task 6. 추천 목록·승인 경합·실제 분석 source

**Files:** recommendations/api.py·book.py·service.py·rules.py, recommend/*.

**Interfaces:** RecommendationView={id,source,snapshot,side,reason,invalidation,missingData,expiresAt,reviewState}. 기존 카드 승인·거절은 유지한다.

- [ ] 승인 응답 대기 중 선택을 변경하는 시험에서 이전 카드 응답이 현재 선택을 덮지 못하게 한다.
- [ ] UI clock 진행만으로 만료 표시와 버튼이 바뀌는지 시험한다. 서버 만료 재검사도 유지한다.
- [ ] list/detail을 추가하고 요청 ID·대상·source를 검증한다.
- [ ] 실제 규칙 추천에 RULE 배지를 표시하고 외부 LLM 표시를 넣지 않는다.
- [ ] 승인 응답 유실·동일 카드 재시도에서 한 번만 반영되는 회귀를 통과한다.
- [ ] 04 화면·검토 단계·만료·누락 상태를 캡처하고 커밋한다.

### Task 7. 자동실행 상태·가변 설정·평가 구성 V2

**Files:** research/automation.py·scheduler.py·storage.py·api.py·evaluation.py·native.py, research/*.

**Interfaces:** 새 config_version/config_hash/leverage, ENTRY_PAUSED 명령·상태, EvaluationConfig. V1 reader·기존 명령은 보존한다.

- [ ] ENTRY_PAUSED에서 봉 처리는 진행되지만 신규 진입은 없고 기존 가상 포지션 관리만 진행되는 시험을 작성한다.
- [ ] 전체 PAUSED는 기존 의미를 유지하는지, resume 시 놓친 진입을 소급하지 않는지 확인한다.
- [ ] 새 leverage가 저장·증거금·native 진입·비용·재생·복구까지 같은 값인지 확인한다.
- [ ] V1 이벤트·hash·metadata를 바꾸지 않고 V2를 읽는 버전 경로를 추가한다.
- [ ] 평가 설정의 구간·평균·비용이 실제 결과 config_hash와 일치하는지 확인한다.
- [ ] stop·진행 중 thread·응답 유실·DB lock·재시작·PAUSED_RECOVERY 회귀를 수행한다.
- [ ] 06–10 화면의 입력·저장 설정·실행 결과를 같은 계약으로 연결하고 커밋한다.

### Task 8. AI 설정·예산·선택적 실제 호출

**Files:** 새 ai/providers.py·budget.py·service.py·api.py, settings/*, diagnostics 조회 모델.

**Interfaces:** provider 상태는 등록/권한/실제 검증 시각을 분리한다. budget.reserve(request_id,max_cost), settle(request_id,actual_cost), reconcile(request_id)는 원자적·멱등적이어야 한다.

- [ ] OFFLINE_TEST에서는 외부 호출이 없고 UI에 미설정/꺼짐이 보이는지 확인한다.
- [ ] 금액은 Decimal 문자열로 처리하고 동시 예약·budget 부족·timeout·재시작을 시험한다.
- [ ] 서버·PAPER 작업과 AI 큐를 분리해 모델 장애가 기존 조회·위험 처리 루프를 막지 않게 한다.
- [ ] 키·권한·확인 잔액이 준비된 경우에만 작은 한도 안에서 실제 연결한다. 준비되지 않은 연결만 미검증으로 남긴다.
- [ ] 실제 연결 전 source=LLM·연결 정상·예산 실제 사용이라고 표시하지 않는다.
- [ ] 11 화면과 부분 장애 12 화면을 캡처하고 비용·권한 검증 결과를 구분해 커밋한다.

### Task 9. 24개 시각 검수·실제 브라우저 회귀

**Files:** apps/web/playwright.config.ts, tests/visual/pages.spec.ts·states.spec.ts, docs/qa/ui-rebuild/.

**Interfaces:** PageReference={pageId,route,viewport,fixtureId,originalSha}; 산출물=원본/실제/겹침/차이/상태/소스 SHA.

- [ ] 고정 OS·브라우저·폰트·locale·timezone·clock·fixture로 24개 화면을 캡처한다.
- [ ] 원본 목업과 영역별로 겹쳐 보고 토큰·컴포넌트에서 차이를 수정한다.
- [ ] 모바일은 첫 viewport와 fullPage를 검사한다. 큰 표 축소·버튼 겹침·글자 축소로 통과시키지 않는다.
- [ ] 360/320px·200% 확대·키보드·가상 키보드·긴 한국어·큰 숫자를 시험한다.
- [ ] 느린 응답·역전·잘못된 JSON·부분 실패·오프라인·백엔드/worker 중단·복구를 실제 테스트 환경에서 시험한다.
- [ ] 실제 검수한 DOM baseline을 별도로 고정하고 회귀 검사·차이 보고서를 CI에 연결한다.
- [ ] 동일 최종 SHA에서 기존 Python·프런트 검사·시각 시험 결과를 남긴다.

### Task 10. 실제 휴대전화 접근 — 별도 접속 조건이 준비된 경우

**Files:** 접근 인증·세션·허용 Host 설정의 전용 모듈과 테스트. 기존 loopback 보호를 삭제하지 않는다.

**Interfaces:** 인증된 동일-origin HTTPS 진입점, 허용 기기 세션, CSRF·권한 검사, 키 없는 모바일 응답.

- [ ] 기본 loopback 접근이 그대로 차단·허용되는 회귀를 먼저 확인한다.
- [ ] 사설 접속의 인증·세션 만료·폐기·CSRF·잘못된 Host/Origin·읽기/명령 권한을 시험한다.
- [ ] 실제 휴대전화에서 탐색·조회·PAPER 검토·응답 유실을 확인한다.
- [ ] 접속 환경이 준비되지 않으면 반응형 UI 완료와 원격 접속 미검증을 별도 보고한다.
- [ ] 외부 배포·서버 구매·실거래 활성화를 이 Task에 끼워 넣지 않는다.

## 15. 병렬 작업·통합 순서

| 담당 | 파일 소유 범위 | 의존 |
|---|---|---|
| A: UI | design/ui/app, 각 페이지 표현·반응형 | Task1·공통 API 계약 |
| B: 도메인·API | capabilities·preview·자동상태·평가·AI예산 | 기존 원장·버전 계약 |
| C: 독립 검토·QA | fixture·계약 시험·브라우저·시각 보고 | A/B 통합 결과 |

- 동시 최대 3명, 같은 파일은 한 명이 소유한다.
- navigation/types/capabilities/명령 상태의 작성 책임자를 먼저 정한다.
- worktree·포트·시험 DB·브라우저 탭을 분리한다. 실제 사용자 DB를 공유 시험하지 않는다.
- Task1→Task2의 shell 고정 후 UI 페이지 작업을 확장한다. 기능 패치는 검증된 계약부터 순서대로 연결한다.
- 수동 주문·자동 상태·평가 schema 변경은 중간 통합 검토를 둔다. 서로 다른 엔진 경로를 각 담당이 새로 만들지 않는다.
- 통합 담당은 새 최종 SHA에서 시각·기능·원격 CI를 확인한다. 예전 성공 로그로 완료 처리하지 않는다.

## 16. 검사 명령·제출 자료

현재 package.json에 있는 프런트 명령:

~~~sh
cd apps/web
npm run test -- --run
npm run lint
npm run typecheck
npm run build
~~~

의존성 설치는 lockfile과 로컬 변경을 확인한 뒤 npm ci를 사용한다. 새 Playwright 의존성·scripts는 고정 버전으로 추가하고 test:visual·test:e2e 명령을 문서화한다. 현재 이미 존재하는 명령이라고 가정하지 않는다.

Python은 기존 pyproject·CI에 정의된 pytest·Ruff·mypy·네이티브 검사를 그대로 실행한다. 기존 CI gate·secret scan·dependency 검사도 유지한다. 실패를 숨기려고 규칙·테스트·타입 검사를 비활성화하지 않는다.

최종 제출:

1. 시작 SHA→최종 SHA, 브랜치·PR·원격 일치·해당 SHA CI.
2. 24페이지 참조→구현 캡처→겹침·차이, 정규화 예외 목록.
3. 실제 데이터·fixture·미실행·차단을 구분한 검증표.
4. 현물 지정가·ENTRY_PAUSED·가변 레버리지·평가 설정 각각의 실제 지원 판정.
5. 미확정 요청·명령 중복·DB·worker·재시작·복구 결과.
6. AI 실제 모델·제공자·권한·비용·예약·미검증 범위.
7. 실제 휴대전화 접근 검증 여부와 안전한 시작·종료·복구 방법.

## 17. 총괄 GPT의 직접 구현·검수 실행 계약

~~~text
총괄 GPT는 이 문서와 TRADING_UI_MOCKUPS_PC_MOBILE_24_20261101.zip을 기준으로
goldeget/multi-exchange-ai-trader의 기존 작업을 직접 이어서 구현·검증한다.
사용자에게 Cursor 인계·코드 복사·에이전트 지휘를 맡기지 않는다.

먼저 최신 로컬/원격/미커밋/DB/미확정 명령을 보존하고 기준 SHA를 기록하라.
원본 24장은 변경하지 말고, 공통 7메뉴·5탭·타이포·단위·환경 계약을 고정하라.
실제 React 컴포넌트로 구현하고 사진 배경·투명 버튼 방식으로 대체하지 마라.

기존 R1~R6 안전 패치와 엔진·원장·복구 계약을 유지하라.
목업과 다른 현물 지정가·신규진입만 일시정지·가변 레버리지·평가 구성은
UI 이름만 바꾸지 말고 명시적 버전·서버·엔진·테스트까지 패치하라.
미지원 상태는 capabilities로 표시하고 실제로 지원하기 전에는 활성화하지 마라.
규칙 추천을 외부 AI 분석이라고 표시하지 마라.

같은 고정 fixture·시간·폰트·viewport로 24개 화면을 캡처하고,
원본과 겹침·차이 비교로 크기·여백·한글·숫자·CTA·상태 표시를 수정하라.
검수한 실제 구현 baseline을 고정하고 이후 시각 회귀를 CI에 연결하라.
실제 API·장애·중복 클릭·응답 유실·재시작·모바일·키보드도 검증하라.

최대3개 독립 담당으로 병렬 진행하고 공통 파일은 한 명이 소유하라.
무거운 검사·네이티브 시험·브라우저는 저사양 PC에서 순차 실행하라.
검토 가능한 커밋·PR·작업 로그를 남기고 최신 최종 SHA의 CI까지 확인하라.

실제 주문·기존 수동 포지션 변경·추가 결제·외부 배포·자동 머지를 하지 마라.
완료와 미검증을 구분하고, 사진100%·무중단·수익보장을 근거 없이 선언하지 마라.
~~~

## 18. 근거와 공식 참고

### 현재 소스·원격 메타데이터

- https://github.com/goldeget/multi-exchange-ai-trader/commit/681d4d67dbabd9a3846864f7f60ad10ecf05242b
- https://github.com/goldeget/multi-exchange-ai-trader/actions/runs/41258880582
- https://github.com/goldeget/multi-exchange-ai-trader/blob/681d4d67dbabd9a3846864f7f60ad10ecf05242b/apps/web/package.json
- 같은 SHA 아래 apps/web/src/App.tsx, styles.css, account/Account.tsx, recommend/Recommend.tsx, research/Research.tsx, paper/Paper.tsx, chart/CandleChart.tsx.
- 같은 SHA 아래 src/multi_exchange_trader/api.py, account/, paper/contract.py·native.py·store.py, research/automation.py·scheduler.py·storage.py·api.py, recommendations/rules.py·service.py·book.py·api.py.
- 같은 SHA 아래 docs/STABILITY.md와 기존 tests/account/, tests/research/, tests/recommendations/.

### 공식 문서

- Playwright 시각 비교·플랫폼 차이: https://playwright.dev/docs/test-snapshots
- Playwright screenshot assertion·허용 오차: https://playwright.dev/docs/api/class-pageassertions
- W3C reflow·320 CSS px: https://www.w3.org/WAI/WCAG22/Understanding/reflow.html
- W3C 최소 명도 대비: https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html
- Pretendard 공식 저장소·폰트·라이선스: https://github.com/orioncactus/pretendard
- 기존 차트 공식 자료: https://tradingview.github.io/lightweight-charts/

공식 자료는 검수 도구와 접근성 근거이며, 이 프로그램의 실제 기능·수익·시각 일치가 이미 검증됐다는 증거가 아니다.
