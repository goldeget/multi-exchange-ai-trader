"""Build the Korean account view. Failed reads do not invent balances."""

import json
import re
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast

from .bybit_readonly import BybitBlocked, BybitReadError, BybitReadOnlyClient
from .config import (
    EnvironmentName,
    live_trading_enabled,
    resolve_environment,
    text_contains_credential,
)
from .freshness import VIEW_DEADLINE_SECONDS, freshness_label
from .models import (
    AccountView,
    CoinBalance,
    Connection,
    LeverageInfo,
    OrderItem,
    PositionItem,
    WalletView,
)

_UNSIGNED = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d+)?$")
_SIGNED = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$")
_SYMBOL = re.compile(r"^[A-Z0-9]{2,20}$")
_COIN = re.compile(r"^[A-Z0-9]{1,10}$")
_CONNECTION_LABELS = {
    "ok": "연결됨",
    "BLOCKED": "연결이 막혔습니다",
    "error": "연결 안 됨",
    "missing_credentials": "비밀키가 없습니다",
    "config": "설정이 잘못되었습니다",
}
_ENVIRONMENT_LABELS = {"mainnet": "실계좌", "demo": "연습 계좌", "testnet": "시험용 거래소"}
_MESSAGES = {
    "missing_credentials": (
        "비밀키가 없습니다. 설정 파일의 비밀키를 확인하세요. "
        "키 값은 보여 주지 않습니다. 조회 요청을 보내지 않았습니다."
    ),
    "config": "시험용 거래소와 연습 계좌를 함께 켤 수 없습니다. 조회를 시작하지 않았습니다.",
    "BLOCKED": (
        "연결이 막혔습니다. Bybit가 이 접속을 막았습니다. 잔고, 포지션, 주문 숫자를 만들지 않았습니다. "
        "우회 설정은 바꾸지 않습니다."
    ),
    "network": "Bybit에 연결하지 못했습니다. 숫자를 만들지 않았습니다.",
    "dns": "Bybit 주소를 찾지 못했습니다. 잔고 숫자는 만들지 않았습니다. DNS나 VPN은 바꾸지 않습니다.",
    "invalid_json": "Bybit 응답을 확인하지 못해 숫자를 숨겼습니다.",
    "response_too_large": "Bybit 응답이 너무 커서 읽지 않고 숫자를 숨겼습니다.",
    "truncated": "포지션이나 주문이 너무 많아 중간 목록은 보여 주지 않습니다. 숫자를 숨겼습니다.",
    "redirect": "Bybit가 다른 주소로 보내려 해서 따라가지 않았습니다. 숫자를 숨겼습니다.",
    "unexpected_account": "통합 계정 잔고가 아니라서 숫자를 숨겼습니다.",
    "http_status": "Bybit가 정상 응답을 주지 않아 숫자를 숨겼습니다.",
    "unexpected": "조회 중 오류가 나서 숫자를 숨겼습니다. 자세한 내용은 비밀키가 섞이지 않도록 표시하지 않습니다.",
    "ret_10002": "요청 시각이 서버와 맞지 않습니다. 숫자를 숨겼습니다.",
    "ret_10003": "접속 키가 맞지 않습니다. 키 값은 표시하지 않습니다.",
    "ret_10004": "서명이 맞지 않습니다. 비밀키는 표시하지 않습니다.",
    "ret_10005": "이 키로는 계좌를 읽을 권한이 없습니다.",
    "ret_10006": "요청이 너무 많습니다. 잠시 뒤에 다시 조회하세요.",
    "ret_10010": "접속 키에 등록된 주소와 이 컴퓨터가 다릅니다. 키 값은 표시하지 않습니다. 숫자를 숨겼습니다.",
    "credential_leak": "오류가 났습니다. 비밀키가 섞이지 않도록 자세한 내용과 숫자를 숨겼습니다.",
    "timeout": "응답 시간을 넘겼습니다. 숫자를 숨겼습니다.",
    "proxy": "중간에 있는 연결이 요청을 거부했습니다. 국가 제한으로 단정하지 않습니다. 연결 설정은 바꾸지 않습니다. 숫자를 숨겼습니다.",
    "http_403": "Bybit가 요청을 거절했습니다. 국가 제한인지 다른 이유인지는 이 응답만으로 확인하지 못했습니다. 숫자를 숨겼습니다.",
    "http_451": "거래소가 법적 제한 형식의 응답을 보냈습니다. 국가 제한 문구는 없어 단정하지 않습니다. 숫자를 숨겼습니다.",
}
_MARGIN_LABELS = {
    "ISOLATED_MARGIN": "격리",
    "REGULAR_MARGIN": "교차",
    "PORTFOLIO_MARGIN": "포트폴리오",
}


