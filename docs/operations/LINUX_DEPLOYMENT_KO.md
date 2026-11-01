# Linux 로컬 전용 배포·복구

이 저장소의 템플릿은 새 Linux x86_64 환경에 **운영자가 나중에 설치할 자료**다. 서버 생성, 유료 자원 구매, 원격 접속, 서비스 변경, 키 이전은 이 작업에서 실행하지 않는다. Windows 검사만으로 Nautilus 네이티브 동작이나 실제 Linux 설치 완료를 주장하지 않는다. 이전 시험 서버의 날짜 제한이나 만료된 실행 가드를 복사하지 않는다.

## 고정 구성과 경계

| 항목 | 경로·설정 |
|---|---|
| 코드·가상환경·Git 이력 | `/opt/trader/app`, `.venv`, `.git` 포함 |
| 실행 사용자 | 전용 비관리자 `trader:trader` |
| 변경 가능한 상태 | `/var/lib/trader/data` |
| 임시·캐시 | `/var/lib/trader/tmp`, `/var/lib/trader/cache` |
| 일반 설정 | `/etc/trader/runtime.env`, root 소유 0600 |
| 별도 PC에서 받을 키 | `/etc/trader/credentials.env`, root 소유 0600 |
| API | `127.0.0.1:8000`, 단일 worker |
| 정적 웹·프록시 | nginx `127.0.0.1:5173` |

템플릿은 LIVE, Bybit DEMO, TESTNET을 모두 끈다. `EnvironmentFile`은 `Environment=`보다 우선하므로 `ExecStart`의 `/usr/bin/env` 인수가 마지막으로 `LIVE_TRADING_ENABLED=false`, `BYBIT_DEMO=false`, `BYBIT_TESTNET=false`, `BYBIT_ENV_FILE=off`, `ANTHROPIC_ENV_FILE=off`를 강제한다. 다른 PC의 파일에 DEMO=true가 있어도 이 서비스는 이를 활성화하지 않는다. systemd가 root 권한으로 0600 파일을 읽고 프로세스 환경으로 전달하며, trader에게 비밀 파일 읽기 권한을 주지 않는다.

AI는 애플리케이션의 `OFFLINE_TEST`, `llm_enabled=false`, `external_calls=false` 계약을 유지한다. 해당 이름의 가상 환경 스위치를 새로 만들지 않는다. Anthropic 키·모델 등록은 `REGISTERED_UNVERIFIED`이며 인증 성공, 잔액, 유료 호출 승인, LLM 분석 실행을 의미하지 않는다. 기본 비밀 파일은 주석만 있다. 일반 공개 시세 호스트도 `runtime.env.example`에서 차단한다. Bybit 실제 DEMO 점검은 연습 계정에 **실제 주문**을 만들 수 있으므로 설치·건강 점검으로 사용하지 않는다.

## 재현 가능한 소스·빌드 준비

대상은 Ubuntu 24.04 x86_64, Python **3.12**, uv **0.12.23**, Node **24** 계열이다. Python 패키지는 `uv.lock`, 웹 패키지는 `apps/web/package-lock.json`을 그대로 사용한다. NautilusTrader **1.231.0**, fsspec **2026.6.0** 보안 override를 유지한다. OS·Node·Python의 실제 패치 버전과 설치 패키지 버전도 설치 기록에 남긴다. `uv sync --upgrade`, 잠금 파일 재생성, 자동 배포·자동 업데이트를 하지 않는다.

설치자가 출처와 SHA를 검토한 Git clone 또는 검증한 Git bundle clone을 **최종 경로 `/opt/trader/app`에** 준비한다. 다른 위치에서 만든 가상환경을 복사하면 절대경로가 깨질 수 있다. Git archive/소스 ZIP만 풀어 `.git`을 없애지 않는다. 원본 commit, Git 상태, 잠금 파일과 빌드 결과 해시를 기록해야 네이티브 자격 검증과 소스 출처를 재현할 수 있다. 프로젝트 밖을 가리키는 worktree의 `.git` 파일도 독립 배포본으로 사용하지 않는다. 자격 증명이 들어간 Git remote URL을 쓰지 않는다.

