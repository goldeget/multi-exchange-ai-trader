# 최종 제품 진행 상태

기준일: 2026-11-03 KST. 이 문서는 프로그램 관리 자격 증명과 선택 DEMO 프로필의 부분 구현 기록이다.
63개 전체 작업이나 실제 자동매매 제품의 완료 판정이 아니다. 기존
[RECOVERY_PROGRESS.md](RECOVERY_PROGRESS.md)의 이력과 완료된 PR20~23의 범위·상태를
변경하거나 재완료로 계산하지 않는다.

## 기준과 현재 범위

외부 최종 감사의 `work-register.json`에 있는 canonical 작업 ID 63개와 alias를 기준으로
유지한다. 감사 원본은 수정하지 않았다. 초기 감사의 Bybit 적용 범위 수와 최신 사용자
선택 범위는 구분한다. 최신 **Bybit scope v1.1**은 SpotTrade를 유지하고 SPOT/LINEAR 선택을
포함하여 DEMO 60개·LIVE 61개·원래 전체 63개 적용 범위이며, 이는 완료 개수가 아니다.
E16이 다음 우선 범위다. 이 자격 증명 작업에서 상품 의미나 권한 요구를 완화하지 않는다.

설계와 구현 범위는
[managed-credentials-design.md](../superpowers/specs/2026-11-03-managed-credentials-design.md),
[managed-credentials.md](../superpowers/plans/2026-11-03-managed-credentials.md),
[CREDENTIALS_KO.md](../operations/CREDENTIALS_KO.md)에 따른다.

| 작업 | 이번에 구현한 부분 | 남은 경계 |
|---|---|---|
| A13 | 명시 최초 등록, OS별 비공개 저장, 계좌·AI 설정 재사용, 선택 DEMO 신원 저장 및 새 시작의 세 필드 인증 | 실제 상품·권한·운용 수용 기준은 별도이며 전체 완료 아님 |
| P06 | CLI 등록과 Linux 기존 배포 설정의 호환·분리된 비밀 관리 안내 | 실제 서버 설치·서비스·복구 수용은 별도이며 전체 완료 아님 |
| U05 | 기존 연결 상태 DTO에 등록됨·미검증을 유지하며 저장 설정을 반영 | 공개 키 입력·계정 프로필 등록 UI를 구현한 것이 아니며 전체 완료 아님 |
| E08 / E09 | 이번 자격 증명 슬라이스에서 기존 실행·네이티브·권한 gate 유지 | 기존 대기·진행 중 상태 유지; 실제 외부·운용 증거로만 후속 판정 |
| P01 | 기존 배포·복구 경계와 독립 자격 확인 유지 | 이번 슬라이스로 완료 전환하지 않음 |

Windows는 현재 사용자 DPAPI와 UI 금지 옵션을 사용하며 machine 범위·평문 fallback이 없다.
Linux는 현재 서비스 사용자 소유 `0700`/`0600` 접근 통제 평문이며 암호화로 표시하지 않는다.
등록·구성·상태 조회는 인증·생성·주문·실행 허용을 만들지 않는다. 저장소 손상 시 관련
연결은 차단하고 기존 환경 계정으로 바뀌지 않는다. API 공개 DTO와 실행 gate는 유지한다.

## 검증 상태와 외부 미해결 사항

부모 자격 증명 구현 `e27f3add3ba707c676dae3f044da025797894882`는 PR24 CI112의 7개
job 성공 후 main `067b540979afea9b7f7c6a9ce050095cdbdaed6c`에 병합되었다. main CI113도
실행 가능한 6개 job 성공과 예상된 dependency-review skip이 확인되었다. root 담당자는
검토된 코드로 실제 Bybit 쌍의 최초 비공개 등록(revision1)과 새 프로세스 재사용을 완료했다.
기존 환경 파일을 끈 새 읽기 클라이언트의 실제 DEMO metadata GET 성공·선택 UID 일치·
SpotTrade 보존을 확인했다. 실제 값이나 운영자 경로는 이 문서에 포함하지 않는다.

선택 DEMO 프로필 PR25는 독립 Task2·수정 검토·전체 브랜치 검토와 최종 수정의 검토를
완료했다. 검토 HEAD `d46553e886ee7dae8e31496afc36cc96fdb5974a`의 CI115는 7개 job이 모두 성공했다. Python은
3359 passed·56 skipped·79 warnings·28 subtests, Ruff 정상·mypy 126개 파일 정상이다.
frontend 256개, operations 244개, browser 102개가 통과했고 browser의 24개 skip은 의도된
viewport 범위다. native36·restart6·restored12가 skip 없이 실행된 XML 이름·개수와 증거 ZIP
digest를 root가 독립 대조했다. 이는 해당 회귀 검증의 완료이며 실제 서버 운용 완료를 뜻하지 않는다.

PR25는 main `5c07a69d237bf7c724b8d0ec7fd66d094468f71c`에 병합되었다. main CI116
(run `41545534597`)도 실행 가능한 6개 job 성공과 PR 전용 dependency-review의 예상 skip이
확인되었고, 디코딩한 로그에서 동일 native36·restart6·restored12 gate를 확인했다.
main 부모는 기존 `067b540979afea9b7f7c6a9ce050095cdbdaed6c`와
검토 HEAD `d46553e886ee7dae8e31496afc36cc96fdb5974a`다.
전체 tree `d65d2de33e14c8adcc90b01d622a4bd6bc163667`와 src tree
`e0001e8ddd23fa8b57a041bec7873ba850574020`이 검토 HEAD와 같다는 원격 readback도 완료했다.

