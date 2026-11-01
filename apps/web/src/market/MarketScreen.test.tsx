import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import MarketDashboard from "./MarketDashboard";

vi.mock("../chart/CandleChart", () => ({ default: ({ candles }: { candles: unknown[] }) => <div aria-label="캔들 차트">{candles.length}개 봉</div> }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.history.replaceState({}, "", "/"); });

describe("shared market screen", () => {
  it("uses injected examples without requests and changes instrument and timeframe", async () => {
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    render(<MarketDashboard demo polished />);
    expect(screen.getByTestId("selected-market-price")).toHaveTextContent("65,210.40 USDT");
    fireEvent.change(screen.getByRole("combobox", { name: "종목" }), { target: { value: "ETHUSDT" } });
    expect(screen.getByTestId("selected-market-price")).toHaveTextContent("2,150.20 USDT");
    fireEvent.click(screen.getByRole("button", { name: "4시간" }));
    expect(screen.getByRole("combobox", { name: "봉 간격" })).toHaveValue("4h");
    expect(screen.getByRole("button", { name: "4시간" })).toHaveAttribute("aria-pressed", "true");
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("renders the selected actual venue price without borrowing from another exchange", async () => {
    const fetcher = vi.fn().mockImplementation(async (url: string) => {
      const query = new URL(url, "http://localhost").searchParams;
      if (url.includes("/candles")) return { ok: true, json: async () => [] };
      if (query.get("exchange") === "bybit") return { ok: false, json: async () => ({ reason: "access_restricted" }) };
      return { ok: true, json: async () => ({ exchange: "binance", symbol: query.get("symbol"), market_type: "spot", last_price: "123.45", observed_at: new Date().toISOString(), fetched_at: new Date().toISOString(), source: "fixture" }) };
    });
    vi.stubGlobal("fetch", fetcher);
    render(<MarketDashboard polished />);
    await waitFor(() => expect(screen.getByTestId("selected-market-price")).toHaveTextContent("123.45 USDT"));
    fireEvent.change(screen.getByRole("combobox", { name: "거래소" }), { target: { value: "bybit" } });
    await waitFor(() => expect(screen.getByTestId("selected-market-price")).toHaveTextContent("—"));
    const bybit = screen.getByRole("row", { name: /바이비트.*BTC \/ USDT/ });
    expect(within(bybit).getByText(/거래소가 시세 조회를 막았습니다/)).toBeVisible();
    expect(screen.queryByText("예시 숫자 · 실제 시세 아님")).not.toBeInTheDocument();
    expect(fetcher.mock.calls.every(([url]) => String(url).includes("/market/"))).toBe(true);
  });

  it("aborts all actual reads on fixture switch and ignores late results", async () => {
    const finishes: Array<(value: unknown) => void> = [];
    const fetcher = vi.fn().mockImplementation(() => new Promise(resolve => finishes.push(resolve)));
    vi.stubGlobal("fetch", fetcher);
    const { rerender } = render(<MarketDashboard polished />);
    expect(fetcher).toHaveBeenCalledTimes(5);
    const signals = fetcher.mock.calls.map(([, options]) => options.signal as AbortSignal);
    rerender(<MarketDashboard demo polished />);
    expect(signals.every(signal => signal.aborted)).toBe(true);
    await act(async () => { for (const finish of finishes) finish({ ok: true, json: async () => [] }); });
    expect(screen.getByTestId("selected-market-price")).toHaveTextContent("65,208.90 USDT");
    expect(fetcher).toHaveBeenCalledTimes(5);
  });
});
