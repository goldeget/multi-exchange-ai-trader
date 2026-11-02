# 영속 이력 보존 명세

## 가장 작은 독립 NEXT: 영속 이력 수집과 coverage 저장

저장 배치 수정이 통합된 뒤의 NEXT는 **현재 읽을 수 있는 이력을 재시작 뒤에도 정확히 보존하고, 실제로 조회한 구간을 식별하는 것**이다. 전체 계좌 자격 composer, native cache 복원, 자동매매 scheduler까지 한 번에 구현하지 않는다. 주문 상태/flat/ledger 자격을 승격하지 않으면서 다음 계층이 소비할 대조 자료를 만든다.

### 파일과 제안 인터페이스

1. 새 `src/multi_exchange_trader/execution/history_store.py`: 계좌·환경에 묶인 `HistoryStore(path, scope)`와 `ingest(snapshot: HistorySnapshot) -> IngestionReceipt`, `coverage(symbol, start_ms, end_ms) -> HistoryCoverage`, `load_window(symbol, start_ms, end_ms)` 계약. 검증된 typed field만 저장하고 provider 원문/credential은 저장하지 않는다. `history.sqlite3`와 명시적 schema version을 사용하며 실제 파일명은 공통 백업 계약과 동시에 확정한다.
2. `HistorySnapshot`의 관측 UUID와 수집 시각, server 시각, query 구간, symbol, injected transport 표식을 저장한다. execution은 scope+execution ID, transaction은 scope+transaction ID로 중복을 대조한다. account-wide transaction은 symbol별 조회가 반복돼도 이중 합산하지 않는다. 같은 event ID에 바뀐 경제값은 덮어쓰지 않고 conflict를 보존하며 해당 coverage를 신뢰할 수 없는 상태로 둔다.
3. order history는 변하는 상태 자료다. venue order ID만을 기준으로 최신값을 과거 관측에 덮어쓰지 않는다. 각각의 관측을 보존하고 같은 관측 시점의 상충, 누적 체결의 역행 등은 별도로 표시한다. 서로 다른 query window에서 실행 기록이 덜 보인다는 이유만으로 체결 감소로 오판하지 않는다.
4. coverage에는 **요청한 bounded window를 빠짐없이 읽어 영속화했음**만 기록한다. 빈 응답, 연속된 window, 재조회 일치가 `venue_event_complete=True`나 flat 증명은 아니다. 마지막 조회 watermark와 실제 이벤트 completeness를 별개 필드/타입으로 유지한다. 구간이 비어 있거나 충돌이 있으면 현재 조회 모델이 이를 노출해야 한다.
5. `src/multi_exchange_trader/execution/worker.py`: `collect_history(symbol, *, start_ms, end_ms) -> IngestionReceipt`처럼 명시 호출을 추가해 기존 `read_history` 결과를 owner loop에서 영속화한다. 요청 전·await 후 계좌/환경/lease/수명주기를 검사하고 stop 이후 SQLite 접근을 막는다. read-only 호출 실패·중간 취소 시 성공 coverage를 전진시키지 않는다. 이 단계에서 CLI의 자동 호출 주기나 새 startup flag는 추가하지 않아도 된다.
6. `src/multi_exchange_trader/execution/history.py`: 기존 parser와 window 제약을 재사용한다. 여러 구간을 취합한 view는 관측 freshness를 현재 시각으로 갱신하지 않으며, 원래 최소 server/관측 시각과 각 구간 provenance를 유지한다. `HistorySnapshot`을 새로 만들어 qualification이 강화된 것처럼 보이지 않게 별도 aggregate 타입을 둘 수 있다.
7. `scripts/operations/state_snapshot.py`: 새 store의 schema/고유키/append-only 검증과 manifest inventory를 추가한다. legacy snapshot은 계속 검증·복원할 수 있어야 한다. 새 monitor evidence bundle과 완성 운용 bundle의 필수 파일 구분은 version으로 명시하고 기존 `--require-runtime` 의미를 조용히 바꾸지 않는다. journal·runtime-state·history가 같은 generation에서 보존되어야 한다.
8. `docs/implementation/NATIVE_WORKER_20261102.md`, `AUTOMATIC_RUNTIME_STATUS_KO.md`: 보존 가능한 조회 구간과 아직 증명되지 않은 실제 거래소 completeness를 구분해 갱신한다. 기존 한도·자정 baseline·신호 주기·history 최초 시작 시점을 임의 생성하지 않는다.

### 필요한 좁은 시험

- 새 `tests/execution/test_history_store.py`: 실제 임시 SQLite에서 같은 snapshot/겹친 window 재수집의 멱등성, 같은 account transaction의 여러 symbol 조회 중복 제거, 바뀐 execution/transaction 경제값의 충돌 보존, 변화하는 order 관측의 append-only 보존.
- coverage 시험: 정확한 빈 구간도 query coverage만 기록, 중간 구간 누락/페이지 실패/취소 시 watermark 미전진, 지연 이벤트가 과거 window 재조회에서 추가됨, 같은 시각의 상충 order 관측, 재시작 후 정확한 Decimal/ID/시각 유지. 구간 경계의 포함 규칙을 테스트로 고정한다.
- crash/transaction 시험: event commit 전 실패와 receipt/coverage commit 전 실패에서 부분 성공으로 보이지 않음. 충돌 탐지 결과가 재시작 뒤 사라지지 않음. immutable 원본은 수정하지 않음.
- `tests/execution/test_worker.py`: `collect_history`의 잘못된 symbol/scope/만료 binding/lease 상실/await 중 stop 거절. 느린 조회 동안 native fill callback은 계속 영속화됨. readback과 history 저장을 모의 네트워크로 검증.
- `tests/operations/test_state_snapshot.py`: 새 store 포함 백업/새 위치 복원 후 동일 coverage와 conflict 상태, 누락/손상/다른 scope 거절, 기존 snapshot 호환. actual user DB는 사용하지 않음.
- qualification 회귀: 복원 또는 완전한 query coverage 뒤에도 `HistorySnapshot.venue_event_complete`, `flat_verified`, `DailyRealizedLoss.ledger_qualified`는 false이고 runtime은 BLOCKED, 정책은 STOPPED. command 재전송/예약 해제/레버리지/취소/유료 AI 효과는 발생하지 않음.

완료 기준은 격리된 입력 이력의 정확한 영속 보존·coverage 조회·복원과 기존 고정 차단 보존이다. 이후 2번 계좌 위험 증거, 3번 restart/cache/terminal reconciliation, 4번 보호 복구를 의존 순서대로 연결한다. 이 작은 패치의 성공을 실제 DEMO 자격이나 PC-off 자동 운용 완료로 보고하지 않는다.