import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import Account, { type AccountView } from "./Account";

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
});
