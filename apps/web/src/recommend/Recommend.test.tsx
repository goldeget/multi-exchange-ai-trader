import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Recommend from "./Recommend";

const card = {
  번호: "a".repeat(32),
  상태: "대기",
  거래소: "바이비트",
  종목: "BTC / USDT",
  상품: "선물",
  방향: "오르면 이익",
  시세: "방금 마감된 봉",
  진입: "129",
  손절: "125",
  익절: "137",
  수량: "0.001",
  레버리지: "2배",
  이유: "짧은 평균이 긴 평균보다 높습니다.",
  반대이유: "규칙으로 만든 설명입니다. 유료 분석이 아닙니다.",
  설명: "규칙 설명",
  만료: "2026-11-01T12:15:00+00:00",
  승인가능: true,
  안내: "수량과 레버리지는 규칙이 정합니다. 승인해도 가상 장부에만 들어갑니다.",
};

function payload(next = card, ok = true) {
  return {
    ok,
    json: async () => ({
      카드: next,
      메시지: ok ? "" : "만료된 추천입니다. 주문은 없습니다.",
      실계좌주문: false,
      가상반영: false,
    }),
  };
}

beforeEach(() => {
  vi.useFakeTimers({toFake:['Date']});
  vi.setSystemTime(new Date('2026-11-01T12:10:00Z'));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("추천", () => {
  it("shows a rule card in Korean and approves with an empty request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(payload());
    vi.stubGlobal("fetch", fetchMock);
    render(<Recommend />);
    expect(await screen.findByText("규칙 설명")).toBeVisible();
    for (const label of ["거래소", "종목", "현물/선물"]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    for (const label of [
      "방향",
      "시세",
      "진입",
      "손절",
      "익절",
      "수량",
      "레버리지",
      "이유",
      "하지 않을 이유",
      "설명",
      "만료",
    ]) {
      expect(screen.getByText(label)).toBeVisible();
    }
    expect(screen.getByText("2배")).toBeVisible();
    expect(screen.getByText(/실계좌로 주문을 보내지 않습니다/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "연습 계좌에 넣고 바로 닫기" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/approve"))).toBe(true);
    });
    const approve = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/approve"));
    expect(approve?.[1]).toMatchObject({ method: "POST", body: "{}" });
    expect(String(approve?.[1]?.body)).not.toMatch(/leverage|quantity|50/);
    fireEvent.click(screen.getByRole("button", { name: "거절" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/reject"))).toBe(true);
    });
    const rejected = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/reject"));
    expect(rejected?.[1]).toMatchObject({ method: "POST", body: "{}" });
  });

  it("does not send an approval after expiry", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      payload({ ...card, 상태: "만료", 승인가능: false }, false),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<Recommend />);
    const button = await screen.findByRole("button", { name: "연습 계좌에 넣고 바로 닫기" });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/approve"))).toBe(false);
  });

  it('disables approval when an initially valid card expires as time passes',async()=>{
    vi.useRealTimers();vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-11-01T12:10:00Z'));
    const fetchMock=vi.fn().mockResolvedValue(payload());
    vi.stubGlobal('fetch',fetchMock);
    await act(async()=>{render(<Recommend/>)});
    const button=screen.getByRole('button',{name:'연습 계좌에 넣고 바로 닫기'});
    expect(button).toBeEnabled();
    act(()=>vi.advanceTimersByTime(5*60*1000));
    expect(button).toBeDisabled();
    expect(screen.getByText('추천 유효시간이 지났습니다. 새로 확인하세요.')).toBeVisible();
    fireEvent.click(button);
    expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/approve'))).toBe(false);
  });
});

 it('does not show an old approval on a newly selected recommendation',async()=>{
 let resolveApproval!:(value:ReturnType<typeof payload>)=>void;
 const fetchMock=vi.fn().mockImplementation((url:string)=>String(url).endsWith('/approve')?new Promise(r=>{resolveApproval=r}):Promise.resolve(payload(String(url).endsWith('/preview')&&fetchMock.mock.calls.length>2?{...card,번호:'b'.repeat(32),종목:'ETH / USDT'}:card)));
 vi.stubGlobal('fetch',fetchMock);render(<Recommend/>);
 await screen.findByText('규칙 설명');fireEvent.click(screen.getByRole('button',{name:'연습 계좌에 넣고 바로 닫기'}));
 fireEvent.change(screen.getByRole('combobox',{name:'종목'}),{target:{value:'ETH / USDT'}});
 await waitFor(()=>expect(screen.getAllByText('ETH / USDT').length).toBeGreaterThan(1));
 resolveApproval(payload({...card,상태:'승인'}));
 await waitFor(()=>expect(screen.queryByText('승인')).not.toBeInTheDocument());
 expect(screen.getAllByText('ETH / USDT').length).toBeGreaterThan(1);
 });
