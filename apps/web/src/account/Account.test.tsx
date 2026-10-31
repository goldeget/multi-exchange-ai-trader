import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import Account, { dataAgeSeconds, freshnessLabel, isAccountView, type AccountView } from "./Account";

const nowStamp = new Date().toISOString();

const meta: Pick<
  AccountView,
  | "계좌마진모드"
  | "계좌마진설명"
  | "레버리지안내"
  | "순차조회"
  | "수집시작시각"
  | "수집완료시각"
  | "거래소응답시각"
  | "계정갱신시각"
  | "수신시각"
  | "캐시사용"
  | "캐시나이초"
  | "데이터나이초"
  | "동시요청공유"
  | "지연기준초"
  | "확인불가기준초"
  | "신선도표시"
  | "주문판단에사용"
> = {
  계좌마진모드: "확인 불가",
  계좌마진설명: "거래소 계정 정보입니다. 포지션의 예전 항목으로 짐작하지 않습니다.",
  레버리지안내: "종목 안내에 적힌 최대 배수입니다. 지금 포지션 크기에 실제로 허용되는 배수와 같지 않을 수 있습니다.",
  순차조회: true,
  수집시작시각: nowStamp,
  수집완료시각: nowStamp,
  거래소응답시각: nowStamp,
  계정갱신시각: "2023-10-12T02:49:06Z",
  수신시각: nowStamp,
  캐시사용: false,
  캐시나이초: 0,
  데이터나이초: 0,
  동시요청공유: false,
  지연기준초: 45,
  확인불가기준초: 120,
  신선도표시: "현재 확인 불가",
  주문판단에사용: false,
};

const blocked: AccountView = {
  연결상태: "차단됨",
  상태코드: "BLOCKED",
  환경: "메인넷",
  환경코드: "mainnet",
  구분: [
    "이 조회: 메인넷 (실계좌 읽기). 데모가 아닙니다.",
    "페이퍼: 아니오. PAPER 탭의 잔고는 가상 돈이며 이 화면과 다릅니다.",
  ],
  페이퍼: false,
  실거래켜짐: false,
  주문보냄: false,
  메시지: "BLOCKED. Bybit가 이 접속을 막았습니다.",
  서버시각: null,
  최대레버리지: null,
  잔고: null,
  포지션: null,
  미체결주문: null,
  오류코드: "BLOCKED",
  ...meta,
};