그 후 root 담당자는 현재 진단 브랜치 작업본과 분리한 실제 main 동결본을 사용했다.
소스 120개 파일의 byte 일치와 6개 검증 모듈의 로드 출처를 확인한 뒤 기본 DEMO 프로필을
한 번 등록·관찰·채택하여 revision2 `COMPLETE`로 저장했다. 정책은
`uid-anchored-adoption-v1`, 기준은 사용자가 먼저 선택한 UID이며 관계 출처는
`PROVIDER_OBSERVED_OPERATOR_ADOPTED`다. 부모·마스터를 독립 확인한 것은 아니고
`independently_confirmed=false`를 유지한다. 실제 UID·키·운영자 경로는 기록하지 않는다.

별도 새 Python 프로세스에서 저장 프로필을 vault보다 먼저 해석하고 세 기대값을 모두
새로 인증했다. 원본 환경 파일·키 메모를 읽지 않고 저장 자격 증명을 재사용했으며
SpotTrade 보존과 `session_closed=true`를 확인했다. 해당 확인의
`monitoring_connected=false`, `trading_qualified=false`는 그대로다. 외부 감사 영수증은
`PROFILE_ENROLLMENT_RECEIPT.json`과 `PROFILE_FRESH_AUTH_RECEIPT.json`에 별도 보존했다.
프로그램은 신원·자격 증명을 기억하지만 명시적 새 시작과 refresh는 계속 세 필드를 새로
인증한다. 저장 관계를 독립 증거나 현재 binding으로 바꾸지 않는다.

현재 `linear_execution_permitted=false`이며, SpotTrade를 보존한 SPOT/LINEAR 상품 프로필
분리(E16)가 남아 있다. 이 속성만으로 Bybit 선물 권한 부재를 추론하거나 SpotTrade 제거를
요구하지 않는다. E16/E02, 실제 주문·보호·복구 운용, UI 초기화·선택 조합, 실제 서버 수용은
별도 작업이다. P01/P06/U05와 전체 63개 작업은 부분/기존 상태다. 이번 문서 담당자는
운영자 파일이나 실제 저장소를 열지 않았다.

별도로 진행한 부모 main 단계에서 root 담당자는 일반 OpenRouter 키를 같은 비공개 저장소
revision2에 현재 사용자 DPAPI로 한 번 암호화 등록했다. 두 Bybit 쌍을 보존했고, 원본 파일을 끈 새 프로세스의
저장 스냅샷 재사용도 확인했다. 실제 키 metadata는 `VERIFIED`, 비무료·비관리 키로
확인되었다. 실제 모델 목록에서 정확한 `qwen/qwen-2.5-7b-instruct` 항목과 출력 128토큰·
전체 문맥 기준 `0.00346752 USD` 견적을 확인했다. 키 metadata 확인과 모델 목록·견적은
해당 키의 모델 생성 접근 성공이나 실제 청구액을 입증하지 않는다.

검토된 부모 main `067b540979afea9b7f7c6a9ce050095cdbdaed6c` 프로그램으로 승인된 유료
generation dispatch를 한 번 수행했으나 `UNCERTAIN` / `HTTP_ERROR`를 반환했고 선택 결과는
없다. 제공자 보고 비용은 확보하지 못했다. `0.01 USD` 불확실 비용 예약은 새 프로세스에서도
보존되었고, 같은 attempt의 반복 활성화는 요청을 보내기 전에 차단되었다. 그 시도의 어댑터가
세부 HTTP 상태를 보존하지 않아 정확한 상태 코드와 원인은 아직 확인되지 않았다. 유료 재시도는
수행하지 않았다. 이 결과를 청구 0, 테스트 통과, 모델 생성 접근 확인 또는 유료 테스트
전체 완료로 해석하지 않는다. 완료된 프로필 검토·CI·main·실제 채택/새 인증과 이 유료 호출의
미해결 원인·비용은 구분한다. 프로필 확인 중 새 AI generation 요청은 0이며 기존 유료
시도는 이 한 번뿐이다. root가 보낸 실제 주문 0·새 서버 0이며 LIVE 주문은 계속 금지된다.

이후 사용자는 현재 OpenRouter 키가 하이쿠 5.5 전용이라고 명시하고 해당 모델의 최소
실호출을 요청했다. root는 기존 Qwen 선택이 요청 모델과 맞지 않았음을 확인하고, 같은
키와 두 Bybit 쌍을 보존한 채 저장 모델을 `anthropic/claude-haiku-5.5`로 수정하여 revision3을
다시 읽어 확인했다. 원본 메모·환경 파일은 읽지 않았다. 새 프로세스의 실제 키 인증·정확한
하이쿠 모델 목록과 유료 가격도 확인했으나 아직 하이쿠 생성 성공은 검증하지 않았다.
기존 Qwen 시도·미확정 비용을 하이쿠 결과로 바꾸거나 초기화하지 않는다. 이 키로 다른
모델·무료 모델·자동 대체를 사용하지 않는다. 짧은 하이쿠 시험을 위한 명시적 추론 설정과
정상 null refusal 응답 호환을 코드·독립 검토·전체 CI/main gate 안에서 추가한다.

최신 승인 범위에는 root 담당자의 실제 DEMO 및 최소 비용 유료 AI 확인이 포함된다.
그 실행과 영수증은 별도 외부 증거로 판정하며 이 코드·문서 변경으로 수행되거나 성공한
것으로 계산하지 않는다. LIVE 주문과 새 서버 비용 지출은 계속 금지된다.
