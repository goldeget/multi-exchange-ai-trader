import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  type UTCTimestamp,
  type ISeriesApi,
} from "lightweight-charts";
import type { Candle } from "../api";
import CrispPriceLine from './CrispPriceLine';
export default function CandleChart({ candles }: { candles: Candle[] }) {
  const [fontStatus, setFontStatus] = useState<"loading" | "ready" | "failed">(
    () => typeof document.fonts?.load === "function" ? "loading" : "ready",
  );
  const container = useRef<HTMLDivElement>(null);
  const instance = useRef<{
    chart: ReturnType<typeof createChart>;
    series: ISeriesApi<"Candlestick">;
    volume: ISeriesApi<"Histogram">;
    priceLine: CrispPriceLine;
    fitted: boolean;
  } | null>(null);
  const previousTimes = useRef<string[]>([]);
  useEffect(() => {
    const fonts = document.fonts;
    if (typeof fonts?.load !== "function") return;
    let active = true;
    // Canvas text-width caches must first measure the bundled font, not a fallback.
    void (async () => {
      try {
        await fonts.load('14px "Pretendard"', "0123456789 한글");
        await fonts.ready;
        if (active) setFontStatus("ready");
      } catch {
        if (active) setFontStatus("failed");
      }
    })();
    return () => { active = false; };
  }, []);
  useEffect(() => () => {
    const current = instance.current;
    if (current) {
      current.series.detachPrimitive(current.priceLine);
      current.chart.remove();
    }
    instance.current = null;
    previousTimes.current = [];
  }, []);
  useEffect(() => {
    if (!container.current || fontStatus !== "ready") return;
    if (!instance.current && candles.length) {
      const chart = createChart(container.current, {
        height: container.current.clientHeight || 380,
        autoSize: true,
        layout: {
          background: { type: ColorType.Solid, color: "#141E2A" },
          textColor: "#A8B5C4",
          fontSize: 14,
          fontFamily: "Pretendard, sans-serif",
          attributionLogo: true,
        },
        grid: {
          vertLines: { color: "#18232E" },
          horzLines: { color: "#18232E" },
        },
        rightPriceScale: {
          borderColor: "#2A3745",
          scaleMargins: { top: 0.08, bottom: 0.25 },
        },
        timeScale: { borderColor: "#2A3745", timeVisible: true },
        handleScroll: { vertTouchDrag: false },
      });
      const series = chart.addSeries(CandlestickSeries, {
        upColor: "#FF6B7A",
        downColor: "#76A1FF",
        borderVisible: false,
        wickUpColor: "#FF6B7A",
        wickDownColor: "#76A1FF",
        priceLineVisible: false,
        lastValueVisible: true,
      });
      // The visible replacement is attached before the first chart paint.
      const priceLine = new CrispPriceLine();
      series.attachPrimitive(priceLine);
      const volume = chart.addSeries(HistogramSeries, {
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
        lastValueVisible: false,
        priceLineVisible: false,
      });
      volume
        .priceScale()
        .applyOptions({ scaleMargins: { top: 0.83, bottom: 0 } });
      instance.current = {chart, series, volume, priceLine, fitted: false};
    }
    const current = instance.current;
    if (!current) return;
    const {chart, series, volume} = current;
    const range = chart.timeScale().getVisibleLogicalRange();
    // Logical indices move when the bounded batch drops/prepends leading bars.
    const newIndices = new Map(candles.map((c, index) => [c.timestamp, index]));
    const oldIndex = previousTimes.current.findIndex(time => newIndices.has(time));
    const newIndex = oldIndex >= 0 ? newIndices.get(previousTimes.current[oldIndex]) : undefined;
    const offset = newIndex === undefined ? null : newIndex - oldIndex;
    previousTimes.current = candles.map(c => c.timestamp);
    series.setData(
      candles.map((c) => ({
        time: Math.floor(Date.parse(c.timestamp) / 1000) as UTCTimestamp,
        open: Number(c.open),
        high: Number(c.high),
        low: Number(c.low),
        close: Number(c.close),
      })),
    );
    volume.setData(
      candles.map((c) => ({
        time: Math.floor(Date.parse(c.timestamp) / 1000) as UTCTimestamp,
        value: Number(c.volume),
        color: Number(c.close) >= Number(c.open) ? "#AD4758" : "#456FB5",
      })),
    );
    const last = candles.at(-1);
    current.priceLine.setPrice(last ? Number(last.close) : null,
      last && Number(last.close) < Number(last.open) ? '#76A1FF' : '#FF6B7A');
    if (!candles.length) current.fitted = false;
    else if (!current.fitted || offset === null) {
      chart.timeScale().fitContent();
      current.fitted = true;
    } else if (range) chart.timeScale().setVisibleLogicalRange({
      from: range.from + offset,
      to: range.to + offset,
    });
  }, [candles, fontStatus]);
  return (
    <div
      ref={container}
      className="chart"
      aria-label="캔들 및 거래량 차트"
      role="img"
    >
      <span className="sr-only">
        가격과 거래량 차트입니다. 상세 수치는 아래 캔들 요약을 참조하세요.
      </span>
      {fontStatus === "failed" && candles.length > 0 && (
        <p role="alert">차트 글꼴을 불러오지 못했습니다. 화면을 새로고침하세요.</p>
      )}
      {!candles.length && (
        <div className="chart-empty">
          <span aria-hidden="true">⌁</span>
          <p>차트를 기다리는 중</p>
        </div>
      )}
    </div>
  );
}
