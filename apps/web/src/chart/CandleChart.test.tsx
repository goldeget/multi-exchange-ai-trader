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
