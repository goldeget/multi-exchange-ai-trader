# PHASE4 실행·검증 보고서 — 2026-10-31 · Asia/Seoul (KST)

**구현된 로컬 기능 범위 PASS. 수익성·실전 승격 판정 아님.** PHASE2A는 PARTIAL이며 제한된 여섯 시장은 BLOCKED 그대로다. PHASE3 수동 PAPER를 보존하고, 별도 과거 spot 연구·synthetic perpetual 연구/자동실행을 추가했다. 실제 선물 데이터 검증은 BLOCKED, BTC/ETH 공유 portfolio native replay는 UNRUN이다. 각 종목은 독립 계좌이며 합산 portfolio 수익률을 만들지 않는다. AI/실거래/Demo/계좌·키·주문/배포는 미구현·미승인이다.

[현재 실행·collector·보존 DB 안내](../../../README.md) · [설계](../../superpowers/specs/2026-10-31-phase4-research-design.md) · [순차 계획](../../superpowers/plans/2026-10-31-phase4-research.md) · [Phase3](../phase3/README.md) · [Phase2A](../phase2a/README.md)

## 데이터와 실제 native 연구

정식 collector는 승인된 `data-api.binance.vision/api/v3/klines` 공개 spot에 BTC/ETH 각각 **1회 GET**, 둘 다 HTTP200이었다. GET body0, redirect/retry/pagination/대체 호스트 없음. 현재 runtime revision1/current/running/restricted/enforced 및 네 호스트 적용을 확인했다. fapi.binance.com/api.bybit.com는 모든 QA backend에서 환경 설정으로 preblock했고 추가 외부 전송0이다. CI/자동루프는 수집하지 않는다. [요청·원본·checksum](data/README.md)을 보존했다.

UTC 반개구간 **2026-09-30 00:00 → 2026-10-30 00:00**, 1시간봉720개/종목, **30일의 짧은 기능 검증 표본**이다. 마감·정렬·중복·gap·정밀도·OHLC·미래시각·checksum 검사를 통과했다. 수집은 BTC 2026-10-31 **10:13:20 KST**, ETH **10:13:21 KST**이며 원본 JSON UTC/가격은 변경하지 않았다. 데이터 공개 접근은 재배포 라이선스나 거래소 보증을 뜻하지 않는다.

NautilusTrader **1.231.0** 실제 BacktestEngine/SimulatedExchange가 체결·잔고·수수료를 계산한다. Linux x86_64/Python3.12 환경과 기존 patched lockfile 계약을 유지한다. Spot은 native CurrencyPair/CASH 계좌의 long/flat 연구이며 선물 mark로 사용하지 않는다. [CASH probe](qualification/native-spot-cash-probe.json)는 실제 native2체결, adverse 가격60000.01→60999.99/수량0.1, 총fee4.84, 최종cash10095.158/inventory0으로 검산했다. Native 원본 라이브러리/라이선스 고지는 [Phase3 qualification](../phase3/qualification.md)에 유지한다.

마감된 prefix만으로 SMA 신호를 만들고 다음 봉 시가에서 실행한다. train60%/validation20%/최종 OOS20%, train에서만 후보(3,10)/(5,20) 선택, expanding walk-forward50/60/70%→다음10%와 동일비용 buy/hold를 별도 계좌로 실행한다. 사후 선택·작은 표본·서로 겹치는 walk-forward 훈련의 불확실성을 감추지 않는다. 비용0.04%/방향별 불리한1tick, 관측되지 않은 intrabar **O-L-H-C 추정 경로**, 구간말 native 종료 비용을 명시한다. funding/실제 호가 유동성/실제 선물 mark는 검증하지 않았다.

| 실제 spot 최종 OOS | 순손익 (가상 USDT) | 순수익률 | 완료 거래 | 최대 연속 순손실 |
|---|---:|---:|---:|---:|
| [BTC 독립 계좌](research/BTCUSDT-spot-report.json) | -58.38587964 | -0.5838587964% | 6 | 3 |
| [ETH 독립 계좌](research/ETHUSDT-spot-report.json) | -24.52092640 | -0.2452092640% | 6 | 6 |

두 전체 native report를 각각 다시 실행하여 reproducibility hash/결과 동일을 확인했다. BTC `9bea8185bc9d2215eaf2b71b91bddb601207de866d1deacf85ac785f8035df6c`, ETH `237794a4203ef712e0b676ce82c06c9d599c88d18b416ed92e71d5207bd290c0`. 손실 결과를 숨기지 않는다. 초기/최종equity 기반 Decimal `net_return`, mark-to-market drawdown, 실제 비용차감 거래·승률/Wilson95%, max consecutive net loss를 기록한다. 순손익0은 streak를 끊는다. 이 곡선과 숫자는 실제 계좌 수익이 아니다.

