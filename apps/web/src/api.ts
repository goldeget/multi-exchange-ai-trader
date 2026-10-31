import { useEffect, useState } from "react";
export type Venue = "binance" | "bybit";
export type Market = "spot" | "perpetual";
export interface Ticker {
  exchange: string;
  symbol: string;
  market_type: Market;
  last_price: string;
  observed_at: string | null;
  fetched_at: string;
  source: string;
}
export interface Candle {
  timestamp: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  is_closed: boolean;
}
export type TerminalReason = "operator_disabled" | "access_restricted" | "geographic_restriction";
const terminalMessages: Record<TerminalReason, string> = {
  operator_disabled: "이 시세는 꺼져 있습니다. 다시 요청하지 않습니다.",
  access_restricted: "거래소가 시세 조회를 막았습니다. 다시 요청하지 않습니다.",
  geographic_restriction: "이 지역에서는 시세를 받을 수 없습니다. 다시 요청하지 않습니다.",
};
function terminalReason(value: unknown): value is TerminalReason {
  return value === "operator_disabled" || value === "access_restricted" || value === "geographic_restriction";
}
export interface Resource<T> {
  blocked?: TerminalReason;
  data?: T;
  error?: string;
  successAt?: number;
}
export const sampleTime = "2026-01-01T00:00:00Z";
export function sampleCandles(symbol: string, interval: string): Candle[] {
  const base = symbol === "BTCUSDT" ? 64000 : 3200;
  const minutes: Record<string, number> = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
  };
  return Array.from({ length: 80 }, (_, i) => {
    const open = base + Math.sin(i / 5) * base * 0.008 + i * base * 0.0001;
    const close = open + Math.sin(i + 1) * base * 0.002;
    return {
      timestamp: new Date(
        Date.parse(sampleTime) + i * minutes[interval] * 60000,
      ).toISOString(),
      open: String(open),
      high: String(Math.max(open, close) + base * 0.001),
      low: String(Math.min(open, close) - base * 0.001),
      close: String(close),
      volume: String(10 + (i % 17)),
      is_closed: i !== 79,
    };
  });
}
export function usePolling<T>(
  url: string,
  interval: number,
  enabled: boolean,
): Resource<T> {
  const [result, setResult] = useState<{ url: string; value: Resource<T> }>({
    url: "",
    value: {},
  });
  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    let pending = false;
    let stopped = false;
    const controllers = new Set<AbortController>();
    async function load() {
      if (pending || stopped) return;
      pending = true;
      const controller = new AbortController();
      controllers.add(controller);
      const timeout = window.setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch(url, { signal: controller.signal });
        if (!response.ok) {
          const body: {reason?: unknown} = await response.json().catch(() => ({}));
          if (terminalReason(body.reason)) {
            const reason = body.reason;
            stopped = true;
            window.clearInterval(timer);
            if (!disposed) setResult(previous => ({url, value: {
              ...(previous.url === url ? previous.value : {}),
              blocked: reason, error: terminalMessages[reason],
            }}));
            return;
          }
          throw new Error("unavailable");
        }
        const data = (await response.json()) as T;
        if (!disposed)
          setResult({ url, value: { data, successAt: Date.now() } });
      } catch {
        if (!disposed)
          setResult((previous) => ({
            url,
            value: {
              ...(previous.url === url ? previous.value : {}),
              error: "연결 안 됨. 시세를 가져오지 못했습니다.",
            },
          }));
      } finally {
        window.clearTimeout(timeout);
        controllers.delete(controller);
        pending = false;
      }
    }
    const timer = window.setInterval(() => {
      void load();
    }, interval);
    void load();
    return () => {
      disposed = true;
      window.clearInterval(timer);
      controllers.forEach((controller) => controller.abort());
    };
  }, [url, interval, enabled]);
  return result.url === url ? result.value : {};
}
export function marketUrl(
  kind: "ticker" | "candles",
  venue: Venue,
  symbol: string,
  market: Market,
  interval?: string,
) {
  const params = new URLSearchParams({
    exchange: venue,
    symbol,
    market_type: market,
  });
  if (interval) {
    params.set("interval", interval);
    params.set("limit", "200");
  }
  return `/api/v1/market/${kind}?${params}`;
}
