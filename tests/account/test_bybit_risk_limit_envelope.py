"""Offline public raw risk-page behavior; fake HTTPX ownership is not qualification."""

import asyncio
import hashlib
import time
from collections.abc import AsyncIterator, Callable
from typing import cast

import httpx
import pytest

from multi_exchange_trader.account import bybit_readonly as module
from multi_exchange_trader.account.config import EnvironmentName, allowed_host
from multi_exchange_trader.account.instrument_envelope import InstrumentSourceError
from multi_exchange_trader.account.risk_limit_envelope import (
    BybitRiskLimitEnvelopeReadV1,
    JsonNumberTokenV1,
)
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget, ReadGroupToken
from multi_exchange_trader.execution.risk_v2_math import RiskV2Rejected
from multi_exchange_trader.trading_product import InstrumentKey

KEY = InstrumentKey("BYBIT", "LINEAR", "BTCUSDT")
RAW = (b'{"retCode":0,"retMsg":"OK","result":{"category":"linear","list":'
       b'[{"id":1,"symbol":"BTCUSDT","riskLimitValue":"100",'
       b'"initialMargin":0.0050,"maintenanceMargin":5e-3,"maxLeverage":"2",'
       b'"mmDeduction":"0","isLowestRisk":1}],'
       b'"nextPageCursor":""},"time":1700000000000}')


class Stream(httpx.AsyncByteStream):
    def __init__(self, raw: bytes = RAW, *, chunk_hook: Callable[[], None] = lambda: None,
                 close_hook: Callable[[], None] = lambda: None) -> None:
        self.raw = raw
        self.chunk_hook = chunk_hook
        self.close_hook = close_hook
        self.closes = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.chunk_hook()
        yield self.raw

    async def aclose(self) -> None:
        self.closes += 1
        self.close_hook()


async def read(client: module.BybitReadOnlyClient, budget: MonitoringReadBudget,
               group: ReadGroupToken, cursor: str = "",
               deadline: float | None = None) -> BybitRiskLimitEnvelopeReadV1:
    return await client.read_risk_limit_envelope(
        KEY, cursor=cursor, budget=budget, group=group, deadline=deadline)


async def test_raw_numbers_one_send_no_legacy_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    from multi_exchange_trader.account import risk_limit_envelope as a0

    sent: list[httpx.Request] = []
    stream = Stream()

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, stream=stream)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("legacy decoder invoked")

    monkeypatch.setattr(module, "_parse_json", forbidden)
    monkeypatch.setattr(module.BybitReadOnlyClient, "_accepted", forbidden)
    original_decoder = a0._decode_risk_limit_json_v1
    decoded: list[bytes] = []

    def decoder(raw: bytes) -> dict[str, object]:
        decoded.append(raw)
        return original_decoder(raw)

    monkeypatch.setattr(a0, "_decode_risk_limit_json_v1", decoder)
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(handler))
    budget = MonitoringReadBudget()
    validated: list[tuple[dict[str, object], int | None]] = []
    with budget.reuse_or_open_receipt() as group, budget.validating_response(
        lambda result, stamp: validated.append((result, stamp))
    ):
        envelope = await read(client, budget, group)
    await client.aclose()
    assert type(envelope) is BybitRiskLimitEnvelopeReadV1
    assert envelope.decoded_body_bytes == RAW
    assert envelope.decoded_body_sha256 == hashlib.sha256(RAW).hexdigest()
    assert envelope.accepted_attempt == 1
    assert (envelope.physical_start, envelope.physical_end, envelope.logical_ordinal) == (0, 1, 1)
    assert len(sent) == 1 and sent[0].url.path == "/v5/market/risk-limit"
    assert sent[0].headers["accept-encoding"] == "identity"
    assert not any(name.startswith("x-bapi") for name in sent[0].headers)
    assert stream.closes == 1
    assert decoded == [RAW, RAW]
    assert validated[0][1] == 1700000000000
    rows = cast(list[dict[str, object]], validated[0][0]["list"])
    assert rows[0]["initialMargin"] == JsonNumberTokenV1("0.0050")
    assert rows[0]["maintenanceMargin"] == JsonNumberTokenV1("5e-3")


async def test_retry_and_pages_keep_owner_and_sorted_wire_cursor() -> None:
    requests: list[httpx.Request] = []
    streams: list[Stream] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        stream = Stream()
        streams.append(stream)
        return httpx.Response(500 if len(requests) == 1 else 200, stream=stream)

    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_TESTNET", transport=httpx.MockTransport(handler))
    waits: list[float] = []

    async def sleeper(delay: float) -> None:
        waits.append(delay)

    client._sleeper = sleeper
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group:
        first = await read(client, budget, group)
        second = await read(client, budget, group, "a +/한")
    await client.aclose()
    assert waits == [0.4]
    assert first.accepted_attempt == 2 and second.accepted_attempt == 1
    assert (first.physical_start, first.physical_end, first.logical_ordinal) == (0, 2, 1)
    assert (second.physical_start, second.physical_end, second.logical_ordinal) == (2, 3, 2)
    assert first.source_id == second.source_id
    assert first.source_generation == second.source_generation
    assert first.source_id != first.source_generation
    assert first.observation_id != second.observation_id
    assert second.params == (("category", "linear"), ("symbol", "BTCUSDT"), ("cursor", "a +/한"))
    assert requests[-1].url.query == b"category=linear&cursor=a+%2B%2F%ED%95%9C&symbol=BTCUSDT"
    assert all(stream.closes == 1 for stream in streams)

@pytest.mark.parametrize("status,quota,expected", [
    (403, None, "ACCESS_RESTRICTED"), (451, None, "ACCESS_RESTRICTED"),
    (429, None, "RATE_LIMITED"), (200, "0", "RATE_LIMITED"),
    (200, "garbage", "RATE_LIMITED"), (200, "9" * 5000, "RATE_LIMITED"),
    (200, "²", "RATE_LIMITED"),
])
async def test_original_quota_precedes_owner_body_origin_and_cleanup(
    status: int, quota: str | None, expected: str,
) -> None:
    client: module.BybitReadOnlyClient

    def bad_close() -> None:
        raise httpx.ReadError("sensitive cleanup detail")

    stream = Stream(b"not json", close_hook=bad_close)
    sends = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sends
        sends += 1
        client._risk_generation = None
        headers = [("content-encoding", "gzip"), ("content-length", "bad")]
        if quota is not None:
            # Non-ASCII header values exercise the budget's narrow integer path.
            headers.append(("x-bapi-limit-status", quota))
        return httpx.Response(status, stream=stream, headers=[
            (name.encode(), value.encode("utf-8")) for name, value in headers
        ], request=httpx.Request("GET", "https://example.org/wrong"))

    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(handler))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(InstrumentSourceError) as caught:
        await read(client, budget, group)
    await client.aclose()
    assert caught.value.code == "INSTRUMENT_SOURCE_" + expected
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert sends == budget.physical_attempts == stream.closes == 1


@pytest.mark.parametrize("raw,expected", [
    (b'{"retCode":10006,"retMsg":"private text"}', "RATE_LIMITED"),
    (b'{"retCode":-1,"retMsg":"private text"}', "PROVIDER_REJECTED"),
    (b'{"retCode":2147483647}', "PROVIDER_REJECTED"),
    (b'{"retCode":0,"retMsg":"not available in your region"}', "ACCESS_RESTRICTED"),
])
async def test_fixed_provider_errors_discard_raw_text(raw: bytes, expected: str) -> None:
    stream = Stream(raw)
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream)))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(InstrumentSourceError) as caught:
        await read(client, budget, group)
    await client.aclose()
    assert caught.value.args == ("INSTRUMENT_SOURCE_" + expected,)
    assert caught.value.__context__ is None
    assert stream.closes == 1 and budget.physical_attempts == 1


