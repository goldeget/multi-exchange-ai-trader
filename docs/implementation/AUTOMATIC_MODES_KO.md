# 자동 운용 모드의 후보 선택 경계

2026-11-02 사용자가 확정한 의미를 순수 함수로 구현한다.

| 모드 | 종목·전략을 선택하는 주체 | 선택 범위 |
| --- | --- | --- |
| `FULL_AUTO` | 사용자 | 사용자가 선택한 종목과 정확한 전략 리비전 |
| `ALL_IN_ONE` | AI의 후보 ID 응답 | 사용자가 허용한 종목·전략 리비전으로 서버가 미리 발급한 후보 목록 |

두 모드는 같은 `PolicyReference`의 계정·환경·리비전을 사용한다. 자본, 레버리지,
손실 한도 등의 실제 수치는 기존 위험 평가 계층이 보관하고 계산한다. 이 모듈은
그 수치를 복제하거나 AI에게서 받지 않는다. `Candidate`에는 변경 불가능한 서버
계획의 `plan_id`, 신호의 `signal_id`, 정확한 전략 리비전과 정책·공개 스냅샷 참조가 있다.

## 신뢰 경계와 실행 범위

`src/multi_exchange_trader/execution/modes.py`는 네트워크나 부작용이 없는 선택 계층이다.
모든 서버 레코드는 frozen dataclass이며 중첩 목록은 tuple이어야 한다. 호출자는 계정 소유권을
이미 확인한 신뢰 가능한 서버 저장소에서 정책·후보·요청을 가져와야 한다. 레코드 생성자나
ID 자체는 인증, 서명 또는 서버 발급의 증명이 아니다. AI가 보내온 레코드나 AI가 고른
계정 정보를 서버 레코드 생성자에 전달하면 안 된다.

선택 결과는 주문 허가가 아니다. 후속 경로에서 최신 위험 평가, 계정 소유권, 원자적 예약,
보호 주문과 네이티브 기능 검증을 모두 다시 통과해야 한다. `LIVE` 문자열은 참조 범위의
일치 여부를 검사하기 위해 존재하며 실제 LIVE 활성화를 뜻하지 않는다. 기존 보호·청산
기능과 코디네이터는 수정하지 않았다. 선택 실패 결과에는 주문·보호·취소 명령이 없다.

## 함수 계약

모든 생략 불가능한 인자는 명시적으로 제공한다. `now`, 발급 시각과 만료 시각은 UTC이며,
나이 한도는 포함 경계이고 만료 시각은 제외 경계이다. 미래 자료와 과도한 전체 TTL을 거부한다.
서버는 후보 목록을 일부 잘못된 자료와 섞어서 전달하지 않아야 한다. 전체 목록에서 비활성,
만료, 허용 범위 밖 후보나 중복 ID가 하나라도 발견되면 전체 요청을 거부한다.

```python
select_full_auto(
    *, scope: SelectionScope, policy: PolicyReference, snapshot: PublicSnapshot,
    candidates: tuple[Candidate, ...], priority_candidate_ids: tuple[str, ...] | None,
    limits: SelectionLimits, now: datetime,
) -> SelectionResult

build_all_in_one_request(
    *, request_id: str, scope: SelectionScope, policy: PolicyReference,
    snapshot: PublicSnapshot, candidates: tuple[Candidate, ...], limits: SelectionLimits,
    now: datetime, expires_at: datetime,
) -> SelectionRequest

public_candidate_request(request: SelectionRequest, *, now: datetime) -> str
parse_ai_selection(payload: str, *, request: SelectionRequest, now: datetime) -> AISelection
select_all_in_one(
    *, request: SelectionRequest, response: str | None, now: datetime,
) -> SelectionResult
```

`SelectionScope`는 `account_id`, `environment`, `allowed_symbols`, `allowed_strategies`,
`selected_symbols`, `selected_strategies`를 분리한다. 전략은 이름뿐 아니라
`StrategyReference(strategy_id, revision)` 전체가 일치해야 한다. 사용자 선택은 허용 목록의
부분집합이어야 한다. 종목 목록과 전략 목록은 각 모드에서 가능한 조합의 범위를 뜻한다.
`FULL_AUTO`는 사용자 선택에 속하는 후보만 고려한다. 일치 후보가 없으면 `NO_CANDIDATE`,
하나이면 `SELECTED`이다. 여러 개이면 서버가 명시한 전체 적격 후보 ID의 중복 없는 순열
`priority_candidate_ids`의 첫 후보를 선택한다. 순열이 없으면 `AMBIGUOUS`로 거부한다.
입력 순서, 이름 정렬 또는 숨은 점수로 선호도를 정하지 않는다.