def _message(code: str) -> str:
    known = _MESSAGES.get(code)
    if known is not None:
        return known
    if code.startswith("ret_") and code.removeprefix("ret_").isdigit():
        return f"Bybit 오류 코드 {code.removeprefix('ret_')}. 숫자를 숨겼습니다."
    return _MESSAGES["unexpected"]


def _distinctions(environment: EnvironmentName | None) -> list[str]:
    if environment == "mainnet":
        current = "이 조회는 실계좌입니다. 연습 계좌가 아닙니다."
    elif environment == "demo":
        current = "이 조회는 연습 계좌입니다. 실계좌가 아닙니다."
    elif environment == "testnet":
        current = "이 조회는 시험용 거래소입니다. 실계좌가 아닙니다."
    else:
        current = "어떤 계좌인지 아직 확인하지 못했습니다."
    return [
        current,
        "가상 거래가 아닙니다. 가상 거래 화면의 돈과 다릅니다.",
        "주문은 보내지 않습니다. 보이는 포지션과 주문은 모두 외부에서 연 것입니다.",
    ]


def _environment() -> EnvironmentName | None:
    try:
        name, _host = resolve_environment()
    except ValueError:
        return None
    return name


def _connection(code: str) -> Connection:
    if code in {"ok", "BLOCKED", "missing_credentials", "config"}:
        return cast(Connection, code)
    return "error"


def blank_view(
    code: str,
    environment: EnvironmentName | None,
    *,
    live_trading: bool,
) -> AccountView:
    connection = _connection(code)
    message = _message(code)
    if live_trading and connection == "ok":
        message = f"{message} 실거래 설정이 켜져 있어도 이 화면은 주문을 보내지 않습니다."
    return AccountView(
        연결상태=_CONNECTION_LABELS[connection],
        상태코드=connection,
        환경=_ENVIRONMENT_LABELS.get(environment or "", "확인 필요"),
        환경코드=environment,
        구분=_distinctions(environment),
        실거래켜짐=live_trading,
        메시지=message,
        서버시각=None,
        최대레버리지=None,
        잔고=None,
        포지션=None,
        미체결주문=None,
        오류코드=None if connection == "ok" else code,
    )


def publish(view: AccountView) -> AccountView:
    """Hide the whole view if a credential slipped into the JSON."""
    rendered = json.dumps(view.model_dump(by_alias=True, mode="json"), ensure_ascii=False)
    if not text_contains_credential(rendered):
        return view
    return blank_view("credential_leak", view.환경코드, live_trading=view.실거래켜짐)


def _decimal(value: object, *, signed: bool = False) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        return None
    pattern = _SIGNED if signed else _UNSIGNED
    if pattern.fullmatch(text) is None:
        return None
    return text


def _is_zero(text: str) -> bool:
    try:
        return Decimal(text) == 0
    except InvalidOperation:
        return False


def _label(value: object, table: dict[str, str]) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        key = str(value)
    elif isinstance(value, str):
        key = value
    else:
        return "확인 필요"
    return table.get(key, "확인 필요")


def _symbol(value: object) -> str:
    if isinstance(value, str) and _SYMBOL.fullmatch(value):
        return value
    return "확인 필요"


def _moment(server_ms: int) -> datetime:
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=server_ms)