다음은 **설치자가 최종 checkout에서** 실행할 빌드·검증이다. 도구와 시스템 패키지 설치는 관리자가 별도로 승인·준비한다. 의존성 다운로드가 필요하므로 아래 명령은 설치 준비 단계이지 서비스 시작 시 자동 작업이 아니다.

```sh
cd /opt/trader/app
test -d .git
git rev-parse HEAD
git status --porcelain
python3.12 --version
uv --version
node --version
npm --version
UV_PYTHON_DOWNLOADS=never uv sync --locked --python python3.12
npm ci --prefix apps/web
uv run --locked pytest -q
uv run --locked ruff check src tests scripts
uv run --locked mypy src scripts
npm test --prefix apps/web -- --run
npm run lint --prefix apps/web
npm run typecheck --prefix apps/web
npm run build --prefix apps/web
sha256sum uv.lock apps/web/package-lock.json apps/web/dist/index.html
```

Windows에서는 일부 네이티브·권한·nginx 검사가 건너뛰어진다. Linux CI의 전체 테스트 및 전용 operations 테스트가 같은 commit에서 통과해야 한다. 실제 서버의 seccomp, 파일 권한, systemd 격리 안 네이티브 실행도 별도 확인한다. `health` 응답이나 import 성공만으로 이 검증을 대체하지 않는다. 테스트는 운영 장부가 아닌 임시 장부를 사용한다.

## 운영자가 실행할 설치 절차

새 전용 호스트를 기준으로 한다. 기존 경로나 설정이 있으면 아래 복사를 실행하지 말고 기존 상태를 보존한 채 검토한다. 코드와 `.venv`는 trader가 수정할 수 없게 관리자가 소유권과 권한을 설정한다. nginx 사용자가 `apps/web/dist`를 읽을 수 있어야 하지만 `/etc/trader`나 상태에는 접근시키지 않는다.

전용 trader 계정이 아직 없을 때 관리자가 만든다. 경로가 심볼릭 링크나 기존 다른 서비스의 상태를 가리키지 않는지 먼저 확인한다. 아래 `install -d`는 새 배포의 명시적 설치 작업이다. 블록은 별도 서브셸에서 실행하며 오류가 나면 중단한다. 네 파일은 기존 파일과 끊어진 심볼릭 링크까지 거절한 경우에만 설치한다. 중간에 중단되면 이미 만든 신규 파일을 자동 삭제하거나 덮어쓰지 않고 검토한다. 부모 경로를 다른 프로세스가 동시에 바꾸지 않는 새 전용 환경을 전제로 한다.

```sh
(
set -e
sudo useradd --system --home-dir /var/lib/trader --shell /usr/sbin/nologin trader
sudo install -d -o root -g root -m 0700 /etc/trader
sudo install -d -o trader -g trader -m 0700 /var/lib/trader
sudo install -d -o trader -g trader -m 0700 /var/lib/trader/data
sudo install -d -o trader -g trader -m 0700 /var/lib/trader/tmp
sudo install -d -o trader -g trader -m 0700 /var/lib/trader/cache
sudo test ! -e /etc/trader/runtime.env &&
  sudo test ! -L /etc/trader/runtime.env &&
  sudo install -o root -g root -m 0600 deploy/linux/runtime.env.example /etc/trader/runtime.env || exit 1
sudo test ! -e /etc/trader/credentials.env &&
  sudo test ! -L /etc/trader/credentials.env &&
  sudo install -o root -g root -m 0600 deploy/linux/credentials.env.example /etc/trader/credentials.env || exit 1
sudo test ! -e /etc/systemd/system/trader.service &&
  sudo test ! -L /etc/systemd/system/trader.service &&
  sudo install -o root -g root -m 0644 deploy/linux/trader.service /etc/systemd/system/trader.service || exit 1
sudo test ! -e /etc/nginx/conf.d/trader.conf &&
  sudo test ! -L /etc/nginx/conf.d/trader.conf &&
  sudo install -o root -g root -m 0644 deploy/linux/nginx.conf /etc/nginx/conf.d/trader.conf || exit 1
)
```

