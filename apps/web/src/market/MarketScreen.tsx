import type { Candle, Market, Resource, Ticker, Venue } from "../api";
import CandleChart from "../chart/CandleChart";
import { intervalLabel, intervalOptions, quoteSource } from "../ko";
import { Badge, Icon, Panel } from "../ui/components";
import "./market-screen.css";

type Props = {
  venue: Venue; symbol: string; market: Market; interval: string;
  onVenue: (value: Venue) => void; onSymbol: (value: string) => void;
  onMarket: (value: Market) => void; onInterval: (value: string) => void;
  preview: boolean; demo: boolean; now: number;
  candles: Resource<Candle[]>; data: Candle[]; quotes: Record<string, Resource<Ticker>>;
};
const names = { binance: "바이낸스", bybit: "바이비트" };
const displayNames = { binance: "Binance", bybit: "Bybit" };
const label = (symbol: string) => symbol.replace("USDT", " / USDT");
function price(value: string | undefined): string {
  if (!value || !/^[+-]?\d+(?:\.\d+)?$/.test(value)) return "—";
  const [whole, decimal = ""] = value.split(".");
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + "." + decimal.padEnd(2, "0");
}
function timestamp(value: string | undefined | null): string {
  if (!value || !Number.isFinite(Date.parse(value))) return "확인 불가";
  return new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).format(new Date(value)) + " KST";
}
function quoteState(quote: Resource<Ticker>, now: number, preview: boolean): { text: string; tone: string } {
  if (preview) return { text: "정상 · 예시", tone: "ok" };
  if (quote.blocked) return { text: `${quote.error}${quote.data ? " · 마지막 가격" : ""}`, tone: "warning" };
  if (quote.successAt && now - quote.successAt >= 15_000) return { text: "늦음 · 마지막 가격", tone: "warning" };
  if (quote.error) return { text: quote.error, tone: "warning" };
  return { text: quote.data ? "정상" : "불러오는 중", tone: quote.data ? "ok" : "muted" };
}
const fixtureChange = (venue: Venue, symbol: string) => symbol === "ETHUSDT"
  ? venue === "bybit" ? "+18.60  +0.87%" : "+17.90  +0.84%"
  : venue === "bybit" ? "+286.30  +0.44%" : "+284.10  +0.44%";

