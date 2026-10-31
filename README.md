# 로컬 시장 연구 · PAPER 플랫폼

공개 시세 대시보드, **NautilusTrader 1.231.0 기반 수동 PAPER**, 과거 데이터 연구, **synthetic 전용 PAPER 자동실행**, 그리고 **Bybit USDT 무기한 읽기 전용 계좌 조회**를 제공합니다. 모두 로컬에서 실행합니다. 주문·레버리지 변경·출금·AI·실거래 전송은 없습니다.

**PHASE2A는 PARTIAL입니다.** Binance spot 두 시장의 기존 확인과 별개로 Binance 무기한/Bybit 여섯 시장은 지역 제한으로 BLOCKED입니다. 과거 spot 연구나 synthetic PAPER 성공으로 이 상태를 통과 처리하지 않습니다. OKX는 비활성·미검증이며 요청하지 않습니다.

## 화면과 검증 범위

| 탭 | 제공 기능 | 중요한 구분 |
|---|---|---|
| 공개 시장 관측 | BTC/ETH 공개 ticker/OHLCV, 차트·시각·지연·오류 상태 | 알려진 제한 호스트는 운영 설정으로 사전 차단합니다. |
| PAPER 가상거래 | 별도 가상자금, long/short, 부분/전체 reduce-only 종료, SL/TP/OCO, 수수료·PnL·원장 | 직접 입력하는 **로컬 가상 가격**입니다. 실제 spot을 선물 mark로 대용하지 않습니다. |
| 연구·가상 자동실행 | 불변 dataset/checksum, native spot/CASH 또는 synthetic perpetual 연구, 시간순 OOS·walk-forward, equity curve·거래·불확실성 | BTC/ETH는 **독립 계좌**이며 공유 portfolio replay는 UNRUN입니다. |
| 같은 연구 화면의 자동실행 영역 | 별도 synthetic 계좌, 시작/pause/resume/stop, 손실·낙폭·노출·연속손실 제한 | 실제 시세를 연결하지 않습니다. 저장 원장 상태와 실제 scheduler health를 구분합니다. |
| Bybit 계좌 조회 | USDT 무기한 서버 시각, BTCUSDT 최대 레버리지, 잔고, 포지션, 미체결 주문 | 읽기 전용입니다. 기존 포지션·주문은 모두 외부입니다. PAPER·데모·메인넷을 구분합니다. 접속이 막히면 BLOCKED이며 숫자를 만들지 않습니다. |

실제 수집한 spot 30일/720봉은 **짧은 기능 검증 표본**입니다. 실제 수익성·실전 준비·선물 성능을 검증하지 않습니다. 마감봉 신호→다음 봉 시가 native 체결, 비용·가정한 O-L-H-C 경로와 종료 정리 비용을 반영합니다. 순수익률·낙폭·승률/표본 수·Wilson 구간·최대 연속 순손실을 보되 미래 수익으로 해석하지 마세요. 순손익 0은 연속손실을 끊습니다. 실제 선물 데이터 검증은 BLOCKED입니다.

- [Phase2A 보고서](docs/qa/phase2a/README.md)
- [Phase3 native/PAPER·재시작 검증](docs/qa/phase3/README.md)
- [Phase4 데이터·native 연구·자동실행·화면/CI 증빙](docs/qa/phase4/README.md)
- [설계](docs/ARCHITECTURE.md), [QA 기준](docs/QA_PLAN.md), [회복 절차](docs/qa/phase2a/CLOUD_RECOVERY.md)

## 실행 환경과 정확한 의존성

Qualified 환경은 **Linux x86_64 / Python 3.12**, Node 24입니다. native wheel은 manylinux2_35 x86_64이며 해당 ABI/glibc 환경이 필요합니다. Native worker는 socket을 차단하고, 지원하지 않는 플랫폼/샌드박스에서는 다른 엔진으로 바꾸지 않고 실패합니다. 외부 공개 배포용 인증 서비스가 아니므로 **127.0.0.1에만 bind**하고 신뢰하는 로컬 사용자 환경에서 실행하세요.

기존 저장소/브랜치와 lockfile을 그대로 사용합니다:

```sh
cd /workspace/multi-exchange-ai-trader
uv sync --locked
npm ci --prefix apps/web
```