별도 PC의 `.env`를 무검토로 전체 복사하지 않는다. 소유자가 필요한 서버용 이름과 값을 검토하여 0600 credentials 파일에 안전하게 전달한다. 여기서는 실제 값을 읽거나 복사하지 않는다. 환경·프로세스 전체를 출력하는 진단을 남기지 않는다. 소스, 웹 번들, 백업 데이터 폴더에 비밀 파일을 넣지 않는다.

nginx 배포판 기본 사이트가 `0.0.0.0:80` 또는 `[::]:80`에서 열릴 수 있다. **모든** 활성 설정을 운영자가 확인하고, 새 전용 호스트에서 기본 공개 사이트가 비활성화된 것을 확인한 뒤 시작한다. 이 템플릿 하나를 추가했다고 다른 listener가 사라지지는 않는다. 이 문서는 기존 설정을 자동 삭제하지 않는다.

```sh
sudo systemd-analyze verify /etc/systemd/system/trader.service
sudo nginx -t
sudo nginx -T
sudo systemctl daemon-reload
sudo systemctl start trader.service
sudo systemctl reload nginx.service
sudo systemctl show trader.service -p User -p Group -p MainPID -p ActiveState -p SubState -p Result
sudo ss -lntp
curl --fail http://127.0.0.1:8000/api/v1/health
curl --fail http://127.0.0.1:5173/api/v1/ready
curl --fail http://127.0.0.1:5173/api/v1/connections/status
curl --fail http://127.0.0.1:5173/api/v1/capabilities
```

기존 nginx가 멈춰 있으면 `reload` 대신 운영자가 검토 후 `start`한다. 자동 부팅 `enable`은 이 확인을 마친 후 선택한다. 앱과 nginx는 정확히 IPv4 loopback에서만 보여야 한다. `ready`는 scheduler/task 상태, `connections`는 등록·검증 여부, `capabilities`는 별도 지원 범위를 확인한다. 200 liveness는 거래·계정·키·모델 준비 완료가 아니다.

브라우저 주소는 `http://127.0.0.1:5173` 또는 `http://localhost:5173`이다. 원격 운영은 운영자가 승인한 SSH 터널을 쓰고 로컬 포트도 **5173 그대로** 유지한다. 프록시가 Host와 Origin을 로컬 값으로 바꾸는 편법, 임의 포트·도메인 허용, 외부 listener를 추가하지 않는다. 템플릿의 `$http_host`, `$http_origin`은 원래 요청을 그대로 전달하여 애플리케이션의 경계 검사가 유지된다.

## 여섯 장부와 데이터셋의 한 세대 백업

`scripts/operations/state_snapshot.py`는 Python 3.12 표준 라이브러리만 쓰는 실제 명령이다. 인수 없이 실행하면 도움말만 표시한다. `verify`는 원본 백업을 수정하지 않고 임시 복사본에서 SQLite를 검사한다. `snapshot`, `restore`만 명시적으로 새 디렉터리를 만든다. 서비스 정지·재개, 마이그레이션, 장부 초기화, 덮어쓰기 기능은 없다.

고정 소스 구조는 위 runtime 템플릿의 `/var/lib/trader/data`이다.

| 파일 | 앱 환경 변수 |
|---|---|
| `paper.sqlite3` | `PAPER_DB_PATH` |
| `spot-paper.sqlite3` | `PAPER_SPOT_DB_PATH` |
| `order-reviews.sqlite3` | `PAPER_REVIEW_DB_PATH` |
| `ai.sqlite3` | `AI_DB_PATH` |
| `recommendations.sqlite3` | `RECOMMENDATION_DB_PATH` |
| `research.sqlite3` | `PHASE4_DB_PATH` |
| `datasets/*.json` | `PHASE4_DATA_ROOT` |

