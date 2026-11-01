import { useState } from "react";
import { Badge, Definitions, Empty, Icon, Panel } from "../ui/components";
import type { AccountView } from "./Account";
import { screenText } from "../ko";
import "./account-screen.css";

type Props = { view: AccountView | null; loading: boolean; refreshing: boolean; problem: string;
  freshness: string; age: number | null; past: boolean; failed: boolean; fixture: boolean;
  notice: string; onRefresh: () => void };
type Position = NonNullable<AccountView["포지션"]>[number];

export function formatAccountDecimal(value: string | null, minimumFraction = 0): string {
  if (value === null || !/^[+-]?\d+(?:\.\d+)?$/.test(value)) return "—";
  const [whole, fraction = ""] = value.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const decimals = fraction.padEnd(minimumFraction, "0");
  return grouped + (decimals ? "." + decimals : "");
}
const baseAsset = (symbol: string) => symbol.endsWith("USDT") ? symbol.slice(0, -4) : "";
const symbolLabel = (symbol: string) => symbol.endsWith("USDT") ? `${baseAsset(symbol)} / USDT` : symbol;
const direction = (value: string) => ["Sell", "매도", "short"].includes(value) ? "숏" : ["Buy", "매수", "long"].includes(value) ? "롱" : value;
const tone = (value: string | null) => value?.startsWith("-") ? "down" : value === null ? "" : "up";
const price = (value: string | null) => value === null ? "확인 불가" : `${formatAccountDecimal(value, 2)} USDT`;
function readTime(value: string | null | undefined): string {
  if (!value || !Number.isFinite(Date.parse(value))) return "확인 불가";
  return new Intl.DateTimeFormat("ko-KR", {timeZone:"Asia/Seoul",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(new Date(value)) + " KST";
}

function PositionCard({ row }: { row: Position }) {
  return <article className="account-position-card">
    <div className="account-position-heading"><h3>{symbolLabel(row.종목)}</h3><Badge tone={direction(row.방향) === "숏" ? "down" : "up"}>{direction(row.방향)}</Badge><span className="account-external-tag">외부 포지션</span></div>
    <p className="account-unmanaged">⊗ 프로그램 관리 안 함</p>
    <dl className="account-position-values">
      <div><dt>수량</dt><dd>{formatAccountDecimal(row.수량)} {baseAsset(row.종목)}</dd></div>
      <div><dt>레버리지</dt><dd>{row.레버리지 ?? "확인 불가"}{row.레버리지 ? "배" : ""}</dd></div>
      <div><dt>진입가</dt><dd>{price(row.진입가)}</dd></div><div><dt>표시가</dt><dd>{price(row.표시가)}</dd></div>
      <div><dt>청산가</dt><dd>{price(row.청산가)}</dd></div><div><dt>미실현 손익</dt><dd className={tone(row.미실현손익)}>{price(row.미실현손익)}</dd></div>
    </dl>
    <div className="account-position-bottom"><span>마진 모드 <strong>{row.마진모드}</strong></span><details><summary>상세 보기</summary><p>포지션 모드: {row.포지션모드}</p><p>외부에서 연 포지션입니다. 프로그램이 감시·보호하거나 변경하지 않습니다.</p></details></div>
  </article>;
}

export function AccountScreen({ view, loading, refreshing, problem, freshness, age, past, failed, fixture, notice, onRefresh }: Props) {
  const [tab, setTab] = useState<"positions" | "orders" | "balances">("positions");
  const healthy = view?.상태코드 === "ok" && !failed && freshness === "방금 확인함";
  const positions = view?.포지션 ?? null;
  const orders = view?.미체결주문 ?? null;
  const wallet = view?.잔고 ?? null;
  const onePosition = positions?.length === 1 && positions[0].종목.endsWith("USDT") ? positions[0] : null;
  const pnl = onePosition?.미실현손익 ?? null;
  const count = (rows: unknown[] | null) => rows === null ? "—" : String(rows.length);
  const refreshLabel = fixture ? "예시 다시 보기" : "새로고침";
  return <div className="account-screen" data-source={fixture ? "FIXTURE" : "ACCOUNT_READONLY"}>
    {loading && !view && !problem && <p role="status" className="account-read-state">불러오는 중. 아직 잔고를 표시하지 않습니다.</p>}
    {problem && <p role="alert" className="account-read-error">{problem}</p>}
    {view?.상태코드 === "BLOCKED" && !failed && <p className="account-read-error" role="status">연결이 막혔습니다. 잔고 숫자를 만들지 않았습니다.</p>}
    {view && view.상태코드 !== "ok" && view.상태코드 !== "BLOCKED" && !failed && <p className="account-read-error" role="status">{screenText(view.메시지)}</p>}
    {past && <p className="account-read-error">과거 조회값 · {freshness}. 아래 숫자는 현재 잔고로 판단하지 마세요.</p>}
    {notice && <p role="status" className="helper">{notice}</p>}
    <Panel className="account-summary account-balance-summary">
      <div className="account-refresh-line"><span className={healthy ? "ok" : "warning"} role="status"><span className="account-state-dot" />{healthy ? `조회 정상 · ${age ?? "—"}초 전` : freshness}</span><button className="secondary" onClick={onRefresh} disabled={refreshing}><Icon name="refresh" />{refreshing ? "조회 중" : refreshLabel}</button></div>
      <div className="account-summary-values"><div><span>총자산 평가</span><strong>{wallet ? `${formatAccountDecimal(wallet.총자산평가, 2)} USD` : "확인 불가"}</strong></div><div><span>지갑 잔고</span><strong>{wallet ? `${formatAccountDecimal(wallet.총지갑잔고, 2)} USD` : "확인 불가"}</strong></div><div><span>미실현 손익{positions && positions.length > 1 ? " · 포지션별" : ""}</span><strong className={tone(pnl)}>{pnl !== null ? `${formatAccountDecimal(pnl, 2)} USDT` : positions?.length ? "포지션별 확인" : positions ? "열린 포지션 없음" : "확인 불가"}</strong></div></div>
    </Panel>
    <div className="account-mobile-tabs" role="tablist" aria-label="계좌 조회 항목">
      {([["positions", "포지션", count(positions)], ["orders", "미체결 주문", count(orders)], ["balances", "잔고", null]] as const).map(([key, label, total]) => <button key={key} role="tab" aria-selected={tab === key} aria-controls={`account-${key}-panel`} onClick={() => setTab(key)}>{label}{total !== null && <span>{total}</span>}</button>)}
    </div>
    <div className="account-position-section" id="account-positions-panel" data-mobile-panel data-active={tab === "positions"}>
      <Panel className="account-positions-table" title={`포지션 (${count(positions)})`} aside={<Badge tone="warning">! 외부 포지션 · 프로그램 관리 안 함</Badge>}>
        {positions === null ? <Empty title="포지션을 확인할 수 없습니다." detail="확인하지 못한 값을 포지션 없음으로 바꾸지 않습니다." /> : positions.length === 0 ? <Empty title="열린 포지션이 없습니다." /> : <>
          <div className="account-desktop-position-table"><table><thead><tr>{["종목", "방향", "수량", "배수", "진입가 (USDT)", "표시가 (USDT)", "청산가 (USDT)", "미실현 손익", "관리구분"].map(label => <th scope="col" key={label}>{label}</th>)}</tr></thead><tbody>{positions.map((row, index) => <tr key={`${row.종목}-${index}`}><th scope="row">{symbolLabel(row.종목)}</th><td><Badge tone={direction(row.방향) === "숏" ? "down" : "up"}>{direction(row.방향)}</Badge></td><td>{formatAccountDecimal(row.수량)} {baseAsset(row.종목)}</td><td>{row.레버리지 ?? "—"} 배</td><td>{formatAccountDecimal(row.진입가, 2)}</td><td>{formatAccountDecimal(row.표시가, 2)}</td><td>{formatAccountDecimal(row.청산가, 2)}</td><td className={tone(row.미실현손익)}>{formatAccountDecimal(row.미실현손익, 2)} USDT</td><td><span className="account-external-tag">외부 포지션</span></td></tr>)}</tbody></table></div>
          <div className="account-mobile-positions">{positions.map((row, index) => <PositionCard key={`${row.종목}-${index}`} row={row} />)}</div>
        </>}
        <p className="helper account-position-help">ⓘ 포지션과 주문은 조회만 하며 변경하지 않습니다.</p>
      </Panel>
    </div>
    <div className="account-detail-grid">
      <div id="account-balances-panel" data-mobile-panel data-active={tab === "balances"}><Panel title={`잔고 (${wallet ? wallet.코인.length : "—"})`} className="account-coins-panel">
        {!wallet ? <Empty title="잔고를 확인할 수 없습니다." /> : <table className="account-coin-table"><thead><tr><th scope="col">자산</th><th scope="col">보유 수량</th><th scope="col">{fixture ? "평가금액 (USD)" : "자산 순잔고"}</th></tr></thead><tbody>{wallet.코인.map(coin => <tr key={coin.코인}><th scope="row"><span className={`account-coin-symbol ${coin.코인 === "BTC" ? "bitcoin" : ""}`}>{coin.코인 === "BTC" ? "₿" : coin.코인 === "USDT" ? "₮" : coin.코인.slice(0, 1)}</span>{coin.코인}</th><td>{formatAccountDecimal(coin.지갑잔고)} {coin.코인}</td><td>{fixture ? formatAccountDecimal(coin.예시USD평가 ?? null, 2) : `${formatAccountDecimal(coin.자산평가)} ${coin.코인}`}</td></tr>)}</tbody></table>}
        <p className="helper">작은 수량도 원본 정밀도를 유지합니다.{!fixture && " 자산 순잔고는 해당 코인의 수량이며 달러 평가액이 아닙니다."}</p>
      </Panel></div>
      <div id="account-orders-panel" data-mobile-panel data-active={tab === "orders"}><Panel title={`미체결 주문 (${count(orders)})`} className="account-orders-panel">
        {orders === null ? <Empty title="미체결 주문을 확인할 수 없습니다." /> : orders.length === 0 ? <><div className="account-order-head"><span>종목</span><span>주문 구분</span><span>방향</span><span>가격 (USDT)</span><span>수량</span></div><Empty title="미체결 주문이 없습니다." /></> : <div className="account-open-orders">{orders.map((order, index) => <article key={`${order.종목}-${index}`}><h3>{symbolLabel(order.종목)} <Badge>{order.상태}</Badge></h3><Definitions rows={[["주문", `${order.유형} · ${order.방향}`], ["수량", `${formatAccountDecimal(order.수량)} ${baseAsset(order.종목)}`], ["가격", price(order.가격)], ["관리", "외부 주문 · 프로그램 관리 안 함"]]} /></article>)}</div>}
      </Panel></div>
    </div>
    <p className="account-external-warning">! 외부 포지션은 이 프로그램이 변경하지 않습니다.</p>
    <button className="primary account-mobile-refresh" onClick={onRefresh} disabled={refreshing}><Icon name="refresh" />{refreshing ? "조회 중" : refreshLabel}</button>
    <Panel className="account-read-details"><span>마지막 조회 {readTime(view?.수신시각)} · 순차 조회값</span><details><summary>조회 상세</summary><Definitions rows={[["데이터 나이", age === null ? "확인 불가" : `${age}초`], ["계좌 마진", view?.계좌마진모드 ?? "확인 불가"], ["자동 관리", "하지 않음"], ["주문 판단", "이 조회값을 사용하지 않음"]]} /></details>{fixture && <span>디자인 참조 · 예시 계좌</span>}</Panel>
  </div>;
}
