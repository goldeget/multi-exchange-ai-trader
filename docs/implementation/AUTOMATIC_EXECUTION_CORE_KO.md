# 자동 실행 코어: 오프라인 통합 계약

이 변경은 실제 자동매매 생명주기의 기반 모듈과 **오프라인 계약 검증용 코디네이터**를 구현한다. 실제 거래소 어댑터, 실제 DEMO 인증, LIVE 활성화 기능은 없다. 기존 PAPER 엔진, 공개 API 및 LIVE 미지원 판정은 그대로다. 코디네이터 상태는 `TEST_ACTIVE` 또는 `TEST_PAUSED`이며 실제 거래가 실행 중이라는 뜻이 아니다.

## 구성과 사용 계약

| 모듈 | 역할 |
| --- | --- |
| `execution/journal.py` | SQLite WAL/FULL 명령·예약·감사 기록, 원자적 claim, 이벤트 inbox와 projection CAS |
| `execution/ownership.py` | 계정·DEMO/LIVE별 실제 운영체제 프로세스 잠금, 파일 동일성·획득 세대 확인 |
| `execution/protection.py` | 소유 주문 매핑, 부분·중복·늦은 체결, 충돌 보존, 보호·평탄 상태의 순수 평가 |
| `execution/risk.py` | 명시적 사용자 정책과 현재 증거를 이용한 Decimal 진입 위험 계산 |
| `execution/codec.py` | 허용된 타입만 복원하는 JSON 스키마. Decimal 문자열, UTC 시각, tuple, 충돌 원본 보존 |
| `execution/coordinator.py` | 주입된 가짜 게이트웨이만 연결하는 `OfflineExecutionCoordinator` |

생성자는 `journal`, `worker_lock`, `lock_directory`, `gateway`, `entry_risk`, `clock`, `max_evidence_age`를 모두 명시적으로 받는다. 잠금은 호출자가 먼저 획득해야 한다. 생성자는 잠금의 실제 클래스, 살아 있는 파일 기술자, 계정·환경, 설정 디렉터리의 파일 경로, 획득 세대를 확인한다. 잠금이 유효한 동안에만 기존 `SENDING`을 `UNKNOWN`으로 복구한다. 이미 해제 후 재획득한 잠금으로 기존 코디네이터를 계속 사용할 수 없다. 새 코디네이터로 대조·활성화를 다시 해야 한다.

모든 워커는 같은 전용 로컬 디렉터리와 같은 계정 journal을 사용해야 한다. 다른 컴퓨터의 동일 계정 사용을 차단하는 분산 잠금은 아니다. 단일 프로세스 안에서도 기존 워커를 종료한 뒤 교체해야 한다. 메서드는 동기식이며 내부 잠금으로 직렬화한다. 종료 때 `shutdown()`을 호출하고, 반환 후 호출자가 journal을 닫고 운영체제 잠금을 해제한다. 이 함수는 포지션 청산이나 실제 거래소 정지 확인을 의미하지 않는다.

`OfflineGateway`는 다음 세 함수와 `capabilities`만 요구한다.

- `submit(client_order_id, payload) -> OfflineOrderReport`
- `query(client_order_id) -> OfflineOrderReport`
- `cancel(client_order_id) -> OfflineOrderReport`

광역 취소·일괄 청산 함수는 없다. `OfflineCapabilities(account_id, environment, attached_protection, kind)`는 환경·계정이 같고 `attached_protection=True`, `kind="OFFLINE_FAKE"`여야 한다. 이 필드는 **모의 계약 선언**이다. 실제 운영 자격의 증거로 사용하지 않는다. `kind="PRODUCTION"`은 거부된다. 실제 운영 게이트웨이를 생성하거나 활성화하는 팩토리는 구현하지 않았다.

## 진입과 모호한 전송