기존 개발 환경의 `work/phase3-data`, `work/phase4-data`, `work/recommendations.sqlite3` 경로를 자동 추측하지 않는다. 기존 설치를 옮길 때는 **모든 writer를 먼저 정지**하고 실제 환경 변수에 따른 6개 DB, 각각의 존재하는 `-wal`/`-shm`, 전체 datasets를 하나의 새 private staging 구조로 보존해 넣은 뒤 이 도구를 실행한다. 원본을 이동·삭제하거나 DB 한 개만 최신으로 섞지 않는다. 여섯 DB가 없거나 구버전 스키마라면 실패하며 빈 DB를 대신 만들지 않는다.

**첫 버전은 온라인 백업을 지원하지 않는다.** 단일 SQLite backup API도 여섯 DB와 파일 데이터셋 전체의 공통 시점을 만들지 못한다. 운영자가 trader 서비스의 모든 프로세스, 별도 개발 서버, 수집기, 노트북·관리 도구, 예약 작업 등 모든 writer를 정지하고 정지 상태를 유지해야 한다. `--writers-stopped`는 그 사실에 대한 운영자의 확인이며 프로세스를 자동 탐지·정지하는 보장이 아니다. 확인할 수 없으면 실행하지 않는다. 실행 전후 해시 검사는 실수 방지 보조이며 실행 중 writer 허용 수단이 아니다.

예를 들어 **운영자가** 서비스를 정지한 후 `systemctl show trader.service -p ActiveState -p SubState -p MainPID`에서 inactive/dead, MainPID=0을 확인하고, `sudo fuser -v /var/lib/trader/data/*.sqlite3*` 등으로 열린 파일과 모든 별도 writer를 점검한다. `fuser` 결과만으로 아직 파일을 열지 않은 writer가 없다고 단정하지 않는다. 서비스 재시작·타이머도 막아 둔다. 예약 중단은 운영자가 관리한다.

다음은 검토 후 **운영자가** 실행할 예다. `/var/backups/trader`는 미리 root 소유 0700으로 준비하고 소스 밖, 링크 없는 경로인지 확인한다. 대상 이름은 매번 새로 정한다. root로 만든 복원본은 root 소유이므로 전환 전 관리자가 trader 소유권을 설정해야 한다.

```sh
sudo systemctl stop trader.service
sudo systemctl show trader.service -p ActiveState -p SubState -p MainPID
# 여기서 운영자가 모든 다른 writer도 정지·확인한다.
sudo python3.12 /opt/trader/app/scripts/operations/state_snapshot.py snapshot \
  --source /var/lib/trader/data --target /var/backups/trader/generation-001 --writers-stopped
sudo python3.12 /opt/trader/app/scripts/operations/state_snapshot.py verify \
  --source /var/backups/trader/generation-001
sudo python3.12 /opt/trader/app/scripts/operations/state_snapshot.py restore \
  --source /var/backups/trader/generation-001 --target /var/lib/trader/recovery-001
```

원본 SQLite를 열거나 checkpoint하지 않고 DB·WAL·SHM을 바이트 그대로 보존한다. SQLite를 열면 SHM 생성/복구가 생길 수 있어 integrity 검사는 항상 폐기 가능한 별도 복사본에서만 한다. manifest와 독립 파일 목록의 완전 일치, 크기, SHA-256, SQLite `integrity_check`, 고정 table/column 목록, user_version과 주요 ledger metadata를 검사한다. 이는 앱의 이벤트 재생·대사·데이터셋 의미 검증을 대체하지 않는다. 원본과 백업이 변경되지 않도록 private 경로를 유지해야 하며, 동시에 공격자가 부모 디렉터리를 바꾸는 환경은 지원하지 않는다.

