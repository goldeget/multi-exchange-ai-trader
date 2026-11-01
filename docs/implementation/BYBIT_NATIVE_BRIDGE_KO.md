# Bybit 네이티브 명령·이벤트 브리지 계약

이 변경은 고정 `nautilus-trader==1.231.0`의 **네이티브 계약 검증**이다. 실제 DEMO 주문, 체결, 보호 주문 또는 완전 자동 운영을 검증한 결과가 아니다. 공개 API·화면, `OfflineExecutionCoordinator`, LIVE 지원 여부와 연결하지 않았다. `NativeBybitBridge.production_supported`는 `False`다.

## 범위와 호출 경계

`execution/bybit_native.py`는 명시적 DEMO, Bybit LINEAR, one-way `position_idx=0`의 MARKET/LIMIT 주문만 만든다. 환경·키 파일·상속된 거래소 인증값을 읽지 않는다. 네이티브 import는 `load_native_bindings()`, `load_native_event_decoder()`, `build_demo_exec_config()`의 명시적 호출 때만 발생한다. native node 생성·connect·run과 HTTPX 주문 전송 경로는 없다.

`load_native_event_decoder()`를 단독 호출해도 설치된 배포 버전이 정확히 `1.231.0`인지 native event/report import 전에 확인한다. 이전·이후 버전은 `PINNED_NATIVE_VERSION_REQUIRED`로 거절한다.

| 인터페이스 | 의미 |
|---|---|
| `NativeOrderRequest(scope, owned, order_type, reference_price, ...)` | frozen 요청. 기존 `ProtectionScope`와 `OwnedOrder`를 재사용한다. |
| `AttachedProtection(stop_loss, take_profit, trigger_source)` | 양수·유한·유계 Decimal, BUY/SELL 방향 관계와 trigger source를 검사한다. |
| `NativeBindings` | 실제 native 식별자·수량·가격·side 변환 함수. 휴대용 테스트만 명시적 fake를 주입한다. |
| `NativeOrderFactory`, `NativeStrategy` | 명령 생성·전달에 필요한 좁은 typed protocol. |
| `NativeBybitBridge.register_owned(request)` | 외부에서 이미 영속화한 정확한 소유권 매핑을 복구한다. 주문을 보내지 않는다. |
| `NativeBybitBridge.submit(request)` | 실제 `OrderFactory.market/limit`로 만든 후 `Strategy.submit_order(order, params=...)`를 호출한다. |
| `NativeSubmission` | 호출 후 `SUBMITTING`, 호출 예외 후 `UNKNOWN`만 반환한다. 접수 ACK가 아니다. |
| `NativeEventContext(scope, native_account_id, received_at_ns)` | 이미 분리·검증된 계정 채널의 출처를 호출자가 명시한다. |
| `consume(event, context)` | 정확한 등록 ID·instrument·account label·scope와 native type을 검사하고 별도 증거를 반환한다. |
| `consume_reports(..., query_succeeded, query_complete)` | 유효한 일부 증거를 보존하되 오류·부분·빈 결과는 incomplete로 남긴다. |

기존 journal의 `ec_` + 32자리 hex, 총 35자 client ID를 그대로 `ClientOrderId`에 전달한다. 접두사만으로 소유권을 주장하지 않는다. 동일 ID의 다른 요청은 거절한다. 만들어진 native 주문의 ID·instrument·side·quantity·price·reduce-only를 원래 요청과 다시 비교하므로 변환 과정의 정밀도 손실을 조용히 허용하지 않는다.

이 브리지는 영속 worker가 아니다. 실행 의도·정책·소유권을 journal에 영속화하고 lease·위험 검사·claim을 완료하는 책임은 미래의 통합 호출자에게 있다. 프로세스 내부의 동일 요청 재호출은 전송하지 않지만 새 프로세스에서 새 브리지를 만들었다고 재전송 권한이 생기지 않는다. `UNKNOWN` 복구는 원래 ID의 주문·체결·포지션 정합을 먼저 수행해야 한다. 현재 구현에 프로세스 간 exactly-once 보장은 없다.

## 고정된 주문 옵션

일반 옵션을 `OrderFactory.limit(..., params=...)`에 넣지 않는다. 가격·수량·ID는 factory 인자이며, 거래소 옵션은 실제 `Strategy.submit_order(order, params=...)`의 native `SubmitOrder.params`에 들어간다.

