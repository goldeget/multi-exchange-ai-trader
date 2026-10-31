import Account from "./account/Account";
import Paper from "./paper/Paper";
import Research from "./research/Research";
import { useEffect, useState } from "react";
import {
  marketUrl,
  sampleCandles,
  sampleTime,
  usePolling,
  type Candle,
  type Market,
  type Ticker,
  type Venue,
} from "./api";
import CandleChart from "./chart/CandleChart";
const venues: Venue[] = ["binance", "bybit"];
const symbols = ["BTCUSDT", "ETHUSDT"];
const names = { binance: "Binance", bybit: "Bybit" };
const price = (value: string) =>
  Number(value).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
function MarketRow({
  venue,
  symbol,
  market,
  preview,
  selected,
  onSelect,
  now,
}: {
  venue: Venue;
  symbol: string;
  market: Market;
  preview: boolean;
  selected: boolean;
  onSelect: () => void;
  now: number;
}) {
  const live = usePolling<Ticker>(
    marketUrl("ticker", venue, symbol, market),
    5000,
    !preview,
  );
  const data = preview
    ? {
        exchange: venue,
        symbol,
        market_type: market,
        last_price: symbol === "BTCUSDT" ? "64428.23" : "3221.41",
        observed_at: sampleTime,
        fetched_at: sampleTime,
        source: "로컬 합성 샘플",
      }
    : live.data;
  const stale = !preview && !!live.successAt && now - live.successAt >= 15000;
  const state = preview
    ? "합성 샘플"
    : live.blocked
      ? `${live.error}${data ? " · 마지막 수신값" : ""}`
    : stale
      ? "지연 · 마지막 수신값"
      : live.error
        ? live.error
        : !data
          ? "불러오는 중"
          : "수신 완료";
  const observed = data?.observed_at;
  return (
    <tr className={selected ? "selected" : ""}>
      <th scope="row">
        <button aria-pressed={selected} onClick={onSelect}>
          {names[venue]}
          <span>
            {symbol.replace("USDT", " / USDT")} ·{" "}
            {market === "spot" ? "현물" : "무기한"}
          </span>
        </button>
      </th>
      <td>
        <strong>{data ? price(data.last_price) : "—"}</strong>
        <small>USDT</small>
      </td>
      <td>
        <span
          role="status"
          className={live.blocked ? "error" : stale ? "stale" : live.error ? "error" : "muted"}
        >
          {state}
        </span>
        {data && (
          <>
            <time dateTime={observed ?? data.fetched_at}>
              {new Date(observed ?? data.fetched_at)
                .toISOString()
                .replace("T", " ")
                .slice(0, 19)}{" "}
              UTC
            </time>
            <small>
              {preview
                ? "고정 샘플 시각"
                : observed
                  ? `관측 ${Math.max(0, Math.floor((now - Date.parse(observed)) / 1000))}초 전`
                  : "관측 시각 미제공 · 수집 시각 표시"}
            </small>
            <small>마지막 수신: {new Date(data.fetched_at).toISOString().replace("T", " ").slice(0, 19)} UTC</small>
            <small>출처: {data.source}</small>
          </>
        )}
      </td>
    </tr>
  );
}
function MarketDashboard() {
  const [preview] = useState(
    () => new URLSearchParams(window.location.search).get("preview") === "1",
  );
  const [venue, setVenue] = useState<Venue>("binance");
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [market, setMarket] = useState<Market>("spot");
  const [interval, setIntervalValue] = useState("15m");
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (preview) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [preview]);
  const candles = usePolling<Candle[]>(
    marketUrl("candles", venue, symbol, market, interval),
    30000,
    !preview,
  );
  const data = preview ? sampleCandles(symbol, interval) : (candles.data ?? []);
  const last = data.at(-1);
  const candleStale =
    !preview && !!candles.successAt && now - candles.successAt >= 60000;
  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="logo" aria-hidden="true">
            ◈
          </span>
          <div>
            <p className="eyebrow">MARKET OBSERVATORY</p>
            <h1>공개 시장 관측소</h1>
          </div>
        </div>
        <span className="safety">공개 시세 전용 · PAPER는 별도 가상 화면</span>
      </header>
      {preview && (
        <div className="preview" role="note">
          <strong>샘플 데이터 · 실제 시세 아님</strong>
          <span>
            로컬 합성 데이터로 화면을 미리 보고 있습니다. 모든 API 요청이 꺼져
            있습니다.
          </span>
        </div>
      )}
      <main>
        <section className="intro">
          <div>
            <p className="eyebrow">PUBLIC MARKET / 01</p>
            <h2>시장의 흐름을 한눈에</h2>
            <p>
              BTC와 ETH의 공개 시세를 관측합니다. 이 화면은 주문과 계좌 조회를 하지
              않습니다. 계좌는 위쪽 Bybit 계좌 조회 화면에서 읽기만 합니다.
            </p>
          </div>
          <span className="mode">
            {preview ? "◌ 합성 미리보기" : "◌ 공개 REST · 5초 주기"}
          </span>
        </section>
        <section className="controls" aria-label="시장 선택">
          <label>
            종목
            <select
              value={symbol}
              onChange={(event) => setSymbol(event.target.value)}
            >
              <option value="BTCUSDT">BTC / USDT</option>
              <option value="ETHUSDT">ETH / USDT</option>
            </select>
          </label>
          <label>
            시장
            <select
              value={market}
              onChange={(event) => setMarket(event.target.value as Market)}
            >
              <option value="spot">현물</option>
              <option value="perpetual">무기한</option>
            </select>
          </label>
          <label>
            봉 간격
            <select
              value={interval}
              onChange={(event) => setIntervalValue(event.target.value)}
            >
              {["1m", "5m", "15m", "1h", "4h", "1d"].map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
          <p>
            호가 통화 <strong>USDT</strong>
            <br />
            캔들 갱신 30초
          </p>
        </section>
        <div className="dashboard">
          <section className="panel markets" aria-labelledby="markets-title">
            <div className="panel-title">
              <h2 id="markets-title">거래소 시세</h2>
              <span>02 활성 / 03 전체</span>
            </div>
            <table>
              <thead>
                <tr>
                  <th scope="col">거래소 / 종목</th>
                  <th scope="col">가격</th>
                  <th scope="col">데이터 상태</th>
                </tr>
              </thead>
              <tbody>
                {venues.flatMap((item) =>
                  symbols.map((instrument) => (
                    <MarketRow
                      key={`${item}-${instrument}-${market}`}
                      venue={item}
                      symbol={instrument}
                      market={market}
                      preview={preview}
                      selected={venue === item && symbol === instrument}
                      onSelect={() => {
                        setVenue(item);
                        setSymbol(instrument);
                      }}
                      now={now}
                    />
                  )),
                )}
              </tbody>
            </table>
            <div className="disabled-venue">
              <button disabled>OKX · 검증 대기</button>
              <p>
                공식 공개 API 계약 검증 전으로 비활성화되어 있습니다. 시세
                요청을 보내지 않습니다.
              </p>
            </div>
            <p className="market-note">
              시세 수신이 15초 이상 멈추면 지연으로 표시합니다.
              <br />
              거래소별 데이터는 서로 대체하지 않습니다.
            </p>
          </section>
          <section className="panel chart-panel" aria-labelledby="chart-title">
            <div className="panel-title">
              <div>
                <p className="eyebrow">
                  {preview ? "SYNTHETIC PREVIEW" : "PUBLIC CANDLES"}
                </p>
                <h2 id="chart-title">
                  {symbol.replace("USDT", " / USDT")}{" "}
                  <span>
                    {names[venue]} · {market === "spot" ? "현물" : "무기한"} ·{" "}
                    {interval}
                  </span>
                </h2>
              </div>
              <span className="chart-unit">USDT</span>
            </div>
            <div
              role="status"
              className={candles.error ? "error chart-status" : "chart-status"}
            >
              {preview
                ? "로컬 합성 캔들"
                : candles.blocked
                  ? `${candles.error}${last ? " · 마지막 수신 캔들" : ""}`
                : candleStale
                  ? "지연 · 마지막 수신 캔들"
                  : (candles.error ??
                    (!last ? "불러오는 중" : "캔들 수신 완료"))}
            </div>
            {candles.successAt && !preview && (
              <p className="chart-status">
                마지막 수신: {new Date(candles.successAt).toISOString().replace("T", " ").slice(0, 19)} UTC · 출처: {names[venue]} 공개 캔들
              </p>
            )}
            <CandleChart key={`${venue}/${symbol}/${market}/${interval}`} candles={data} />
            <p aria-label="캔들 요약" className="summary">
              {last
                ? `마지막 봉: 시가 ${price(last.open)} · 고가 ${price(last.high)} · 저가 ${price(last.low)} · 종가 ${price(last.close)} USDT · 거래량 ${price(last.volume)} · ${last.is_closed ? "마감" : "형성 중"}`
                : "표시할 캔들이 없습니다. 공개 시세 연결 상태를 확인하세요."}
            </p>
            <a
              className="attribution"
              href="https://www.tradingview.com/"
              target="_blank"
              rel="noreferrer"
            >
              Charts powered by TradingView Lightweight Charts™
            </a>
          </section>
        </div>
        <footer>
          <span>공개 시세 전용 · 계좌 조회는 별도 화면 · 주문 기능 없음</span>
          <span>
            {preview
              ? "샘플 시각 2026-01-01 UTC"
              : "시각 기준 UTC · 공개 데이터 지연 가능"}
          </span>
        </footer>
      </main>
    </div>
  );
}

