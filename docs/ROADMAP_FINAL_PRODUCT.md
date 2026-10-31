# 최종 제품 로드맵과 현황 감사 (Phase 0)

작성 기준: 2026-10-31 KST, `main` = `00a89ee` (PR #1 병합 커밋). 이 문서는 **감사와 계획**입니다. 기능 코드는 바꾸지 않았고, 수익성이나 실거래 준비 완료를 뜻하지 않습니다.

## 1. 확인한 저장소 상태

| 항목 | 결과 |
|---|---|
| 로컬 `main` vs `origin/main` | 둘 다 `00a89ee87acd084cd88d76241e4ab72a212a4b3b` (동일) |
| PR #1 | MERGED (2026-10-31T03:26:43Z), 병합 커밋 `00a89ee` |
| 브랜치 | 로컬 `main`만 존재, 원격 `main`, `codex/initial-platform` |
| 미커밋 변경 | 없음 (작업 트리 깨끗함) |
| 고정 의존성 | `nautilus-trader==1.231.0`, Python 3.12 (`pyproject.toml`, `uv.lock`) |
| 코드 규모 | Python 소스 약 2,400줄 (`src/multi_exchange_trader`), React 화면 3개 (공개 시장 / PAPER / 연구) |
| 실행 환경 | Native 엔진은 **Linux x86_64 전용** (Windows에서는 PAPER/연구 native worker가 실행되지 않음. `README.md` 참고) |

## 2. NautilusTrader 1.231.0 어댑터 확인 범위

확인 대상: GitHub 태그 `v1.231.0` (태그 객체 `d3e1685e…`, 커밋 `27a8e54e7ac3c57d6cbf8891f0283dfbaee97317`). 확인 방법: 해당 태그의 `nautilus_trader/adapters/{bybit,binance,okx}/` 파일 목록, `config.py`, `docs/integrations/*.md`.

| 항목 | Bybit | Binance | OKX |
|---|---|---|---|
| 어댑터 존재 (data / execution) | 있음 (`data.py`, `execution.py`) | 있음 (spot/futures 하위 포함) | 있음 |
| 통합 문서의 안정성 표기 | stable | stable | stable |
| 환경 설정 | MAINNET / DEMO / TESTNET | LIVE / TESTNET / DEMO | LIVE / DEMO (TESTNET 없음) |
| 주문 종류 (문서) | 시장가·지정가·조건부 등 표 존재 | spot OCO, futures 조건부(algo) 등 | 조건부(algo), 트레일링 등 |
| 포지션 모드 | One-way / Hedge(BothSides, `positionIdx`) | Futures One-way / Hedge | Net / Long-Short (계정 설정은 거래소 앱에서 변경, 어댑터는 감지) |
| 외부 주문 처리 | 프로그램이 만들지 않은 주문은 `external_order_claims` 또는 `EXTERNAL` 전략으로 분류 (문서 341~342행) | 외부 주문을 상태 보고서로부터 생성 (문서 349행) | 문서에 reconciliation 언급 있음 |
| 응답 유실 처리 | 미확인 | 문서: 상태 불명 시 어댑터가 거짓 거절을 내지 않으며, 전략은 재제출하지 말고 reconcile을 기다려야 함 (296~306행) | 미확인 |
| 주의 | **Demo는 WebSocket Trade API 미지원**(HTTP 사용), `tp_trigger_price`/`sl_trigger_price` 미지원. Demo 공개 시세는 메인넷 공개 스트림 사용 | `use_gtd`/`reduce_only`/`position_ids` 옵션이 다름. Hedge 모드에서 reduce_only 비활성 | Spread 주문 목록·FOK·modify 일부 미지원 (문서 423행) |

**직접 확인한 것**: 위 표의 모든 내용은 태그 시점의 파일/문서 내용을 읽은 것입니다.

**확인하지 못한 것 (검증 필요)**:
- 실제 거래소 서버에 연결한 동작 (이 작업에서는 어떤 거래소 요청도 보내지 않음).
- 재시작 시 reconcile 세부 설정 (`LiveExecEngineConfig`의 reconciliation 옵션)은 이번에 읽지 않았음. 문서의 "있다"는 설명을 실제 Demo에서 시험한 적이 없음.
- 부분 체결, 취소 실패, 응답 유실 시 Bybit/OKX의 실제 동작.
- 거래소별 최소 주문 수량, 수수료 체계, Demo 계정의 호가 깊이는 Nautilus가 보장하지 않음.
- 한국/현재 네트워크에서 Bybit·Binance 선물 접속 가능 여부는 **차단/미검증** 상태이며 우회 시도 없음.
- 정확한 wheel(manylinux) 실행은 기존 Phase 3 증빙(`docs/qa/phase3/qualification.md`)을 따름. 이번에 재실행하지 않음.

## 3. 현황 표 (최종 제품 목표 대비)

분류: **완료** / **부분 구현** / **미구현** / **차단**.

### 3.1 흐름 단계

| # | 목표 항목 | 분류 | 근거와 설명 |
|---|---|---|---|
| 1 | 단일 대시보드 (다중 거래소) | 부분 구현 | `apps/web/src/App.tsx`가 공개 시장/PAPER/연구 화면을 제공. 거래소 선택 UI는 있으나 계정·포지션·추천 화면 없음 |
| 2 | 데이터 수집 – Binance spot | 완료 (제한적) | `market_data/adapters/binance.py`, REST 폴링 5초. WebSocket 없음 |
| 3 | 데이터 수집 – Bybit | 차단 | `adapters/bybit.py` 코드는 있으나 지역 제한으로 접속 불가 (`docs/qa/phase2a/README.md`: 6개 시장 BLOCKED). 우회 시험 안 함 |
| 4 | 데이터 수집 – Binance 선물 | 차단 | 위와 동일 (`MARKET_DATA_BLOCKED_HOSTS`로 사전 차단) |
| 5 | 데이터 수집 – OKX | 미구현 | `adapters/okx.py`는 항상 "unverified" 오류만 반환 (공식 계약 미검증) |
| 6 | 분석 / 지표 | 부분 구현 | 이동평균(SMA) 교차만 존재 (`research/strategy.py`). 다중 시간대·변동성 등 없음 |
| 7 | 전략 신호 | 부분 구현 | `SMA_CROSS_V1`, `BUY_HOLD_V1` 두 가지만. 실시간 신호 생성/만료(TTL) 없음, 연구용 과거 데이터 전용 |
| 8 | AI 설명 | 미구현 | `docs/AI_DESIGN.md` 설계만 있음. 코드·모델 호출 없음 |
| 9 | 추천 항목(진입 구간, SL/TP, 손익비, 수량/레버리지/증거금, 예상 손실 %, 근거, 무효 조건) | 미구현 | 추천 데이터 모델 없음 |
| 10 | 결정적 위험 점검 | 부분 구현 | 가상 자동실행의 최대 손실·낙폭·노출·연속 손실 (`research/automation.py`), PAPER 주문 검증 (`paper/contract.py`, `paper/native.py`). 실데이터·실제 신호와 연결되지 않음 |
| 11 | 사용자 승인 (모드 A) | 미구현 | 승인 흐름, 만료, 주문 직전 재검증 없음 |
| 12 | 제한적 자동매매 (모드 B) | 부분 구현 | 합성(synthetic) BTC-PAPER/ETH-PAPER 가상 계좌에서만 동작. 허용 거래소/종목/전략 목록, 일일 손실 한도, 최대 레버리지/포지션 수, 신호 TTL, 데이터 지연·연결 실패 정지 없음 |
| 13 | 일시정지/중지/긴급중지 의미 | 부분 구현 | Pause = 포지션 유지, Stop = native reduce-only 정리, 한도 위반 = HALTED (`automation.py`, README). "신규 진입 차단 / 미체결 취소 / 포지션 청산"의 3단계 분리와 긴급중지는 정의되지 않음 |
| 14 | 주문·체결 확인 (거래소) | 미구현 | 거래소 주문 경로 없음. 가상 체결만 (`paper/native.py`) |
| 15 | 포지션 관리 | 부분 구현 | 가상 포지션, 부분/전체 종료, SL/TP/OCO, 청산 모델 (`paper/`). 거래소 포지션 조회 없음 |
| 16 | 기록 / 성과 | 부분 구현 | append-only 원장, 감사 로그, OOS 리포트 (`paper/store.py`, `research/storage.py`). 실제 추천·주문 성과 기록 없음 |

### 3.2 안전 원칙

| 원칙 | 분류 | 근거 |
|---|---|---|
| 비밀키/계좌/주문 접근 없음 | 완료 | 소스에 키 입력·서명·비공개 엔드포인트 없음 (`docs/SECURITY.md`, `market_data/`) |
| 프로그램 주문과 외부 주문/포지션 표시 구분 | 미구현 | 문서상 "기존 수동 Bybit 포지션은 외부·비관리"만 명시. 태그 필드와 코드 없음 |
| 사용자의 기존 수동 Bybit 포지션 자동 채택 금지 | 부분 구현 | 계정 접근 자체가 없어 현재는 구조적으로 불가. 이후 Phase에서 반드시 테스트로 고정해야 함 |
| AI가 위험 한도를 바꾸거나 자유 문장으로 주문 불가 | 미구현 (설계만) | `docs/AI_DESIGN.md`에 원칙 명시, AI 자체가 없음 |
| 숫자는 검증된 데이터/코드에서만 | 부분 구현 | 연구·PAPER는 Decimal 문자열과 체크섬 사용. AI 경로는 없음 |
| 단일 실행 경로의 주문 생성/멱등성/정합 | 부분 구현 | PAPER는 SQLite 멱등 키와 native 정합 (`paper/store.py`). 거래소 주문 경로에는 없음 |
| 거래소 Demo 수명주기 (부분 체결, 취소, 응답 유실, 재시작 정합) | 미구현 | 어떤 Demo 계정도 사용하지 않음 |
| 지역 제한 | 차단/미검증 | Bybit·Binance 선물 6개 시장 BLOCKED. 우회 없이 현상 유지 |
| 장기 실데이터 전략 검증 | 부분 구현 | 30일/720봉 spot 표본 1회. 실제 OOS 결과는 손실 (아래) |
| Windows 실행 | 차단 | native worker는 Linux seccomp 전제 → WSL/Linux 필요 |

### 3.3 현재 연구 결과 (손실 포함, 있는 그대로)

`docs/qa/phase4/README.md`: 실제 Binance spot BTC/ETH 최종 OOS(마지막 20%)는 **둘 다 손실**입니다.

| 종목 | 순손익 (가상 USDT) | 순수익률 | 거래 수 |
|---|---:|---:|---:|
| BTC | -58.38587964 | -0.58% | 6 |
| ETH | -24.52092640 | -0.25% | 6 |

표본이 짧고(30일) 거래 6건이라 통계적 결론을 낼 수 없습니다. 선물, funding, 공유 자본 포트폴리오는 미검증(UNRUN)입니다.

## 4. 단계별 계획 (순서 고정)

각 단계는 이전 단계의 완료 기준을 통과해야 시작합니다. 비밀키는 파일·로그·커밋에 넣지 않습니다. 출금 권한 키는 어떤 단계에서도 사용하지 않습니다.

### Phase 1. PAPER 안정화 (Linux/WSL)
- 목표: Linux에서 `uv sync --locked`, pytest, ruff, mypy, npm test/build가 모두 통과하는 기준선 확보. (WSL 설치는 다른 작업이 진행 중이므로 그 작업 종료 후 진행)
- 완료 기준: CI와 동일한 명령이 로컬 Linux에서 통과, 기존 DB/work 데이터 보존, 재시작 복구 확인.
- 사용자 준비: 없음.

### Phase 2. Bybit 공개 시세 + 읽기 전용 계정
- 목표: Bybit 공개 시세 확인(차단 여부 있는 그대로 기록), 읽기 전용 키로 잔고·포지션·주문 조회만. 주문 함수는 코드에 넣지 않음.
- 설계 필수: 모든 조회 결과에 `origin = program | external | unknown` 태그. 기존 수동 포지션은 항상 `external`이며 자동 채택 불가.
- 완료 기준: 읽기 전용 테스트(권한 없는 호출은 거절됨), 키가 로그·화면·파일에 노출되지 않는지 테스트, 지역 제한이 있으면 BLOCKED로 표시하고 중단.
- 사용자 준비: Bybit 읽기 전용 API 키(Read-Only). **출금(Withdraw)·거래(Trade)·이체 권한은 모두 끔**, 가능하면 IP 제한. 키는 채팅/파일에 붙여 넣지 않고 환경변수로만 사용. (시작 전까지는 준비할 것 없음)

### Phase 3. Bybit Demo 주문 수명주기
- 목표: Demo 키로 주문 생성/부분 체결/취소/응답 유실/프로세스 재시작 후 reconcile을 실제로 시험. Nautilus Bybit 어댑터 사용, 주문 생성 경로는 하나만.
- 알려진 제약: Demo는 WS Trade API 미지원, `tp_trigger_price`/`sl_trigger_price` 미지원 (Nautilus 문서).
- 완료 기준: 시나리오별 증빙(체결 중복 0, 고아 주문 0, 재시작 후 상태 일치).
- 사용자 준비: Bybit Demo 전용 키(메인넷 실계좌 키와 별개).

### Phase 4. AI 추천 + 사용자 승인 (모드 A)
- 결정적 신호/위험 계산이 숫자를 만들고, AI는 설명만 작성. 추천 필드(거래소, 종목, 현물/선물, 방향, 데이터 신선도, 시간대, 진입 구간, SL/TP, 손익비, 수량/레버리지/증거금, 예상 손실과 계좌 대비 %, 근거 찬반, 무효 조건) 저장.
- 만료 시각과 주문 직전 재검증 필수. AI가 한도 변경·주문하는 경로는 만들지 않음.
- AI 제공자/비용/전송 데이터는 사용자 별도 승인 필요.

### Phase 5. 장기 실데이터 전략 검증
- 수수료, 슬리피지, funding, 시간순 OOS, 공유 자본 포트폴리오 포함. 손실 결과도 그대로 기록. 현재 BTC/ETH spot OOS 손실을 숨기지 않음.
- 통과 기준을 사전에 문서로 확정(사후 조정 금지).

### Phase 6. 제한적 자동매매 (모드 B)
- 승인 모드와 **별도 설정**으로만 켬. 허용 거래소/종목/전략, 계좌별 한도, 거래당/일일/낙폭 손실 한도, 최대 레버리지/포지션/노출, 연속 손실 정지, 신호 TTL, 쿨다운, 데이터 지연·연결 실패 정지.
- 정지 의미를 3가지로 분리해 정의·테스트: (a) 신규 진입만 차단 (b) 미체결 주문 취소 (c) 포지션 청산(긴급중지). 기본값은 (a)이며 청산은 명시 행동.

### Phase 7. Binance / OKX 확장
- 거래소 간 동일하다고 가정하지 않음(최소 주문, 포지션 모드, 관리 주문, Demo 차이). 거래소마다 Phase 2~3을 다시 통과. OKX는 공식 V5 계약 검증부터.

### Phase 8. 실거래 준비 점검
- 사용자의 **별도 명시적 승인** 없이는 시작하지 않음. 소액 한도, 보안 검토, 복구 절차, 법·세금 확인 항목 포함. 수익을 보장하지 않음.

## 5. 사용자 결정이 필요한 항목

1. Bybit가 현재 네트워크에서 차단된 상태인데, 합법적인 접속 환경(예: 허용된 지역/계정)을 사용자가 직접 마련할지. 이 프로젝트는 우회를 시도하지 않음.
2. 첫 실시간 대상: Bybit spot만 / spot+선물 (선물은 레버리지·청산 위험이 큼).
3. AI 제공자(원격 API / 로컬 모델)와 월 비용 한도.
4. 수동 보유 Bybit 포지션은 영구히 "외부(관리 안 함)"로 둘지.