export function MarketScreen(props: Props) {
  const { venue, symbol, market, interval, onVenue, onSymbol, onMarket, onInterval, preview, now, candles, data, quotes } = props;
  const selected = quotes[`${venue}-${symbol}`];
  const ticker = selected.data;
  const selectedState = quoteState(selected, now, preview);
  const last = data.at(-1);
  const candleStale = !preview && !!candles.successAt && now - candles.successAt >= 60_000;
  const candleState = preview ? "예시 차트" : candles.blocked
    ? `${candles.error}${last ? " · 마지막 차트" : ""}`
    : candleStale ? "늦음 · 마지막 차트" : candles.error ?? (!last ? "불러오는 중" : "차트를 받았습니다");
  const choose = (nextVenue: Venue, nextSymbol: string) => { onVenue(nextVenue); onSymbol(nextSymbol); };
  return <div className="market-screen" data-source={preview ? "FIXTURE" : "PUBLIC_MARKET"}>
    {preview && <p className="market-example-note" role="note"><strong>예시 숫자 · 실제 시세 아님</strong><span>디자인 참조용 고정 시세입니다. 거래소에 연결하지 않습니다.</span></p>}
    <Panel className="market-filter-panel"><div className="market-filters" aria-label="시장 선택">
      <label><span>거래소</span><select aria-label="거래소" value={venue} onChange={event => onVenue(event.target.value as Venue)}><option value="bybit">Bybit</option><option value="binance">Binance</option></select></label>
      <label><span>시장</span><select aria-label="시장" value={market} onChange={event => onMarket(event.target.value as Market)}><option value="spot">현물</option><option value="perpetual">선물</option></select></label>
      <label className="market-symbol-filter"><span>종목</span><select aria-label="종목" value={symbol} onChange={event => onSymbol(event.target.value)}><option value="BTCUSDT">BTC / USDT</option><option value="ETHUSDT">ETH / USDT</option></select></label>
      <span className="market-refresh-status"><Icon name="refresh" size={18} />{preview ? "예시 · 3초 전" : selectedState.text}</span>
    </div></Panel>
    <div className="market-content-grid">
      <Panel title={preview ? "거래소별 예시 시세" : "거래소별 시세"} className="market-quotes-panel">
        <table className="market-quotes"><thead><tr><th scope="col">거래소 / 종목</th><th scope="col">현재가 (USDT)</th><th scope="col">데이터 상태</th></tr></thead><tbody>
          {(["bybit", "binance"] as const).flatMap(item => ["BTCUSDT", "ETHUSDT"].map(instrument => {
            const resource = quotes[`${item}-${instrument}`];
            const quote = resource.data;
            const state = quoteState(resource, now, preview);
            const active = venue === item && symbol === instrument;
            return <tr aria-label={`${names[item]} ${label(instrument)}`} key={`${item}-${instrument}`} className={active ? "selected" : ""} data-mobile-selected-symbol={instrument === symbol}>
              <th scope="row"><button aria-label={`${names[item]}${label(instrument)} · ${market === "spot" ? "현물" : "선물"}`} aria-pressed={active} onClick={() => choose(item, instrument)}><strong>{displayNames[item]}</strong><span>{label(instrument)}</span></button></th>
              <td><strong>{quote ? price(quote.last_price) : "—"}</strong><small className="market-mobile-unit">USDT</small>{preview && <small className="up market-change">{fixtureChange(item, instrument)}</small>}</td>
              <td><span role="status" className={state.tone}><span className="market-status-dot" />{state.text}</span>{quote && <details><summary>시세 상세</summary><p>가격 시각: {timestamp(quote.observed_at)}</p><p>마지막으로 받은 시각: {timestamp(quote.fetched_at)}</p><p>받은 곳: {quoteSource(quote.source, names[item])}</p></details>}</td>
            </tr>;
          }))}
          <tr className="market-okx-row"><th scope="row"><button disabled>OKX · 아직 준비 안 됨</button></th><td>—</td><td>미연결</td></tr>
        </tbody></table>
        <p className="market-isolation-note">연결이 끊겨도 다른 거래소 값으로 바꾸지 않습니다. {preview ? "각 거래소의 예시 시세를 개별로 표시합니다." : "각 거래소의 공개 시세를 개별로 표시합니다."}</p>
      </Panel>
      <Panel className="market-chart-panel">
        <div className="market-chart-heading"><h2 aria-label={`${label(symbol)} ${names[venue]} · ${market === "spot" ? "현물" : "선물"} · ${intervalLabel(interval)}`}>{label(symbol)}<span className="sr-only"> {names[venue]} · {market === "spot" ? "현물" : "선물"} · {intervalLabel(interval)}</span></h2><Badge>{preview ? "예시 시세" : "공개 시세"}</Badge>
          <div className="market-timeframes" aria-label="차트 봉 간격">{[["15m", "15분"], ["1h", "1시간"], ["4h", "4시간"], ["1d", "1일"]].map(([value, text]) => <button key={value} aria-pressed={interval === value} onClick={() => onInterval(value)}>{text}</button>)}</div>
        </div>
        <div className="market-selected-quote"><strong data-testid="selected-market-price">{ticker ? price(ticker.last_price) : "—"}<small> USDT</small></strong>{preview ? <span className="up market-daily-change">{fixtureChange(venue, symbol)}</span> : <span className="market-price-source">{selectedState.text} · {displayNames[venue]} {market === "spot" ? "현물" : "선물"}</span>}</div>
        <label className="market-interval-select">봉 간격<select value={interval} onChange={event => onInterval(event.target.value)}>{intervalOptions.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>
        <p role="status" className={`market-chart-state ${candles.error || candleStale ? "warning" : "muted"}`}>{candleState}{!preview && candles.successAt ? ` · 마지막으로 받은 시각: ${timestamp(new Date(candles.successAt).toISOString())}` : ""}</p>
        <CandleChart key={`${venue}/${symbol}/${market}/${interval}`} candles={data} />
        <p className="sr-only" aria-label="캔들 요약">{last ? `마지막 봉: 시가 ${price(last.open)} · 고가 ${price(last.high)} · 저가 ${price(last.low)} · 종가 ${price(last.close)} USDT · 거래량 ${price(last.volume)} · ${last.is_closed ? "마감" : "형성 중"}` : "표시할 차트가 없습니다. 시세 연결을 확인하세요."}</p>
        <div className="market-chart-footer"><span>출처 {displayNames[venue]} {market === "spot" ? "현물" : "선물"} · {preview ? "화면 설계용 예시" : "공개 시세"}</span><span>갱신 시각 {timestamp(ticker?.fetched_at)}</span><a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView</a></div>
      </Panel>
    </div>
    <p className="market-readonly-note">시세만 보여 줍니다. 가상 거래는 다른 화면입니다.</p>
  </div>;
}
