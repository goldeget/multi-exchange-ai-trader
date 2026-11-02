# 선택한 Bybit DEMO 계정 프로필

계정 프로필은 운영자가 선택한 **BYBIT DEMO 계정 한 개**의 기대 신원을 재시작 후에도 보관하기 위한 별도 로컬 설정이다. API 키와 시크릿은 기존 자격 증명 저장소에서 관리한다. 프로필에는 키, 키 해시·접두사, 권한·잔고 스냅샷, 호스트·파일 경로, 실행 플래그를 넣지 않는다.

구현 범위는 UID 최초 등록과 상태 조회, 별도 GET 전용 관찰 세션에서의 명시적 최초 관계 채택,
저장 기대값을 이용한 명시적 워커 시작이다. 저장 기대값은 현재 인증이 아니다. 등록·채택은
주문, 모니터링, AI 호출 또는 서비스 활성화를 수행하지 않는다. 이 코드의 실제 운영자 사용은
독립 검토·최종 CI·main 확인 후 root 담당자가 별도로 검증한다.

## 명시적 등록과 조회

```text
python -m multi_exchange_trader.account_profiles_cli
python -m multi_exchange_trader.account_profiles_cli --profile-store "<절대 파일 경로>" status
python -m multi_exchange_trader.account_profiles_cli --profile-store "<절대 파일 경로>" register-uid --uid "<운영자가 선택한 DEMO UID>"
```

인수가 없는 호출은 `DRY_CHECK`만 출력하며 저장소를 읽거나 만들지 않는다. `status`는 지정한 기존 프로필만 읽으며 파일이 없으면 `{"state": "NO_PROFILE"}`을 출력한다. 없는 파일이나 디렉터리를 조회만으로 만들지 않는다. 등록과 조회 모두 자격 증명 저장소와 HTTP에 접근하지 않는다. 실제 UID와 운영자 파일 경로는 공개 문서·로그·Git에 기록하지 않는다.

최초 등록은 파일이 없을 때만 성공한다. 생성된 프로필은 `PENDING`, `OPERATOR_SELECTED`이며 관계 기대값을 제공하지 못한다. 기존 프로필의 UID 변경, 추가 프로필, 교체·삭제·자동 마이그레이션은 지원하지 않는다. `--expected-revision`은 저장 경계의 충돌 확인용이며, 최초 등록에 revision을 지정하거나 기존 파일에 재등록하면 `REVISION_CONFLICT`다.

성공 결과와 기본 상태 출력 필드는 `schema`, `revision`, `profile_id`, `state`, `basis`뿐이다. UID·parent UID·경로는 출력하지 않는다. 인수 및 저장소 오류는 고정된 코드로 보고하며 입력값과 운영체제 진단을 그대로 내보내지 않는다.

## 파일 선택과 보호

`TRADER_ACCOUNT_PROFILE_FILE=off`는 기본 선택을 끈다. 해당 변수에 절대 경로를 지정하거나 한 호출에서 `--profile-store`를 지정할 수 있다. 선택 함수는 경로만 결정하며 파일 상태를 조사하지 않는다. 기본 경로는 다음과 같다.

| 플랫폼 | 기본 경로 |
|---|---|
| Windows | `%LOCALAPPDATA%/MultiExchangeTrader/private/account-profiles.json` |
| Linux | `$XDG_CONFIG_HOME/multi-exchange-trader/private/account-profiles.json` |
| Linux, XDG 미설정 | `$HOME/.config/multi-exchange-trader/private/account-profiles.json` |

파일은 최대 UTF-8 64KiB의 schema1 JSON이며 정확히 한 프로필만 포함한다. revision은 bool을 제외한 정수 `1..2^63-1`, ID는 소문자 표준 UUIDv4, 시각은 UTC `Z` 형식이다. 시각은 초 단위 또는 0이 아닌 마이크로초의 여섯 자리 표현을 쓴다. UID는 선행 0이 없는 양의 ASCII 숫자 1~20자리이며 parent UID만 `0`을 허용한다. 본인의 UID와 같은 parent UID는 거부한다. `is_master`는 정확한 bool이며 parent UID에서 추론하지 않는다.

