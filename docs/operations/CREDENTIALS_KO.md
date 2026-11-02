# 프로그램 관리 자격 증명 등록

이 문서는 명시적으로 선택한 로컬 파일에서 자격 증명을 한 번 등록하고, 원본 파일 없이
프로그램의 비공개 저장소에서 다시 읽는 기능을 설명한다. 등록 결과는 **등록됨·미검증**이다.
등록은 Bybit 인증, AI 생성, 네트워크 요청, 주문, 실행 모드 변경을 수행하지 않는다.
저장된 DEMO 키가 있어도 `BYBIT_DEMO=true`나 거래·AI 실행 허용 설정을 만들지 않는다.
기존 계좌·AI 클라이언트의 시작 연결은 이 저장소를 사용한다. 실제 외부 검증은 별도 작업이다.

## 명시적 최초 등록과 변경

프로젝트가 설치된 Python 3.12 환경에서 실행한다. 명령줄에는 키 값을 쓰지 않는다.
`--import-env`는 절대 경로가 필수이며, 자동 `.env` 검색은 없다.

```powershell
python -m multi_exchange_trader.credentials_cli --import-env C:\secure\source.env
```

특정 저장소를 선택하려면 `--store`에 절대 경로를 지정한다.

```powershell
python -m multi_exchange_trader.credentials_cli --import-env C:\secure\source.env --store C:\secure\trader-private\credentials.bin
```

최초 등록은 저장소가 없어야 성공한다. 이미 존재하는 저장소를 변경할 때는 이전 성공
결과의 revision을 명시한다. 아래 예는 revision 1을 읽은 운영자가 변경을 등록하는 경우다.
동시에 다른 등록이 먼저 성공했으면 `REVISION_CONFLICT`로 실패한다. 최신 revision과
변경 내용을 확인한 다음 다시 실행한다. 초기화나 강제 덮어쓰기 옵션은 없다.

```powershell
python -m multi_exchange_trader.credentials_cli --import-env C:\secure\source.env --expected-revision 1
```

성공 출력에는 `schema`, `revision`, `namespaces`만 포함한다. namespace는 등록된 키가
있는 `BYBIT_LIVE`, `BYBIT_DEMO`, `ANTHROPIC`, `OPENROUTER`다. 출력에는 키, 모델 값,
암호문, 원본 경로가 없다. 원본 파일은 수정하거나 삭제하지 않는다. 필요한 보관·삭제는
운영자가 별도로 결정해야 한다. 실행 중인 프로세스가 이미 읽은 스냅샷은 변경하지 않는다.

## 가져오는 항목과 검증

다음 항목만 가져온다.