const ready: AccountView = {
  ...blocked,
  연결상태: "연결됨",
  상태코드: "ok",
  메시지: "계좌를 읽었습니다. 주문은 하지 않았습니다.",
  서버시각: "2026-11-01 00:00:00 UTC",
  오류코드: null,
  최대레버리지: { 종목: "BTCUSDT", 최대: "125.5", 최소: "1", 간격: "0.01" },
  잔고: {
    계정유형: "통합(UNIFIED)",
    총자산평가: "20.5",
    총지갑잔고: "20",
    총사용가능: "18",
    코인: [{ 코인: "USDT", 지갑잔고: "12.50", 자산평가: "12.50" }],
  },
  포지션: [
    {
      구분: "외부 · 관리 안 함",
      관리함: false,
      종목: "BTCUSDT",
      방향: "매수",
      수량: "0.01",
      레버리지: "3",
      진입가: "60000",
      표시가: "61000",
      청산가: "50000",
      미실현손익: "1.2",
      마진모드: "교차",
      포지션모드: "단방향",
    },
  ],
  미체결주문: [],
};

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Bybit 계좌 조회", () => {
  it("shows BLOCKED without inventing balances", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => blocked });
    vi.stubGlobal("fetch", fetchMock);
    render(<Account />);
    expect(await screen.findByText("연결이 막혔습니다. 잔고 숫자를 만들지 않았습니다.")).toBeVisible();
    expect(screen.getByText("잔고를 가져오지 못했습니다.")).toBeVisible();
    expect(screen.queryByText(/BLOCKED|PAPER|메인넷|데모/)).not.toBeInTheDocument();
    expect(screen.queryByText("12.50")).not.toBeInTheDocument();
    expect(screen.queryByText("125.5")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "주문" })).not.toBeInTheDocument();
  });

  it("shows a mainnet read and marks positions external", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ready });
    vi.stubGlobal("fetch", fetchMock);
    render(<Account />);
    expect((await screen.findAllByText("12.50")).length).toBeGreaterThan(0);
    expect(screen.getByText("외부에서 연 포지션")).toBeVisible();
    expect(screen.getByText("실계좌입니다. 연습 계좌가 아닙니다.")).toBeVisible();
    expect(screen.getByText("가상 거래가 아닙니다. 가상 거래 화면의 돈과 다릅니다.")).toBeVisible();
    expect(screen.getByText("실거래 꺼짐")).toBeVisible();
    expect(screen.getByText("미체결 주문이 없습니다.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "주문" })).not.toBeInTheDocument();
  });

  it("labels a demo account separately from mainnet and paper", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        ...ready,
        환경: "데모",
        환경코드: "demo",
        구분: ["이 조회: 데모. 메인넷 실계좌가 아닙니다."],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<Account />);
    expect(await screen.findByText("연습 계좌입니다. 실계좌가 아닙니다.")).toBeVisible();
    expect(screen.getByText("가상 거래가 아닙니다. 가상 거래 화면의 돈과 다릅니다.")).toBeVisible();
    expect(screen.queryByText(/데모|메인넷|PAPER/)).not.toBeInTheDocument();
  });

  it("does not invent numbers when the server cannot be reached", async () => {
    fetchMock.mockRejectedValue(new Error("offline"));
    vi.stubGlobal("fetch", fetchMock);
    render(<Account />);
    expect(await screen.findByText(/잔고를 추측하지 않습니다/)).toBeVisible();
    expect(screen.queryByText("12.50")).not.toBeInTheDocument();
  });

  it("opens from the account tab", async () => {
    window.history.replaceState({}, "", "/?view=account");
    fetchMock.mockResolvedValue({ ok: true, json: async () => blocked });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Bybit 잔고" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Bybit 잔고" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByText("연결이 막혔습니다. 잔고 숫자를 만들지 않았습니다.")).toBeVisible();
    expect(document.body.textContent ?? "").not.toMatch(/\b(BLOCKED|PAPER|DEMO|API)\b/);
  });

  it("does not start a second read until the first one finishes", async () => {
    let release: (value: { ok: boolean; json: () => Promise<AccountView> }) => void = () => undefined;
    const gate = new Promise<{ ok: boolean; json: () => Promise<AccountView> }>((resolve) => {
      release = resolve;
    });
    fetchMock.mockImplementation(() => gate);
    vi.stubGlobal("fetch", fetchMock);
    render(<Account pollIntervalMs={30} deadlineMs={5000} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 70));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    release({ ok: true, json: async () => ready });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it("ignores an older success after the screen has moved on", async () => {
    let resolveFirst: (value: { ok: boolean; json: () => Promise<AccountView> }) => void = () => undefined;
    fetchMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const first = render(<Account pollIntervalMs={60000} deadlineMs={5000} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    first.unmount();
    fetchMock.mockResolvedValue({ ok: false, status: 500 });
    render(<Account pollIntervalMs={60000} deadlineMs={5000} />);
    expect(await screen.findByText(/잔고를 추측하지 않습니다/)).toBeVisible();
    resolveFirst({ ok: true, json: async () => ready });
    await new Promise((resolve) => setTimeout(resolve, 40));
    expect(screen.queryByText("12.50")).not.toBeInTheDocument();
  });

  it("stops polling after the screen closes", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ready });
    vi.stubGlobal("fetch", fetchMock);
    const view = render(<Account pollIntervalMs={30} deadlineMs={5000} />);
    expect(await screen.findByText("방금 확인함")).toBeVisible();
    const calls = fetchMock.mock.calls.length;
    view.unmount();
    await new Promise((resolve) => setTimeout(resolve, 80));
    expect(fetchMock.mock.calls.length).toBe(calls);
  });

  it("refreshes when the screen becomes visible again", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ready });
    vi.stubGlobal("fetch", fetchMock);
    render(<Account pollIntervalMs={60000} deadlineMs={5000} />);
    expect((await screen.findAllByText("12.50")).length).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    document.dispatchEvent(new Event("visibilitychange"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it("stops a read that takes too long", async () => {
    fetchMock.mockImplementation((_url: string, init?: RequestInit) => {
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new DOMException("aborted", "AbortError"));
        });
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<Account pollIntervalMs={60000} deadlineMs={30} />);
    expect(await screen.findByText(/이번 조회를 멈췄습니다/)).toBeVisible();
    expect(screen.queryByText("12.50")).not.toBeInTheDocument();
  });

  it("rejects a broken balance instead of showing zero", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ ...ready, 잔고: { 계정유형: "통합 계좌", 총자산평가: "0" }, 포지션: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<Account pollIntervalMs={60000} />);
    expect(await screen.findByText(/형식이 맞지 않아/)).toBeVisible();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
    expect(screen.queryByText("열린 포지션이 없습니다.")).not.toBeInTheDocument();
  });

  it("marks an old successful read as a past value", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        ...ready,
        수집완료시각: "2020-01-01T00:00:00Z",
        신선도표시: "방금 확인함",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<Account pollIntervalMs={60000} />);
    expect(await screen.findByText("현재 확인 불가")).toBeVisible();
    expect(screen.getByText("과거 조회값")).toBeVisible();
    expect(screen.getAllByText("12.50").length).toBeGreaterThan(0);
    expect(screen.getByText(/주문 결정에 쓰지 않습니다/)).toBeVisible();
  });

  it("keeps the age rules next to the screen", () => {
    expect(freshnessLabel(10, "ok", 45, 120, false)).toBe("방금 확인함");
    expect(freshnessLabel(45, "ok", 45, 120, false)).toBe("갱신 지연");
    expect(freshnessLabel(120, "ok", 45, 120, false)).toBe("현재 확인 불가");
    expect(freshnessLabel(1, "ok", 45, 120, true)).toBe("현재 확인 불가");
    expect(dataAgeSeconds("2020-01-01T00:00:00Z", Date.parse("2020-01-01T00:01:00Z"))).toBe(60);
    expect(isAccountView({ ...ready, 포지션: {} })).toBe(false);
  });
});