@pytest.mark.parametrize("raw,expected", [
    (b'\xff', "RISK_V2_INPUT_INVALID"),
    (b'{"retCode":0,"ret\\u0043ode":0}', "RISK_V2_INPUT_INVALID"),
    (RAW.replace(b'"time":1700000000000', b'"time":1700000000000.0'), "RISK_V2_INPUT_INVALID"),
    (RAW.replace(b'0.0050', b'1e1001'), "RISK_V2_RESOURCE_LIMIT"),
    (RAW.replace(b'0.0050', b'1' * 1025), "RISK_V2_RESOURCE_LIMIT"),
    (RAW.replace(b'0.0050', b'1' * 257), "RISK_V2_RESOURCE_LIMIT"),
    (b'{"retCode":0,"result":' + b'[' * 33 + b'0' + b']' * 33 + b'}', "RISK_V2_RESOURCE_LIMIT"),
    (b'{"retCode":0,"x":[' + b'0,' * 8192 + b'0]}', "RISK_V2_RESOURCE_LIMIT"),
    (b'{' + b','.join(b'"k%d":0' % n for n in range(65)) + b'}', "RISK_V2_RESOURCE_LIMIT"),
    (RAW.replace(b'"category":"linear"', b'"category":"linear","extra":0'), "RISK_V2_INPUT_INVALID"),
    (RAW.replace(b'1700000000000', b'253402300799999'), "RISK_V2_CAUSAL_ORDER_INVALID"),
    (b'{"retCode":2147483648}', "RISK_V2_INPUT_INVALID"),
])
async def test_a0_errors_retain_owners(raw: bytes, expected: str) -> None:
    stream = Stream(raw)
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream)))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(RiskV2Rejected) as caught:
        await read(client, budget, group)
    await client.aclose()
    assert caught.value.code == expected
    assert stream.closes == 1 and budget.physical_attempts == 1


@pytest.mark.parametrize("length,expected", [(65536, None), (65537, "RESOURCE_LIMIT")])
async def test_application_body_cap(length: int, expected: str | None) -> None:
    raw = RAW + b' ' * (length - len(RAW))
    stream = Stream(raw)
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream)))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group:
        if expected is None:
            assert (await read(client, budget, group)).decoded_body_bytes == raw
        else:
            with pytest.raises(InstrumentSourceError, match=expected):
                await read(client, budget, group)
    await client.aclose()
    assert stream.closes == 1 and budget.physical_attempts == 1


@pytest.mark.parametrize("headers,expected", [
    ([("content-encoding", "gzip")], "ENCODING_UNSUPPORTED"),
    ([("content-encoding", "identity"), ("content-encoding", "identity")], "ENCODING_UNSUPPORTED"),
    ([("content-length", "-1")], "RESPONSE_INVALID"),
    ([("content-length", "65537")], "RESOURCE_LIMIT"),
    ([("content-length", "9" * 5000)], "RESOURCE_LIMIT"),
    ([("content-length", "1"), ("content-length", "1")], "RESPONSE_INVALID"),
])
async def test_headers_fail_before_iteration(headers: list[tuple[str, str]], expected: str) -> None:
    def forbidden() -> None:
        raise AssertionError("body iterated despite rejected headers")

    stream = Stream(chunk_hook=forbidden)
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, headers=headers, stream=stream)))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(InstrumentSourceError, match=expected):
        await read(client, budget, group)
    await client.aclose()
    assert stream.closes == budget.physical_attempts == 1


@pytest.mark.parametrize("change", ["query", "origin", "method", "userinfo", "fragment", "header", "history"])
async def test_original_response_request_and_history_cannot_be_forged(
    change: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = Stream()

    def handler(request: httpx.Request) -> httpx.Response:
        actual = httpx.Request("GET", request.url, headers=request.headers)
        if change == "query":
            actual.url = actual.url.copy_with(query=b"category=linear&limit=1&symbol=BTCUSDT")
        elif change == "origin":
            actual.url = actual.url.copy_with(host="api-testnet.bybit.com")
        elif change == "method":
            actual.method = "POST"
        elif change == "userinfo":
            actual.url = actual.url.copy_with(username="user")
        elif change == "fragment":
            actual.url = actual.url.copy_with(fragment="x")
        elif change == "header":
            actual.headers["X-BAPI-SIGN"] = "forged"
        response = httpx.Response(200, stream=stream, request=actual)
        if change == "history":
            response.history = [httpx.Response(302)]
        return response

    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream)))
    original_send = client._client.send

    async def send(request: httpx.Request, *, stream: bool) -> httpx.Response:
        # HTTPX normalizes application association/history inside send; corrupt the
        # original response only after the real owned fake transport has returned.
        original = await original_send(request, stream=stream)
        forged = handler(request)
        original.request = forged.request
        original.history = forged.history
        return original

    monkeypatch.setattr(client._client, "send", send)
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(InstrumentSourceError, match="ORIGIN_MISMATCH"):
        await read(client, budget, group)
    await client.aclose()
    assert stream.closes == budget.physical_attempts == 1


@pytest.mark.parametrize("stage", ["send", "chunk", "close", "validator", "retry"])
async def test_owner_retirement_at_await_or_callback_prevents_envelope(stage: str) -> None:
    client: module.BybitReadOnlyClient

    def retire() -> None:
        client._risk_generation = None

    stream = Stream(chunk_hook=retire if stage == "chunk" else lambda: None,
                    close_hook=retire if stage == "close" else lambda: None)

    def handler(request: httpx.Request) -> httpx.Response:
        if stage == "send":
            retire()
        return httpx.Response(500 if stage == "retry" else 200, stream=stream)

    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(handler))

    async def sleeper(delay: float) -> None:
        retire()

    client._sleeper = sleeper
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, budget.validating_response(
        lambda result, stamp: retire() if stage == "validator" else None
    ), pytest.raises(InstrumentSourceError, match="RETIRED"):
        await read(client, budget, group)
    await client.aclose()
    assert stream.closes == budget.physical_attempts == 1


@pytest.mark.parametrize("change,expected", [
    ("client", "RETIRED"), ("host", "ORIGIN_MISMATCH"),
    ("environment", "ORIGIN_MISMATCH"), ("credentials", "ORIGIN_MISMATCH"),
])
async def test_owner_snapshot_survives_callbacks(change: str, expected: str) -> None:
    client: module.BybitReadOnlyClient
    original: httpx.AsyncClient

    def mutate() -> None:
        if change == "client":
            client._client = cast(httpx.AsyncClient, object())
        elif change == "host":
            client.base_url = "https://api-testnet.bybit.com"
        elif change == "environment":
            client.environment = "demo"
        else:
            client._api_key = "forged"

    stream = Stream(chunk_hook=mutate)
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream)))
    original = client._client
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(InstrumentSourceError, match=expected):
        await read(client, budget, group)
    client._client = original
    await client.aclose()
    assert stream.closes == 1


async def test_cancellation_closes_original_without_success() -> None:
    entered = asyncio.Event()

    class WaitingStream(Stream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            entered.set()
            await asyncio.Event().wait()
            yield RAW

    stream = WaitingStream()
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream)))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group:
        task = asyncio.create_task(read(client, budget, group))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await client.aclose()
    assert stream.closes == budget.physical_attempts == 1


@pytest.mark.parametrize("close_error,expected", [
    (httpx.ReadError("secret"), "NETWORK"), (httpx.ReadTimeout("secret"), "TIMEOUT"),
])
async def test_close_failure_prevents_success(close_error: Exception, expected: str) -> None:
    def fail() -> None:
        raise close_error

    stream = Stream(close_hook=fail)
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream)))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(InstrumentSourceError, match=expected):
        await read(client, budget, group)
    await client.aclose()
    assert stream.closes == budget.physical_attempts == 1


@pytest.mark.parametrize("environment", ["demo", "mainnet", "testnet"])
async def test_authenticated_families_never_send_public_risk(environment: str) -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        raise AssertionError("authenticated client sent a public risk request")

    client = module.BybitReadOnlyClient(
        api_key="fake", api_secret="fake", base_url=allowed_host(cast(EnvironmentName, environment)),
        environment=cast(EnvironmentName, environment), transport=httpx.MockTransport(forbidden))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(InstrumentSourceError, match="SOURCE_UNSUPPORTED"):
        await read(client, budget, group)
    await client.aclose()
    assert budget.physical_attempts == 0


