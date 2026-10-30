"""Local wire fixtures exercise the evidence CLI without external requests."""

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).parents[2] / "scripts/verify_phase2a_live.py"


def runner():
    assert SCRIPT.exists(), "Missing repeatable live evidence runner"
    spec = importlib.util.spec_from_file_location("live_runner", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def response(request, *, old=False, malformed=False):
    now = int(datetime.now(UTC).timestamp() * 1000)
    stamp = now - 600_000 if old else now - 30_000
    symbol = request.url.params["symbol"]
    candles = request.url.path.endswith(("klines", "kline"))
    if malformed:
        return httpx.Response(200, json={"unexpected": "schema"})
    if request.url.host == "api.bybit.com":
        rows = (
            [[str(stamp), "10", "12", "9", "11", "2", "22"]]
            if candles
            else [{"symbol": symbol, "lastPrice": "11"}]
        )
        result = {"category": request.url.params["category"], "list": rows}
        if candles:
            result["symbol"] = symbol
        body = {"retCode": 0, "retMsg": "OK", "time": stamp, "result": result, "retExtInfo": {}}
    elif candles:
        body = [[stamp, "10", "12", "9", "11", "2", stamp + 59_999, "22", 2, "1", "11", "0"]]
    else:
        body = {"symbol": symbol, "price": "11", "time": stamp}
    return httpx.Response(200, json=body)


@pytest.mark.asyncio
async def test_eight_markets_use_real_adapters_and_complete_evidence():
    report, code = await runner().run(httpx.MockTransport(response))
    assert code == 0
    assert len(report["markets"]) == 8
    assert len({(r["exchange"], r["market_type"], r["symbol"]) for r in report["markets"]}) == 8
    assert all(
        r["ticker"]["last_price"] == "11" and r["candles"]["count"] == 1 and r["status"] == "valid"
        for r in report["markets"]
    )
    assert report["started_at"] <= report["ended_at"]
    assert len(report["code_sha"]) == 40
    assert len(report["requests"]) == 16
    assert all(
        r["elapsed_ms"] >= 0
        and r["http_status"] == 200
        and r["host"] in {"api.bybit.com", "fapi.binance.com", "data-api.binance.vision"}
        for r in report["requests"]
    )


@pytest.mark.asyncio
async def test_connect_denial_stops_every_further_request_to_same_host():
    attempts = []

    def denied(request):
        attempts.append(request.url.host)
        raise httpx.ProxyError("403 Forbidden credentials=TOP_SECRET")

    report, code = await runner().run(httpx.MockTransport(denied))
    assert code == 1
    assert len(attempts) == 3
    assert len(set(attempts)) == 3
    assert all(r["status"] == "blocked" for r in report["markets"])
    assert "TOP_SECRET" not in json.dumps(report)
    assert {r["failure"] for r in report["requests"]} == {"host_denied"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("old", "stale"),
        ("malformed", "schema"),
        ("provider", "provider"),
        ("transport", "transport"),
    ],
)
async def test_failures_remain_distinguishable_and_never_pass(kind, expected):
    def fail(request):
        if kind == "transport":
            raise httpx.ConnectError("sensitive provider diagnostic")
        if kind == "provider":
            return httpx.Response(503, text="sensitive provider response")
        return response(request, old=kind == "old", malformed=kind == "malformed")

    report, code = await runner().run(httpx.MockTransport(fail))
    assert code == 1
    assert all(r["status"] == "failed" for r in report["markets"])
    assert all(
        expected in {r["ticker"]["status"], r["candles"]["status"]} for r in report["markets"]
    )
    assert "sensitive" not in json.dumps(report)


@pytest.mark.asyncio
async def test_empty_candles_are_schema_failure():
    def empty(request):
        reply = response(request)
        body = reply.json()
        if request.url.path.endswith(("klines", "kline")):
            if isinstance(body, list):
                body = []
            else:
                body["result"]["list"] = []
        return httpx.Response(200, json=body)

    report, code = await runner().run(httpx.MockTransport(empty))
    assert code == 1
    assert all(r["candles"]["status"] == "schema" for r in report["markets"])


def test_cli_writes_json_and_returns_failed_matrix_exit_code(tmp_path, monkeypatch):
    module = runner()
    actual_run = module.run

    async def offline_run():
        return await actual_run(
            httpx.MockTransport(
                lambda request: httpx.Response(503, json={"error": "provider unavailable"})
            )
        )

    target = tmp_path / "evidence.json"
    monkeypatch.setattr(module, "run", offline_run)
    monkeypatch.setattr("sys.argv", [str(SCRIPT), "--output", str(target)])
    assert module.main() == 1
    assert json.loads(target.read_text())["matrix_passed"] is False


@pytest.mark.asyncio
async def test_allowlist_rejects_unconfigured_routes_before_transport():
    module = runner()
    attempts = []

    def track(request):
        attempts.append(request)
        return httpx.Response(200, json={})

    async with module.EvidenceClient([], httpx.MockTransport(track)) as client:
        with pytest.raises(Exception, match="fixed public allowlist"):
            await client.get("https://api.bybit.com/v5/order/create")
    assert attempts == []


@pytest.mark.asyncio
async def test_candle_values_are_normalized_in_evidence():
    report, code = await runner().run(httpx.MockTransport(response))
    assert code == 0
    for row in report["markets"]:
        candle = row["candles"]["items"][0]
        assert (
            candle["open"],
            candle["high"],
            candle["low"],
            candle["close"],
            candle["volume"],
        ) == ("10", "12", "9", "11", "2")
        assert isinstance(candle["is_closed"], bool)
        assert candle["timestamp"].endswith("Z")


@pytest.mark.asyncio
async def test_timeouts_are_distinguished_from_other_transport_failures():
    def timeout(request):
        raise httpx.ReadTimeout("secret diagnostic")

    report, code = await runner().run(httpx.MockTransport(timeout))
    assert code == 1
    assert all(row["ticker"]["status"] == "timeout" for row in report["markets"])
    assert all(item["failure"] == "timeout" for item in report["requests"])


@pytest.mark.asyncio
async def test_nonpositive_ticker_is_schema_failure():
    def zero(request):
        reply = response(request)
        body = reply.json()
        if request.url.path.endswith(("price", "tickers")):
            if request.url.host == "api.bybit.com":
                body["result"]["list"][0]["lastPrice"] = "0"
            else:
                body["price"] = "0"
        return httpx.Response(200, json=body)

    report, code = await runner().run(httpx.MockTransport(zero))
    assert code == 1
    assert all(row["ticker"]["status"] == "schema" for row in report["markets"])


@pytest.mark.asyncio
@pytest.mark.parametrize("proxy", [True, False])
async def test_upstream_403_and_connect_403_are_distinguishable(proxy):
    def forbidden(request):
        if proxy:
            raise httpx.ProxyError("403 Forbidden")
        return httpx.Response(403, text="body omitted")

    report, code = await runner().run(httpx.MockTransport(forbidden))
    assert code == 1
    attempted = [item for item in report["requests"] if not item.get("skipped")]
    assert len(attempted) == 3
    assert all(
        item["denial_source"] == ("proxy_connect" if proxy else "upstream_http")
        for item in attempted
    )


@pytest.mark.asyncio
async def test_injected_transport_cannot_be_reported_as_live_verified():
    report, code = await runner().run(httpx.MockTransport(response))
    assert code == 0
    assert report["matrix_passed"] is True
    assert report["evidence_mode"] == "injected_transport"
    assert report["live_verified"] is False