## 가상 자동실행·저장 경계

Synthetic BTC-PAPER/ETH-PAPER만 가능하다. 별도 Phase4 SQLite에서 pure socket-denied native replay 결과, cursor, 원자적 journal/command response/idempotency를 하나의 BEGIN IMMEDIATE transaction으로 저장한다. 수동 PaperStore DB에는 쓰지 않는다. 별도 지속 engine DB/외부효과가 없으므로 2DB 원자성을 주장하거나 불필요한 saga를 만들지 않는다. 미래에 두 번째 지속 효과를 도입하면 durable fixed-key intent reconcile이 먼저 필요하다.

기존 수동16scenario/500event 제한은 유지하며 Phase4도16session/500native events다. 최대80봉·봉당ordinary6events와 **종료 여유10events**로 열린 포지션 종료 공간을 확보한다. O-L-H-C 네 native quote 및 reversal exit/entry를 포함한다. maxloss/drawdown/exposure/연속손실/소스종료/이벤트한도 시 native flatten, gap 손실·음수 잔고는 보존한다. 새 완료 native roundtrip 수로 종료를 감지하고 partial을 합산하여 손실→0→손실·중복 집계 회귀를 검증했다. `next_due_at`은 native/SQL 완료 후 최소1초, 중복resume은 cooldown을 초기화하지 않는다.

신규ID만 가상10000USDT 1회 seed, reset/delete/redeposit 없음. 생성PAUSED, 명시 start/resume; restart RUNNING→PAUSED_RECOVERY, 기존PAUSED 유지. pause는 포지션을 남기며 stop은 native reduce-only 종료다. 단일 로컬 프로세스 qualified 범위이고 분산/다중 worker 운영 보장은 UNRUN이다. native/cache/events replay·snapshot/journal·idempotency response 검증 실패 시 fail-closed한다. Runtime DB/WAL/SHM은 커밋하지 않는다.

## RED → GREEN 및 전체 회귀

| 검사 | 관측 결과 / 범위 |
|---|---|
| 데이터/collector | offline RED19 → GREEN19; 해당 체크포인트 전체 Python338 PASS |
| native/evaluation | RED8 → GREEN8; 실제 native integration 포함, 체크포인트 전체346 PASS |
| auto/storage/metrics | risk-time 실제 mismatch, idempotency-response 손상 미검출, zero-net 종료/streak·cadence·중복resume RED 후 GREEN; core CI365 PASS |
| API/scheduler/UI | API 계약·failure pause 원인·uncertain retry·selection 격리 RED/GREEN; 전체 Python368 PASS/79 upstream warnings |
| 최종 UI 정보 보완 | scheduler FAIL_CLOSED/저장RUNNING 구분, native curve, immutable selected config: RED3 → frontend45 PASS |
| 정적/빌드 | Ruff/strict mypy33files, ESLint, TypeScript, Vite build PASS |
| 의존성 | 기존 정확한 Nautilus/fsspec/Vite/Vitest/minimatch locks 유지; Dependency review gate SUCCESS, 재실행 npm audit 모든 severity0 ([원문](verification/npm-audit.json)) |

[로컬 RED/GREEN 로그 checksum/요약](verification/local-test-log-index.json), [소스 CI 실제 Python 로그](verification/info-python-log-excerpts.json), [소스 CI 나머지 안전한 로그](info-ci-log-excerpts.json), [실제 job API 원문](verification/info-ci-jobs-raw.json)을 보존한다.

회귀에서 fixture/mock은 offline 계약 증빙으로만 표시한다. 실제 native worker integration/real local API/browser와 별도다. 임시 export에 git metadata가 없어 legacyCLI21개가 실패했던 시도는 같은 원본 read-only git metadata를 제공해 전체346개를 다시 통과했다. 검사를 제외하지 않았다. 기존 PHASE3 감사·재시작 증빙도 보존한다.

## 실제 로컬 브라우저·프로세스 QA

[기계 요약·KST 시각·파일 checksum](evidence-index.json) 및 [browser manifest](browser-20261031/README.md). PC1440x1000/mobile390x844 Chromium에서 실제 API/native workflow를 실행했다. preview/합성 응답 주입으로 실제 spot 성공을 대신하지 않았다. Synthetic 자동실행 가격은 명시된 로컬 가상 데이터다.