부착 보호는 일반 `stop_loss`/`take_profit` + `MarkPrice`/`LastPrice`/`IndexPrice` + 양쪽 `Market` + `tpsl_mode="Full"`만 만든다. 별도 DEMO `tp_trigger_price`/`sl_trigger_price`, one-way 이외 index, reduce-only EXIT의 부착 TP/SL은 제출 전에 거절한다. MARKET에는 limit 가격을 받지 않는다. ENTRY/EXIT 외 주문 역할·broad cancel·flatten·보호 변경 명령은 구현하지 않는다.

`build_demo_exec_config(api_key=..., api_secret=..., max_retries=1)`은 설정 객체만 반환한다. DEMO/LINEAR를 명시하고 `futures_leverages`, `margin_mode`, `position_mode`를 모두 `None`으로 둔다. 계정 설정 변경은 하지 않는다. spot 자동 상환과 spot position report도 끈다. URL override는 노출하지 않는다.

고정 native config의 `max_retries`는 `PositiveInt`다. 0이 허용된다고 가정하지 않고 애플리케이션 경계에서 1~10의 정수만 받는다. 기본은 1, retry delay는 500~1000ms다. 실제 native `config.json()`/`type(config).parse()`로 양수 설정의 허용과 0의 거절을 Linux에서 검증한다. native 내부 HTTP 재시도가 존재하므로 “재전송이 전혀 없다”고 보장하지 않는다. 상위 자동 재시도 경로는 추가하지 않았다.

## 비동기 증거와 소유권

`execution/native_events.py`는 `SUBMITTED`, `ACCEPTED`, `DENIED`, `REJECTED`, `CANCELED`, `EXPIRED`, 개별 `FILL`, 누적 주문 `STATUS`를 구분한다. 전송 함수 반환과 submitted 이벤트만으로 접수·체결·노출·보호 상태를 만들지 않는다. CANCELED 이후에도 이미 관측한 fill은 사라지지 않는다. native `OrderStatusReport(FILLED)`의 누적 `filled_qty`를 개별 체결로 변환하지 않는다.

실제 native class 객체의 정확한 type registry를 사용한다. 이름만 같은 임의 객체는 native 증거가 아니다. 휴대용 테스트의 fake registry는 명시적으로 주입한 경계다. 미등록 ID, 다른 account/environment/instrument, fill side 불일치는 거절한다. native `OrderDenied`는 거래소 전송 전 로컬 이벤트여서 native account 값이 없으며, 이 경우도 호출자의 격리된 context와 정확한 소유권 매핑이 필요하다.

`BYBIT-UNIFIED`는 native adapter의 account label이며 인증된 Bybit UID가 아니다. 실제 UID·subaccount·환경은 미래의 계정 검증·연결 계층에서 별도로 검증하고 이 context에 결합해야 한다. 현재 객체를 만들거나 임의 context를 제공하는 것만으로 실제 계정 출처가 인증되지 않는다.

`NativeFillEvidence`는 execution/trade ID, client ID, position 매핑, instrument, side, 개별 수량·가격, 수수료와 통화, `ts_event_ns`, native `ts_init_ns`, 수신 `received_at_ns`를 보존한다. 음수 maker rebate도 손실 없이 보존한다. `.to_owned_fill()`은 기존 순수 `FillEvent`로 투영하며 원본 ns와 비용 정보는 native 증거에 계속 남는다. 기존 owned reducer의 execution ID 중복·충돌 검사를 적용할 수 있으나 이 패치에서 coordinator에 자동 적용하지 않는다.

이벤트·수송 예외와 거래소 거절 원문은 고정 오류 코드로 대체한다. 임의 provider reason·info·응답 body를 이 증거 객체에 복사하지 않는다. 수량·가격은 float 변환 없이 bounded Decimal로 검증한다.

## 보고서가 입증하지 못하는 항목

`NativeReportBatch.complete`는 요청자가 성공·완전성을 명시한 **비어 있지 않은 유효 scoped 증거 묶음**에만 해당하며 계정 전체 정합의 성공 표시가 아니다. 오류·미지원·외부·부분·빈 결과는 incomplete다. 포지션 보고서의 빈 배열이나 FLAT 한 건을 실제 계정 전체 무노출 증거로 승격하지 않는다.

native 일반 position report에는 원시 leverage/TP/SL이 없다. 모든 결과의 `flat_verified`, `leverage_verified`, `protection_verified`, `terminal_confirmed`는 `False`다. 실제 레버리지 적용·보호 주문 존재·잔여 수량 전체 보호·계정 전체 평탄화는 이 브리지에서 판정하지 않는다.