async def test_public_read_and_anchor_remain_denied() -> None:
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=RAW)))
    with pytest.raises(InstrumentSourceError, match="INPUT_INVALID"):
        await client.read("/v5/market/risk-limit", [], signed=False)
    with pytest.raises(InstrumentSourceError, match="INPUT_INVALID"):
        client.anchor_server_time(1700000000000)
    assert client._client._trust_env is False
    assert client._api_key == client._api_secret == ""
    await client.aclose()

async def test_raw_field_pointers_and_economics_keep_original_spelling() -> None:
    from fractions import Fraction

    from multi_exchange_trader.execution.bybit_linear_model_inputs_v1 import (
        decode_bybit_risk_limit_page_v1,
    )
    from multi_exchange_trader.execution.risk_v2_math import decimal_fraction_v2

    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=Stream())))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group:
        envelope = await read(client, budget, group)
    await client.aclose()
    row = decode_bybit_risk_limit_page_v1(envelope, page_ordinal=1).rows[0]
    assert row.initial_margin_rate.reference.wire_spelling == "0.0050"
    assert row.maintenance_margin_rate.reference.wire_spelling == "5e-3"
    assert row.initial_margin_rate.reference.json_pointer == "/result/list/0/initialMargin"
    assert row.maintenance_margin_rate.reference.json_pointer == "/result/list/0/maintenanceMargin"
    assert row.initial_margin_rate.reference != row.maintenance_margin_rate.reference
    assert decimal_fraction_v2(row.initial_margin_rate.value) == Fraction(1, 200)
    assert decimal_fraction_v2(row.maintenance_margin_rate.value) == Fraction(1, 200)


@pytest.mark.parametrize("kind", ["missing", "foreign", "forged", "subclass", "ended"])
async def test_invalid_group_never_sends(kind: str) -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        raise AssertionError("send before group proof")

    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(forbidden))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as ended:
        pass
    with budget.reuse_or_open_receipt():
        if kind == "missing":
            selected = cast(ReadGroupToken, None)
        elif kind == "foreign":
            other = MonitoringReadBudget()
            with other.reuse_or_open_receipt() as foreign:
                selected = foreign
        elif kind == "forged":
            selected = object.__new__(ReadGroupToken)
        elif kind == "subclass":
            class SubToken(ReadGroupToken):
                pass
            selected = object.__new__(SubToken)
        else:
            selected = ended
        with pytest.raises(InstrumentSourceError, match="GROUP_REQUIRED"):
            await read(client, budget, selected)
    await client.aclose()
    assert budget.physical_attempts == 0


async def test_budget_exact_type_and_overlap_never_send() -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        raise AssertionError("send before exact budget proof")

    class SubBudget(MonitoringReadBudget):
        pass

    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(forbidden))
    for budget in (cast(MonitoringReadBudget, None), SubBudget()):
        with pytest.raises(InstrumentSourceError, match="BUDGET_REQUIRED"):
            await read(client, budget, cast(ReadGroupToken, None))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, client._monitoring_operation(
        MonitoringReadBudget()
    ), pytest.raises(InstrumentSourceError, match="OVERLAP"):
        await read(client, budget, group)
    await client.aclose()
    assert budget.physical_attempts == 0


@pytest.mark.parametrize("deadline", [0, True, -1.0, float("nan"), float("inf")])
async def test_deadline_requires_exact_finite_nonnegative_float(deadline: object) -> None:
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: pytest.fail("invalid deadline sent")))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(InstrumentSourceError, match="INPUT_INVALID"):
        await read(client, budget, group, deadline=cast(float, deadline))
    await client.aclose()
    assert budget.physical_attempts == 0


@pytest.mark.parametrize("stage", ["before", "chunk", "parse", "close"])
async def test_deadline_checked_at_every_boundary(stage: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import time
    from types import SimpleNamespace

    from multi_exchange_trader.account import risk_limit_envelope as a0

    now = time.monotonic()
    clock = [now]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def expire() -> None:
        clock[0] = now + 2.0

    if stage == "before":
        expire()
    original_decoder = a0._decode_risk_limit_json_v1

    def decoder(raw: bytes) -> dict[str, object]:
        body = original_decoder(raw)
        if stage == "parse":
            expire()
        return body

    monkeypatch.setattr(a0, "_decode_risk_limit_json_v1", decoder)
    stream = Stream(chunk_hook=expire if stage == "chunk" else lambda: None,
                    close_hook=expire if stage == "close" else lambda: None)
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream)))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(InstrumentSourceError, match="TIMEOUT"):
        await read(client, budget, group, deadline=now + 1.0)
    await client.aclose()
    assert budget.physical_attempts == (0 if stage == "before" else 1)
    assert stream.closes == (0 if stage == "before" else 1)


@pytest.mark.parametrize("clock", [float("nan"), float("inf"), -1.0, 0.0])
async def test_nonfinite_or_backward_route_clock_fails(clock: float, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    stream = Stream(chunk_hook=lambda: monkeypatch.setattr(
        module, "time", SimpleNamespace(monotonic=lambda: clock)))
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream)))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(InstrumentSourceError, match="CLOCK_INVALID"):
        await read(client, budget, group)
    await client.aclose()
    assert stream.closes == budget.physical_attempts == 1


@pytest.mark.parametrize("fault", [ValueError("guard bug"), module.BybitReadError("unknown_code")])
async def test_unknown_faults_propagate_after_cleanup(fault: Exception) -> None:
    def fail() -> None:
        raise fault

    stream = Stream(chunk_hook=fail)
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream)))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(type(fault)) as caught:
        await read(client, budget, group)
    await client.aclose()
    assert caught.value is fault and stream.closes == 1


async def test_close_revokes_before_transport_await(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=Stream())))
    original_close = client._client.aclose

    async def close() -> None:
        entered.set()
        await release.wait()
        await original_close()

    monkeypatch.setattr(client._client, "aclose", close)
    closer = asyncio.create_task(client.aclose())
    await entered.wait()
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(InstrumentSourceError, match="RETIRED"):
        await read(client, budget, group)
    release.set()
    await closer
    assert budget.physical_attempts == 0

@pytest.mark.parametrize("cursor,expected", [
    (cast(str, None), "RISK_V2_INPUT_INVALID"),
    (cast(str, 1), "RISK_V2_INPUT_INVALID"),
    ("é" * 513, "RISK_V2_RESOURCE_LIMIT"),
    ("\ud800", "RISK_V2_INPUT_INVALID"),
])
async def test_cursor_exact_type_utf8_byte_limit_precedes_send(cursor: str, expected: str) -> None:
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: pytest.fail("invalid cursor sent")))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(RiskV2Rejected) as caught:
        await read(client, budget, group, cursor)
    await client.aclose()
    assert caught.value.code == expected and budget.physical_attempts == 0


async def test_cursor_1024_utf8_bytes_is_unmodified() -> None:
    cursor = "é" * 512
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, stream=Stream())

    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(handler))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group:
        value = await read(client, budget, group, cursor)
    await client.aclose()
    assert value.params[-1] == ("cursor", cursor)
    assert requests[0].url.params["cursor"] == cursor


@pytest.mark.parametrize("key,expected", [
    (InstrumentKey("BYBIT", "SPOT", "BTCUSDT"), "RISK_V2_UNSUPPORTED_PRODUCT"),
    (cast(InstrumentKey, None), "RISK_V2_INPUT_INVALID"),
])
async def test_key_owned_validation_precedes_send(key: InstrumentKey, expected: str) -> None:
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: pytest.fail("invalid key sent")))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(RiskV2Rejected) as caught:
        await client.read_risk_limit_envelope(key, cursor="", budget=budget, group=group, deadline=None)
    await client.aclose()
    assert caught.value.code == expected and budget.physical_attempts == 0


@pytest.mark.parametrize("quota", [False, True])
async def test_ended_group_after_send_and_quota_precedence(quota: bool) -> None:
    budget = MonitoringReadBudget()
    stream = Stream()

    def handler(request: httpx.Request) -> httpx.Response:
        receipt = budget._receipt.get()
        assert receipt is not None
        receipt.active = False
        return httpx.Response(200, stream=stream,
                              headers={"x-bapi-limit-status": "0"} if quota else {})

    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(handler))
    with budget.reuse_or_open_receipt() as group, pytest.raises(InstrumentSourceError) as caught:
        await read(client, budget, group)
    await client.aclose()
    assert caught.value.code == "INSTRUMENT_SOURCE_" + ("RATE_LIMITED" if quota else "GROUP_REQUIRED")
    assert budget.physical_attempts == stream.closes == 1


