import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  type UTCTimestamp,
  type ISeriesApi,
} from "lightweight-charts";
import type { Candle } from "../api";
export default function CandleChart({ candles }: { candles: Candle[] }) {
  const container = useRef<HTMLDivElement>(null);
  const instance = useRef<{
    chart: ReturnType<typeof createChart>;
    series: ISeriesApi<"Candlestick">;
    volume: ISeriesApi<"Histogram">;
    fitted: boolean;
  } | null>(null);
  const previousTimes = useRef<string[]>([]);
  useEffect(() => () => {
    instance.current?.chart.remove();
    instance.current = null;
    previousTimes.current = [];
  }, []);
  useEffect(() => {
    if (!container.current) return;
    if (!instance.current && candles.length) {
      const chart = createChart(container.current, {
        height: 380,
        autoSize: true,
        layout: {
          background: { type: ColorType.Solid, color: "#121A23" },
          textColor: "#A8B5C4",
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
        upColor: "#36C99A",
        downColor: "#FF737D",
        borderVisible: false,
        wickUpColor: "#36C99A",
        wickDownColor: "#FF737D",
      });
      const volume = chart.addSeries(HistogramSeries, {
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
      });
      volume
        .priceScale()
        .applyOptions({ scaleMargins: { top: 0.83, bottom: 0 } });
      instance.current = {chart, series, volume, fitted: false};
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
        color: Number(c.close) >= Number(c.open) ? "#267E67" : "#A34F59",
      })),
    );
    if (!candles.length) current.fitted = false;
    else if (!current.fitted || offset === null) {
      chart.timeScale().fitContent();
      current.fitted = true;
    } else if (range) chart.timeScale().setVisibleLogicalRange({
      from: range.from + offset,
      to: range.to + offset,
    });
  }, [candles]);
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
      {!candles.length && (
        <div className="chart-empty">
          <span aria-hidden="true">⌁</span>
          <p>차트를 기다리는 중</p>
        </div>
      )}
    </div>
  );
}