별도 기존 GET-only `BybitReadOnlyClient`로 원시 positionIdx·leverage·TP/SL과 페이지 전체를 읽는 후속 정합 경로는 아키텍처상 허용된다. 읽기 결과의 손실 없는 보존을 위해 Rust fork가 필수라고 단정하지 않는다. 주문·설정 **쓰기**는 계속 Nautilus 한 경로만 사용해야 한다. dynamic trading-stop, 부모/자식 보호 식별, 재시작 정합, 외부 주문 경계와 실제 DEMO lifecycle은 후속 과제다.

## 검증과 한계

휴대용 테스트는 의도적으로 fake factory/strategy/event 경계만 사용한다. 최초 27개 테스트의 구현 누락 RED를 확인했고, 변환 변경 5개와 identity 예외 누출 1개의 추가 RED도 확인한 뒤 구현했다. 정확한 journal ID 유지, 비동기 분리, 재호출 방지, 범위·보호 제약, 수수료·ns 보존과 오류/빈 보고서의 미확정을 검사한다. Windows에서 native 사례를 건너뛰는 것은 이 실제 wheel 검증을 대신하지 않는다.

Linux 전용 테스트는 `REQUIRE_NATIVE_CONTRACTS=1`과 `NATIVE_NETWORK_ISOLATED=1`을 요구한다. 별도 네트워크 namespace, PID 1과 다른 netns, loopback 외 interface 부재와 IPv4/IPv6 route 불가를 먼저 확인한 뒤 exact wheel version을 검사한다. Python socket monkeypatch만으로 Rust 네트워크를 막았다고 주장하지 않는다. 필수 실행 모드에서 의존성·환경·서명이 맞지 않으면 skip 대신 실패한다.

인터페이스 목록은 `socket.if_nameindex()`로 현재 프로세스의 network namespace를 커널에 조회한다. `unshare --net` 뒤에도 `/sys/class/net`은 공유된 sysfs mount의 기존 namespace를 보여줄 수 있어 격리 검사의 근거로 사용하지 않는다.

실제 native `OrderFactory`, 등록된 `Strategy`, native `SubmitOrder`의 RiskEngine 메시지 경계, 실제 Bybit adapter의 `_submit_order`·validity·position resolver·position cache·PyO3 `BybitNativeTpSlParams`를 사용한다. HTTP/event emission 경계만 fake로 교체한다. 실제 native 설정·OrderSubmitted/Accepted/Rejected/Denied/Canceled/Filled·OrderStatusReport/FillReport/PositionStatusReport 객체도 사용한다. native node나 execution client를 생성·시작·연결하지 않는다. CI는 총 테스트 수 양수와 skip 0을 별도로 확인한다.

이 문서는 테스트 설계와 구현 범위를 설명한다. 최종 exact-head Linux 성공 여부와 전체 회귀 결과는 해당 변경의 CI 증거로 확인해야 한다. 테스트 통과가 실제 DEMO의 주문 접수·체결·TP/SL 유효성이나 MAINNET 운용 자격을 뜻하지 않는다.

공식 근거는 고정 소스 `27a8e54e7ac3c57d6cbf8891f0283dfbaee97317`의 [Strategy API](https://github.com/nautechsystems/nautilus_trader/blob/27a8e54e7ac3c57d6cbf8891f0283dfbaee97317/nautilus_trader/trading/strategy.pyx#L805-L885), [Bybit adapter](https://github.com/nautechsystems/nautilus_trader/blob/27a8e54e7ac3c57d6cbf8891f0283dfbaee97317/nautilus_trader/adapters/bybit/execution.py#L903-L1099), [native config](https://github.com/nautechsystems/nautilus_trader/blob/27a8e54e7ac3c57d6cbf8891f0283dfbaee97317/nautilus_trader/adapters/bybit/config.py), [event 객체](https://github.com/nautechsystems/nautilus_trader/blob/27a8e54e7ac3c57d6cbf8891f0283dfbaee97317/nautilus_trader/model/events/order.pyx), [report 객체](https://github.com/nautechsystems/nautilus_trader/blob/27a8e54e7ac3c57d6cbf8891f0283dfbaee97317/nautilus_trader/execution/reports.py), [설정 검증 hook](https://github.com/nautechsystems/nautilus_trader/blob/27a8e54e7ac3c57d6cbf8891f0283dfbaee97317/nautilus_trader/common/config.py#L269-L306)이다. 구현 전 조사 보고서 `BYBIT_NAUTILUS_LIVE_CONTRACT_KO.md`의 제한을 유지한다.