@pytest.mark.parametrize("cap", ["physical", "logical"])
async def test_existing_shared_caps_prevent_additional_sends(cap: str) -> None:
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: pytest.fail("budget cap bypassed")))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group:
        if cap == "physical":
            budget.physical_attempts = 200
        else:
            receipt = budget._receipt.get()
            assert receipt is not None
            receipt.logical = 32
        with pytest.raises(InstrumentSourceError, match="BUDGET_EXHAUSTED"):
            await read(client, budget, group)
    await client.aclose()
    assert budget.physical_attempts == (200 if cap == "physical" else 0)


async def test_owner_guard_programmer_fault_retains_identity_after_cleanup() -> None:
    ready = False
    defect = ValueError("owner programming defect")

    def guard() -> None:
        if ready:
            raise defect

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal ready
        ready = True
        return httpx.Response(200, headers={"x-bapi-limit-status": "9"}, stream=stream)

    stream = Stream()
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(handler))
    budget = MonitoringReadBudget()
    with budget.receipt(owner_guard=guard), budget.reuse_or_open_receipt() as group, pytest.raises(
        ValueError
    ) as caught:
        await read(client, budget, group)
    await client.aclose()
    assert caught.value is defect and stream.closes == 1


async def test_actual_close_during_send_revokes_inflight_read() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    stream = Stream()

    async def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return httpx.Response(200, stream=stream)

    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(handler))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group:
        task = asyncio.create_task(read(client, budget, group))
        await entered.wait()
        await client.aclose()
        release.set()
        with pytest.raises(InstrumentSourceError, match="RETIRED"):
            await task
    assert budget.physical_attempts == stream.closes == 1


async def test_closed_underlying_client_cannot_start_page() -> None:
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: pytest.fail("closed transport sent")))
    await client._client.aclose()
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(InstrumentSourceError, match="CLOSED"):
        await read(client, budget, group)
    await client.aclose()
    assert budget.physical_attempts == 0


async def test_separate_public_owners_have_distinct_lifetime_ids() -> None:
    values: list[BybitRiskLimitEnvelopeReadV1] = []
    for _ in range(2):
        client = module.BybitReadOnlyClient.for_public_instruments(
            source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=Stream())))
        budget = MonitoringReadBudget()
        with budget.reuse_or_open_receipt() as group:
            values.append(await read(client, budget, group))
        await client.aclose()
    assert values[0].source_id != values[1].source_id
    assert values[0].source_generation != values[1].source_generation
    assert values[0].observation_id != values[1].observation_id


async def test_parent_operation_changed_by_callback_blocks_return() -> None:
    client: module.BybitReadOnlyClient
    foreign = MonitoringReadBudget()

    def change() -> None:
        client._operation.set(foreign)

    stream = Stream(chunk_hook=change)
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream)))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(InstrumentSourceError, match="OVERLAP"):
        await read(client, budget, group)
    await client.aclose()
    assert budget.physical_attempts == stream.closes == 1

@pytest.mark.parametrize("change", ["path", "query", "signature", "scheme", "port"])
async def test_forged_outbound_request_is_rejected_before_charge(
    change: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: pytest.fail("forged request sent")))
    original_build = client._build_request

    def build(path: str, params: list[tuple[str, str]], *, signed: bool) -> httpx.Request:
        request = original_build(path, params, signed=signed)
        if change == "path":
            request.url = request.url.copy_with(path="/v5/market/instruments-info")
        elif change == "query":
            request.url = request.url.copy_with(query=b"category=linear&symbol=BTCUSDT&limit=1")
        elif change == "signature":
            request.headers["X-BAPI-SIGN"] = "injected"
        elif change == "scheme":
            request.url = request.url.copy_with(scheme="http")
        else:
            request.url = request.url.copy_with(port=444)
        return request

    monkeypatch.setattr(client, "_build_request", build)
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(
        InstrumentSourceError, match="ORIGIN_MISMATCH"
    ):
        await read(client, budget, group)
    assert budget.physical_attempts == 0
    await client.aclose()


async def test_unknown_budget_owner_error_is_not_fallback_translated() -> None:
    from multi_exchange_trader.execution.monitoring_budget import MonitoringBudgetError

    ready = False
    defect = MonitoringBudgetError("UNLISTED_OWNER_ERROR")

    def guard() -> None:
        if ready:
            raise defect

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal ready
        ready = True
        return httpx.Response(200, stream=stream)

    stream = Stream()
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(handler))
    budget = MonitoringReadBudget()
    with budget.receipt(owner_guard=guard), budget.reuse_or_open_receipt() as group, pytest.raises(
        MonitoringBudgetError
    ) as caught:
        await read(client, budget, group)
    await client.aclose()
    assert caught.value is defect and stream.closes == 1

@pytest.mark.parametrize("stage", ["send", "return"])
@pytest.mark.parametrize("guard_owner", ["budget", "receipt"])
@pytest.mark.parametrize("mutation", ["end", "replace", "context", "token"])
async def test_guard_group_loss_rejects_send_and_final_return(
    stage: str, guard_owner: str, mutation: str,
) -> None:
    import inspect
    from contextvars import ContextVar, copy_context

    from multi_exchange_trader.execution.monitoring_budget import _ReceiptBudget

    fired = False
    sends: list[bool] = []
    original: _ReceiptBudget | None = None
    replacement: _ReceiptBudget | None = None
    budget: MonitoringReadBudget

    def guard() -> None:
        nonlocal fired
        names = {frame.function for frame in inspect.stack()}
        selected = ("before_attempt" in names if stage == "send" else
                    budget.physical_attempts == 1 and "_read" not in names)
        if fired or not selected or original is None:
            return
        fired = True
        if mutation == "end":
            original.active = False
        elif mutation == "replace":
            assert replacement is not None
            # An actually issued replacement can retain the old token after an
            # arbitrary guard mutates it. Receipt object identity still matters.
            replacement.token = original.token
            budget._receipt.set(replacement)
        elif mutation == "context":
            budget._receipt = ContextVar("replaced_receipt_context", default=original)
        else:
            assert replacement is not None
            original.token = replacement.token

    budget = MonitoringReadBudget(guard=guard if guard_owner == "budget" else lambda: None)
    original_context = budget._receipt
    stream = Stream()

    def handler(request: httpx.Request) -> httpx.Response:
        current = budget._receipt.get()
        sends.append(current is original and current is not None and current.active)
        return httpx.Response(200, stream=stream)

    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(handler))
    replacement_context = copy_context()
    replacement_scope = budget.receipt()
    replacement_context.run(replacement_scope.__enter__)
    replacement = replacement_context.run(budget._receipt.get)
    assert replacement is not None
    try:
        with budget.receipt(guard=guard if guard_owner == "receipt" else lambda: None), \
                budget.reuse_or_open_receipt() as group:
            original = budget._receipt.get()
            assert original is not None and original is not replacement
            try:
                with pytest.raises(InstrumentSourceError) as caught:
                    await read(client, budget, group)
                assert caught.value.code == "INSTRUMENT_SOURCE_GROUP_REQUIRED"
                assert caught.value.__context__ is None and caught.value.__cause__ is None
            finally:
                budget._receipt = original_context
    finally:
        replacement_context.run(replacement_scope.__exit__, None, None, None)
        await client.aclose()
    assert fired
    assert sends == ([] if stage == "send" else [True])
    # before_attempt already charged the failed send boundary; there is no refund.
    assert budget.physical_attempts == 1
    assert stream.closes == (0 if stage == "send" else 1)



