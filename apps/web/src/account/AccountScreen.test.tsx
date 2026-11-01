import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Account from "./Account";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("shared account screen", () => {
  it("injects the fixed example without calling any account endpoint", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    render(<Account demo polished />);
    expect(await screen.findByText("12,498.00 USD")).toBeVisible();
    expect(screen.getAllByText("0.000010 BTC").length).toBeGreaterThan(0);
    expect(screen.getByText("디자인 참조 · 예시 계좌")).toBeVisible();
    expect(screen.getAllByText(/프로그램 관리 안 함/).length).toBeGreaterThan(0);
    expect(fetcher).not.toHaveBeenCalled();
    fireEvent.click(screen.getAllByRole("button", { name: "예시 다시 보기" })[0]);
    expect(screen.getByText(/고정 예시를 다시 표시했습니다/)).toBeVisible();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("uses the same view for live data and never relabels aggregate USD as USDT", async () => {
    const { ACCOUNT_FIXTURE } = await import("./fixture");
    const current = { ...ACCOUNT_FIXTURE, 수집완료시각: new Date().toISOString(), 수신시각: new Date().toISOString() };
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => current });
    vi.stubGlobal("fetch", fetcher);
    render(<Account polished pollIntervalMs={60_000} />);
    expect(await screen.findByText("12,498.00 USD")).toBeVisible();
    expect(screen.queryByText("12,498.00 USDT")).not.toBeInTheDocument();
    expect(screen.queryByText("디자인 참조 · 예시 계좌")).not.toBeInTheDocument();
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    expect(screen.getAllByText("0.000010 BTC").length).toBeGreaterThan(0);
    expect(screen.getByRole("columnheader", { name: "자산 순잔고" })).toBeVisible();
    expect(screen.queryByRole("columnheader", { name: "평가금액 (USD)" })).not.toBeInTheDocument();
  });

  it("shares one pending read across manual refresh buttons", async () => {
    const { ACCOUNT_FIXTURE } = await import("./fixture");
    let finish: (value: unknown) => void = () => undefined;
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ACCOUNT_FIXTURE })
      .mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    vi.stubGlobal("fetch", fetcher);
    render(<Account polished pollIntervalMs={60_000} />);
    expect(await screen.findByText("12,498.00 USD")).toBeVisible();
    const buttons = screen.getAllByRole("button", { name: "새로고침" });
    fireEvent.click(buttons[0]);
    fireEvent.click(buttons[1]);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(buttons[0]).toBeDisabled();
    expect(buttons[1]).toBeDisabled();
    await act(async () => finish({ ok: true, json: async () => ACCOUNT_FIXTURE }));
    await waitFor(() => expect(buttons[0]).not.toBeDisabled());
  });

  it("aborts an old live read when switching to the fixture and ignores its late response", async () => {
    const { ACCOUNT_FIXTURE } = await import("./fixture");
    let finish: (value: unknown) => void = () => undefined;
    const fetcher = vi.fn().mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    vi.stubGlobal("fetch", fetcher);
    const { rerender } = render(<Account polished />);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    const signal = fetcher.mock.calls[0][1].signal as AbortSignal;
    rerender(<Account polished demo />);
    expect(signal.aborted).toBe(true);
    await act(async () => finish({ ok: true, json: async () => ({ ...ACCOUNT_FIXTURE, 잔고: null }) }));
    expect(screen.getByText("12,498.00 USD")).toBeVisible();
    expect(screen.getByText("디자인 참조 · 예시 계좌")).toBeVisible();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("preserves precision for balances too small or too large for floating point formatting", async () => {
    const { ACCOUNT_FIXTURE } = await import("./fixture");
    const fixture = { ...ACCOUNT_FIXTURE, 잔고: { ...ACCOUNT_FIXTURE.잔고!, 코인: [
      { 코인: "XAUT", 지갑잔고: "0.0000000000000000001", 자산평가: null },
      { 코인: "EUR", 지갑잔고: "12345678901234567890.12345678", 자산평가: null },
    ] } };
    render(<Account polished fixture={fixture} />);
    expect(await screen.findByText("0.0000000000000000001 XAUT")).toBeVisible();
    expect(screen.getByText("12,345,678,901,234,567,890.12345678 EUR")).toBeVisible();
  });
});