1. 부팅 시 진입을 막는다. `reconcile(SimulatedReconciliation(...))`로 최신·완전·동일 범위의 모의 계정 상태와 소유 주문 ID 조회를 대조한다. 그 다음에만 명시적인 `activate()`를 허용한다. 대조 증거가 만료되면 활성화·진입을 거부한다.
2. `queue_entry(command_key, position_id, plan, protection_policy)`는 명령과 위험·명목금액·증거금 예약을 원자적으로 기록하고, 불변 소유 주문 매핑을 별도 이벤트 CAS로 저장한다. 두 커밋 사이에 중단되면 매핑이 확인되지 않은 명령은 전송할 수 없다. 같은 불변 입력을 재등록하여 매핑을 복원하는 경로가 필요하다.
3. `dispatch(command_key)`는 전송 직전에 `EntryRiskCheck(intent, now, journal_reservations)`를 호출한다. 반환된 `EntryDecision`은 현재 시각의 `SUBMIT`, `submission_ready=True`, 빈 차단 사유여야 한다. 순수 위험 함수를 다시 실행하여 결과를 검증한다. `PLAN`은 전송 허가가 아니다.
4. 정책 전체, 수량, 진입·손절·익절 계획, 레버리지, 위험·비용·증거금, 상품 제한, 시세의 실행 관련 값과 위험 구간이 큐에 저장한 계획과 같아야 한다. 최신 관측 시각·시세 ID 갱신 자체는 허용하지만 계산 계획이 달라지면 기존 명령을 그대로 보류한다.
5. journal의 `claim()` 커밋이 성공한 뒤에만 가짜 게이트웨이를 호출한다. 호출 중 SQLite 트랜잭션을 유지하지 않는다. ACK는 체결이 아니며 예약을 해제하지 않는다.
6. 전송의 일반 예외·잘못된 응답은 고정 사유 `AMBIGUOUS_OFFLINE_EFFECT`로 `UNKNOWN`을 기록한다. 예외 원문을 감사 기록에 넣지 않는다. 새 ID 생성·자동 재전송·자동 예약 해제는 없다. 프로세스 중단으로 남은 `SENDING`도 다음 소유 워커에서 `UNKNOWN`으로 복구한다.
7. `UNKNOWN`은 같은 ID로만 조회한다. 한 번의 `ABSENT` 보고는 재전송 허가가 아니다. 이전 ACK가 있었어도 현재 조회가 부재·불명확하면 완전 대조로 인정하지 않는다.

콜백과 모의 스냅샷 공급자는 **신뢰되는 테스트 경계**다. 타입·시간·정책 일치 검사는 실제 증거의 출처를 인증하지 못한다. 실제 계정 원장, 청산 계산기, 적용 레버리지 readback, 시세·상품·위험 구간 공급자를 이 콜백으로 자격 검증했다고 주장해서는 안 된다. 로그·저장 입력에 비밀을 넣지 않는 것도 공급자의 책임이다.

예약 스냅샷에는 필수 `snapshot_id`를 넣고 청산 증거의 `reservation_snapshot_id`와 결합한다. 전송할 자기 명령의 예약은 다시 제안 손실에 더하지 않으며 다른 미해결 journal 예약은 포함한다. 계정 노출·손실·증거금 집계는 그 예약과 겹치면 안 된다. 계정 집계의 비중복 정의와 journal 밖의 모든 노출에 대한 완전성은 신뢰되는 공급자의 책임이다. 이 하네스는 외부 원장 공급자를 구현하지 않는다.

위험 계산은 나눗셈 결과를 긴 Decimal로 보존할 수 있다. journal 예약은 256 유효 숫자 저장 한계 때문에 증거금만 그 한계로 **올림**한다. 실행 수량·가격·레버리지를 바꾸지 않고, 위험 계산보다 적은 증거금을 예약하지 않는다. 예약 합계는 현재 Decimal 컨텍스트에 의한 절사를 피한다. 과도한 크기·기록 한계는 실패로 처리한다.

## 소유 체결과 보호 관리

`record_fill(FillEvent)`은 순수 reducer 결과와 원본 이벤트를 같은 journal CAS 트랜잭션에 저장한다. 정확한 중복 이벤트의 재처리는 최신 projection을 반환하므로 나중 체결을 되돌리지 않는다. 같은 execution ID의 상충 보고는 별도 불변 inbox 증거로 저장하여 충돌을 보존한다. 모르는 주문의 체결도 버리지 않고 충돌 원본을 저장하며 진입을 막는다. 취소 뒤의 늦은 체결도 실제 소유 수량에 반영한다.