- `BYBIT_API_KEY`, `BYBIT_API_SECRET`
- `BYBIT_DEMO_API_KEY`, `BYBIT_DEMO_API_SECRET`
- `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`
- `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `AI_PROVIDER`

권한 목록, UID·부모·마스터 계정 주장, 호스트 주소, DB 경로, 거래·AI 허용 플래그,
`BYBIT_DEMO` 등은 가져오지 않는다. 권한을 검증하거나 활성화하는 기능도 없다.
등록은 기존 값을 합친 최종 결과를 검증한다. 각 Bybit namespace의 키와 시크릿은 함께
있어야 하며 LIVE/DEMO에서 같은 키 또는 같은 시크릿을 재사용할 수 없다. AI 키·모델은
기존 제공자 검증기를 명시적 매핑으로 호출해 검증하며 파일 대체 로딩을 끈다.

원본은 UTF-8(선택적 BOM), 최대 16 KiB·256줄이다. 빈 줄, 주석, `export NAME=value`,
일반 `NAME=value`, 바깥쪽의 한 쌍 따옴표를 지원한다. 보간·명령 실행·이스케이프 해석은
없다. 중복 변수 이름, 잘못된 대입 형식, 닫히지 않은 바깥 따옴표, 제어 문자, 잘못된
키·모델을 거부한다. 가져오지 않는 변수도 중복·제어 문자 검사를 우회할 수 없다.

저장 형식은 schema 1이며 원문 페이로드는 16 KiB, 디스크 envelope는 64 KiB 이하다.
JSON 중복 필드, 알 수 없는 필드·버전, 잘못된 자료형·revision을 거부한다. 손상되거나
읽을 수 없는 기존 저장소는 오류다. 원본 `.env`를 다시 찾아서 조용히 대체하지 않는다.

## 저장 위치와 OS 보호

`TRADER_CREDENTIAL_FILE`이 절대 경로이면 그 위치를 선택한다. 값이 `off`이면 기본
저장소를 비활성화한다. CLI의 명시적 `--store`는 그 호출에서 사용할 저장소를 선택한다.
지정하지 않았을 때 기본 위치는 다음과 같다.

- Windows: `%LOCALAPPDATA%\MultiExchangeTrader\private\credentials.bin`
- Linux: `$XDG_CONFIG_HOME/multi-exchange-trader/private/credentials.json`
- Linux에서 XDG 설정이 없으면 `$HOME/.config/multi-exchange-trader/private/credentials.json`

Windows는 현재 사용자 DPAPI와 UI 금지 옵션으로 보호한 암호문만 디스크에 기록한다.
machine 범위나 평문 대체 경로가 없다. 다른 사용자·컴퓨터로 파일을 복사하는 것만으로
복원이 보장되지 않는다. `chmod`가 Windows ACL을 설정한다고 주장하지 않는다. DPAPI는
암호문 기밀성을 제공하며, 디렉터리 접근·파일 삭제 방지는 운영 Windows ACL 관리에
달려 있다. 같은 사용자 권한으로 실행되는 악성 프로세스에 대한 격리는 보장하지 않는다.

Linux는 현재 서비스 사용자 소유 디렉터리 `0700`, 파일·고정 잠금 파일 `0600`을 요구한다.
새 경로는 이 모드로 만들고 기존의 잘못된 소유권·모드는 거부한다. 이 형식은 접근 통제된
평문이며 암호화가 아니다. envelope의 base64 인코딩도 암호화가 아니다. 기존 root 소유
`0600` systemd `EnvironmentFile` 방식은 별개의 배포 방법으로 유지된다.

읽기는 파일·디렉터리를 만들지 않는다. 상대 경로·`..`·심볼릭 링크·junction·reparse
구성요소·하드링크·일반 파일이 아닌 대상을 거부한다. 등록은 같은 디렉터리의 고정
`.credentials.lock`을 OS 잠금으로 최대 5초 기다린다. 잠금 파일을 지우거나 강제로
해제하지 않는다. revision과 대상·잠금 파일 교체를 확인하고, 독점 임시 파일에 쓰기·flush·
fsync 후 같은 디렉터리에서 원자적 교체한다. POSIX에서는 디렉터리도 fsync한다. Windows의
Python 표준 라이브러리에는 같은 디렉터리 fsync 기능이 없어 파일 fsync와 교체 후 재읽기로
확인한다. 교체 후 확인 또는 디렉터리 fsync가 실패한 경우 성공을 출력하지 않지만 새
revision이 이미 디스크에 있을 수 있다. 오류 뒤에는 상태를 확인하고 수정해야 한다.

## 오류와 비밀 취급

오류는 `UNSAFE_PATH`, `INVALID_STORE`, `INVALID_VALUES`, `INVALID_IMPORT`,
`REVISION_CONFLICT`, `LOCK_TIMEOUT`, `PERSISTENCE_FAILED`, `PROTECTION_UNAVAILABLE`,
`INVALID_ARGUMENTS` 중 고정 코드만 출력한다. 원시 OS 예외나 입력값을 출력하지 않는다.
`CredentialSnapshot`과 등록 결과는 수정할 수 없고 repr에는 안전한 메타데이터만 있다.
스냅샷은 비공개 클라이언트 구성에만 사용하며 API DTO나 일반 직렬화에 넘기지 않는다.
Python 런타임의 비밀 문자열을 안전하게 영구 삭제한다고 보장하지 않는다.

이 저장소는 업무 SQLite에 저장하지 않으며 업무 백업 목록에 추가하지 않는다. 운영자는
원본·OS 사용자 프로필·비공개 저장소를 자체 비밀 관리 정책으로 다뤄야 한다. 테스트에서는
기본 저장소를 `off`로 설정하고 합성 값과 임시 경로만 사용한다. 명시적인 테스트 보호기
주입은 `test-injected` 형식으로 구분하며 일반 생성자는 이 저장소를 거부한다.

## 재시작과 기존 설정 호환성

API는 계좌 클라이언트와 연결 상태용 AI 메타데이터에 하나의 비공개 시작 컨텍스트를
공유한다. 모듈 import, `create_app()`와 라우터 설치는 비밀 파일을 읽지 않는다. 첫 시작
해석 때 저장소를 한 번 읽고 환경 변수를 복사하여 사용한다. 실행 중 등록 revision이나
원본 파일이 바뀌어도 이미 선택한 키·모델은 바뀌지 않으므로 변경 적용에는 프로세스 재시작이
필요하다. 상태 조회는 재인증·생성·주문이나 비밀 파일 재검색을 하지 않는다.

유효한 저장 namespace가 우선한다. LIVE/DEMO 키·시크릿은 각각 한 쌍으로 선택하고,
최종 선택된 두 쌍 사이의 동일 키·시크릿도 거부한다. 환경의 반쪽 키와 파일의 나머지
시크릿을 섞지 않는다. 저장된 제공자 키가 있으면 같은 제공자의 오래된 환경 모델도
빌리지 않는다. 저장 namespace가 없을 때만 기존 명시 환경·파일 설정을 사용한다.
기존 Bybit 기본 `.env` 호환 경로는 저장되지 않은 namespace에만 적용하며 프로세스 환경을
수정하지 않는다. AI는 기존 명시 `AI_ENV_FILE`·`ANTHROPIC_ENV_FILE` 설정만 사용하고
기본 `.env` 검색은 하지 않는다. 명시 매핑으로 호출한 두 AI 설정 로더는 기본 저장소를
검색하지 않는다. 저장 값을 조합한 매핑은 파일 대체 로딩을 끈다.

기존 저장소가 손상·읽기 불가이면 환경·원본 파일로 다른 계정을 고르지 않는다. 연결은
잘못된 설정으로 표시되며 관련 계좌·AI 경로를 차단한다. 별개 PAPER·리서치·건강 조회는
그 오류만으로 종료하지 않는다. 비밀 문자열·경로는 연결 DTO·모델·예외 메시지에 포함하지
않으며, 저장된 Bybit 키도 기존 계좌 응답의 비밀 노출 검사에 포함한다.

`python -m multi_exchange_trader.execution.worker_cli`의 기본 `DRY_CHECK`는 저장소·키·
네트워크·장부를 열지 않는다. 명시 `--run-monitor`만 한 번 선택한 DEMO 쌍을 사용하며
`BYBIT_DEMO=true`, 독립 UID·부모·마스터 정보, 인증 binding, 권한·네이티브·운용 자격
검사는 그대로 필요하다. 등록은 이 값이나 자격을 만들어 주지 않는다. Linux 전용 모니터
진입 조건도 유지된다.

비공개 OpenRouter 팩토리는 같은 제공자·키·모델 스냅샷으로 기존 metadata client,
`OpenRouterGenerationClient`, `AIDispatcher`만 구성한다. 생성 시 로컬 발송 장부를 준비할 수
있지만 네트워크 호출·가격 견적·예산 변경·생성 활성화는 없다. 호출하려면 기존 유효한 가격
견적과 명시 예산·발송 조건을 모두 통과해야 한다. 공개 API에 유료 생성 경로를 추가하지
않으며 direct Anthropic 생성도 지원한다고 표시하지 않는다.
