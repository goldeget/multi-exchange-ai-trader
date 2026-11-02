# OpenRouter 명시적 읽기 전용 확인

`multi_exchange_trader.ai.openrouter_metadata.OpenRouterMetadataClient`는 호출자가 직접
전달한 키와 모델만 사용한다. 환경 변수나 파일을 읽지 않고 생성 시 통신하지 않는다.
앱 시작, 상태 조회, 거래 실행과 연결되어 있지 않다.

```python
client = OpenRouterMetadataClient(api_key, "anthropic/claude-haiku-5.5")
key_result = client.verify_key()
model_result = client.retrieve_model()
```

실제 비밀키나 결과 원문을 로그로 남기지 않는다. 이 예시의 실행은 운영자의 명시적
조회 동작에만 해당하며 시작 시 자동 인증 절차가 아니다.

## 계약

생성자 키워드 인자는 `transport: httpx.BaseTransport | None = None`,
`timeout_seconds: float = 5.0`, `max_body_bytes: int = 65536`이다.
주입한 transport는 단위 테스트용이며 API 주소를 바꾸는 설정은 없다.

- `verify_key()` → 동결된 `OpenRouterKeyResult`.
  `status=VERIFIED`는 현재 키 조회의 인증 성공이다. `verified_at`은 UTC 시각이다.
  `metadata`는 `usage: Decimal`, `limit/limit_remaining: Decimal | None`,
  `is_free_tier: bool`, `is_management_key: bool | None`,
  `expires_at: datetime | None`만 보유한다. 관리 키 여부와 만료 시각은 공급자 정보이며
  실행 허용 여부를 계산하지 않는다. 누락된 선택 정보는 `None`으로 유지한다.
- `retrieve_model()` → 동결된 `OpenRouterModelResult`.
  `requested_model_id`, `retrieved_at`과 정확히 일치한 모델의
  `resolved_model_id/display_name/context_length/pricing`을 반환한다.
  `pricing`은 문서의 prompt/completion과 존재하는 request/image 문자열을 담은 불변
  튜플이다. 금액을 부동소수점으로 바꾸지 않는다. 성공 상태는 `CATALOGUE_FOUND`이고
  `model_access_verified=False`다. 별칭이 다른 ID로 해석되면 실패한다.
- 두 결과 모두 `generation_enabled=False`, `provider_balance=None`이다.
  실패 시 `status=FAILED`, 고정된 `failure` 코드와 빈 메타데이터를 반환한다.
  공급자 오류 본문, 헤더, 레이블, 사용자/조직/작업공간 ID는 결과에 복사하지 않는다.

`limit_remaining`은 키의 남은 한도다. 계정 잔액이나 내부 예산, 모델 사용 권한,
결제 승인 또는 유료 요청 허가를 의미하지 않는다. 카탈로그 조회도 같은 권한을 증명하지
않는다. 내부 예산 및 주문 안전장치는 독립적으로 유지된다.

## 통신과 응답 제한

메서드마다 고정된 HTTPS 주소로 GET을 최대 한 번 수행한다. Bearer 인증과 JSON 수락,
identity 인코딩만 요청한다. 프록시 환경 상속, 리디렉션, 재시도와 대체 공급자가 없다.
생성·주문·계정 변경 또는 credits 요청 경로는 없다.

본문은 기본 64 KiB로 제한된다. 타임아웃은 기본 5초이며 스트리밍 중과 파싱 직후에도
경과 시간을 검사한다. 동기 전송의 기본 연결/읽기 제한도 적용한다. 실행 중인 임의의
주입 transport를 강제로 중단하는 별도 스레드는 만들지 않는다.

JSON의 중복 키, 비유한 수, 잘못된 필드 타입, 모델 불일치, 깊이 12 초과,
컨테이너 원소 128 초과, 전체 노드 4096 초과, 문자열 4096자 초과, 제어문자와 비밀키
반사를 거부한다. 알 수 없는 필드도 같은 검사를 통과해야 하며 결과에서는 버린다.
문서에 있는 `data.label`의 생략 부호가 포함된 짧은 키 레이블만 허용하고 반환하지 않는다.
숫자형 금액은 원문에서 바로 `Decimal`로 읽어 정밀도와 `null`을 보존한다.

## 공식 근거와 검증 범위

2026-11-02 확인한 공식 문서:

- [현재 키 조회](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key):
  `GET https://openrouter.ai/api/v1/key`, Bearer 인증, `data` 객체.
- [단일 모델 조회](https://openrouter.ai/docs/api/api-reference/models/get-a-model-by-its-slug):
  `GET https://openrouter.ai/api/v1/model/{author}/{slug}`, Bearer 인증, `data` 객체.
  여기의 `model`은 단수다. 지정 모델 경로는
  `/api/v1/model/anthropic/claude-haiku-5.5`다.
- [크레딧 조회](https://openrouter.ai/docs/api/api-reference/credits/get-remaining-credits):
  관리 키가 필요한 별도 기능이다. 이 클라이언트에서는 호출하지 않는다.

단위 테스트는 합성 키와 `httpx.MockTransport`만 사용한다. 실제 인증 여부는 이 테스트의
주장이 아니며 별도 명시적 검증이 필요하다. 유료 생성 및 실제 거래는 수행하지 않는다.
