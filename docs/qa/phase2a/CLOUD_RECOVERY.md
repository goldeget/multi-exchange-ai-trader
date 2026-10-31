## Current PHASE4 local recovery supplement — 2026-10-31 KST

[README](../../../README.md) and [PHASE4 report](../phase4/README.md) describe the current manual PAPER + native research/synthetic automation implementation. PHASE2A remains PARTIAL with six blocked markets. Existing published revision1/current/running/restricted/enforced policy was observed; earlier draft/unapplied descriptions below are historical, not the current state. No denied-host reprobe or policy/proxy/TLS change.

Preserve original work/backups/DB/WAL/SHM. Manual default `work/phase3-data/paper.sqlite3`, research default `work/phase4-data/research.sqlite3`, datasets `work/phase4-data/datasets`; actual QA uses separate preserved `qa-paper.sqlite3` and `qa/research.sqlite3`. Reuse the configured paths, do not replace with fresh DB. Keep `MARKET_DATA_BLOCKED_HOSTS=fapi.binance.com,api.bybit.com`, exact locked dependencies and127.0.0.1 bind. Import existing verified dataset files using the README's offline loader before considering any fresh collection.

After actual process restart, RUNNING becomes PAUSED_RECOVERY; inspect native cash/positions/journal then explicitly resume. PAUSED remains paused. Do not reset funds, delete state, clear browser uncertain-intent storage or retry unknown commands under a new key. Same persisted intent/key/payload after network/5xx must reconcile the prior response. Actual PID34698→36667/same DB/seed1/noautostep proof is in the PHASE4 report. Single-process scope only.

## Current production guard / chart continuity — 2026-10-31 KST