- [PC](browser-20261031/desktop.json)/[모바일](browser-20261031/mobile.json): 실제 보존 BTC/ETH spot native 연구, 별도 synthetic 연구, 신규PAUSED→명시 시작→native 진입→pause/cursor 동결. 380px 차트, td padding0, 확대/이동/resize, 한글/기본 label·keyboard focus, 가로넘침0/pageerror0/시장요청0.
- [실제 프로세스 재시작](browser-20261031/process-restart.json) PASS: PID34698 종료 후 **committed SQL 경계**와 PID36667 복구 비교. 열린 position/native fills/cursor/config 보존, RUNNING→PAUSED_RECOVERY/PAUSED 유지, 2.5초 기다려 자동step0, SQL funding create count각1. 단순 새 객체 생성 검사가 아니다.
- [PC 응답유실](browser-20261031/desktop-post-restart.json)/[모바일 응답유실·위험정지](browser-20261031/mobile-post-restart.json) PASS: 실제 stop commit HTTP200 뒤 브라우저 로컬502를 전달, reload 뒤 **같은 key/payload/resource**로 retry하여 exit 중복0. 모바일 create commit 뒤 localabort/탭 닫기·복구도 같은 key로 재시도, funding1. 별도 synthetic 세션 실제 native2fills 후 loss_limit HALTED/flat/비용차감 잔고 보존.
- 첫 fault harness가 502 뒤 dialog가 사라지길 기다린 시도는 **FAIL(테스트 harness 기대 오류)**이었다. 제품은 올바르게 불확실성을 유지했다. 원본 로그/실패 화면은 보존했다. 수정 harness는 이미 stop된 계좌를 reset하지 않고 **별도 fresh fault fixture**를 사용했다. 원래 process-restart 증빙과 혼동하지 않는다.
- [최종 PC](browser-20261031/desktop-ui-contract.json)/[모바일](browser-20261031/mobile-ui-contract.json) read-only smoke PASS: 실제 native OOS curve576점 전부 표시, 저장 selected run 설정은 새 폼 edit와 독립, 실제 scheduler health 표시. FAIL_CLOSED+stored RUNNING 표시는 offline UI 회귀이며 실제 loop장애를 브라우저에서 유발했다고 주장하지 않는다. POST0/시장요청0/pageerror0/차트380px.

자동화 기반 기본 접근성/Chromium touch 검사이며 물리 기기·완전 접근성 인증은 UNRUN이다. Phase4에서 실제 모바일 pinch 별도 재검증은 UNRUN이며 기존 Phase2 차트 증빙과 구분한다.

## CI·보안 역사와 최종 판정

| CI | 결과 |
|---|---|
| 41154244112 / data source | Python/Frontend/Dependency SUCCESS, Secret FAIL4 |
| 41154605265 / 안전한 metadata 진단 | Secret FAIL4, 나머지 SUCCESS |
| 41155316580 / native | 네 job SUCCESS |
| 41156389171 / auto/store | 네 job SUCCESS |
| 41157653747 / API/UI | Python/Frontend/Dependency SUCCESS, Secret FAIL1 |
| 41158669936 / review UI + namespace correction | 네 job SUCCESS: Python368, frontend45, history59commits/no leaks |

역사적 네 UUIDv4는 외부 인증이 없는 로컬 PAPER idempotency 식별자, 새1건은 공개 localStorage namespace였다. Work가 원격 source/metadata를 독립 확인했다. [정확한5개 commit:file:rule:line 및 사유](security-findings.json)만 `.gitleaksignore`에 허용하고 전체 history/원래 scanner exit/gate를 유지한다. UUID/key/path/rule 광범위 ignore는 없다. namespace 문자열을 유지한 변수명 수정으로 이전 브라우저 미확정 intent를 보존한다. 새 증빙은 raw key를 빼고 request digest/동일성 boolean/실제 payload 요약을 구분한다.

기존 로컬 Gitleaks 바이너리가 없어 추가 로컬 scan은 **UNRUN**이다. 이전 다운로드 자동거절을 재시도/우회하지 않았다. 공식 CI checksum download와 전체 history Secret scan이 별도 실제 보안 증빙이다. raw secret/Finding 출력·보고서 업로드 없이 metadata만 진단했다.

**최종 문서·데이터·스크린샷 커밋은 skip 없이 정상 CI를 실행한다.** 그 정확한 head의 전체 history Secret scan 포함 네 job 결과는 PR/최종 응답에 기록하고 결과 기록만을 위한 추가 커밋 루프를 만들지 않는다. PHASE4 PASS는 위 구현/검증 범위로 한정한다. PHASE2A6시장/실제선물 BLOCKED, 공유 portfolio/실전승격 UNRUN은 그대로다.