def _e2a_faults() -> list[BaseException]:
    from multi_exchange_trader.execution.monitoring_budget import MonitoringBudgetError

    return [
        *(module.BybitReadError(code) for code in (
            "timeout", "proxy", "dns", "network", "response_too_large", "invalid_json",
            "redirect", "http_403", "http_451", "ret_10006", "http_status",
            "monitoring_operation_overlap")),
        module.BybitBlocked(),
        *(MonitoringBudgetError(code) for code in (
            "MONITORING_GROUP_REQUIRED", "MONITORING_DEADLINE_OR_CLOCK",
            "MONITORING_LOGICAL_GET_LIMIT", "MONITORING_PHYSICAL_ATTEMPTS",
            "MONITORING_READ_FAILED")),
        httpx.ReadTimeout("synthetic owner"), httpx.ProxyError("synthetic owner"),
        httpx.ReadError("synthetic owner"), TimeoutError("synthetic owner"),
        InstrumentSourceError("INSTRUMENT_SOURCE_RATE_LIMITED"),
        RiskV2Rejected("RISK_V2_INPUT_INVALID"), asyncio.CancelledError("synthetic owner"),
        ValueError("synthetic owner"),
    ]


@pytest.mark.parametrize("fault", _e2a_faults())
@pytest.mark.parametrize("stage", [
    "entry", "send", "response", "chunk", "retry", "close", "parse", "validator", "final",
])
async def test_e2a_guard_fault_identity_at_every_boundary(
    fault: BaseException, stage: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import inspect

    from multi_exchange_trader.account import risk_limit_envelope as a0

    phase = "entry"
    sends: list[httpx.Request] = []
    stream = Stream()
    fired = False

    def guard() -> None:
        nonlocal fired
        names = {frame.function for frame in inspect.stack()}
        final = (bool(sends) and stream.closes == 1
                 and "read_risk_limit_envelope" in names and "_read" not in names)
        selected = (phase == stage if stage not in ("send", "final") else
                    budget.physical_attempts == 1 and not sends if stage == "send" else final)
        if selected:
            fired = True
            raise fault

    def mark(value: str) -> None:
        nonlocal phase
        phase = value

    stream.chunk_hook = lambda: mark("chunk")
    stream.close_hook = lambda: mark("close")

    def handler(request: httpx.Request) -> httpx.Response:
        sends.append(request)
        mark("response")
        return httpx.Response(500 if stage == "retry" else 200, stream=stream)

    original_decoder = a0._decode_risk_limit_json_v1

    def decoder(raw: bytes) -> dict[str, object]:
        value = original_decoder(raw)
        mark("parse")
        return value

    monkeypatch.setattr(a0, "_decode_risk_limit_json_v1", decoder)
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(handler))

    async def sleeper(delay: float) -> None:
        mark("retry")

    client._sleeper = sleeper
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, budget.validating_response(
        lambda result, stamp: mark("validator")
    ), pytest.raises(type(fault)) as caught:
        await client.read_risk_limit_envelope(
            KEY, cursor="", budget=budget, group=group, deadline=None, owner_guard=guard)
    await client.aclose()
    assert fired and caught.value is fault
    assert budget._terminal_failure is None
    assert len(sends) == (0 if stage in ("entry", "send") else 1)
    assert budget.physical_attempts == (0 if stage == "entry" else 1)
    assert stream.closes == (0 if stage in ("entry", "send") else 1)


@pytest.mark.parametrize("guard", [False, 1, object()])
async def test_e2a_noncallable_guard_rejects_before_send(guard: object) -> None:
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: pytest.fail("invalid guard sent")))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(
        InstrumentSourceError, match="INPUT_INVALID"
    ):
        await client.read_risk_limit_envelope(
            KEY, cursor="", budget=budget, group=group, deadline=None,
            owner_guard=cast(Callable[[], None], guard))
    await client.aclose()
    assert budget.physical_attempts == 0


@pytest.mark.parametrize("fault", [ValueError("cleanup"), httpx.ReadError("cleanup")])
@pytest.mark.parametrize("primary_kind", ["quota", "fixed", "owner", "programmer", "cancel"])
async def test_e2a_ordinary_close_fault_preserves_primary(
    fault: Exception, primary_kind: str,
) -> None:
    primary: BaseException = (asyncio.CancelledError("original") if primary_kind == "cancel"
                              else ValueError("original"))
    ready = False

    def chunk() -> None:
        if primary_kind in ("programmer", "cancel"):
            raise primary

    def close() -> None:
        raise fault

    def guard() -> None:
        if ready and primary_kind == "owner":
            raise primary

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal ready
        ready = True
        return httpx.Response(429 if primary_kind == "quota" else 200,
                              headers={"content-encoding": "gzip"}
                              if primary_kind == "fixed" else {}, stream=stream)

    stream = Stream(chunk_hook=chunk, close_hook=close)
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(handler))
    budget = MonitoringReadBudget()
    expected = InstrumentSourceError if primary_kind in ("quota", "fixed") else type(primary)
    with budget.reuse_or_open_receipt() as group, pytest.raises(expected) as caught:
        await client.read_risk_limit_envelope(
            KEY, cursor="", budget=budget, group=group, deadline=None, owner_guard=guard)
    await client.aclose()
    if primary_kind in ("quota", "fixed"):
        assert isinstance(caught.value, InstrumentSourceError)
        assert caught.value.code == "INSTRUMENT_SOURCE_" + (
            "RATE_LIMITED" if primary_kind == "quota" else "ENCODING_UNSUPPORTED")
    else:
        assert caught.value is primary
    assert budget.physical_attempts == stream.closes == 1


@pytest.mark.parametrize("marked", [False, True])
async def test_e2a_fresh_cleanup_cancellation_wins_over_ordinary_primary(marked: bool) -> None:
    primary = module.BybitReadError("timeout")
    fresh = asyncio.CancelledError("fresh cleanup")
    ready = False

    def guard() -> None:
        if ready and marked:
            raise primary

    def chunk() -> None:
        raise primary

    def close() -> None:
        raise fresh

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal ready
        ready = True
        return httpx.Response(200, stream=stream)

    stream = Stream(chunk_hook=chunk, close_hook=close)
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(handler))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(asyncio.CancelledError) as caught:
        await client.read_risk_limit_envelope(
            KEY, cursor="", budget=budget, group=group, deadline=None, owner_guard=guard)
    await client.aclose()
    assert caught.value is fresh and stream.closes == 1


@pytest.mark.parametrize("when", ["first_clock", "final_clock", "guard"])
@pytest.mark.parametrize("mutation", ["operation_context", "operation_none", "receipt", "client"])
async def test_e2a_original_contexts_and_last_clock_cannot_lose_owner(
    when: str, mutation: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import inspect
    from contextvars import ContextVar
    from types import SimpleNamespace

    original_clock = time.monotonic
    stream: Stream
    fired = False
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream)))
    raw_client = client._client
    original_operation = client._operation
    budget = MonitoringReadBudget()
    stream = Stream()

    def mutate() -> None:
        nonlocal fired
        if fired:
            return
        fired = True
        if mutation == "operation_context":
            client._operation = ContextVar("replaced_operation", default=budget)
        elif mutation == "operation_none":
            original_operation.set(None)
        elif mutation == "receipt":
            current = budget._receipt.get()
            assert current is not None
            current.active = False
        else:
            client._client = cast(httpx.AsyncClient, object())

    def final() -> bool:
        names = {frame.function for frame in inspect.stack()}
        return (stream.closes == 1 and "read_risk_limit_envelope" in names
                and "_read" not in names)

    def clock() -> float:
        if when == "first_clock" or (when == "final_clock" and final()):
            mutate()
        return original_clock()

    def guard() -> None:
        if when == "guard" and final():
            mutate()

    with budget.reuse_or_open_receipt() as group:
        monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=clock))
        # None was already the original pre-entry value, so that one mutation is
        # deliberately applied only inside the entered operation.
        if when == "first_clock" and mutation == "operation_none":
            monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=original_clock))
            stream.chunk_hook = mutate
        with pytest.raises(InstrumentSourceError) as caught:
            await client.read_risk_limit_envelope(
                KEY, cursor="", budget=budget, group=group, deadline=None, owner_guard=guard)
    assert fired
    assert caught.value.code == "INSTRUMENT_SOURCE_" + (
        "GROUP_REQUIRED" if mutation == "receipt" else
        "RETIRED" if mutation == "client" else "OVERLAP")
    assert original_operation.get() is None
    client._client = raw_client
    await client.aclose()
    assert budget.physical_attempts == (0 if when == "first_clock"
                                        and mutation != "operation_none" else 1)


