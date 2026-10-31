# 참고 프로젝트와 아키텍처 원칙

확인일: 2026-10-31 KST. 확인 방법: GitHub API로 저장소·최신 릴리스·태그·라이선스 메타데이터 확인. **소스 전체를 읽거나 설치·실행해 본 것은 아닙니다** (NautilusTrader만 설정 파일과 통합 문서를 읽음). 라이선스는 저장소 표기이며 법률 검토가 아닙니다.

## 핵심 원칙

1. **주문 경로는 하나**: 같은 계정의 주문 생성·멱등성·정합(reconcile)은 단일 실행 경로(NautilusTrader)만 담당합니다. 두 라이브러리가 같은 계정에 주문을 내지 않습니다.
2. **거래소 동일성 가정 금지**: 최소 주문 수량, 포지션 모드(One-way/Hedge/Net), 관리형 주문(조건부/algo), Demo 지원 범위는 거래소마다 다릅니다. 거래소별로 시험하고 기록합니다.
3. **AI는 설명만**: AI는 위험 한도를 바꾸거나 자유 문장으로 주문하지 않습니다. 숫자는 검증된 데이터와 코드에서만 나옵니다.
4. **비밀키는 파일·로그·커밋에 넣지 않음**, 출금 권한 키 사용 금지.
5. **GPL 코드 복사 금지**: FreqUI, Freqtrade는 설계 참고만.

## 요약 표

| 참고 | 역할 | 의존성 여부 |
|---|---|---|
| NautilusTrader | 핵심 실행/백테스트 엔진 | **의존성 (유지)** |
| pybit | Bybit 공식 Python SDK | 보류: 조회 전용 보조로만 검토 (주문 경로 아님) |
| CCXT | 다거래소 통합 REST | 보류: 공개/읽기 전용 보조 후보 |
| Freqtrade / FreqAI | 봇 워크플로, ML 연구 | 설계 참고만 |
| FreqUI | 대시보드 UI | 설계 참고만 (GPL, 코드 복사 금지) |
| Lightweight Charts | 차트 | **의존성 (이미 사용)** |
| TradingAgents | 다중 에이전트 분석 구조 | 설계 참고만 |

## 상세

### 1. NautilusTrader
- URL: https://github.com/nautechsystems/nautilus_trader
- 확인한 태그/커밋: `v1.231.0` / `27a8e54e7ac3c57d6cbf8891f0283dfbaee97317` (태그 객체 `d3e1685e979925d7b0ffacd1b3f442547686e18f`)
- 참고 내용: `adapters/{bybit,binance,okx}`의 데이터/실행 어댑터, 환경(DEMO/TESTNET), 포지션 모드, 외부 주문 분류, 응답 유실 시 재제출 금지 원칙.
- 분류: **핵심 의존성**. 현재는 BacktestEngine/SimulatedExchange로 PAPER·연구에 사용 중 (`src/multi_exchange_trader/paper/native.py`, `research/native.py`).
- 적용: Phase 3부터 Bybit Demo용 라이브 실행 클라이언트를 같은 엔진 위에서 사용. Phase 7에서 Binance/OKX.
- 라이선스: LGPL-3.0. 고지는 `docs/licenses/NAUTILUS_NOTICE.md` 유지, 원본 수정 없음.
- 미지원/미검증: 실제 거래소 연결 동작, 재시작 reconcile 세부 옵션, 부분 체결/취소 실패 동작. Bybit Demo는 WS Trade API와 `tp_trigger_price`/`sl_trigger_price` 미지원 (문서 기준). OKX는 TESTNET 환경 없음(DEMO만). 자세한 표는 `ROADMAP_FINAL_PRODUCT.md` 2절.

### 2. pybit
- URL: https://github.com/bybit-exchange/pybit
- 확인: 최신 태그 `v5.17.0` (`fdab314ec1e9d7ad6127aaa1721f24557d6705ee`), GitHub 릴리스 항목은 5.16.0. LICENSE 파일 본문은 MIT (GitHub 메타데이터는 NOASSERTION으로 표시). `_http_manager.py`에 testnet/demo 서브도메인 설정이 있음.
- 참고 내용: Bybit V5 엔드포인트 이름과 파라미터, demo/testnet 호스트 구분.
- 분류: 현재 **미사용**. Phase 2에서 읽기 전용 조회가 필요하면 보조로 검토. **주문은 pybit로 내지 않음** (주문 경로는 Nautilus 하나).
- 라이선스: MIT (저작권 고지 유지).
- 미확인: 실제 호출, 속도 제한 동작, 지역 제한 상황.

