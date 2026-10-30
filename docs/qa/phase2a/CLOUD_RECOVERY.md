# PHASE 2-A Cloud 장애 복구 및 재개

상태: **검증 완료 아님**. Cloud 설정 저장 오류가 보고됐으나, 현재 런타임의 Git 작업과 테스트 실행은 가능하다. 현재 런타임 revision 1은 거래소 호스트가 없는 제한 정책을 적용하고 있다. 플랫폼 저장 UI의 내부 오류 원인은 제공 도구로 관측할 수 없다. 보존 진단 메타데이터 조회는 `history_status: not_found`, records 0을 반환했다. 로그 부재는 플랫폼 정상의 증거가 아니다.

## 보존한 결과

- 모든 완료 작업을 `codex/initial-platform`에 순차 커밋·푸시한다. `main`과 Draft PR 상태는 유지한다.
- Git bundle 백업은 커밋된 저장소 이력을 담는다. 소스 ZIP은 같은 커밋의 추적 파일만 담는다. 둘 다 Cloud 임시 파일이나 설치된 의존성/비밀 파일을 포함하지 않는다.
- 실제 시세 수집 도구: [live-runner.md](live-runner.md). 기본 출력은 `work/phase2a-live.json`이며 자동 커밋하지 않는다. 테스트용 응답은 실제 시장 검증 증거가 아니다.
- CI 재진단: [cloud-recovery-ci.md](cloud-recovery-ci.md). pinned action은 공개 저장소에서 HTTP403을 일반적인 Dependency graph 안내문으로 바꾸므로, 현재 오류만으로 설정 토글이 꺼졌다고 확정할 수 없다.

## Git bundle에서 복구

다운로드한 bundle이 있는 폴더에서 기존 프로젝트를 덮어쓰지 않고 복원한다:

```sh
git clone --branch codex/initial-platform phase2a-recovery.bundle multi-exchange-ai-trader
cd multi-exchange-ai-trader
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