**A/B follow-up scope PASS; full PHASE2A NOT COMPLETE.** Production now serializes each public host through denial classification, supports startup preblocking and safe API reasons, and stops terminal resource polling while retaining labeled last values/time/source. Actual configured QA observed fapi0/Bybit0 outgoing attempts; only Binance spot was called. Same-batch chart refresh preserves the instance/range; the separately reviewed rolling-window fix anchors to shared timestamps and explicitly fits disjoint batches. PC1440x1000/mobile390x844 real30s refresh, actual new1m rolling drop, selections,resize,height380,Korean/status/focus and emulated pinch PASS. Python277,finalfrontend21 and static/build PASS. Both allowed source CIs[41144951973](https://github.com/goldeget/multi-exchange-ai-trader/actions/runs/41144951973) and[41145424278](https://github.com/goldeget/multi-exchange-ai-trader/actions/runs/41145424278) PASS in all four jobs. [Design,RED/GREEN,operational settings,zero-send evidence,actual JSON/screenshots,rolling review fix and remaining gates](production-guard-20261031/README.md). Six geographically restricted markets remain BLOCKED without new requests; physical-device/exhaustive accessibility UNRUN. Original UTC data/backups/work/ preserved. Earlier pinch/continuity UNRUN statements below are historical and superseded within this scope.

### Current QA recovery rule

Stop the prior QA process normally before starting a replacement. In this already restricted environment, keep `MARKET_DATA_BLOCKED_HOSTS=fapi.binance.com,api.bybit.com` on every worker. Start with `MARKET_DATA_BLOCKED_HOSTS=fapi.binance.com,api.bybit.com uv run uvicorn multi_exchange_trader.main:app --host 127.0.0.1 --port 8000`, preserving inherited proxy/CA/TLS. Invalid host/URL settings fail startup. Do not clear this setting to probe known-denied hosts. There is no TTL/probe/reset endpoint: changing a block requires authorized operational review and a restart. Dynamic denial state is single-process; configured blocks are independently seeded by every worker. [Full configuration and proof](production-guard-20261031/README.md).

## Historical / superseded — Chart correction checkpoint — 2026-10-31 KST

**PARTIAL / EXTERNAL GEOGRAPHIC RESTRICTIONS BLOCKED.** Collector451 stop fixed in dcd571d; chart growth fixed separately in345add1. Actual DOM experiments confirmed global market-table border/layout leaking into Lightweight Charts and resize feedback; scoping selectors to `.markets` resolves it without adding fixed-height masking. PC1440x1000/mobile390x844 GREEN:54 total height samples all380px, resize/restore,BTC/ETH,six intervals,zoom/drag/touch,70-second induced stale and actual recovery. No horizontal overflow or uncaught JS errors. [Initial FAIL,causal experiments,source diff,actual screenshots/JSON and GREEN](chart-stability-20261031/README.md). Both necessary source CIs[41142392526](https://github.com/goldeget/multi-exchange-ai-trader/actions/runs/41142392526) and[41143157865](https://github.com/goldeget/multi-exchange-ai-trader/actions/runs/41143157865) PASS in all four jobs, each run once. [Exactly two new diagnostic requests](followup-20261031/one-shot-diagnostics.json) identified geographic restrictions; six markets remainBLOCKED. OriginalUTC JSON and backups retained. No eight-market/full Phase2A success claim. Earlier chart FAIL is historical and now corrected; pinch,zoom persistence across polling,and exhaustive accessibility remainUNRUN.

## Historical / superseded — Follow-up checkpoint — 2026-10-31 KST

**PARTIAL / NOT COMPLETE.** The451 host-stop defect is fixed with offline RED/GREEN,264 Python/8 frontend and static/build PASS. Single code[CI41142392526](https://github.com/goldeget/multi-exchange-ai-trader/actions/runs/41142392526) PASS in all four jobs. Exactly one new ticker probe per denied host found geographic response phrases: Binance451 restricted location; Bybit403 block access from your country. Both GET bodies0bytes. PC/mobile70s ticker/candle stale and actual-response recovery observed; **new chart height growth FAIL remains unresolved**. See[latest sanitized diagnostics,code diff,regression,CI,QA JSON/screenshots and remaining gates](followup-20261031/README.md). No eight-market/full Phase2A completion claim. Earlier checkpoints are historical.

## Historical / superseded — Current recovery checkpoint — 2026-10-31 (Asia/Seoul · KST)

The allowlist is now applied: revision 1/current/running/connected/restricted/enforced, four approved hosts and package-manager preset. Default socket EPERM was not evidence of an external proxy failure: official `require_escalated` git read succeeded, followed by fetch of existing codex/initial-platform. Keep proxy/CA/TLS intact and use only supported execution permission. Do not regenerate source from main. See [current evidence and remaining gates](live-20261031/README.md).

Live snapshot passed two Binance spot markets; fapi HTTP 451 and Bybit upstream HTTP 403 block six markets. Do not retry denied hosts or substitute hosts, relays, VPN, preview or synthetic data. Existing runner does not stop on 451; that limitation is explicitly recorded. Full Phase 2-A acceptance remains unmet. Historical procedures/results below are retained; they do not describe the current applied policy.

# PHASE 2-A Cloud 장애 복구 및 재개

상태: **검증 완료 아님**. Cloud 설정 저장 오류가 보고됐으나, 현재 런타임의 Git 작업과 테스트 실행은 가능하다. 현재 런타임 revision 1은 거래소 호스트가 없는 제한 정책을 적용하고 있다. 플랫폼 저장 UI의 내부 오류 원인은 제공 도구로 관측할 수 없다. 보존 진단 메타데이터 조회는 `history_status: not_found`, records 0을 반환했다. 로그 부재는 플랫폼 정상의 증거가 아니다.

## 최신 재검증 상태 — 2026-10-31 KST

- **GitHub CI 해결:** [run41135809249 attempt2](https://github.com/goldeget/multi-exchange-ai-trader/actions/runs/41135809249/attempts/2)의 필수 검사 네 개가 모두 성공했다. Dependency review 기존 실패 작업만 한 번 재실행했고 비교 결과도 성공했다. 더 이상 동일 오류 재실행이나 설정 변경은 필요하지 않다. [실제 CI 기록](final-recheck-ci.md).
- **Cloud 정책 미적용:** 연결 환경은 여전히 revision1/current/enforced이며, `allowed_hosts: []`, preset `package_managers`다. 필요한 네 호스트가 모두 없다. 설정 저장·Publish·환경 재시작/선택을 수행할 도구는 제공되지 않는다. [환경/config/version ID와 진단 근거](final-recheck-cloud.md).
- **회귀 검사 성공:** Python257개, frontend8개와 린트·타입 검사·빌드·Gitleaks가 통과했다. 기존 scratch 복원본이 테스트 중복 수집을 일으켜 내용을 보존한 채 숨김 work 폴더로 옮긴 뒤 기본 pytest257개 통과도 확인했다. [실행 기록](final-recheck-tests.md).
- 사용자는 기존 복구 ZIP을 별도로 다운로드했다. 이번 검증에서는 백업 생성·삭제·덮어쓰기를 요청하지 않았으며 기존 artifact13015637577 하나가 유지되는 것을 확인했다. 보고서 변경 커밋에는 `[skip ci]`를 써 불필요한 CI와 백업 생성을 피한다.

### 사용자 환경 설정에서 필요한 값과 확인 조건

현재 대화에 연결된 환경의 설정을 편집해야 한다. 현재 확인된 source config ID는 위 환경 기록에 있으며, 최신 초안 ID/내용은 도구에서 볼 수 없다. 이 둘이 같은 환경인지 단정하지 않는다.

필요한 값은 아래 공개 HTTPS 호스트 네 개다. 기존 package-manager preset은 유지하고 키/Secret을 추가하지 않는다:

```text
data-api.binance.vision
fapi.binance.com
api.bybit.com
api.github.com
```

공식 설정 workflow가 `requires_publish: true`를 반환하는 경우 검토·저장 후 Publish까지 완료해야 한다. 현재 화면의 정확한 메뉴 이름과 Publish 클릭 절차, 현재 초안의 Publish 필요 여부는 검증할 UI/API가 없어 **미확인**이다. 현재 도구로 저장 오류를 재현하거나 새 실행 환경을 연결할 수 없다. 이 문서는 검증되지 않은 메뉴 경로나 저장 성공을 지어내지 않는다.

설정 적용 이후 같은 작업에 연결된 런타임에서 `observations_current: true`, spec/observed revision 일치, `network_policy.state: enforced`, 네 호스트가 `effective_allowed_hosts`에 포함되는지 다시 확인해야 한다. 새 런타임이면 environment/source config/version ID도 함께 대조한다. 저장·게시 메시지만으로 통과 처리하지 않는다. 저장 오류가 계속되면 실제 오류 문구/시각/요청 식별자/편집 환경 ID가 추가로 필요하며 현재 진단에는 해당 기록이 없다.

정책 적용 확인 전에는 아래 live collector나 정상 화면 QA를 실행하지 않는다. 정책이 확인되면 공개 데이터만 수집하고, 기존 Bybit 포지션/주문/계정·키는 접근하지 않는다. PHASE3 구현은 이번 작업 범위 밖이다.

## 보존한 결과

- 모든 완료 작업을 `codex/initial-platform`에 순차 커밋·푸시한다. `main`과 Draft PR 상태는 유지한다.
- GitHub Actions의 Secret scan 작업은 Gitleaks 성공 뒤에만 복구 산출물을 만든다. 별도 bare clone 복원·SHA 일치·git fsck·파일 체크섬을 확인한 후 업로드한다. Git bundle 백업은 산출물의 SNAPSHOT_SHA.txt 커밋까지 저장소 이력을 담는다. 소스 ZIP은 같은 커밋의 추적 파일만 담는다. 둘 다 Cloud 임시 파일이나 설치된 의존성/비밀 파일을 포함하지 않는다. Actions 산출물은 GitHub 로그인 후 다운로드하며 보존기간은 3일이다. 반드시 다운로드해 별도 보관한다. 만료 이후에도 원격 브랜치의 Git 이력은 유지된다.
- 실제 시세 수집 도구: [live-runner.md](live-runner.md). 기본 출력은 `work/phase2a-live.json`이며 자동 커밋하지 않는다. 테스트용 응답은 실제 시장 검증 증거가 아니다.
- CI 재진단: [cloud-recovery-ci.md](cloud-recovery-ci.md). pinned action은 공개 저장소에서 HTTP403을 일반적인 Dependency graph 안내문으로 바꾸므로, 현재 오류만으로 설정 토글이 꺼졌다고 확정할 수 없다.

## 다운로드

[검증된 복구 백업](https://github.com/goldeget/multi-exchange-ai-trader/actions/runs/41135809249/artifacts/13015637577) — GitHub 로그인 필요. 스냅샷 SHA: `6c19097a2f265a37db004bafc3c8f44f1266c192`. **2026-11-03 07:06 KST 만료**이므로 미리 다운로드한다. ZIP을 풀면 bundle/source ZIP/스냅샷 SHA/체크섬이 나온다. 아래 Git clone은 압축을 푼 폴더에서 실행한다. 최신 보고서의 원격 SHA와 이 코드 백업 SHA는 서로 다를 수 있다.

## Git bundle에서 복구

다운로드한 bundle이 있는 폴더에서 기존 프로젝트를 덮어쓰지 않고 복원한다:

```sh
git clone --branch recovery-source phase2a-recovery.bundle multi-exchange-ai-trader
cd multi-exchange-ai-trader
git switch -c codex/initial-platform
git bundle verify ../phase2a-recovery.bundle
git remote set-url origin https://github.com/goldeget/multi-exchange-ai-trader.git
git rev-parse HEAD
```

제공한 `SHA256SUMS.txt`와 로컬 SHA-256 도구로 다운로드 파일 무결성을 확인한다. bundle 검증은 복원한 Git 저장소 안에서 실행한다. GitHub 원격 복제도 가능하다:

```sh
git clone --branch codex/initial-platform https://github.com/goldeget/multi-exchange-ai-trader.git
```

## 허용된 실행 환경에서 재개

Cloud의 차단을 다른 프록시/호스트/릴레이로 우회하지 않는다. 현재 환경에는 아직 시세 도구를 실제 연결 모드로 실행하지 않는다. Cloud 정책이 정상 적용되거나 사용자가 자신의 로컬 환경에서 직접 실행할 때 아래 절차를 사용한다. 이 문서는 로컬 실행이 이미 수행됐다는 주장이 아니다.

요구 사항: Python3.12, Node24, uv0.12.23. 기존 잠금 파일을 유지한다:

```sh
uv sync --locked
npm ci --prefix apps/web
uv run pytest -q
uv run ruff check src tests scripts
uv run mypy src scripts
npm test --prefix apps/web -- --run
npm run lint --prefix apps/web
npm run typecheck --prefix apps/web
npm run build --prefix apps/web
```

필요한 공개 HTTPS 호스트는 아래 네 개다. 이 목록은 플랫폼 설정의 import 파일이 아니며 실제 적용 여부는 별도로 확인해야 한다.

```text
data-api.binance.vision
fapi.binance.com
api.bybit.com
api.github.com
```

거래소 세 호스트의 적용이 확인된 뒤에만 실행한다:

```sh
uv run python scripts/verify_phase2a_live.py --output work/phase2a-live.json
```

0이 아닌 종료 코드나 BLOCKED/FAIL 결과를 성공으로 바꾸지 않는다. 성공해도 이 도구는 가격·캔들 수집 부분만 검증하며, CI와 화면 QA 전체의 완료를 뜻하지 않는다. Binance 현물 ticker는 원래 source timestamp가 없으므로 수집 시각을 거래소 시각으로 대체하지 않는다.

## 실제 가격 기반 화면 검증

서로 다른 터미널에서 backend/frontend를 시작한다:

```sh
uv run uvicorn multi_exchange_trader.main:app --host 127.0.0.1 --port 8000
```

```sh
npm run dev --prefix apps/web -- --host 127.0.0.1 --port 5173
```

`http://127.0.0.1:5173/`에서 PC1440×1000, 모바일390×844를 검증한다. `?preview=1`, 응답 가로채기, 합성 가격을 사용하지 않는다. Binance/Bybit, BTC/ETH, 현물/무기한 선물 선택과 실제 캔들·타임스탬프·지연 표시, 정상 수신 후 연결 중단/복구, 가로 넘침을 확인한다. 실제 데이터 화면을 캡처하고 수집 JSON과 비교한다. 단순 오류 화면은 정상 데이터 QA 증거가 아니다.

## 완료 조건

1. 공개 시세 8개 시장의 실제 가격·캔들·시각 검증 성공.
2. 실제 가격 기반 두 화면 크기의 차트/선택/오류복구/지연 QA와 화면 증거 확보.
3. GitHub Dependency review를 포함한 필수 CI 성공. 원인 변화가 없으면 반복 실행하지 않는다.
4. 전체 테스트와 Gitleaks, 보고서 갱신, 최종 원격 SHA 대조.

모두 충족되기 전에는 PHASE2A 완료 또는 PHASE3 PAPER 개발 준비 완료로 표시하지 않는다. 실주문, 거래소 API Key 입력, 유료 API/러너/서버 구매, `main` 병합은 금지한다.

## PHASE3 local PAPER recovery supplement — 2026-10-31 KST

Preserve existing branch `codex/initial-platform`, DraftPR1, main7ddcded and all existing backups/work files. PHASE2A remains PARTIAL with six restricted markets BLOCKED. Keep `MARKET_DATA_BLOCKED_HOSTS=fapi.binance.com,api.bybit.com`; do not probe these hosts again.

PAPER design/report: `docs/superpowers/specs/2026-10-31-phase3-paper-design.md`, `docs/qa/phase3/README.md`. Use Python3.12/Linuxx86_64, `uv sync --locked` (exact native1.231.0 plus documented fixed fsspec2026.6.0 override), and a preserved `PAPER_DB_PATH`. Default DB: `work/phase3-data/paper.sqlite3`; runtime DB/WAL/SHM must stay uncommitted. Bind API only to127.0.0.1:8000, web only127.0.0.1:5173; anonymous PAPER mutations must not be externally deployed. Do not delete an existing DB or clear browser pending-command storage as a recovery shortcut. Preserve both and reconcile/restart; startup replay fails closed on corruption/version mismatch. There is no live/reset mode or endpoint. Browser uncertain commands resume their stored original key/payload after refresh/tab recovery.

Code checkpoint1 `f486c6714878c2e702f3923713db13c34e5119ea`; checkpoint2 `feefbe7c7c8af161fb913b82253338f5215056ee`. Source CI1 failed on actual fsspec High GHSA-27vj-qcqg-25rc; do not waive it. The patched dependency review passed in CI41151437466. Latest report records final CI and evidence. Only local synthetic PAPER data is public evidence; neither real positions/keys nor runtime state is committed.

Security checkpoint4: `edf89138ee2c58446db5d922726f368f6137aa2b`, user-authorized after whole-tree npm High/Critical discovery. Use the exact updated npm lock (Vite7.3.7/Vitest4.1.12/scoped minimatch11.0.1 override); resolved audit0. Preserve `browser-20261031/process-restart.json`: actual PID18265→23233, same DB/native cash/position/protection/audit, restored TP once, fundingonce. Never replace this proof with a new seed. Patched-toolchain evidence and final CI are linked in `docs/qa/phase3/README.md`.

Final security-source CI41152341568: all four jobs SUCCESS (Python319/frontend30/static/build, locked npm install audit0, secret scan, unchanged dependency-review policy). Full report includes actual job logs/API results and source `edf89138ee2c58446db5d922726f368f6137aa2b`. Final evidence-only commit uses `[skip ci]`; recover its exact SHA from branch history. PHASE2A sixBLOCKED and local-only PAPER boundaries remain unchanged.
