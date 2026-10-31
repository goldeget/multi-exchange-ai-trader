// Offline lifecycle contract tests; live range behavior is verified separately in Chromium.
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Candle } from "../api";
const mocks=vi.hoisted(()=>{
  const charts: Array<{addSeries: ReturnType<typeof vi.fn>,timeScale: ReturnType<typeof vi.fn>,remove: ReturnType<typeof vi.fn>,setRange: ReturnType<typeof vi.fn>,fit: ReturnType<typeof vi.fn>,setData: ReturnType<typeof vi.fn>}> = [];
  const createChart=vi.fn(()=>{
    const fit=vi.fn(),setData=vi.fn(),setRange=vi.fn();
    const chart={fit,setData,setRange,addSeries:vi.fn(()=>({setData,priceScale:()=>({applyOptions:vi.fn()})})),timeScale:vi.fn(()=>({fitContent:fit,getVisibleLogicalRange:()=>({from:25,to:75}),setVisibleLogicalRange:setRange})),remove:vi.fn()};charts.push(chart);return chart;
  });return {createChart,charts};
});
vi.mock("lightweight-charts",()=>({createChart:mocks.createChart,CandlestickSeries:{},HistogramSeries:{},ColorType:{Solid:"solid"}}));
import CandleChart from "./CandleChart";
const candle: Candle={timestamp:"2026-10-31T00:00:00Z",open:"10",high:"12",low:"9",close:"11",volume:"2",is_closed:false};
afterEach(()=>{cleanup();mocks.createChart.mockClear();mocks.charts.length=0;});
it("updates live series in place without recreating or fitting the user's periodic viewport",()=>{
 const view=render(<CandleChart candles={[candle]}/>);const first=mocks.charts[0];
 view.rerender(<CandleChart candles={[{...candle,close:"11.5"}]}/>);
 expect(mocks.createChart).toHaveBeenCalledTimes(1);expect(first.remove).not.toHaveBeenCalled();expect(first.fit).toHaveBeenCalledTimes(1);
 expect(first.setData.mock.calls.at(-2)?.[0][0].close).toBe(11.5);
 expect(first.setRange).toHaveBeenLastCalledWith({from:25,to:75});
 view.unmount();expect(first.remove).toHaveBeenCalledTimes(1);
});
it("initializes empty-to-live, clears empty data, and fits a new selection lifecycle",()=>{
 const view=render(<CandleChart key="BTC" candles={[]}/>);expect(mocks.createChart).not.toHaveBeenCalled();
 view.rerender(<CandleChart key="BTC" candles={[candle]}/>);const first=mocks.charts[0];
 view.rerender(<CandleChart key="ETH" candles={[]}/>);expect(first.remove).toHaveBeenCalledTimes(1);
 view.rerender(<CandleChart key="ETH" candles={[{...candle,open:"20",high:"22",low:"19",close:"21"}]}/>);
 expect(mocks.createChart).toHaveBeenCalledTimes(2);const second=mocks.charts[1];expect(second.fit).toHaveBeenCalledTimes(1);
 expect(second.setData.mock.calls[0][0][0].open).toBe(20);
 view.rerender(<CandleChart key="ETH" candles={[]}/>);expect(second.setData).toHaveBeenLastCalledWith([]);
 view.unmount();expect(second.remove).toHaveBeenCalledTimes(1);
});

const stamps = ["2026-10-31T00:00:00Z", "2026-10-31T00:01:00Z", "2026-10-31T00:02:00Z", "2026-10-31T00:03:00Z"];
it.each([
  ["rolling drop", [stamps[1],stamps[2],stamps[3]], {from:-0.5,to:0.5}],
  ["append only", stamps, {from:0.5,to:1.5}],
  ["prepend", ["2026-10-30T23:59:00Z",...stamps.slice(0,3)], {from:1.5,to:2.5}],
])("preserves historical timestamp position and zoom width on %s", (_label,next,want)=>{
  const view=render(<CandleChart candles={stamps.slice(0,3).map(timestamp=>({...candle,timestamp}))}/>);
  const first=mocks.charts[0];
  first.timeScale.mockReturnValue({fitContent:first.fit,getVisibleLogicalRange:()=>({from:0.5,to:1.5}),setVisibleLogicalRange:first.setRange});
  view.rerender(<CandleChart candles={next.map(timestamp=>({...candle,timestamp}))}/>);
  expect(first.setRange).toHaveBeenLastCalledWith(want);
  expect(first.fit).toHaveBeenCalledTimes(1);expect(mocks.createChart).toHaveBeenCalledTimes(1);
});
it("fits a disjoint replacement batch instead of preserving an empty old time window",()=>{
  const view=render(<CandleChart candles={stamps.slice(0,3).map(timestamp=>({...candle,timestamp}))}/>);
  const first=mocks.charts[0];
  view.rerender(<CandleChart candles={[{...candle,timestamp:"2026-11-01T00:00:00Z"}]}/>);
  expect(first.fit).toHaveBeenCalledTimes(2);expect(first.setRange).not.toHaveBeenCalled();
  expect(mocks.createChart).toHaveBeenCalledTimes(1);
});