type AppView = "market" | "paper" | "research" | "account";

function currentView(): AppView {
  const value = new URLSearchParams(window.location.search).get("view");
  if (value === "paper" || value === "research" || value === "account") return value;
  return "market";
}

export default function App() {
  const [view, setView] = useState<AppView>(currentView);
  function select(value: AppView) {
    setView(value);
    const url = new URL(window.location.href);
    if (value !== "market") url.searchParams.set("view", value);
    else url.searchParams.delete("view");
    window.history.replaceState({}, "", url);
  }
  return (
    <>
      <nav className="app-mode-nav" aria-label="화면 선택">
        <button aria-pressed={view === "market"} onClick={() => select("market")}>
          공개 시장 관측
        </button>
        <button aria-pressed={view === "paper"} onClick={() => select("paper")}>
          PAPER 가상거래
        </button>
        <button aria-pressed={view === "research"} onClick={() => select("research")}>
          연구·가상 자동실행
        </button>
        <button aria-pressed={view === "account"} onClick={() => select("account")}>
          Bybit 계좌 조회
        </button>
      </nav>
      {view === "research" ? (
        <Research />
      ) : view === "paper" ? (
        <Paper />
      ) : view === "account" ? (
        <Account />
      ) : (
        <MarketDashboard />
      )}
    </>
  );
}