Nautilus1.231.0 및 patched fsspec2026.6.0/Vite7.3.7/Vitest4.1.12/scoped minimatch11.0.1 계약은 lockfile에 고정돼 있습니다. 보안 수정에 필요한 의존성 override의 upstream 계약 차이와 검증 한계는 [qualification](docs/qa/phase3/qualification.md)에 기록했습니다. 원본 라이브러리는 수정하지 않았고 [LGPL 고지/소스 링크](docs/licenses/NAUTILUS_NOTICE.md)를 보존합니다. 임의 `--force` 설치·취약점 ignore로 바꾸지 마세요.

## 보존된 실제 spot 자료를 로컬에 등록 (선택)

새 checkout에서 실제 연구 snapshot을 쓰려면 재수집 전에 검증된 파일을 불변 loader로 등록할 수 있습니다. 네트워크 요청은 없으며 기존 동일 ID 파일을 덮어쓰지 않습니다. backend 시작 전에 실행하세요:

```sh
uv run python - <<'PY_DATA'
from pathlib import Path
import re
from multi_exchange_trader.research.data import load_dataset, save_dataset
for file in Path("docs/qa/phase4/data").glob("*.json"):
    if re.fullmatch(r"[a-f0-9]{64}", file.stem):
        save_dataset(Path("work/phase4-data/datasets"), load_dataset(file.parent, file.stem))
PY_DATA
```

## 기존 DB를 보존하며 시작

backend:

```sh
MARKET_DATA_BLOCKED_HOSTS=fapi.binance.com,api.bybit.com \
PAPER_DB_PATH=work/phase3-data/paper.sqlite3 \
PHASE4_DB_PATH=work/phase4-data/research.sqlite3 \
PHASE4_DATA_ROOT=work/phase4-data/datasets \
uv run uvicorn multi_exchange_trader.main:app --host 127.0.0.1 --port 8000
```

별도 터미널에서 frontend:

```sh
npm run dev --prefix apps/web -- --host 127.0.0.1 --port 5173
```

`http://127.0.0.1:5173/` , `http://127.0.0.1:5173/?view=research` , 또는 계좌 조회 `http://127.0.0.1:5173/?view=account` 를 엽니다. 프로세스가 응답하는지는 `curl -fsS http://127.0.0.1:8000/api/v1/health`로 확인합니다. `http://127.0.0.1:8000/api/v1/ready`는 기능별 상태입니다. 둘 다 실거래 준비가 됐다는 뜻이 아닙니다.

## Bybit 계좌 조회 (읽기 전용)

화면은 `http://127.0.0.1:5173/?view=account` 입니다. JSON은 `http://127.0.0.1:8000/api/v1/account/bybit/view` 입니다. 키는 환경 변수 또는 저장소 루트의 `.env`에 있는 `BYBIT_API_KEY`와 `BYBIT_API_SECRET`만 읽습니다. `.env`는 git에 넣지 않습니다. 화면·로그·응답에 키를 찍지 않습니다. 비밀키가 없으면 계좌 클라이언트는 시작하지 않고, 다른 화면은 그대로 켜집니다.

`LIVE_TRADING_ENABLED`의 기본값은 false입니다. 주문과 레버리지 주소는 항상 거부합니다. 이 값을 켜도 이 클라이언트는 주문을 보내지 않습니다. 나중 주문은 NautilusTrader 한 경로만 사용하며, 그 경로는 아직 시작하지 않습니다. Nautilus Bybit 실행 클라이언트는 접속할 때 레버리지를 바꿀 수 있어 이 조회에서는 쓰지 않습니다.

공개 시세의 `MARKET_DATA_BLOCKED_HOSTS`는 그대로입니다. 계좌 조회만 별도 읽기 클라이언트를 사용합니다. 지역 제한 문구가 있을 때만 연결이 막힌 것으로 보여 주고, 403만으로 국가 제한이라고 하지 않습니다. 잔고 숫자를 만들어 보여 주지 않습니다. VPN이나 프록시는 바꾸지 않습니다. 조회 값의 나이와 마진 표시는 [읽기 전용 계좌 문서](docs/account/BYBIT_READONLY.md)와 [안정성 메모](docs/STABILITY.md)에 있습니다.

이미 있는 포지션과 미체결 주문은 모두 외부입니다. 프로그램이 가져가거나 수정하지 않습니다. PAPER 탭의 숫자는 가상 돈이라 이 잔고와 다릅니다. 자세한 설명은 [읽기 전용 계좌 문서](docs/account/BYBIT_READONLY.md)에 있습니다.

위 DB 경로는 기본 경로입니다. 이미 다른 경로를 쓰고 있다면 **동일한 보존 경로**를 지정하세요. DB/WAL/SHM·기존 work/·백업을 삭제하거나 새로운 빈 DB로 바꿔 회복했다고 주장하지 않습니다. runtime state는 gitignore 대상이며 공개 커밋하지 않습니다. schema/checksum/native reconciliation 실패 시 원본을 보존하고 중단합니다.