### 3. CCXT
- URL: https://github.com/ccxt/ccxt
- 확인: 릴리스 `v4.5.91` (`53b46f3f839ffe0db35c7d78610b9539eee631cc`), MIT.
- 참고 내용: 거래소 간 심볼/시장 정규화 방식, `exchange.has` 기능 점검 방식.
- 분류: 현재 미사용. Binance/OKX 공개 시세 확장 시 읽기 전용 후보. 주문 생성에는 사용하지 않음.
- 적용: Phase 7의 시장 메타데이터(최소 수량 등) 조회 보조. 단, 정규화된 파라미터가 거래소마다 같은 의미라고 가정하지 않음.
- 미확인: WebSocket은 별도 상품(CCXT Pro)이며 약관 별도 확인 필요. 이번 감사에서 실행하지 않음.

### 4. Freqtrade / FreqAI
- URL: https://github.com/freqtrade/freqtrade
- 확인: 릴리스 `2026.10` (태그 참조 `4cf0351e08d26e96a6429ef48103b726bb230cf8`), GPL-3.0.
- 참고 내용: dry-run → 소액 실거래 순서, 전략/백테스트/하이퍼옵트 워크플로, FreqAI의 시간순 재학습 개념.
- 분류: **설계 참고만**. 코드 복사·의존성 추가 없음 (GPL-3.0, 또한 별도 실행 엔진이 되어 주문 경로가 둘이 되는 것을 피함).
- 적용: Phase 5 검증 절차, Phase 4/6의 명령(명시 승인) 개념 참고.
- 미확인: FreqAI 세부 구현, 현재 버전의 거래소 지원 범위.

### 5. FreqUI
- URL: https://github.com/freqtrade/frequi
- 확인: 릴리스 `3.2.1` (`4e1717ad1eaaf98447ebe2bdf4c00c8d755d2191`), GPL-3.0.
- 참고 내용: 화면 구성 아이디어(거래 목록, 성과, 로그 등)만.
- 분류: **디자인 참고만, 코드 복사 금지**. 파일을 열어 코드를 옮겨 쓰지 않고, 필요 시 문서만 보고 독자적으로 구현.
- 적용: Phase 4~6의 승인 화면/포지션/기록 화면 구성.
- 미확인: 소스 내용 (일부러 읽지 않음).

### 6. TradingView Lightweight Charts
- URL: https://github.com/tradingview/lightweight-charts
- 확인: 릴리스 `v5.2.1` (`b2ce010e4ad59f6556c9ed60e2b2b30feccdd2d1`), Apache-2.0. 프로젝트는 5.2.1을 이미 사용 (`docs/ARCHITECTURE.md`).
- 참고 내용: 캔들 차트, 마커/가격선(SL/TP/진입 구간 표시에 사용 예정).
- 분류: **의존성 (사용 중)**. 적용: `apps/web/src/chart/CandleChart.tsx`.
- 라이선스/고지: Apache-2.0 + TradingView 출처 표시 유지 (화면에 링크 존재: `App.tsx`의 tradingview.com 링크). NOTICE 요건은 릴리스 시 재확인.
- 미확인: 모바일 실제 기기 검증.

### 7. TradingAgents
- URL: https://github.com/TauricResearch/TradingAgents
- 확인: 릴리스 `v0.7.1` (`c4544913a14ed3df5f1ed7b9e8483270ce20682a`), Apache-2.0.
- 참고 내용: 역할별 분석(찬반 토론, 위험 검토) 구조를 "설명 생성" 단계의 설계 아이디어로만 참고.
- 분류: **설계 참고만**. 코드/에이전트 도구 실행 권한을 가져오지 않음. 이 프로젝트에서 AI는 주문·위험 한도 변경 권한이 없음 (`docs/AI_DESIGN.md`).
- 적용: Phase 4의 찬반 근거(pro/con) 필드 작성 방식.
- 미확인: 성능/수익성 주장은 검증하지 않음. 외부 LLM 호출은 사용자 승인 전까지 금지.

## 역할 분담도

```
공개/읽기 데이터 (자체 HTTPX 어댑터, 필요 시 CCXT/pybit 읽기 전용)
   -> 검증/신선도 확인 -> 지표/신호 (결정적 코드)
   -> AI 설명 (텍스트만, 권한 없음)
   -> 결정적 위험 점검 (코드, 한도는 사람만 변경)
   -> 사용자 승인 또는 별도 활성화된 제한 자동모드
   -> [단일 실행 경로] NautilusTrader -> 거래소 (Demo 먼저)
   -> 체결 확인/정합 -> 포지션 관리 -> 기록/성과
```
