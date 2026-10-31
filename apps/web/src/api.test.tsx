// Offline UI fixtures: never evidence of live provider success.
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { usePolling, type Ticker } from "./api";

const fetchMock = vi.fn();
const last = { last_price: "105", fetched_at: "2026-10-31T00:00:00Z", source: "offline fixture" };
const ok = () => ({ ok: true, json: async () => last });
const denied = (reason: string) => ({ ok: false, json: async () => ({reason, message:"PRIVATE"}) });
function View({url = "/first"}: {url?: string}) {
  const result = usePolling<Ticker>(url, 5000, true);
  return <div><span>{result.error ?? "normal"}</span><span>{result.data?.last_price}</span><time>{result.data?.fetched_at}</time><span>{result.data?.source}</span></div>;
}
beforeEach(() => {vi.useFakeTimers();fetchMock.mockReset();vi.stubGlobal("fetch", fetchMock);});
afterEach(() => {cleanup();vi.useRealTimers();vi.unstubAllGlobals();});
async function tick(ms=0) {await act(async()=>{await vi.advanceTimersByTimeAsync(ms);});}

it.each([
  ["operator_disabled", "이 시세는 꺼져 있습니다"],
  ["access_restricted", "거래소가 시세 조회를 막았습니다"],
  ["geographic_restriction", "이 지역에서는 시세를 받을 수 없습니다"],
])("stops polling terminal %s and never prints raw diagnostics", async(reason,label)=>{
  fetchMock.mockResolvedValue(denied(reason));render(<View/>);await tick();
  expect(screen.getByText(new RegExp(label))).toBeVisible();
  await tick(90000);expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(screen.queryByText(/PRIVATE/)).not.toBeInTheDocument();
});

it("retains last receipt/source under terminal error and restarts for a different resource",async()=>{
  fetchMock.mockResolvedValueOnce(ok()).mockResolvedValue(denied("access_restricted"));
  const view=render(<View/>);await tick();await tick(5000);
  expect(screen.getByText(/거래소가 시세 조회를 막았습니다/)).toBeVisible();
  expect(screen.getByText("105")).toBeVisible();expect(screen.getByText(last.fetched_at)).toBeVisible();
  expect(screen.getByText(last.source)).toBeVisible();expect(screen.queryByText("normal")).not.toBeInTheDocument();
  await tick(30000);expect(fetchMock).toHaveBeenCalledTimes(2);
  fetchMock.mockResolvedValue(ok());view.rerender(<View url="/other"/>);await tick();
  expect(fetchMock).toHaveBeenCalledTimes(3);expect(screen.getByText("normal")).toBeVisible();
});

it.each(["timeout", "unrecognized_future_reason"])("continues recovery polling for %s",async(reason)=>{
  fetchMock.mockResolvedValueOnce(denied(reason)).mockResolvedValue(ok());
  render(<View/>);await tick();expect(screen.getByText(/연결 안 됨/)).toBeVisible();
  await tick(5000);expect(fetchMock).toHaveBeenCalledTimes(2);expect(screen.getByText("105")).toBeVisible();
});
