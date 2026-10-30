import httpx
import pytest

from multi_exchange_trader.market_data.adapters.base import MarketDataUnavailable
from multi_exchange_trader.market_data.adapters.okx import OKXAdapter


@pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT"])
@pytest.mark.parametrize("market", ["spot", "perpetual"])
@pytest.mark.parametrize("kind", ["ticker", "candles"])
async def test_unverified_okx_is_unavailable_without_any_request(symbol, market, kind):
    calls = []

    def fail_on_call(request):
        calls.append(request)
        pytest.fail("Unverified OKX adapter attempted transport")

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail_on_call)) as client:
        adapter = OKXAdapter(client)
        with pytest.raises(MarketDataUnavailable) as caught:
            if kind == "ticker":
                await adapter.get_ticker(symbol, market)
            else:
                await adapter.get_candles(symbol, market, "1m", 200)
        assert caught.value.reason == "unverified"
        assert "unverified" in str(caught.value).lower()
        assert calls == []
