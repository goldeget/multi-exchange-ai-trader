import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import RecommendationHistory, { HISTORY_FIXTURE } from "./RecommendationHistory";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const reply = (body: unknown) => ({ ok: true, json: async () => body });

describe("saved recommendation history", () => {
  it("injects the same example history without fetching or executing an order", () => {
    const fetcher = vi.fn(), selected = vi.fn(); vi.stubGlobal("fetch", fetcher);
    render(<RecommendationHistory demo onSelect={selected} />);
    fireEvent.click(screen.getAllByRole("button", { name: /추천 열기/ })[0]);
    expect(selected).toHaveBeenCalledWith(expect.objectContaining({ 번호: HISTORY_FIXTURE[0].id }));
    expect(screen.getByText(/고정 예시 목록/)).toBeVisible();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("fetches and checks the selected detail before forwarding its card", async () => {
    const entry = HISTORY_FIXTURE[0], selected = vi.fn(), pending = vi.fn();
    const fetcher = vi.fn().mockResolvedValueOnce(reply({ recommendations: [entry] }))
      .mockResolvedValueOnce(reply({ recommendation: entry }));
    vi.stubGlobal("fetch", fetcher);
    render(<RecommendationHistory onSelect={selected} onSelectionPending={pending} />);
    fireEvent.click(await screen.findByRole("button", { name: /추천 열기/ }));
    await waitFor(() => expect(selected).toHaveBeenCalledTimes(1));
    expect(pending).toHaveBeenCalledTimes(1);
    expect(pending.mock.invocationCallOrder[0]).toBeLessThan(selected.mock.invocationCallOrder[0]);
    expect(fetcher.mock.calls[1][0]).toBe(`/api/v1/recommendations/${entry.id}`);
    expect(fetcher.mock.calls.every(([, options]) => options.method === "GET")).toBe(true);
    expect(screen.getByText("RULE · 규칙 설명")).toBeVisible();
  });

  it.each([
    { source: "UNKNOWN" }, { snapshot: {} }, { execution: "LIVE" },
    { approval_effects: [{ execution: "DEMO", kind: "UNKNOWN" }] },
  ])("does not expose a malformed list as selectable history: %j", async change => {
    const selected = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(reply({ recommendations: [{ ...HISTORY_FIXTURE[0], ...change }] })));
    render(<RecommendationHistory onSelect={selected} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("저장된 추천 목록을 확인하지 못했습니다");
    expect(screen.queryByRole("button", { name: /추천 열기/ })).not.toBeInTheDocument();
    expect(selected).not.toHaveBeenCalled();
  });

  it("rejects a detail belonging to a different recommendation", async () => {
    const selected = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(reply({ recommendations: HISTORY_FIXTURE }))
      .mockResolvedValueOnce(reply({ recommendation: HISTORY_FIXTURE[1] })));
    render(<RecommendationHistory onSelect={selected} />);
    fireEvent.click((await screen.findAllByRole("button", { name: /추천 열기/ }))[0]);
    expect(await screen.findByRole("alert")).toHaveTextContent("선택한 추천 상세를 확인하지 못했습니다");
    expect(selected).not.toHaveBeenCalled();
  });

  it("keeps a known unresolved DEMO effect visible and forwards no approval permission", async () => {
    const original = HISTORY_FIXTURE[0], expiresAt = "2099-01-01T00:00:00Z", selected = vi.fn();
    const entry = { ...original, expiresAt, snapshot: { ...original.snapshot, expires_at: expiresAt },
      card: { ...original.card, 만료: expiresAt, 승인가능: true, 연습주문상태: "연습 결과 확인 불가" },
      approvable: true, approval_blocked: true, approval_state: "DEMO_UNCERTAIN",
      approval_effects: [...original.approval_effects, { execution: "DEMO", kind: "FIXED_CONNECTIVITY_ROUND_TRIP", symbol: "BTCUSDT", market_type: "perpetual", side: "BUY_THEN_REDUCE_ONLY_SELL", quantity: "0.001", follows_recommendation_side: false, verified_closed: false, registered: true }] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(reply({ recommendations: [entry] }))
      .mockResolvedValueOnce(reply({ recommendation: entry })));
    render(<RecommendationHistory onSelect={selected} />);
    fireEvent.click(await screen.findByRole("button", { name: /추천 열기/ }));
    await waitFor(() => expect(selected).toHaveBeenCalledWith(expect.objectContaining({ 승인가능: false })));
    expect(screen.getByText(/추천 방향과 독립적인 BTCUSDT 0.001/)).toBeVisible();
    expect(screen.getByText(/외부 요청 상태를 확인할 때까지 승인할 수 없습니다/)).toBeVisible();
  });

  it("ignores an older detail after a newer selection and aborts its read", async () => {
    const selected = vi.fn(), finish: Array<(value: unknown) => void> = [];
    const fetcher = vi.fn().mockResolvedValueOnce(reply({ recommendations: HISTORY_FIXTURE }))
      .mockImplementation(() => new Promise(resolve => finish.push(resolve)));
    vi.stubGlobal("fetch", fetcher);
    render(<RecommendationHistory onSelect={selected} />);
    const buttons = await screen.findAllByRole("button", { name: /추천 열기/ });
    fireEvent.click(buttons[0]); fireEvent.click(buttons[1]);
    expect((fetcher.mock.calls[1][1].signal as AbortSignal).aborted).toBe(true);
    await act(async () => finish[1](reply({ recommendation: HISTORY_FIXTURE[1] })));
    await act(async () => finish[0](reply({ recommendation: HISTORY_FIXTURE[0] })));
    expect(selected).toHaveBeenCalledTimes(1);
    expect(selected).toHaveBeenCalledWith(expect.objectContaining({ 번호: HISTORY_FIXTURE[1].id, 승인가능: false }));
  });
});