중복·누락·알 수 없는 필드, 알 수 없는 정책·버전, NaN/Infinity, 잘못된 타입·시각·UID, 제어문자, 여러 프로필은 거부한다. 링크·Windows 재분석 지점·하드링크·일반 파일이 아닌 대상 및 위험한 경로를 거부한다. Linux는 현재 사용자 소유와 디렉터리 `0700`/파일 `0600`을 요구한다. Windows 파일은 암호화하지 않으며 `chmod`를 Windows ACL 보장으로 해석하지 않는다.

프로필 전용 고정 `.account-profiles.lock`을 사용하고 대기 한도는 5초다. 쓰기는 잠금 안에서 기대 revision·프로필 ID·UID·파일 식별자를 확인하고, 같은 디렉터리의 배타적 임시 파일에 기록·fsync한 뒤 원자 교체하고 다시 읽어 확인한다. Linux에서는 디렉터리 fsync도 수행한다. 실패하거나 교체 결과가 불확실하면 성공을 반환하지 않으며 자동 롤백·재시도를 하지 않는다. 운영자는 오류 후 명시적인 상태 조회로 현재 파일을 확인해야 한다.

## 명시적 GET 관찰과 한 번의 채택

먼저 운영자가 선택한 UID를 `register-uid`로 고정한다. 부모·마스터의 최초 제공자 주장을
다음 명령으로 명시적으로 채택할 수 있다. 기존 자격 증명 저장소 또는 명시적 기존 환경의
DEMO 쌍 한 스냅샷과 기존 `BYBIT_DEMO`/`BYBIT_TESTNET` 조건을 사용한다.

```text
python -m multi_exchange_trader.account_profiles_cli --profile-store "<절대 파일 경로>" enroll-observed --policy uid-anchored-adoption-v1 --adopt-observed-relationship
```

이 명령의 이름·정책·채택 의도는 모두 명시해야 한다. 같은 살아 있는 프로세스 안에서
`observe()`로 후보를 받은 뒤 별도 `adopt()`를 호출한다. 기본 명령·`status`·`register-uid`·
워커 시작은 관찰 응답을 자동 채택하지 않는다. API 관찰은 고정된 `api-demo.bybit.com`의
서버 시각과 키 정보 GET 두 경로만 사용한다. 관찰기는 그 응답으로 `authenticate`를 호출하거나
binding·attestation·receipt·native 권한을 만들지 않는다. 키 echo·빈 secret·미리 선택한 UID·
부모·마스터의 독립된 원시 타입·전체 권한 스키마를 기존 엄격한 파서로 검사한다.

후보는 공개 데이터나 UUID가 아닌 정확한 세션·세대·클라이언트·transport·호스트·키 쌍·
프로필·UID·revision에 묶인 메모리 핸들이다. 수명은 요청 시작부터 최대 30초이며 벽시계·
단조 시계·두 서버 응답 시각을 검사한다. 새 시도·실패·취소·종료·대상 변경은 이전 후보를
폐기한다. 주입 transport는 관찰 테스트에 쓸 수 있지만 실제 채택에 사용할 수 없다.

채택은 가능한 쓰기보다 먼저 후보를 소진하고, 잠금 전·획득 후·게시 직전 같은 진행 중
채택 작업과 원래 시간 제한을 반복 검사한다. 잠금 대기로 만료되거나 게시 결과가
불확실해도 후보는 재사용할 수 없다. 자동 롤백·재시도 대신 별도 `status`로 현재 상태를
확인한다. 이미 완전한 프로필을 바꾸는 기능은 제공하지 않는다.

반복 guard는 외부 효과나 권한 변경을 하지 않는다. 중간 검사 사이의 시계 역행을
잡기 위한 비공개 벽시계·단조 시계 표본 watermark만 기억할 수 있다. 이 표본은 원래
요청 시작 시각·30초 만료를 갱신하거나 신원·대상·세대·소진 상태를 바꾸지 않는다.
정상 시계 보정도 등록 도중 역행하면 보수적으로 거부할 수 있다.

## 관계의 출처와 소비 계약

완전한 관계는 다음 출처 조합만 허용한다.

| basis | policy | source | provider_observed_at |
|---|---|---|---|
| `INDEPENDENT_CONFIRMED` | `independent-v1` | `BYBIT_UI`, `BYBIT_SUPPORT`, `PRIOR_VERIFIED_RECORD` | null |
| `PROVIDER_OBSERVED_OPERATOR_ADOPTED` | `uid-anchored-adoption-v1` | `BYBIT_DEMO_QUERY_API` | 원래 관찰 시각 |

