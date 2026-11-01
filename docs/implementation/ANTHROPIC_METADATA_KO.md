# Anthropic 모델 메타데이터 조회 계약

확인 기준일: 2026-11-02. 이 구현은 모델 정보 조회만 제공한다. 실제 계정 인증과 모델 접근 권한은 이 작업에서 확인하지 않았다. 자동 분석, 유료 생성, 거래 실행 또는 공개 API/UI에 연결하지 않았다.

## 공식 근거와 고정 요청

- [모델 단건 조회](https://platform.claude.com/docs/en/api/models/retrieve): 단건 GET과 별칭 해석, 현재 인증 예제 및 응답 필드의 근거.
- [API 버전](https://platform.claude.com/docs/en/api/versioning): 요청 버전 헤더의 근거.
- [모델 목록](https://platform.claude.com/docs/en/models/overview): 현재 Haiku 5.5 식별자 확인의 근거. 목록에 있다는 사실이 이 계정의 접근 가능성을 증명하지 않는다.

명시적인 `retrieve()` 호출마다 `https://api.anthropic.com/v1/models/{model_id}`에 GET을 최대 한 번 전송한다. `Authorization: Bearer <명시적으로 전달된 키>`, `anthropic-version: 2026-10-21`, JSON 수락과 `Accept-Encoding: identity`를 설정한다. 예전 API 버전이나 다른 인증 헤더로 자동 전환하지 않는다. 워크스페이스 선택 헤더는 이 최소 구현에서 지원하지 않는다.

`AnthropicMetadataClient(api_key: str, model_id: str, *, transport: httpx.BaseTransport | None = None, timeout_seconds: float = 5.0, max_body_bytes: int = 65_536)` 생성자는 네트워크를 호출하지 않는다. `retrieve() -> MetadataResult`는 동기식이며, 호출자가 주입한 HTTPX 전송 객체로 모의 검증할 수 있다. 실제 기본 전송에는 자동 재시도가 없으며 리디렉션과 환경 기반 HTTPX 설정도 비활성화한다. 환경 변수나 파일에서 키를 읽는 로더는 이 모듈에 없다.

POST, Messages, 모델 목록 순회, 다른 모델이나 공급자 대체, 상태 페이지 조회 또는 시작 시 자동 확인 경로는 없다. 별칭의 반환 ID는 다시 문법 검증하고 원래 요청 ID와 따로 보관하며, 이를 확인하기 위한 두 번째 요청은 하지 않는다. `claude-haiku-5-5`를 포함해 모델 ID는 제한된 `claude-...` 문법만 허용한다.

## 결과와 검증 경계

`MetadataResult`와 `ModelMetadata`는 동결된 슬롯 데이터 클래스다. 결과에는 요청 ID, `VERIFIED` 또는 `FAILED` 상태, 고정 `MetadataFailure` 코드, 성공 시 UTC `verified_at`, 검증된 모델 정보만 담긴다. 모든 결과의 `generation_enabled`는 생성자에서 바꿀 수 없는 `False`, `provider_balance`는 `None`이다. 조회 성공은 모델 카탈로그 응답 검증을 뜻하며 생성 권한, 요금, 잔액, 예산, 거래 허가를 뜻하지 않는다. `deprecated` 또는 `retired` 모델의 카탈로그 응답도 검증될 수 있다.

현재 공식 `ModelInfo`의 필드를 모두 요구한다: `type`, `id`, `capabilities`, `created_at`, `deprecated_at`, `display_name`, `lifecycle`, `line`, `max_input_tokens`, `max_tokens`, `retires_at`. 필드 생략과 명시적 `null`을 구분한다. 타입은 `model`만, lifecycle은 문서의 세 값만 받는다. 토큰 한도는 `null` 또는 0~2,147,483,647 범위의 정확한 정수만 허용하며 bool/소수/문자열을 정수로 변환하지 않는다. line은 `null` 또는 제한된 소문자 식별자이며 새 모델 계열을 허용한다.

시간은 시간대가 있는 RFC 3339 형태(대문자 T/Z, 소수 초 최대 6자리)만 허용하고 UTC datetime으로 변환한다. 숫자 시간대는 변환 전에 시 00~23, 분 00~59의 ASCII 문법으로 제한한다. 따라서 `+00:60` 또는 `+01:99`를 날짜 라이브러리가 다른 유효 시간대로 정규화하기 전에 거부한다. 이 검사는 created_at/deprecated_at/retires_at 모두에 적용하며 양·음수의 유효한 최대 오프셋은 유지한다. 표시 이름은 비어 있지 않은 최대 128자 문자열이다. capabilities는 문서의 현재 중첩 필드를 모두 확인하며 `supported`에는 정확한 bool만 받는다. 문서가 nullable로 정한 항목은 `null`을 허용한다. 반환값에는 검증한 고정 capability 경로와 bool/null의 불변 튜플만 남기며 임의 객체를 노출하지 않는다.

본문은 최대 64KiB이며 호출자가 1~64KiB로 낮출 수 있다. Content-Length가 큰 경우 본문을 읽기 전에 중단하고, 헤더가 없거나 잘못 작게 선언되어도 실제 읽은 바이트를 제한한다. 압축 응답은 해제 전에 거부한다. 기본 HTTPX 연결/읽기/쓰기/풀 시간 제한은 각 5초이며 0.1~30초로 제한한다. 스트림 청크 사이에서도 전체 경과 시간을 확인한다. 이는 프로세스를 강제로 중단하는 별도 타이머가 아니며 진행 중인 한 번의 읽기는 HTTPX 시간 제한을 따른다.

JSON 중복 키, 잘못된 UTF-8/구문, 비유한 수, 과도한 숫자 범위를 거부한다. 알려지지 않은 향후 필드도 깊이 12, 객체/배열 원소 128, 문자열 4,096자 제한과 비밀값 검사를 통과해야 하며 결과에는 복사하지 않는다. 응답의 모든 문자열과 키에서 제공된 인증 키의 반사, Anthropic 키 형태, Bearer 표현, 제어 문자를 검사한다. JSON 이스케이프로 표현된 반사도 디코딩 후 검사한다. 이 검사는 임의의 다른 비밀값을 완전히 식별한다는 보장이 아니므로 원문과 미선택 필드를 노출하지 않는 경계도 함께 유지한다.

키는 전송 객체의 비공개 슬롯에만 보관한다. 클라이언트 repr은 고정 문자열이며 공개 설정/결과 객체에 키를 넣지 않는다. 응답 본문, 헤더, 예외 문자열을 로깅하거나 결과로 반환하지 않는다. 401/403/404/429/5xx/리디렉션과 시간 초과/TLS/네트워크/기타 전송 오류는 각각 고정 코드로만 반환한다. 알 수 없는 응답이나 과대 응답은 성공으로 간주하지 않는다.

## 검증 결과와 남은 확인

합성 키와 `httpx.MockTransport`만 사용하는 집중 테스트 144개를 통과했다. 요청 방식·현재 헤더·별칭·필수 중첩 필드·bool/정수 구분·중복 JSON·비유한 수·비밀 반사·스트림 크기/시간 제한·재시도 및 리디렉션 부재·고정 오류를 검증했다. 직접/명시적 원인/암묵적 문맥에 있는 TLS 오류를 구분하는 회귀 테스트에서 실패를 확인한 뒤 수정했다. 독립 검토에서 발견한 잘못된 시간대 정규화도 세 날짜 필드의 양·음수 잘못된 오프셋을 먼저 실패로 재현한 뒤 수정하고, 유효한 경계 오프셋의 UTC 변환을 함께 검증했다. 해당 두 Python 파일은 Ruff와 strict mypy 검사를 통과했다.

실제 키, 인증된 요청, 유료 생성 호출을 사용하지 않았다. 실제 계정의 모델 접근 가능성은 별도의 명시적 인증 확인 작업으로 남아 있다. 기존 OFFLINE_TEST/생성 비활성 설정과 예산 상한은 이 모듈로 변경되지 않는다.