async def test_e2a_final_guard_request_mutation_is_reproved_after_charge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: pytest.fail("guard-corrupted request sent")))
    original = client._build_request

    def build(path: str, params: list[tuple[str, str]], *, signed: bool) -> httpx.Request:
        request = original(path, params, signed=signed)
        requests.append(request)
        return request

    monkeypatch.setattr(client, "_build_request", build)
    budget = MonitoringReadBudget()

    def guard() -> None:
        if budget.physical_attempts == 1:
            requests[-1].url = requests[-1].url.copy_with(path="/v5/order/create")

    with budget.reuse_or_open_receipt() as group, pytest.raises(
        InstrumentSourceError, match="ORIGIN_MISMATCH"
    ):
        await client.read_risk_limit_envelope(
            KEY, cursor="", budget=budget, group=group, deadline=None, owner_guard=guard)
    await client.aclose()
    assert budget.physical_attempts == 1


async def test_e2a_original_operation_resets_without_caller_repair() -> None:
    from contextvars import ContextVar

    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream)))
    original_context = client._operation
    stream: Stream
    budget = MonitoringReadBudget()

    def replace() -> None:
        client._operation = ContextVar("new_operation", default=budget)

    stream = Stream(chunk_hook=replace)
    with budget.reuse_or_open_receipt() as group, pytest.raises(
        InstrumentSourceError, match="OVERLAP"
    ):
        await read(client, budget, group)
    assert original_context.get() is None
    assert client._operation is not original_context
    await client.aclose()



@pytest.mark.parametrize("stage", ["entry", "final"])
@pytest.mark.parametrize("fault", [SystemExit("synthetic"), KeyboardInterrupt("synthetic")])
async def test_e2a_public_guard_process_control_in_caller_task(
    stage: str, fault: BaseException,
) -> None:
    import inspect

    stream = Stream()
    sends: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sends.append(request)
        return httpx.Response(200, stream=stream)

    def guard() -> None:
        names = {frame.function for frame in inspect.stack()}
        final = (stream.closes == 1 and "read_risk_limit_envelope" in names
                 and "_read" not in names)
        if (stage == "entry" and not sends) or (stage == "final" and final):
            raise fault

    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(handler))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(type(fault)) as caught:
        await client.read_risk_limit_envelope(
            KEY, cursor="", budget=budget, group=group, deadline=None, owner_guard=guard)
    await client.aclose()
    assert caught.value is fault and budget._terminal_failure is None
    assert len(sends) == budget.physical_attempts == stream.closes == (stage == "final")


@pytest.mark.parametrize("primary_kind", ["none", "owner", "quota", "cancel", "control"])
@pytest.mark.parametrize("cleanup_kind", ["ordinary", "fresh_cancel", "fresh_control", "same", "ok"])
async def test_e2a_direct_cleanup_precedence_keeps_exact_control_objects(
    primary_kind: str, cleanup_kind: str,
) -> None:
    from datetime import UTC, datetime

    primary: BaseException | None = {
        "none": None, "owner": module.BybitReadError("timeout"),
        "quota": None, "cancel": asyncio.CancelledError("original cancel"),
        "control": KeyboardInterrupt("original control"),
    }[primary_kind]
    cleanup: BaseException | None = {
        "ordinary": ValueError("ordinary cleanup"),
        "fresh_cancel": asyncio.CancelledError("fresh cancel"),
        "fresh_control": SystemExit("fresh control"),
        "same": primary, "ok": None,
    }[cleanup_kind]
    ready = False

    def guard() -> None:
        if ready and primary is not None:
            raise primary

    def close() -> None:
        if cleanup is not None:
            raise cleanup

    stream = Stream(close_hook=close)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal ready
        ready = True
        return httpx.Response(429 if primary_kind == "quota" else 200, stream=stream)

    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(handler))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, client._monitoring_operation(budget):
        context = budget._receipt
        generation = client._risk_generation
        assert generation is not None
        route = module._RiskRoute(
            KEY, budget, group, datetime.now(UTC), 0.0, 0, owner=client,
            client=client._client, source_id=client._risk_source_id, generation=generation,
            params=(("category", "linear"), ("symbol", "BTCUSDT")),
            owner_guard=guard, operation_context=client._operation, operation_entered=True,
            receipt_context=context, receipt=context.get())
        request = client._build_request("/v5/market/risk-limit", list(route.params), signed=False)
        request.headers["Accept-Encoding"] = "identity"
        expected = (cleanup if cleanup is not None and not isinstance(cleanup, Exception) else
                    primary if primary is not None else cleanup)
        if primary_kind == "quota" and (cleanup is None or isinstance(cleanup, Exception)):
            with pytest.raises(InstrumentSourceError, match="RATE_LIMITED"):
                await client._bounded_response(request, route=route)
        elif expected is not None:
            with pytest.raises(type(expected)) as caught:
                await client._bounded_response(request, route=route)
            assert caught.value is expected
        else:
            response = await client._bounded_response(request, route=route)
            assert response.content == RAW
    await client.aclose()
    assert stream.closes == budget.physical_attempts == 1


async def test_e2a_quota_skips_new_guard_and_retains_original_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = False
    original: list[InstrumentSourceError] = []
    original_bounded = module.BybitReadOnlyClient._bounded_response

    async def observe(self: module.BybitReadOnlyClient, request: httpx.Request, *,
                      route: module._InstrumentRoute | None = None) -> httpx.Response:
        try:
            return await original_bounded(self, request, route=route)
        except InstrumentSourceError as error:
            original.append(error)
            raise

    monkeypatch.setattr(module.BybitReadOnlyClient, "_bounded_response", observe)

    def guard() -> None:
        if ready:
            pytest.fail("owner checked after original quota rejection")

    def close() -> None:
        raise ValueError("ordinary cleanup")

    stream = Stream(close_hook=close)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal ready
        ready = True
        return httpx.Response(429, stream=stream)

    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(handler))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(InstrumentSourceError) as caught:
        await client.read_risk_limit_envelope(
            KEY, cursor="", budget=budget, group=group, deadline=None, owner_guard=guard)
    await client.aclose()
    assert original == [caught.value] and original[0] is caught.value
    assert caught.value.code == "INSTRUMENT_SOURCE_RATE_LIMITED"
    assert stream.closes == budget.physical_attempts == 1


@pytest.mark.parametrize("stage", ["first_clock", "chunk", "guard"])
async def test_e2a_original_receipt_context_swap_is_detected_without_repair(
    stage: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextvars import ContextVar
    from types import SimpleNamespace

    original_clock = time.monotonic
    budget = MonitoringReadBudget()
    original_context = budget._receipt
    fired = False

    def replace() -> None:
        nonlocal fired
        if not fired:
            fired = True
            budget._receipt = ContextVar("replacement_receipt", default=None)
            budget._receipt.set(original_context.get())

    def clock() -> float:
        if stage == "first_clock":
            replace()
        return original_clock()

    def guard() -> None:
        if stage == "guard" and budget.physical_attempts == 1:
            replace()

    stream = Stream(chunk_hook=replace if stage == "chunk" else lambda: None)
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream)))
    # E2A owns only raw operation cleanup. Existing budget scope reset still
    # raises on its replaced field; E2 owns precedence around that existing exit.
    with pytest.raises(ValueError, match="different ContextVar"), \
            budget.reuse_or_open_receipt() as group:
        original = original_context.get()
        assert original is not None
        monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=clock))
        with pytest.raises(InstrumentSourceError, match="GROUP_REQUIRED"):
            await client.read_risk_limit_envelope(
                KEY, cursor="", budget=budget, group=group, deadline=None, owner_guard=guard)
    assert fired and original is not None and not original.active
    assert budget._receipt is not original_context
    assert client._operation.get() is None
    await client.aclose()
    assert budget.physical_attempts == (0 if stage == "first_clock" else 1)



