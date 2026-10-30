import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Candle } from "../api";
export default function CandleChart({ candles }: { candles: Candle[] }) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!container.current || !candles.length) return;
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
    chart.timeScale().fitContent();
    return () => chart.remove();
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
          <p>공개 캔들 데이터 대기 중</p>
        </div>
      )}
    </div>
  );
}