신규 ID에만 10,000 가상 USDT를 한 번 seed합니다. 기존 원장 reset/삭제/재입금 endpoint가 없습니다. 수동 PAPER의 기존16시나리오/500이벤트 제한은 유지합니다. 별도 자동실행도16세션/500native events, 최대80봉·봉당6ordinary events·종료 여유10을 적용합니다. 고정 수량/선택된 세션의 실제 immutable 위험 설정은 새 세션 생성 폼과 별도로 표시됩니다.

생성은 PAUSED이며 **명시 시작/재개**가 필요합니다. 실행 중 프로세스를 재시작하면 PAUSED_RECOVERY로 복구하며 자동 재가동하지 않습니다. Pause는 포지션을 닫지 않고, stop은 진행 중 transaction 확인 후 native reduce-only로 정리합니다. 미확정 HTTP 응답은 브라우저에 보존한 **동일 키·payload**로 재시도합니다. 브라우저 저장소를 지워 결과를 숨기거나 새 키로 중복 제출하지 마세요. Gap 초과 손실·음수 잔고는 보존되며 근사 청산 모델을 실제 거래소와 동일하다고 주장하지 않습니다.

## 정식 과거 spot collector

현재 작업 환경은 revision1/current/running/restricted/enforced 및 승인 호스트 적용을 확인했습니다. 이미 적용된 환경을 미게시 draft로 가정하지 않습니다. 새 환경에서는 정식 runtime 상태/정책을 먼저 확인하고, inherited proxy/CA/TLS를 유지하세요. 알려진 거절 호스트에 재요청하거나 새 호스트·VPN·프록시로 우회하지 않습니다.

수집은 UI/자동루프/CI에서 실행하지 않는 **명시적 수동 CLI**입니다. 승인된 고정 `data-api.binance.vision`의 공개 spot endpoint만 BTC/ETH 각1회 bounded GET하며 retry/redirect/pagination이 없습니다. 예시 기간은 보존된 실제 연구 snapshot과 같으므로 회복 시에는 먼저 [검증된 기존 자료](docs/qa/phase4/data/README.md)를 확인하고 불필요하게 재수집하지 마세요:

```sh
MARKET_DATA_BLOCKED_HOSTS=fapi.binance.com,api.bybit.com \
uv run python scripts/collect_phase4_spot.py \
  --start 2026-09-30T00:00:00+00:00 \
  --end 2026-10-30T00:00:00+00:00 \
  --output work/phase4-data/datasets
```

각 JSON은 실제 raw 가격/UTC 시각, 요청 기간·수집 시각·source/market, raw SHA256 및 canonical dataset ID를 보존합니다. 중복/역순/gap/미마감/미래/잘못된 정밀도·OHLC는 거절합니다. 호스트 거절이나 품질 실패는 실패로 남기고 대체하지 않습니다. 공개 API 접근이 데이터 재배포 라이선스나 거래소 보증을 뜻하지 않습니다.

## 공개 시장 샘플뷰

`http://127.0.0.1:5173/?preview=1`은 **공개 시장 탭의 synthetic 샘플뷰**입니다. 그 뷰에서 공개 시장 API를 호출하지 않습니다. 이는 다른 PAPER/연구 탭의 로컬 API까지 비활성화한다는 뜻이 아닙니다. 샘플 가격을 실제 시세나 실데이터 검증으로 쓰지 않습니다.

## 검사와 CI

```sh
uv run pytest -q
uv run ruff check src tests scripts
uv run mypy src scripts
npm test --prefix apps/web -- --run
npm run lint --prefix apps/web
npm run typecheck --prefix apps/web
npm run build --prefix apps/web
npm audit --prefix apps/web
```

CI는 exchange 시장/계정 API를 호출하지 않고 offline fixture와 실제 로컬 native engine을 검증합니다. 전체 Git history Secret scan과 Dependency review gate를 유지합니다. 역사적 synthetic UUID4건/공개 storage namespace1건은 [정확한 fingerprint와 근거](docs/qa/phase4/security-findings.json)로만 처리하며 전역 key/UUID/path allowlist는 없습니다. 최종 Phase4 증빙 커밋도 정상 CI로 그 정확한 head를 검사합니다. 필수 검사 PASS는 수익성·실거래 승인·모든 보안 문제 부재를 의미하지 않습니다.
