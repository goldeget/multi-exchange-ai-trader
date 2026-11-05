"""Pinned public Bybit market routes and interval spellings."""

TICKER_URL = "https://api.bybit.com/v5/market/tickers"
CANDLE_URL = "https://api.bybit.com/v5/market/kline"
INTERVALS = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}
CATEGORIES = {"spot": "spot", "perpetual": "linear"}