SQLite가 손상된 WAL을 무시하고 과거 main DB만 정상으로 읽는 경우를 막기 위해 WAL header와 frame의 salt·누적 checksum도 별도 확인한다. 형식은 [SQLite 공식 파일 형식](https://www.sqlite.org/fileformat.html#wal_file_format)을 따른다. 첫 버전은 보수적으로 미완성·미커밋·재사용된 오래된 꼬리가 있는 WAL도 거절한다. 정상 SQLite가 처리할 수 있는 일부 crash/reuse 상태도 수동 검토 대상으로 남긴다는 제한이다. 원본 WAL 삭제나 자동 checkpoint로 검사를 통과시키지 않는다.

상한은 파일당 **512 MiB**, 전체 **2 GiB**, 파일 **10,000개**, manifest **4 MiB**, SQLite 검사별 progress deadline **10초**다. 데이터셋은 현재 앱처럼 한 단계의 안전한 이름 JSON만 허용한다. 초과 시 자동 상한 확대나 일부 백업을 하지 않는다. 링크·junction·hardlink·특수 파일, `..`, 알 수 없는 파일, rollback journal, 빠진 DB, 잘못된 manifest, 해시·스키마 오류는 거절한다. credentials와 코드·Git 이력은 이 상태 백업 범위에 없으며 별도 보관한다. 해시는 서명이나 암호화가 아니다. 백업은 개인정보·거래 기록을 포함할 수 있어 접근 권한과 오프호스트 보관도 별도 관리한다.

POSIX 출력 디렉터리는 0700, 파일은 0600으로 만든다. 대상은 **존재하지 않아야** 하고 소스 안일 수 없다. 유효성을 확인한 뒤 exclusive mkdir로만 예약한다. I/O 실패·전원 중단 시 새 대상에 부분 파일이 남을 수 있으나 완료 manifest가 없거나 해시가 달라 `verify`를 통과하지 못한다. 자동 삭제·덮어쓰기 재시도하지 말고 원인을 확인하여 새 대상 이름으로 다시 실행한다. 이 도구는 전원 차단 내구성을 보장하는 원격 백업 시스템이 아니므로 완료 후 별도 매체의 검증된 복사본을 유지한다.

## 복원 후 전환·롤백

복원은 언제나 새 `/var/lib/trader/recovery-001`로만 한다. 운영자가 원본 정지 상태를 유지한 채 복원본 권한, 6개 DB 경로와 dataset 경로를 모두 함께 검토한다. 경로 전환이 필요하면 `runtime.env`의 7개 경로를 같은 세대로 변경하고 원래 설정과 원본 디렉터리는 보존한다. 소스 버전도 manifest와 별도로 보관한 정확한 Git SHA와 lock 파일에 맞춘다. 구버전 코드로 새 스키마를 열거나 자동 다운그레이드하지 않는다.

첫 앱 시작은 복원본에 애플리케이션 자체 대사·재생을 수행한다. 기존 research RUNNING/ENTRY_PAUSED 세션은 코드의 복구 정책에 따라 `PAUSED_RECOVERY`로 멈추며, 운영자가 상태·포지션·미확정 명령을 검토하고 **명시적으로 재개**하기 전 자동매매가 복구되었다고 표시하거나 재개하지 않는다. AI uncertain reservation, 검토 토큰, 추천의 미확정 명령은 그대로 보존한다. 앱이 fail-closed면 원본 장부를 고치거나 기록을 지우는 대신 멈춘 상태로 원인을 확인한다.

롤백도 서비스를 정지하고 모든 writer를 확인한 뒤 이전 코드 SHA·설정·검증된 **하나의 전체 세대**를 새 디렉터리에 복원해 전환한다. 이미 생성된 현재 장부 위로 덮어쓰거나 서로 다른 DB 세대를 합치지 않는다. HTTP 응답, SSH 재접속, systemd active만으로 복원·계정·거래·LLM 연결을 완료 판정하지 않는다.