@pytest.mark.parametrize("kind", ["str_subclass", "custom_equality"])
@pytest.mark.parametrize("field,expected", [
    ("_risk_generation", "RETIRED"), ("_risk_source_id", "RETIRED"),
    ("_public_source_environment", "ORIGIN_MISMATCH"), ("environment", "ORIGIN_MISMATCH"),
    ("base_url", "ORIGIN_MISMATCH"), ("_api_key", "ORIGIN_MISMATCH"),
    ("_api_secret", "ORIGIN_MISMATCH"),
])
async def test_e2a_final_owner_proof_rejects_open_equality_without_calling_it(
    kind: str, field: str, expected: str,
) -> None:
    equalities: list[str] = []

    class EqualStr(str):
        def __eq__(self, other: object) -> bool:
            equalities.append("eq")
            return True

        def __ne__(self, other: object) -> bool:
            equalities.append("ne")
            return False

        __hash__ = str.__hash__

    class EqualObject:
        def __eq__(self, other: object) -> bool:
            equalities.append("eq")
            return True

        def __ne__(self, other: object) -> bool:
            equalities.append("ne")
            return False

    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: pytest.fail("open comparison admitted a send")))
    budget = MonitoringReadBudget()

    def guard() -> None:
        if budget.physical_attempts == 1:
            setattr(client, field, EqualStr("") if kind == "str_subclass" else EqualObject())

    with budget.reuse_or_open_receipt() as group, pytest.raises(
        InstrumentSourceError, match=expected
    ):
        await client.read_risk_limit_envelope(
            KEY, cursor="", budget=budget, group=group, deadline=None, owner_guard=guard)
    await client.aclose()
    assert equalities == [] and budget.physical_attempts == 1


@pytest.mark.parametrize("owner", ["client", "budget", "raw_client"])
async def test_e2a_final_proof_rejects_changed_original_factory_types(owner: str) -> None:
    class ForeignOwner(module.BybitReadOnlyClient):
        pass

    class ForeignBudget(MonitoringReadBudget):
        pass

    class ForeignRawClient(httpx.AsyncClient):
        pass

    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: pytest.fail("changed factory type admitted a send")))
    budget = MonitoringReadBudget()
    raw_client = client._client

    def guard() -> None:
        if budget.physical_attempts == 1:
            if owner == "client":
                client.__class__ = ForeignOwner
            elif owner == "budget":
                budget.__class__ = ForeignBudget
            else:
                raw_client.__class__ = ForeignRawClient

    with budget.reuse_or_open_receipt() as group, pytest.raises(
        InstrumentSourceError, match="GROUP_REQUIRED" if owner == "budget" else "RETIRED"
    ):
        await client.read_risk_limit_envelope(
            KEY, cursor="", budget=budget, group=group, deadline=None, owner_guard=guard)
    await client.aclose()
    assert budget.physical_attempts == 1



async def test_e2a_fix_original_late_header_vector_never_sends_after_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    sent: list[bool] = []
    callbacks: list[int] = []
    stream = Stream()

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(client._risk_generation is None)
        return httpx.Response(200, stream=stream)

    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(handler))
    operation_context = client._operation
    original_build = client._build_request

    def build(path: str, params: list[tuple[str, str]], *, signed: bool) -> httpx.Request:
        request = original_build(path, params, signed=signed)
        requests.append(request)
        return request

    monkeypatch.setattr(client, "_build_request", build)
    budget = MonitoringReadBudget()

    class LateHeaders(httpx.Headers):
        def get_list(self, key: str, split_commas: bool = False) -> list[str]:
            if key.lower() == "accept-encoding":
                callbacks.append(budget.physical_attempts)
                client._risk_generation = None
            return super().get_list(key, split_commas=split_commas)

    def guard() -> None:
        if budget.physical_attempts == 1 and not sent:
            requests[-1].headers = LateHeaders(requests[-1].headers)

    with budget.reuse_or_open_receipt() as group, pytest.raises(InstrumentSourceError) as caught:
        await client.read_risk_limit_envelope(
            KEY, cursor="", budget=budget, group=group, deadline=None, owner_guard=guard)
    await client.aclose()
    assert sent == [] and callbacks == []
    assert budget.physical_attempts == 1 and stream.closes == 0
    assert caught.value.code == "INSTRUMENT_SOURCE_ORIGIN_MISMATCH"
    assert operation_context.get() is None


async def test_e2a_fix_original_final_state_vector_cannot_return_or_call_equality() -> None:
    import inspect

    from httpx._client import ClientState

    sent: list[httpx.Request] = []
    callbacks: list[bool] = []
    stream = Stream()

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, stream=stream)

    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(handler))
    raw_client = client._client
    operation_context = client._operation
    original_state = raw_client._state
    budget = MonitoringReadBudget()
    fired = False

    class LateState:
        def __eq__(self, other: object) -> bool:
            callbacks.append(operation_context.get() is budget)
            operation_context.set(None)
            raw_client._state = original_state
            return False

    def guard() -> None:
        nonlocal fired
        names = {frame.function for frame in inspect.stack()}
        if (stream.closes == 1 and "read_risk_limit_envelope" in names
                and "_read" not in names):
            fired = True
            raw_client._state = cast(ClientState, LateState())

    try:
        with budget.reuse_or_open_receipt() as group, pytest.raises(
            InstrumentSourceError, match="RETIRED"
        ):
            await client.read_risk_limit_envelope(
                KEY, cursor="", budget=budget, group=group, deadline=None, owner_guard=guard)
        assert fired and callbacks == [] and operation_context.get() is None
        assert len(sent) == budget.physical_attempts == stream.closes == 1
    finally:
        # Only restore the fixture's library state to close it; never repair the
        # original operation context, whose reset is part of the assertion.
        raw_client._state = original_state
        await client.aclose()


async def test_e2a_fix_preexisting_header_subclass_rejected_without_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import inspect

    requests: list[httpx.Request] = []
    callbacks: list[int] = []
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: pytest.fail("retired during observation then sent")))
    original_build = client._build_request

    def build(path: str, params: list[tuple[str, str]], *, signed: bool) -> httpx.Request:
        request = original_build(path, params, signed=signed)
        requests.append(request)
        return request

    monkeypatch.setattr(client, "_build_request", build)

    class ObservedHeaders(httpx.Headers):
        def get_list(self, key: str, split_commas: bool = False) -> list[str]:
            if key == "accept-encoding":
                callbacks.append(budget.physical_attempts)
                client._risk_generation = None
            return super().get_list(key, split_commas=split_commas)

    def mutate() -> None:
        if requests and any(frame.function == "before_attempt" for frame in inspect.stack()):
            requests[-1].headers = ObservedHeaders(requests[-1].headers)

    budget = MonitoringReadBudget(guard=mutate)
    with budget.reuse_or_open_receipt() as group, pytest.raises(
        InstrumentSourceError, match="ORIGIN_MISMATCH"
    ):
        await read(client, budget, group)
    await client.aclose()
    assert callbacks == [] and budget.physical_attempts == 1
    assert client._operation.get() is None


@pytest.mark.parametrize("stage", ["entry", "final"])
async def test_e2a_fix_real_closed_state_retains_closed_error(stage: str) -> None:
    import inspect

    from httpx._client import ClientState

    stream = Stream()
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream)))
    if stage == "entry":
        await client._client.aclose()
    budget = MonitoringReadBudget()

    def guard() -> None:
        names = {frame.function for frame in inspect.stack()}
        if (stage == "final" and stream.closes == 1 and "read_risk_limit_envelope" in names
                and "_read" not in names):
            client._client._state = ClientState.CLOSED

    with budget.reuse_or_open_receipt() as group, pytest.raises(
        InstrumentSourceError, match="CLOSED"
    ):
        await client.read_risk_limit_envelope(
            KEY, cursor="", budget=budget, group=group, deadline=None, owner_guard=guard)
    await client.aclose()
    assert client._operation.get() is None
    assert budget.physical_attempts == stream.closes == (stage == "final")