def _format_time(server_ms: int) -> str:
    return _moment(server_ms).strftime("%Y-%m-%d %H:%M:%S") + " 세계 표준시"


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _account_fields(result: dict[str, object]) -> tuple[str, str | None]:
    """Read margin mode from account info. Do not infer it from position tradeMode."""
    raw = result.get("marginMode")
    label = _MARGIN_LABELS.get(raw, "확인 불가") if isinstance(raw, str) else "확인 불가"
    updated = result.get("updatedTime")
    stamp: str | None = None
    if isinstance(updated, str) and updated.isdigit():
        millis = int(updated)
        if millis > 0:
            stamp = _iso(_moment(millis))
    elif isinstance(updated, int) and not isinstance(updated, bool) and updated > 0:
        stamp = _iso(_moment(updated))
    return label, stamp


def _rows(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise BybitReadError("invalid_json")
    rows: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            raise BybitReadError("invalid_json")
        mapped: dict[str, object] = {}
        for key, inner in item.items():
            if not isinstance(key, str):
                raise BybitReadError("invalid_json")
            mapped[key] = inner
        rows.append(mapped)
    return rows


def _leverage(result: dict[str, object]) -> LeverageInfo:
    if result.get("category") != "linear":
        raise BybitReadError("invalid_json")
    rows = _rows(result.get("list"))
    if len(rows) != 1 or rows[0].get("symbol") != "BTCUSDT":
        raise BybitReadError("invalid_json")
    raw = rows[0].get("leverageFilter")
    if not isinstance(raw, dict):
        raise BybitReadError("invalid_json")
    filt: dict[str, object] = {}
    for key, item in raw.items():
        if not isinstance(key, str):
            raise BybitReadError("invalid_json")
        filt[key] = item
    maximum = _decimal(filt.get("maxLeverage"))
    minimum = _decimal(filt.get("minLeverage"))
    step = _decimal(filt.get("leverageStep"))
    if maximum is None or minimum is None or step is None:
        raise BybitReadError("invalid_json")
    return LeverageInfo(종목="BTCUSDT", 최대=maximum, 최소=minimum, 간격=step)


def _wallet(result: dict[str, object]) -> WalletView:
    rows = _rows(result.get("list"))
    if len(rows) != 1 or rows[0].get("accountType") != "UNIFIED":
        raise BybitReadError("unexpected_account")
    account = rows[0]
    coins: list[CoinBalance] = []
    for row in _rows(account.get("coin")):
        name = row.get("coin")
        coins.append(
            CoinBalance(
                코인=name if isinstance(name, str) and _COIN.fullmatch(name) else "확인 필요",
                지갑잔고=_decimal(row.get("walletBalance")),
                자산평가=_decimal(row.get("equity")),
            )
        )
    return WalletView(
        총자산평가=_decimal(account.get("totalEquity")),
        총지갑잔고=_decimal(account.get("totalWalletBalance")),
        총사용가능=_decimal(account.get("totalAvailableBalance")),
        코인=coins,
    )


def _position(row: dict[str, object]) -> PositionItem | None:
    size = _decimal(row.get("size"))
    if size is not None and _is_zero(size):
        return None
    return PositionItem(
        종목=_symbol(row.get("symbol")),
        방향=_label(row.get("side"), {"Buy": "매수", "Sell": "매도"}),
        수량=size or "확인 필요",
        레버리지=_decimal(row.get("leverage")),
        진입가=_decimal(row.get("avgPrice")),
        표시가=_decimal(row.get("markPrice")),
        청산가=_decimal(row.get("liqPrice")),
        미실현손익=_decimal(row.get("unrealisedPnl"), signed=True),
        # tradeMode is deprecated and always 0. It is not this position's margin mode.
        마진모드="확인 불가",
        포지션모드=_label(
            row.get("positionIdx"),
            {"0": "한 방향", "1": "양쪽 포지션 · 매수", "2": "양쪽 포지션 · 매도"},
        ),
    )


def _order(row: dict[str, object]) -> OrderItem:
    order_type = _label(row.get("orderType"), {"Limit": "지정가", "Market": "시장가"})
    price = _decimal(row.get("price"))
    if order_type == "시장가" and price is not None and _is_zero(price):
        price = None
    reduce_only = row.get("reduceOnly")
    if reduce_only is True:
        reduce_label = "예"
    elif reduce_only is False:
        reduce_label = "아니오"
    else:
        reduce_label = "확인 필요"
    quantity = _decimal(row.get("qty"))
    return OrderItem(
        종목=_symbol(row.get("symbol")),
        방향=_label(row.get("side"), {"Buy": "매수", "Sell": "매도"}),
        유형=order_type,
        수량=quantity or "확인 필요",
        가격=price,
        상태=_label(row.get("orderStatus"), {"New": "미체결", "PartiallyFilled": "일부 체결"}),
        줄이기만=reduce_label,
    )


def _read_age_ms(wallet_ms: int | None, public_ms: int) -> int:
    """Clock for this read's age.

    Account info may omit its own server time. Use the wallet response time,
    then the public market time. Do not invent a clock.
    """
    if wallet_ms is not None and wallet_ms > 0:
        return wallet_ms
    return public_ms


def _success_message(live_trading: bool) -> str:
    message = "계좌를 읽었습니다. 주문·레버리지 변경·포지션 변경은 하지 않았습니다."
    if live_trading:
        return f"{message} 실거래 설정이 켜져 있어도 이 화면은 주문을 보내지 않습니다."
    return message


async def build_account_view(
    client: BybitReadOnlyClient | None,
    problem: str | None,
    *,
    live_trading: bool | None = None,
    deadline_at: float | None = None,
) -> AccountView:
    trading = live_trading_enabled() if live_trading is None else live_trading
    if client is None:
        code = problem or "missing_credentials"
        if code not in {"missing_credentials", "config"}:
            code = "missing_credentials"
        return blank_view(code, _environment(), live_trading=trading)
    started = datetime.now(UTC)
    deadline = time.monotonic() + VIEW_DEADLINE_SECONDS if deadline_at is None else deadline_at
    try:
        _time_result, public_ms = await client.read(
            "/v5/market/time", [], signed=False, deadline=deadline
        )
        if public_ms is None:
            raise BybitReadError("invalid_json")
        client.anchor_server_time(public_ms)
        instrument, _ignored = await client.read(
            "/v5/market/instruments-info",
            [("category", "linear"), ("symbol", "BTCUSDT")],
            signed=False,
            deadline=deadline,
        )
        wallet, wallet_ms = await client.read(
            "/v5/account/wallet-balance",
            [("accountType", "UNIFIED")],
            signed=True,
            deadline=deadline,
        )
        account, _account_ms = await client.read(
            "/v5/account/info", [], signed=True, deadline=deadline
        )
        observed_ms = _read_age_ms(wallet_ms, public_ms)
        positions = await client.read_pages(
            "/v5/position/list",
            [("category", "linear"), ("settleCoin", "USDT")],
            deadline=deadline,
        )
        orders = await client.read_pages(
            "/v5/order/realtime",
            [("category", "linear"), ("settleCoin", "USDT")],
            deadline=deadline,
        )
        received = datetime.now(UTC)
        margin, account_updated = _account_fields(account)
        parsed_positions: list[PositionItem] = []
        for row in positions:
            item = _position(row)
            if item is not None:
                parsed_positions.append(item)
        finished = datetime.now(UTC)
        return AccountView(
            연결상태=_CONNECTION_LABELS["ok"],
            상태코드="ok",
            환경=_ENVIRONMENT_LABELS[client.environment],
            환경코드=client.environment,
            구분=_distinctions(client.environment),
            실거래켜짐=trading,
            메시지=_success_message(trading),
            서버시각=_format_time(public_ms),
            최대레버리지=_leverage(instrument),
            잔고=_wallet(wallet),
            포지션=parsed_positions,
            미체결주문=[_order(row) for row in orders],
            오류코드=None,
            계좌마진모드=margin,
            수집시작시각=_iso(started),
            수집완료시각=_iso(finished),
            거래소응답시각=_iso(_moment(observed_ms)),
            계정갱신시각=account_updated,
            수신시각=_iso(received),
            신선도표시=freshness_label(0, "ok"),
            주문판단에사용=False,
        )
    except BybitBlocked:
        return blank_view("BLOCKED", client.environment, live_trading=trading)
    except BybitReadError as exc:
        return blank_view(exc.code, client.environment, live_trading=trading)
    except Exception:  # noqa: BLE001 - never return exception text; it can contain a credential
        return blank_view("unexpected", client.environment, live_trading=trading)
