import type { Candle, Market, Ticker, Venue } from "../api";
import { fixtureCandles, fixtureClock } from "../visual/scenarios";

const quotes = {
  "bybit-BTCUSDT": "65210.40", "bybit-ETHUSDT": "2150.20",
  "binance-BTCUSDT": "65208.90", "binance-ETHUSDT": "2149.80",
};
export function fixtureTicker(venue: Venue, symbol: string, market: Market): Ticker {
  return { exchange: venue, symbol, market_type: market,
    last_price: quotes[`${venue}-${symbol}` as keyof typeof quotes],
    observed_at: "2026-11-01T00:10:00Z", fetched_at: fixtureClock, source: "예시 숫자" };
}

export function fixtureMarketCandles(symbol: string, interval: string): Candle[] {
  const minutes: Record<string, number> = { "1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440 };
  const lastClose = Number(fixtureCandles.at(-1)!.close);
  const target = symbol === "ETHUSDT" ? 2150.20 : 65210.40;
  const scale = target / lastClose;
  const end = Date.parse("2026-11-01T00:00:00Z");
  return fixtureCandles.map((candle, index) => ({ ...candle,
    timestamp: new Date(end - (fixtureCandles.length - 1 - index) * (minutes[interval] ?? 60) * 60_000).toISOString(),
    open: (Number(candle.open) * scale).toFixed(2), high: (Number(candle.high) * scale).toFixed(2),
    low: (Number(candle.low) * scale).toFixed(2), close: (Number(candle.close) * scale).toFixed(2),
  }));
}
