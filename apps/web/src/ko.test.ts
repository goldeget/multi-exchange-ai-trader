import { describe, expect, it } from "vitest";
import {
  connectionLabel,
  environmentSentences,
  instrumentLabel,
  quoteSource,
  runStatusLabel,
  schedulerLabel,
  screenText,
  sideLabel,
} from "./ko";

describe("쉬운 한국어", () => {
  it("상태 코드를 화면 문장으로 바꿉니다", () => {
    expect(connectionLabel("ok")).toBe("연결됨");
    expect(connectionLabel("BLOCKED")).toBe("연결이 막혔습니다");
    expect(connectionLabel("error")).toBe("연결 안 됨");
    expect(runStatusLabel("RUNNING")).toBe("진행 중");
    expect(runStatusLabel("PAUSED")).toBe("일시정지");
    expect(runStatusLabel("PAUSED_RECOVERY")).toBe("다시 시작을 기다리는 중");
    expect(runStatusLabel("HALTED")).toBe("긴급 정지");
    expect(runStatusLabel("STOPPED")).toBe("종료");
    expect(schedulerLabel("FAIL_CLOSED")).toBe("자동 진행이 멈춤");
    expect(schedulerLabel("READY")).toBe("자동 진행 준비됨");
  });

  it("서버 문장에 남은 영어 코드를 숨깁니다", () => {
    expect(screenText("BLOCKED. Bybit가 이 접속을 막았습니다.")).not.toMatch(/BLOCKED/);
    expect(screenText("자동 loop 검증 실패")).toBe("자동 진행 확인에 실패했습니다");
    expect(screenText("2026-11-01 00:00:00 UTC")).toBe("2026-11-01 00:00:00 세계 표준시");
    expect(instrumentLabel("BTC-PAPER")).toBe("BTC 가상");
    expect(instrumentLabel("ETHUSDT")).toBe("ETH / USDT");
    expect(sideLabel("long")).toBe("오르면 이익");
    expect(sideLabel("short")).toBe("내리면 이익");
    expect(environmentSentences("mainnet")[0]).toBe("실계좌입니다. 연습 계좌가 아닙니다.");
    expect(environmentSentences("demo")[0]).toBe("연습 계좌입니다. 실계좌가 아닙니다.");
    expect(environmentSentences("testnet")[0]).toContain("시험용 거래소");
    expect(quoteSource("https://api.bybit.com/v5/market/tickers", "바이비트")).toBe(
      "바이비트 공개 시세",
    );
    expect(quoteSource("https://fapi.binance.com/fapi/v2/ticker/price", "바이낸스")).toBe(
      "바이낸스 공개 시세",
    );
  });
});
