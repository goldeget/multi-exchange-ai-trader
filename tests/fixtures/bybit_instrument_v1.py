"""Shared offline Bybit instrument response factory."""


def row(product: str, symbol: str = "BTCUSDT") -> dict[str, object]:
    value: dict[str, object] = {
        "symbol": symbol,
        "baseCoin": symbol[:-4],
        "quoteCoin": "USDT",
        "status": "Trading",
        "priceFilter": {"tickSize": "0.10"},
        "lotSizeFilter": {
            "basePrecision": "0.001",
            "quotePrecision": "0.01",
            "minOrderAmt": "5",
            "maxLimitOrderQty": "100",
            "maxMarketOrderQty": "50",
        },
    }
    if product == "linear":
        value |= {
            "settleCoin": "USDT",
            "contractType": "LinearPerpetual",
            "priceScale": "2",
            "isPreListing": False,
            "priceFilter": {"tickSize": "0.10", "minPrice": "0.1", "maxPrice": "1000000"},
            "lotSizeFilter": {
                "qtyStep": "0.001",
                "minOrderQty": "0.001",
                "maxOrderQty": "100",
                "maxMktOrderQty": "50",
                "minNotionalValue": "5",
            },
            "leverageFilter": {"minLeverage": "1", "maxLeverage": "100", "leverageStep": "0.01"},
        }
    return value

