# AI 공급자 등록 설정

서버는 시작할 때 선택한 공급자의 등록 정보를 한 번 읽습니다. 설정을 바꾼 뒤에는 서버를 다시 시작해야 합니다. 앱 생성, 연결 상태 조회와 진단 화면은 공급자 인증이나 모델 생성을 요청하지 않습니다.

## 명시적인 OpenRouter 선택

운영자가 서버 프로세스에 다음 항목을 설정합니다. 실제 키는 저장소나 브라우저에 넣지 않습니다.

| 항목 | 의미 |
| --- | --- |
| `AI_PROVIDER=OPENROUTER` | OpenRouter를 명시적으로 선택 |
| `OPENROUTER_API_KEY` | 운영자가 별도로 보관하는 OpenRouter 키. `sk-or-v1-` 접두사와 제한된 문자 형식을 검사 |
| `OPENROUTER_MODEL=anthropic/claude-haiku-5.5` | OpenRouter의 `author/slug` 모델 식별자. 직접 Anthropic의 `claude-haiku-5-5`와 구별 |
| `AI_ENV_FILE` | 필요한 경우 명시적으로 지정하는 서버 전용 공유 설정 파일 경로 |

`AI_ENV_FILE` 파일 안에도 `AI_PROVIDER`, 두 공급자의 키·모델 항목을 둘 수 있습니다. 비어 있지 않은 프로세스 환경값이 파일값보다 우선합니다. 빈 환경값은 기존 파일값을 지우지 않습니다. 같은 인식 항목이 파일에 두 번 나오면 환경값으로 덮어쓰더라도 설정 오류입니다. 다른 공급자의 항목은 선택한 공급자의 자격 증명으로 사용하지 않습니다.

`AI_ENV_FILE`이 없으면 현재 작업 디렉터리의 `.env`를 찾지 않습니다. 명시적인 빈 문자열, `off`, `none`, `0`은 파일 읽기를 끕니다. 이 항목을 명시적으로 끄면 기존 `ANTHROPIC_ENV_FILE`도 읽지 않습니다. 파일은 UTF-8 또는 UTF-8 BOM, 최대 16 KiB와 256줄의 일반 파일이어야 하며, 심볼릭 링크·디렉터리 정션을 경유하지 않아야 합니다. 공유 파일의 다른 서비스 설정은 무시하며 환경에 내보내지 않습니다.

## 기존 Anthropic 설정

새 공급자 선택과 공유 파일 지정이 없으면 기본 공급자는 `ANTHROPIC`입니다. 기존 `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_ENV_FILE` 동작을 유지합니다. 기존 파일에서는 두 Anthropic 항목만 인식하므로 `AI_PROVIDER`와 OpenRouter 항목은 중복·따옴표 검사 대상에도 포함하지 않고 무시합니다. 모든 공급자 항목을 엄격하게 검사하려면 `AI_ENV_FILE`을 명시합니다. `AI_PROVIDER=ANTHROPIC`으로 명시할 수도 있습니다. OpenRouter를 선택한 경우 `ANTHROPIC_ENV_FILE`을 읽거나 Anthropic 키로 대체하지 않습니다. 인식하지 못하는 공급자 선택은 안전한 기본 표시 `ANTHROPIC`과 `INVALID_CONFIGURATION`을 반환하며 등록 성공으로 처리하지 않습니다.

OpenRouter 모델은 소문자 영문·숫자로 시작하는 공급자/모델 형식으로 제한합니다. 공급자 부분은 최대 64자, 모델 부분은 최대 128자입니다. URL, 쿼리, 추가 경로, `..`, 제어문자는 거부합니다. 인식한 파일 항목과 두 공급자의 환경값 중 어느 후보 키라도 공개 모델값에 들어 있으면 모델값을 공개하지 않습니다. 로더의 불변 반환 객체에는 공급자·등록 여부·고정 상태·검사된 모델만 남고 키·경로·원문 오류는 남지 않습니다.

## 연결 표시가 의미하는 것

`/api/v1/connections/status`의 `ai.provider`와 `ai.model_id`는 선택한 공급자를 설명합니다. 형식에 맞는 키가 있으면 `registered=true`, `status=REGISTERED_UNVERIFIED`이며 이는 설정 등록만 의미합니다. 키가 없으면 `NOT_CONFIGURED`, 잘못된 키·모델·파일이면 `INVALID_CONFIGURATION`입니다. 모델만 잘못된 경우 키의 존재는 유지할 수 있으나 오류 상태이고 모델값은 `null`입니다. 로컬 AI 저장소를 읽을 수 없으면 상태는 `STORAGE_UNAVAILABLE`입니다.

`verified_at=null`, `mode=OFFLINE_TEST`, `external_calls=false`를 유지합니다. 이 등록 정보는 인증 성공, 모델 접근 권한, 계정 잔액, 키 잔여 한도, 유료 생성 승인 또는 자동매매 준비 완료를 뜻하지 않습니다. 기존 예산·실행 경계도 유지합니다.
