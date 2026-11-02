import gzip

import httpx
import pytest

from multi_exchange_trader.account.bybit_readonly import BybitReadError, BybitReadOnlyClient


@pytest.mark.parametrize("body", [
    b'{"retCode":0,"retCode":1,"time":1,"result":{}}',
    b'{"retCode":0,"time":1,"result":{"leverage":NaN}}',
    b'{"retCode":0,"time":1,"result":{"leverage":"1","leverage":"2"}}',
    b'\xff', b'[' * 2000,
])
async def test_ambiguous_execution_json_is_refused(body):
    client = BybitReadOnlyClient(api_key="fake-key-only", api_secret="fake-secret-only",
        base_url="https://api-demo.bybit.com", environment="demo",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)))
    try:
        with pytest.raises(BybitReadError, match="invalid_json"):
            await client.read("/v5/market/time", [], signed=False)
    finally:
        await client.aclose()


async def test_stream_stops_and_closes_at_body_limit():
    class Stream(httpx.AsyncByteStream):
        closed = False
        chunks = 0

        async def __aiter__(self):
            for _ in range(20):
                self.chunks += 1
                yield b"x" * 100_000

        async def aclose(self):
            self.closed = True

    stream = Stream()
    client = BybitReadOnlyClient(api_key="fake-key-only", api_secret="fake-secret-only",
        base_url="https://api-demo.bybit.com", environment="demo",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream)))
    try:
        with pytest.raises(BybitReadError, match="response_too_large"):
            await client.read("/v5/market/time", [], signed=False)
        assert stream.chunks == 11
        assert stream.closed
    finally:
        await client.aclose()


@pytest.mark.parametrize("oversized", [False, True])
async def test_compressed_stream_is_decoded_once_and_bounded(oversized):
    body = b'{"retCode":0,"time":1,"result":{"list":[]}}'
    if oversized:
        body = b" " * 1_100_000 + body
    compressed = gzip.compress(body)

    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            for offset in range(0, len(compressed), 11):
                yield compressed[offset:offset + 11]

        async def aclose(self):
            self.closed = True

    stream = Stream()
    client = BybitReadOnlyClient(api_key="fake-key-only", api_secret="fake-secret-only",
        base_url="https://api-demo.bybit.com", environment="demo",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream,
            headers={"Content-Encoding": "gzip", "Content-Length": str(len(compressed))})))
    try:
        if oversized:
            with pytest.raises(BybitReadError, match="response_too_large"):
                await client.read("/v5/market/time", [], signed=False)
        else:
            assert await client.read("/v5/market/time", [], signed=False) == ({"list": []}, 1)
        assert stream.closed
    finally:
        await client.aclose()