@pytest.mark.parametrize("mutation", [
    "request_type", "url_type", "headers_type", "method_type", "reference_type",
    "reference_subclass", "url_scheme_type", "url_userinfo_type", "url_host_type",
    "url_port_type", "url_path_type", "url_query_type", "url_fragment_type",
    "header_list_type", "header_entry_type", "header_raw_name_type",
    "header_lookup_name_type", "header_value_type", "header_encoding_type",
    "header_count", "aggregate_bytes", "method_value", "url_scheme_value",
    "url_userinfo_value", "url_host_value", "url_port_value", "url_path_value",
    "url_query_value", "url_fragment_value", "header_raw_name_value",
    "header_lookup_name_value", "header_value_value", "header_encoding_value",
    "url_replacement", "headers_replacement", "reference_shape",
    "missing_method", "missing_reference", "missing_header_list", "missing_header_encoding",
])
async def test_e2a_fix_final_wire_leaves_reject_without_open_callbacks_or_send(
    mutation: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections.abc import Iterator

    from httpx._urlparse import ParseResult

    events: list[str] = []
    requests: list[httpx.Request] = []

    class OpenStr(str):
        def __eq__(self, other: object) -> bool:
            events.append("str equality")
            return True

        def __ne__(self, other: object) -> bool:
            events.append("str inequality")
            return False

        __hash__ = str.__hash__

    class OpenBytes(bytes):
        def lower(self) -> bytes:
            events.append("bytes lower")
            return super().lower()

        def decode(self, encoding: str = "utf-8", errors: str = "strict") -> str:
            events.append("bytes decode")
            return super().decode(encoding, errors)

    class OpenList(list[tuple[bytes, bytes, bytes]]):
        def __iter__(self) -> Iterator[tuple[bytes, bytes, bytes]]:
            events.append("header list iteration")
            return super().__iter__()

    class OpenTuple(tuple[bytes, ...]):
        def __iter__(self) -> Iterator[bytes]:
            events.append("header tuple iteration")
            return super().__iter__()

    class OpenRequest(httpx.Request):
        def __getattribute__(self, name: str) -> object:
            events.append("request attribute")
            return super().__getattribute__(name)

    class OpenURL(httpx.URL):
        @property
        def scheme(self) -> str:
            events.append("URL property")
            return super().scheme

    class OpenHeaders(httpx.Headers):
        def get_list(self, key: str, split_commas: bool = False) -> list[str]:
            events.append("header method")
            return super().get_list(key, split_commas=split_commas)

    class OpenReference(ParseResult):
        def __iter__(self) -> Iterator[str | int | None]:
            events.append("reference iteration")
            return super().__iter__()

    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: pytest.fail("changed/open wire leaf reached transport")))
    original_build = client._build_request

    def build(path: str, params: list[tuple[str, str]], *, signed: bool) -> httpx.Request:
        request = original_build(path, params, signed=signed)
        requests.append(request)
        return request

    monkeypatch.setattr(client, "_build_request", build)
    budget = MonitoringReadBudget()

    def guard() -> None:
        if budget.physical_attempts != 1:
            return
        request = requests[-1]
        reference = request.url._uri_reference
        if mutation == "missing_method":
            del request.method
        elif mutation == "missing_reference":
            del request.url._uri_reference
        elif mutation == "missing_header_list":
            del request.headers._list
        elif mutation == "missing_header_encoding":
            del request.headers._encoding
        elif mutation == "reference_shape":
            request.url._uri_reference = tuple.__new__(ParseResult, ())
        elif mutation == "request_type":
            request.__class__ = OpenRequest
        elif mutation == "url_type":
            request.url.__class__ = OpenURL
        elif mutation == "headers_type":
            request.headers.__class__ = OpenHeaders
        elif mutation == "method_type":
            request.method = OpenStr("GET")
        elif mutation == "reference_type":
            request.url._uri_reference = cast(ParseResult, object())
        elif mutation == "reference_subclass":
            request.url._uri_reference = OpenReference(*reference)
        elif mutation.startswith("url_") and mutation.endswith(("_type", "_value")):
            field = mutation[4:].rsplit("_", 1)[0]
            if mutation.endswith("_type"):
                value: object = True if field == "port" else OpenStr("x")
            else:
                value = 444 if field == "port" else "changed"
            replace = cast(Callable[..., ParseResult], reference._replace)
            request.url._uri_reference = replace(**{field: value})
        elif mutation == "header_list_type":
            request.headers._list = OpenList(request.headers._list)
        elif mutation == "header_entry_type":
            request.headers._list[0] = cast(tuple[bytes, bytes, bytes],
                                             OpenTuple(request.headers._list[0]))
        elif mutation in ("header_raw_name_type", "header_lookup_name_type", "header_value_type",
                          "header_raw_name_value", "header_lookup_name_value", "header_value_value"):
            index = 0 if "raw_name" in mutation else 1 if "lookup_name" in mutation else 2
            entry = list(request.headers._list[0])
            entry[index] = OpenBytes(entry[index]) if mutation.endswith("_type") else b"changed"
            request.headers._list[0] = (entry[0], entry[1], entry[2])
        elif mutation == "header_encoding_type":
            request.headers._encoding = OpenStr("ascii")
        elif mutation == "header_encoding_value":
            request.headers._encoding = "utf-8"
        elif mutation == "header_count":
            request.headers._list.extend([(b"x", b"x", b"y")] * 65)
        elif mutation == "aggregate_bytes":
            request.headers._list.append((b"x", b"x", b"y" * 65536))
        elif mutation == "method_value":
            request.method = "POST"
        elif mutation == "url_replacement":
            request.url = httpx.URL(request.url)
        else:
            assert mutation == "headers_replacement"
            request.headers = httpx.Headers(request.headers)

    with budget.reuse_or_open_receipt() as group, pytest.raises(
        InstrumentSourceError, match="ORIGIN_MISMATCH"
    ):
        await client.read_risk_limit_envelope(
            KEY, cursor="", budget=budget, group=group, deadline=None, owner_guard=guard)
    assert events == [] and budget.physical_attempts == 1
    assert client._operation.get() is None
    await client.aclose()


async def test_e2a_fix_unchanged_wire_with_higher_guard_keeps_positive_capture() -> None:
    sent: list[httpx.Request] = []
    guards: list[int] = []
    stream = Stream()

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, stream=stream)

    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(handler))
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group:
        envelope = await client.read_risk_limit_envelope(
            KEY, cursor="", budget=budget, group=group, deadline=None,
            owner_guard=lambda: guards.append(budget.physical_attempts))
    await client.aclose()
    assert envelope.decoded_body_bytes == RAW and guards
    assert len(sent) == budget.physical_attempts == stream.closes == 1
    assert client._operation.get() is None



async def test_e2a_fix_raw_credential_name_cannot_hide_behind_normalized_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: pytest.fail("hidden raw credential header sent")))
    original_build = client._build_request

    def build(path: str, params: list[tuple[str, str]], *, signed: bool) -> httpx.Request:
        request = original_build(path, params, signed=signed)
        raw_name, lookup_name, value = request.headers._list[0]
        assert raw_name.lower() == lookup_name
        request.headers._list[0] = (b"X-BAPI-SIGN", lookup_name, value)
        return request

    monkeypatch.setattr(client, "_build_request", build)
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group, pytest.raises(
        InstrumentSourceError, match="ORIGIN_MISMATCH"
    ):
        await read(client, budget, group)
    await client.aclose()
    assert budget.physical_attempts == 1 and client._operation.get() is None



@pytest.mark.parametrize("change,expected", [
    ("retire", "RETIRED"), ("wire", "ORIGIN_MISMATCH"),
])
async def test_e2a_fix_controlled_pre_guard_observation_is_bracketed(
    change: str, expected: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.account.instrument_envelope import HttpsOrigin

    # Controlled observation-time test hook over an exact closed request. This
    # models ordering; it does not qualify attacks on global library methods.
    callbacks: list[int] = []
    client = module.BybitReadOnlyClient.for_public_instruments(
        source_environment="PUBLIC_MAINNET", transport=httpx.MockTransport(
            lambda request: pytest.fail("observation changed owner/wire then sent")))
    budget = MonitoringReadBudget()
    original_observation = module._RiskRoute.request_origin

    def observe(route: module._RiskRoute, request: httpx.Request) -> HttpsOrigin:
        origin = original_observation(route, request)
        if budget.physical_attempts == 1:
            callbacks.append(1)
            if change == "retire":
                client._risk_generation = None
            else:
                request.url = request.url.copy_with(path="/v5/order/create")
        return origin

    monkeypatch.setattr(module._RiskRoute, "request_origin", observe)
    with budget.reuse_or_open_receipt() as group, pytest.raises(
        InstrumentSourceError, match=expected
    ):
        await read(client, budget, group)
    await client.aclose()
    assert callbacks == [1] and budget.physical_attempts == 1
    assert client._operation.get() is None