`projection(position_id)`는 매번 영속 기록을 읽는다. JSON의 타입·범위·불변 매핑·체결 이력 검증이 실패하면 복원을 거부한다. 소유권은 journal에 저장된 정확한 매핑으로만 판단한다. ID 접두사나 외부 순포지션으로 소유권을 추정하지 않는다. 모든 포지션 projection은 현재 하나의 64 KiB 제한 레코드에 저장된다. 기록이 커지면 실패하므로 실제 운영 전 저장 구조·보관 정책의 별도 설계가 필요하다.

`pause_entries()`는 새 진입만 막는다. 다음 기능은 잠금을 유지하는 동안 계속 사용할 수 있다.

- 체결·충돌 기록 및 `assess_protection(position_id, policy, evidence, position)` 순수 평가
- `queue_exit(command_key, position_id, quantity, policy_revision)`와 `dispatch()`를 통한 소유 수량 이하의 reduce-only 모의 종료
- `cancel_owned(command_key, client_order_id)`를 통한 정확한 소유 ID의 모의 취소

종료 주문은 큐 등록 때와 전송 직전에 소유 수량·방향·reduce-only를 확인한다. 취소 ACK만으로 무체결을 추정하지 않는다. `CANCELLED` 확인을 받은 경우에만 취소 증거를 저장하고, 이후 체결도 계속 허용한다.

보호 평가에는 현재 계정·환경·포지션·방향·수량, 현재 정책·세대, trigger 값·출처, reduce-only, ACTIVE 상태, 동일 cycle과 신선한 관측 증거가 필요하다. 확인되지 않은 보호를 인메모리 플래그로 승인하지 않는다. 이 좁은 하네스는 보호 평가가 성공해도 기존 열린 노출이 있으면 추가 진입을 허용하지 않는다. 미해결 ACK 진입 역시 추가 진입을 막는다.

**자동 SL/TP 신규 주문 생성·교체 전송·연속 감독·예약 종료 판정은 구현하지 않았다.** 모의 게이트웨이에 진입 보호 정책을 전달하는 것과 실제 거래소의 attached 보호 주문 확인은 별개다. 기존 보호 세대의 취소 후보 계산은 순수 모듈의 결과이며 자동 실행하지 않는다. `STOPPED_FLAT`의 실제 운영 판정이나 자동 예약 해제 경로도 코디네이터에 없다. 새 워커의 완전 모의 대조는 운영체제·DB·가짜 보고 계약 검사이며 실제 거래소의 전체 주문·조건부 주문·체결 이력 대조 자격 검증을 대신하지 않는다.

## 검증 범위와 후속 작업

통합 테스트는 수락 뒤 타임아웃, 새 DB 연결 재시작과 SENDING 복구, 같은 ID 조회·재전송 방지, 실제 SQLite claim 트랜잭션 실패, 게이트웨이 호출 시 커밋·쓰기 잠금 해제, 잠금 해제 후 재획득, 잘못된 환경·모의 capability, 오래되거나 바뀐 위험·정책·상품 계획, 외부 충돌, 중복·상충 체결 보존, 보호 평가의 pause 중 유지, 소유 종료·취소 경계를 확인한다. codec 테스트는 Decimal/UTC/취소/충돌 전체의 왕복 보존과 잘못된 복원 거부를 확인한다. 이 결과는 네이티브 어댑터 인증 결과가 아니다.

실제 실행 전에는 NautilusTrader 1.231.0의 단일 주문 경로에 대해 다음을 별도 구현·검증해야 한다.

- 현재 거래소 계약과 인증된 DEMO 증거를 통한 어댑터·출처 자격 검증
- 실제 사용자 위험 설정, 수수료·펀딩·슬리피지와 UTC 원장의 완전성
- 레버리지 적용과 독립 readback, 정확한 제안·계정·예약 스냅샷에 결합된 청산 증거
- native attached SL/TP 확인, 부분 체결·보호 실패·교체·취소 경합·재접속 감독
- 전체 주문·조건부 주문·체결·포지션의 재대조와 확인된 예약 종료
- 실행 journal을 포함한 백업·복구·스키마 마이그레이션 자격 검증

현재의 여섯 원장 백업에는 새 execution journal이 포함되지 않는다. 현재 실제 운영 쓰기는 연결하지 않았으며, 실행 DB까지 포함한 백업·복구 변경을 검증하기 전에는 실제 활성화할 수 없다. 자격 증명 전송, 인증 요청, 실제 주문, 유료 AI 호출, 서버 설치·추가 지출은 이 변경의 작업 범위에 없다.
