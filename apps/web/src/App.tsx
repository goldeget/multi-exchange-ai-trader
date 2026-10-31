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
import { intervalLabel, intervalOptions, quoteSource } from "./ko";
const venues: Venue[] = ["binance", "bybit"];
const symbols = ["BTCUSDT", "ETHUSDT"];
const names = { binance: "바이낸스", bybit: "바이비트" };
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
        source: "예시 숫자",
      }
    : live.data;
  const stale = !preview && !!live.successAt && now - live.successAt >= 15000;
  const state = preview
    ? "예시 숫자"
    : live.blocked
      ? `${live.error}${data ? " · 마지막 가격" : ""}`
    : stale
      ? "늦음 · 마지막 가격"
      : live.error
        ? live.error
        : !data
          ? "불러오는 중"
          : "받음";
  const observed = data?.observed_at;
  return (
    <tr className={selected ? "selected" : ""}>
      <th scope="row">
        <button aria-pressed={selected} onClick={onSelect}>
          {names[venue]}
          <span>
            {symbol.replace("USDT", " / USDT")} ·{" "}
            {market === "spot" ? "현물" : "선물"}
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
              세계 표준시
            </time>
            <small>
              {preview
                ? "예시용 고정 시각"
                : observed
                  ? `${Math.max(0, Math.floor((now - Date.parse(observed)) / 1000))}초 전 가격`
                  : "가격 시각이 없어 받은 시각을 표시합니다"}
            </small>
            <small>마지막으로 받은 시각: {new Date(data.fetched_at).toISOString().replace("T", " ").slice(0, 19)} 세계 표준시</small>
            <small>받은 곳: {quoteSource(data.source, names[venue])}</small>
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
            <p className="eyebrow">시세</p>
            <h1>지금 시세</h1>
          </div>
        </div>
        <span className="safety">시세만 보여 줍니다. 가상 거래는 다른 화면입니다.</span>
      </header>
      {preview && (
        <div className="preview" role="note">
          <strong>예시 숫자 · 실제 시세 아님</strong>
          <span>
            만든 예시 숫자로 화면만 보고 있습니다. 거래소에는 연결하지 않습니다.
          </span>
        </div>
      )}
      <main>
        <section className="intro">
          <div>
            <p className="eyebrow">시세</p>
            <h2>가격 흐름을 한눈에</h2>
            <p>
              BTC와 ETH의 공개 시세를 봅니다. 이 화면은 주문과 잔고 조회를 하지
              않습니다. 잔고는 위쪽 Bybit 잔고 화면에서 읽기만 합니다.
            </p>
          </div>
          <span className="mode">
            {preview ? "◌ 예시 화면" : "◌ 5초마다 시세 갱신"}
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
              <option value="perpetual">선물</option>
            </select>
          </label>
          <label>
            봉 간격
            <select
              value={interval}
              onChange={(event) => setIntervalValue(event.target.value)}
            >
              {intervalOptions.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <p>
            가격 단위 <strong>USDT</strong>
            <br />
            차트는 30초마다 갱신
          </p>
        </section>
        <div className="dashboard">
          <section className="panel markets" aria-labelledby="markets-title">
            <div className="panel-title">
              <h2 id="markets-title">거래소 시세</h2>
              <span>2곳 표시 · 전체 3곳</span>
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
              <button disabled>OKX · 아직 준비 안 됨</button>
              <p>OKX는 아직 연결하지 않습니다. 시세를 요청하지 않습니다.</p>
            </div>
            <p className="market-note">
              시세가 15초 넘게 안 오면 늦음으로 표시합니다.
              <br />
              한 거래소 시세가 끊겨도 다른 거래소 숫자로 바꾸지 않습니다.
            </p>
          </section>
          <section className="panel chart-panel" aria-labelledby="chart-title">
            <div className="panel-title">
              <div>
                <p className="eyebrow">
                  {preview ? "예시 차트" : "시세 차트"}
                </p>
                <h2 id="chart-title">
                  {symbol.replace("USDT", " / USDT")}{" "}
                  <span>
                    {names[venue]} · {market === "spot" ? "현물" : "선물"} ·{" "}
                    {intervalLabel(interval)}
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
                ? "예시 차트"
                : candles.blocked
                  ? `${candles.error}${last ? " · 마지막 차트" : ""}`
                : candleStale
                  ? "늦음 · 마지막 차트"
                  : (candles.error ??
                    (!last ? "불러오는 중" : "차트를 받았습니다"))}
            </div>
            {candles.successAt && !preview && (
              <p className="chart-status">
                마지막으로 받은 시각: {new Date(candles.successAt).toISOString().replace("T", " ").slice(0, 19)} 세계 표준시 · 받은 곳: {names[venue]} 공개 시세
              </p>
            )}
            <CandleChart key={`${venue}/${symbol}/${market}/${interval}`} candles={data} />
            <p aria-label="캔들 요약" className="summary">
              {last
                ? `마지막 봉: 시가 ${price(last.open)} · 고가 ${price(last.high)} · 저가 ${price(last.low)} · 종가 ${price(last.close)} USDT · 거래량 ${price(last.volume)} · ${last.is_closed ? "마감" : "형성 중"}`
                : "표시할 차트가 없습니다. 시세 연결을 확인하세요."}
            </p>
            <a
              className="attribution"
              href="https://www.tradingview.com/"
              target="_blank"
              rel="noreferrer"
            >
              차트 그림: TradingView
            </a>
          </section>
        </div>
        <footer>
          <span>시세만 봅니다. 잔고는 다른 화면입니다. 주문은 없습니다.</span>
          <span>
            {preview
              ? "예시 시각 2026-01-01 세계 표준시"
              : "시각은 세계 표준시입니다. 시세가 조금 늦을 수 있습니다."}
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

const titles: Record<AppView, string> = {
  market: "시세 보기",
  paper: "가상 거래",
  research: "연습 자동매매",
  account: "Bybit 잔고",
};

export default function App() {
  const [view, setView] = useState<AppView>(currentView);
  useEffect(() => {
    document.title = titles[view];
  }, [view]);
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
          시세 보기
        </button>
        <button aria-pressed={view === "paper"} onClick={() => select("paper")}>
          가상 거래
        </button>
        <button aria-pressed={view === "research"} onClick={() => select("research")}>
          연습 자동매매
        </button>
        <button aria-pressed={view === "account"} onClick={() => select("account")}>
          Bybit 잔고
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