`SelectionLimits` 필드 순서는 `max_candidates`, `max_response_bytes`, `max_reason_chars`,
`max_identifier_chars`, `max_snapshot_age_seconds`, `max_candidate_age_seconds`,
`max_request_age_seconds`, `max_ttl_seconds`이다. 양의 정수만 허용하며 bool·float를 거부한다.
호출자가 각각 값을 지정해야 한다. 운영상 절대 상한은 후보 1,024개, JSON 65,536 UTF-8
바이트, 설명 4,096자, ID 256자, 각 시간 31,536,000초이다. 모든 tuple도 1,024개로 제한한다.
이들은 계산 자원의 상한이며 금융 정책 기본값이 아니다. 공개 요청 JSON에도 같은 바이트
한도를 적용한다. 전체 UTF-8 바이트와 설명 문자 수를 모두 검사한다.

## AI 공개 요청과 엄격한 응답

`SelectionRequest`는 계정 등 비공개 결합 정보를 포함하므로 공급자에 직접 직렬화하면 안 된다.
`public_candidate_request`만 공개 입력을 만든다. 공개 필드는 아래와 같다.

```json
{
  "schema_version": 1,
  "request_id": "opaque-request-id",
  "snapshot_id": "opaque-public-snapshot-id",
  "policy_revision": "opaque-policy-revision",
  "candidates": [
    {
      "candidate_id": "opaque-candidate-id",
      "symbol": "BTCUSDT",
      "strategy_id": "trend",
      "strategy_revision": "v1"
    }
  ]
}
```

계정 ID, 환경, 계정 잔액, 신호 ID와 계획 ID는 공개 요청 필드에 없다. 서버는 공개 ID·리비전과
전략 이름에 계정 정보나 비밀을 삽입하지 않은 공급자 공개용 참조를 발급해야 한다. 이 순수
계층이 임의 문자열의 비밀 포함 여부나 서버 발급 이력을 증명할 수는 없다.

AI 응답은 다음 일곱 필드를 정확히 갖는 JSON 객체여야 한다.

```json
{
  "schema_version": 1,
  "request_id": "opaque-request-id",
  "snapshot_id": "opaque-public-snapshot-id",
  "policy_revision": "opaque-policy-revision",
  "decision": "SELECT",
  "candidate_id": "opaque-candidate-id",
  "reason": "허용 후보를 선택한 설명"
}
```

`SELECT`는 서버 요청에 들어 있는 정확한 후보 ID만 허용한다. `ABSTAIN`이면 `candidate_id`는
반드시 JSON `null`이다. 나머지 결합 값은 요청과 정확히 일치해야 한다. boolean 또는 소수
`schema_version`, 중복 키, 추가 필드, 앞뒤 지시문·코드 블록, 비유한 JSON 수치, 제어 문자와
잘못된 Unicode를 거부한다. 가격·수량·레버리지·도구·코드 필드는 무시하지 않고 거부한다.
`reason`은 길이를 제한한 비신뢰 데이터다. 설명 안에 지시문처럼 보이는 문장이 있어도 실행,
평가 또는 위험 설정에 사용하지 않으며 서버 후보를 변경할 수 없다.

선택 시 요청뿐 아니라 원본 스냅샷·후보의 최신성, 만료, 허용 목록, 계정·환경과 정책 리비전을
다시 확인한다. 응답이 늦게 도착하면 유효했던 요청도 거부될 수 있다. 정상 `SELECTED` 결과는
원본 후보와 정책 객체를 그대로 보존한다. 빈 후보 목록으로 AI 요청을 만들 수 없다.

`response=None`은 공급자 부재·실패를 외부 호출자가 명시한 경우이며 `UNAVAILABLE`로
후보 없이 반환한다. 정상 기권은 `ABSTAINED`이다. 잘못된 응답·오래된 증거는
`SelectionRejected`로 거부된다. 어느 경우에도 `FULL_AUTO`나 다른 전략으로 자동 전환하지 않는다.

## 확인과 아직 없는 기능

집중 테스트는 두 모드의 선택 차이, 동일 전략명의 서로 다른 리비전, 원본 참조 보존, 허용 목록과
계정 범위, 엄격한 JSON, 자원·시간 경계, 결정적 우선순위와 기권·부재의 무대체 동작을 검증한다.
구현 파일과 테스트 파일에 Ruff 및 strict mypy를 적용한다.

AI 공급자 호출·전송 계층, 공급자 가용성 판정, API·UI 연결, 인증 정보 접근, 주문 실행과 서버
기동은 구현하지 않았다. 실제 네이티브 검증과 LIVE 활성화는 별도 작업으로 남아 있다.
