"""Offline public raw risk-page behavior; fake HTTPX ownership is not qualification."""

import asyncio
import hashlib
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