이번 저장 경계가 새로 생성하는 관계는 두 번째 조합뿐이며, 정확한 pending 프로필에 한 번만 게시할 수 있다. 이미 완전한 관계는 일치하는 revision으로도 덮어쓸 수 없다. UID 기준 제공자 관찰 채택은 최초 parent/master 주장을 독립적으로 확인했다는 의미가 아니며, 첫 응답 자체가 틀린 경우 이를 독립적으로 탐지하지 못한다.

파일에서 읽은 스냅샷은 불변이며 기본 표현에서 개인 필드를 숨긴다. 기대값 해석은 소비자가 지원하는 정책 집합을 명시해야 한다. 독립 확인 전용 소비자는 제공자 관찰 채택 정책을 거부해야 한다. `last_observation`은 관찰 기록일 뿐 기대 신원의 출처가 아니다. 로컬 설정·객체·직렬화 문자열 자체는 인증 바인딩이나 외부 암호학적 증명이 아니다. 동일 사용자에 의한 파일 반영·과거 버전 복원을 막는다는 주장을 하지 않는다.

내부 `_adopt_initial` 경계에는 고정한 `profile_id`, `expected_uid`, `expected_revision`과 검증된 관계·관찰 및 반복 가능한 무효과 guard를 전달한다. 저장소는 잠금 전, 잠금 획득 후, 임시 파일 fsync 후 게시 직전에 guard를 호출한다. 후속 등록 세션이 후보의 실제 소유권·세대·시간 제한과 한 번 사용 후 소진을 책임진다. 저장소는 후보 인증 권한을 만들지 않는다.

저장된 기대값은 현재 인증이나 실행 자격을 뜻하지 않는다. 명시적인 새 시작은 UID·parent UID·is_master 세 필드를 새 제공자 응답과 다시 비교해야 하며, 기존 DEMO·Linux/native·권한·실행 승인 조건은 계속 적용된다. LIVE 주문이나 추가 서버 지출을 허용하지 않는다.

## 저장 기대값을 이용한 명시적 Linux 시작

기존 워커의 `--run-monitor`는 두 방식 중 하나만 선택한다.

- 기존 `--uid`·`--parent-uid`·`--is-master` 세 값을 모두 지정하면 프로필을 전혀 읽지 않는다.
  이 경우 `--profile-store`는 위치 옵션이므로 무시한다. `--account-profile`과의 혼합은 거부한다.
- 세 기존 값을 모두 생략하면 저장된 선택 프로필을 사용한다. `--account-profile <UUID>`는
  선택된 canonical UUIDv4와 정확히 일치해야 하는 선택적 확인이다. `--profile-store`는 위치만
  선택하며 그 자체는 신원 override가 아니다. 부분 legacy 입력은 프로필로 보완하지 않는다.

```text
python -m multi_exchange_trader.execution.worker_cli --run-monitor --profile-store "<절대 파일 경로>" --account-profile "<선택된 UUID>" --symbol BTCUSDT --directory "<기존 장부 디렉터리>" --policy-db "<기존 정책 DB>"
```

저장 프로필은 키 해석보다 먼저 검사한다. 부재·pending·손상·알 수 없는 정책이면 기존
환경 신원으로 fallback하지 않는다. 지원 정책은 `independent-v1`과 명시적
`uid-anchored-adoption-v1`이다. 워커 시작과 모든 refresh가 같은 불변의 비공개 신원·출처
문맥을 유지하며, 파일·환경 변경은 실행 중인 계정을 바꾸지 않는다. 새 시작은 새 파일을
읽은 후 별도 세션에서 반드시 세 필드를 새로 인증한다. 이후 UID·부모·마스터 중 하나라도
달라지면 인증이 실패하고 이전 binding도 폐기되며 프로필은 수정하지 않는다.

인수 없는 `DRY_CHECK`, import, 기본 앱 생성은 프로필·vault I/O를 하지 않는다. 지원하지
않는 플랫폼과 필수 실행 인수 오류도 파일 접근 전에 차단한다. 기존 symbol·directory·
policy-db·lease·native·DEMO·권한 gate와 `--connect-monitoring`의 명시성은 그대로다.
SpotTrade 관찰을 보존하며 상품 의미와 E16/E02 해소를 이 신원 설정으로 대신하지 않는다.
