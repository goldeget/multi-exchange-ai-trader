import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
vi.mock("./chart/CandleChart", () => ({
  default: () => <div aria-label="캔들 차트" />,
}));
const fetchMock = vi.fn();
const epoch = "2026-10-30T12:00:00Z";
function response(url: string) {
  const q = new URL(url, "http://localhost").searchParams;
  return Promise.resolve({
    ok: true,
    json: async () =>
      url.includes("/candles")
        ? [
            {
              timestamp: epoch,
              open: "100",
              high: "110",
              low: "90",
              close: "105",
              volume: "10",
              is_closed: false,
            },
          ]
        : {
            exchange: q.get("exchange"),
            symbol: q.get("symbol"),
            market_type: q.get("market_type"),
            last_price: q.get("symbol") === "ETHUSDT" ? "205" : "105",
            observed_at: epoch,
            fetched_at: epoch,
            source: "fixture",
          },
  });
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(epoch));
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
async function settle() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}
describe("공개 시세 대시보드", () => {
  it("preview is explicit and makes zero API requests", () => {
    window.history.replaceState({}, "", "/?preview=1");
    render(<App />);
    expect(screen.getByText("샘플 데이터 · 실제 시세 아님")).toBeVisible();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText(/형성 중/)).toBeVisible();
  });
  it("shows BTC and ETH overview prices for both venues and selects a chart row", async () => {
    fetchMock.mockImplementation(response);
    render(<App />);
    await settle();
    for (const venue of ["Binance", "Bybit"]) {
      for (const [symbol, price] of [
        ["BTC", "105.00"],
        ["ETH", "205.00"],
      ]) {
        const row = screen.getByRole("row", {
          name: new RegExp(`${venue}.*${symbol} / USDT`),
        });
        expect(within(row).getByText(price)).toBeVisible();
        expect(
          fetchMock.mock.calls.some(([url]) => {
            const requested = new URL(String(url), "http://localhost");
            return (
              requested.pathname.endsWith("/ticker") &&
              requested.searchParams.get("exchange") === venue.toLowerCase() &&
              requested.searchParams.get("symbol") === `${symbol}USDT`
            );
          }),
        ).toBe(true);
      }
    }
    fireEvent.click(
      screen.getByRole("button", { name: "BybitETH / USDT · 현물" }),
    );
    await settle();
    expect(screen.getByRole("combobox", { name: "종목" })).toHaveValue(
      "ETHUSDT",
    );
    expect(
      screen.getByRole("heading", { name: "ETH / USDT Bybit · 현물 · 15m" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "BybitETH / USDT · 현물" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getByRole("button", { name: "BybitBTC / USDT · 현물" }),
    ).toHaveAttribute("aria-pressed", "false");
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).includes("/candles?exchange=bybit&symbol=ETHUSDT"),
      ),
    ).toBe(true);
  });
  it("shows mocked live data and semantic controls", async () => {
    fetchMock.mockImplementation(response);
    render(<App />);
    await settle();
    expect(
      screen.getByText("공개 시세 전용 · PAPER는 별도 가상 화면"),
    ).toBeVisible();
    expect(screen.getAllByText("105.00").length).toBeGreaterThan(0);
    expect(screen.getByRole("table")).toBeVisible();
    expect(
      screen.getByRole("columnheader", { name: "거래소 / 종목" }),
    ).toBeVisible();
    expect(screen.getByRole("combobox", { name: "종목" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "시장" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "봉 간격" })).toBeVisible();
    expect(screen.getByLabelText("캔들 요약")).toHaveTextContent("형성 중");
  });
  it("disables OKX and never requests it even after polling", async () => {
    fetchMock.mockImplementation(response);
    render(<App />);
    await settle();
    expect(
      screen.getByRole("button", { name: "OKX · 검증 대기" }),
    ).toBeDisabled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(
      fetchMock.mock.calls.every(([url]) => !String(url).includes("okx")),
    ).toBe(true);
  });
  it("reports loading and errors honestly", async () => {
    fetchMock.mockRejectedValue(new Error("offline"));
    render(<App />);
    expect(screen.getAllByText("불러오는 중").length).toBeGreaterThan(0);
    await settle();
    expect(screen.getAllByText(/연결 오류/).length).toBeGreaterThan(0);
    expect(
      screen.queryByText("샘플 데이터 · 실제 시세 아님"),
    ).not.toBeInTheDocument();
  });
  it("marks retained data stale after 15 seconds and preserves observation time", async () => {
    fetchMock.mockImplementation(response);
    render(<App />);
    await settle();
    fetchMock.mockRejectedValue(new Error("offline"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(16000);
    });
    expect(screen.getAllByText(/지연 ·/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/12:00:00/).length).toBeGreaterThan(0);
  });
  it("prevents overlapping requests and aborts on unmount", async () => {
    fetchMock.mockImplementation(() => new Promise(() => {}));
    const view = render(<App />);
    const initial = fetchMock.mock.calls.length;
    expect(initial).toBeGreaterThan(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(initial);
    const signals = fetchMock.mock.calls.map(
      ([, opts]) => opts.signal as AbortSignal,
    );
    expect(signals.every((s) => !s.aborted)).toBe(true);
    view.unmount();
    expect(signals.every((s) => s.aborted)).toBe(true);
  });
  it("offers the Bybit account screen without calling it from the market view", async () => {
    fetchMock.mockImplementation(response);
    render(<App />);
    await settle();
    expect(screen.getByRole("button", { name: "Bybit 계좌 조회" })).toBeVisible();
    expect(fetchMock.mock.calls.every(([url]) => !String(url).includes("/account/"))).toBe(true);
  });
  it("changes the selected instrument using keyboard accessible controls", async () => {
    fetchMock.mockImplementation(response);
    render(<App />);
    await settle();
    fireEvent.change(screen.getByRole("combobox", { name: "종목" }), {
      target: { value: "ETHUSDT" },
    });
    await settle();
    expect(
      fetchMock.mock.calls.some(
        ([url]) =>
          String(url).includes("/candles?") &&
          String(url).includes("symbol=ETHUSDT"),
      ),
    ).toBe(true);
  });
});

it("keeps terminal restriction visible instead of replacing it with generic stale state", async()=>{
  fetchMock.mockImplementation(response);render(<App/>);await settle();
  fetchMock.mockResolvedValue({ok:false,json:async()=>({reason:"operator_disabled"})});
  await act(async()=>{await vi.advanceTimersByTimeAsync(65000);});
  expect(screen.getAllByText(/운영 설정으로 비활성화/).length).toBe(5);
  expect(screen.queryByText("지연 · 마지막 수신 캔들")).not.toBeInTheDocument();
  expect(screen.getAllByText(/12:00:00/).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/fixture/).length).toBeGreaterThan(0);
});
