import { useEffect, useState } from "react";
import { connectionLabel, environmentSentences, screenText } from "../ko";

type Money = string | null;
type Coin = { 코인: string; 지갑잔고: Money; 자산평가: Money };
type Position = {
  구분: string;
  관리함: false;
  종목: string;
  방향: string;
  수량: string;
  레버리지: Money;
  진입가: Money;
  표시가: Money;
  청산가: Money;
  미실현손익: Money;
  마진모드: string;
  포지션모드: string;
};
type OpenOrder = {
  구분: string;
  관리함: false;
  종목: string;
  방향: string;
  유형: string;
  수량: string;
  가격: Money;
  상태: string;
  줄이기만: string;
};
export type AccountView = {
  연결상태: string;
  상태코드: string;
  환경: string;
  환경코드: "mainnet" | "demo" | "testnet" | null;
  구분: string[];
  페이퍼: false;
  실거래켜짐: boolean;
  주문보냄: false;
  메시지: string;
  서버시각: string | null;
  최대레버리지: { 종목: string; 최대: string; 최소: string; 간격: string } | null;
  잔고: {
    계정유형: string;
    총자산평가: Money;
    총지갑잔고: Money;
    총사용가능: Money;
    코인: Coin[];
  } | null;
  포지션: Position[] | null;
  미체결주문: OpenOrder[] | null;
  오류코드: string | null;
  계좌마진모드: string;
  계좌마진설명: string;
  레버리지안내: string;
  순차조회: true;
  수집시작시각: string | null;
  수집완료시각: string | null;
  거래소응답시각: string | null;
  계정갱신시각: string | null;
  수신시각: string | null;
  캐시사용: boolean;
  캐시나이초: number;
  데이터나이초: number;
  동시요청공유: boolean;
  지연기준초: 45;
  확인불가기준초: 120;
  신선도표시: string;
  주문판단에사용: false;
};

const STATUSES = new Set(["ok", "BLOCKED", "error", "missing_credentials", "config"]);

function isEnvironment(value: unknown): value is AccountView["환경코드"] {
  return value === null || value === "mainnet" || value === "demo" || value === "testnet";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function isMoney(value: unknown): value is Money {
  return value === null || typeof value === "string";
}

function isText(value: unknown): value is string {
  return typeof value === "string";
}

function isCoin(value: unknown): value is Coin {
  return isRecord(value) && isText(value.코인) && isMoney(value.지갑잔고) && isMoney(value.자산평가);
}

function isPosition(value: unknown): value is Position {
  return (
    isRecord(value) &&
    value.관리함 === false &&
    isText(value.종목) &&
    isText(value.방향) &&
    isText(value.수량) &&
    isText(value.마진모드) &&
    isText(value.포지션모드) &&
    isMoney(value.레버리지) &&
    isMoney(value.진입가) &&
    isMoney(value.표시가) &&
    isMoney(value.청산가) &&
    isMoney(value.미실현손익)
  );
}

function isOrder(value: unknown): value is OpenOrder {
  return (
    isRecord(value) &&
    value.관리함 === false &&
    isText(value.종목) &&
    isText(value.방향) &&
    isText(value.유형) &&
    isText(value.수량) &&
    isText(value.상태) &&
    isText(value.줄이기만) &&
    isMoney(value.가격)
  );
}

export function isAccountView(value: unknown): value is AccountView {
  if (!isRecord(value)) return false;
  const row = value;
  if (!isText(row.상태코드) || !STATUSES.has(row.상태코드)) return false;
  if (!isText(row.메시지) || row.페이퍼 !== false || row.주문보냄 !== false) return false;
  if (typeof row.실거래켜짐 !== "boolean") return false;
  if (!isEnvironment(row.환경코드)) return false;
  if (row.순차조회 !== true || row.주문판단에사용 !== false) return false;
  if (row.지연기준초 !== 45 || row.확인불가기준초 !== 120) return false;
  if (!isText(row.신선도표시) || !isText(row.계좌마진모드) || !isText(row.계좌마진설명)) return false;
  if (!isText(row.레버리지안내)) return false;
  if (typeof row.캐시사용 !== "boolean" || typeof row.동시요청공유 !== "boolean") return false;
  if (typeof row.캐시나이초 !== "number" || typeof row.데이터나이초 !== "number") return false;
  for (const key of ["수집시작시각", "수집완료시각", "거래소응답시각", "계정갱신시각", "수신시각", "서버시각"]) {
    const item = row[key];
    if (item !== null && !isText(item)) return false;
  }
  const wallet = row.잔고;
  const positions = row.포지션;
  const orders = row.미체결주문;
  const walletOk =
    wallet === null ||
    (isRecord(wallet) &&
      isText(wallet.계정유형) &&
      isMoney(wallet.총자산평가) &&
      isMoney(wallet.총지갑잔고) &&
      isMoney(wallet.총사용가능) &&
      Array.isArray(wallet.코인) &&
      wallet.코인.every(isCoin));
  const positionsOk = positions === null || (Array.isArray(positions) && positions.every(isPosition));
  const ordersOk = orders === null || (Array.isArray(orders) && orders.every(isOrder));
  if (!walletOk || !positionsOk || !ordersOk) return false;
  const leverage = row.최대레버리지;
  const leverageOk =
    leverage === null ||
    (isRecord(leverage) &&
      isText(leverage.종목) &&
      isText(leverage.최대) &&
      isText(leverage.최소) &&
      isText(leverage.간격));
  if (!leverageOk) return false;
  if (row.상태코드 === "ok") {
    return wallet !== null && Array.isArray(positions) && Array.isArray(orders) && isText(row.수집완료시각);
  }
  return true;
}

export function dataAgeSeconds(collectedAt: string | null, nowMs: number): number | null {
  if (!collectedAt) return null;
  const parsed = Date.parse(collectedAt);
  if (Number.isNaN(parsed)) return null;
  return Math.max(0, Math.floor((nowMs - parsed) / 1000));
}

export function freshnessLabel(
  age: number | null,
  status: string,
  staleAfter: number,
  unusableAfter: number,
  failed: boolean,
): string {
  if (failed || status !== "ok" || age === null) return "현재 확인 불가";
  if (age >= unusableAfter) return "현재 확인 불가";
  if (age >= staleAfter) return "갱신 지연";
  return "방금 확인함";
}

function text(value: string | null | undefined): string {
  return value && value.trim() ? value : "—";
}

function formatKst(value: string | null): string {
  if (!value) return "시각 없음";
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return "시각 확인 불가";
  const formatted = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(parsed);
  return `${formatted} 한국시간`;
}

type AccountProps = { pollIntervalMs?: number; deadlineMs?: number };

export default function Account({ pollIntervalMs = 15000, deadlineMs = 20000 }: AccountProps) {
  const [view, setView] = useState<AccountView | null>(null);
  const [problem, setProblem] = useState("");
  const [loading, setLoading] = useState(true);
  const [latestFailed, setLatestFailed] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let disposed = false;
    let generation = 0;
    let timer = 0;
    let active: AbortController | null = null;

    const arm = (delay: number) => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => void run(), delay);
    };

    async function run() {
      if (disposed || active) return;
      const myGeneration = ++generation;
      const controller = new AbortController();
      active = controller;
      const deadline = window.setTimeout(() => controller.abort(), deadlineMs);
      try {
        const response = await fetch("/api/v1/account/bybit/view", { signal: controller.signal });
        const body: unknown = response.ok ? await response.json() : null;
        if (disposed || myGeneration !== generation) return;
        if (!response.ok || !isAccountView(body)) {
          setProblem(
            response.ok
              ? "서버 응답 형식이 맞지 않아 잔고를 표시하지 않습니다. 없는 잔고로 바꾸지 않습니다."
              : "화면이 서버에 연결하지 못했습니다. 잔고를 추측하지 않습니다.",
          );
          setLatestFailed(true);
        } else {
          setView(body);
          setProblem("");
          setLatestFailed(false);
        }
      } catch (error) {
        if (disposed || myGeneration !== generation) return;
        if (error instanceof DOMException && error.name === "AbortError") {
          setProblem("응답이 늦어서 이번 조회를 멈췄습니다. 잔고를 추측하지 않습니다.");
        } else {
          setProblem("화면이 서버에 연결하지 못했습니다. 잔고를 추측하지 않습니다.");
        }
        setLatestFailed(true);
      } finally {
        window.clearTimeout(deadline);
        if (active === controller) active = null;
        if (!disposed && myGeneration === generation) {
          setLoading(false);
          arm(pollIntervalMs);
        }
      }
    }

    const onVisible = () => {
      if (document.visibilityState === "visible" && !active && !disposed) {
        window.clearTimeout(timer);
        void run();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    void run();
    return () => {
      disposed = true;
      generation += 1;
      window.clearTimeout(timer);
      active?.abort();
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [deadlineMs, pollIntervalMs]);

  const age = view ? dataAgeSeconds(view.수집완료시각, nowMs) : null;
  const shownFreshness = view
    ? freshnessLabel(age, view.상태코드, view.지연기준초, view.확인불가기준초, latestFailed)
    : "현재 확인 불가";
  const past =
    !!view &&
    (latestFailed || (age !== null && age >= view.지연기준초)) &&
    (view.잔고 !== null || (view.포지션?.length ?? 0) > 0);
  const blocked = view?.상태코드 === "BLOCKED";
  const ready = view?.상태코드 === "ok" && !latestFailed;

  return (
    <main className="account">
      <header className="account-heading">
        <div>
          <p className="eyebrow">잔고만 보기</p>
          <h1>Bybit 잔고</h1>
        </div>
        <span className="safety">읽기만 합니다. 주문은 없습니다.</span>
      </header>
      <p className="account-warning">
        이 화면은 USDT 선물만 봅니다. 주문, 레버리지 변경, 포지션 변경을 하지 않습니다. 이미
        있는 포지션과 주문은 외부에서 연 것이며 이 프로그램이 관리하지 않습니다. 시세
        화면에서는 바이비트 가격을 볼 수 있습니다.
      </p>
      {loading && !view && !problem && (
        <p role="status">불러오는 중. 아직 잔고를 표시하지 않습니다.</p>
      )}
      {problem && (
        <p role="alert" className="error">
          {problem}
        </p>
      )}
      {view && (
        <>
          <section className="account-panel" aria-labelledby="account-status">
            <h2 id="account-status">연결 상태</h2>
            <p role="status">{shownFreshness}</p>
            {!latestFailed && <p>{connectionLabel(view.상태코드)}</p>}
            <p>{latestFailed ? "아래 숫자가 있으면 과거 조회값입니다." : screenText(view.메시지)}</p>
            <p>{view.실거래켜짐 ? "실거래가 켜져 있습니다. 그래도 이 화면은 주문하지 않습니다." : "실거래 꺼짐"}</p>
            <p>이 숫자는 주문 결정에 쓰지 않습니다.</p>
            <p>잔고, 포지션, 주문은 한순간에 같이 받은 값이 아닙니다. 차례로 읽었습니다.</p>
            <p>
              데이터 나이 {age === null ? "확인 불가" : `${age}초`}. 지연 기준 {view.지연기준초}초,
              확인 불가 기준 {view.확인불가기준초}초.
            </p>
            {view.캐시사용 && (
              <p>같은 조회 결과를 다시 보여 줍니다. 저장된 나이는 {view.캐시나이초}초입니다.</p>
            )}
            {view.동시요청공유 && <p>여러 화면이 같은 조회를 함께 기다렸습니다.</p>}
            <p>받은 시각: {formatKst(view.수신시각)}</p>
            <p>거래소 응답 시각: {formatKst(view.거래소응답시각)}</p>
            <p>계정 정보 갱신 시각: {formatKst(view.계정갱신시각)}</p>
            <p>서버 시각: {text(view.서버시각 ? screenText(view.서버시각) : null)}</p>
          </section>
          <section className="account-panel" aria-labelledby="account-env">
            <h2 id="account-env">어떤 계좌인지</h2>
            <p>실계좌는 이 화면에서 읽기만 합니다. 연습 계좌는 추천 승인에서만 확인합니다. 실거래는 꺼져 있습니다.</p>
            <ul>
              {environmentSentences(view.환경코드).map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
            <p>
              계정 마진: {view.계좌마진모드}. {view.계좌마진설명}
            </p>
          </section>
          {blocked && !latestFailed && (
            <p className="account-blocked" role="status">
              연결이 막혔습니다. 잔고 숫자를 만들지 않았습니다.
            </p>
          )}
          {past && <p className="account-past">과거 조회값</p>}
          {ready && view.최대레버리지 && (
            <section className="account-panel" aria-labelledby="account-leverage">
              <h2 id="account-leverage">BTCUSDT 최대 레버리지</h2>
              <p>
                거래소가 알려 준 최대 {view.최대레버리지.최대}배, 최소 {view.최대레버리지.최소}배,
                간격 {view.최대레버리지.간격}. {view.레버리지안내} 이 프로그램은 레버리지를 바꾸지
                않습니다.
              </p>
            </section>
          )}
          <section className="account-panel" aria-labelledby="account-wallet">
            <h2 id="account-wallet">잔고</h2>
            {view.잔고 === null ? (
              <p>잔고를 가져오지 못했습니다.</p>
            ) : (
              <>
                <p>
                  {view.잔고.계정유형.includes("통합") || view.잔고.계정유형.includes("UNIFIED")
                    ? "통합 계좌"
                    : "계좌 종류 확인 필요"}{" "}
                  · 총자산 {text(view.잔고.총자산평가)} · 지갑 {text(view.잔고.총지갑잔고)} · 사용
                  가능 {text(view.잔고.총사용가능)}
                </p>
                <div className="account-table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">코인</th>
                        <th scope="col">지갑 잔고</th>
                        <th scope="col">자산 평가</th>
                      </tr>
                    </thead>
                    <tbody>
                      {view.잔고.코인.map((coin) => (
                        <tr key={coin.코인}>
                          <th scope="row">{coin.코인}</th>
                          <td>{text(coin.지갑잔고)}</td>
                          <td>{text(coin.자산평가)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </section>
          <section className="account-panel" aria-labelledby="account-positions">
            <h2 id="account-positions">포지션</h2>
            {view.포지션 === null ? (
              <p>포지션을 가져오지 못했습니다.</p>
            ) : view.포지션.length === 0 ? (
              <p>열린 포지션이 없습니다.</p>
            ) : (
              <div className="account-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">구분</th>
                      <th scope="col">종목</th>
                      <th scope="col">방향</th>
                      <th scope="col">수량</th>
                      <th scope="col">설정된 배수</th>
                      <th scope="col">마진</th>
                      <th scope="col">진입가</th>
                      <th scope="col">표시가</th>
                      <th scope="col">청산가</th>
                      <th scope="col">미실현 손익</th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.포지션.map((row) => (
                      <tr key={`${row.종목}-${row.방향}-${row.수량}`}>
                        <td>외부에서 연 포지션</td>
                        <th scope="row">{row.종목}</th>
                        <td>{row.방향 === "Buy" ? "매수" : row.방향 === "Sell" ? "매도" : row.방향}</td>
                        <td>{row.수량}</td>
                        <td>{text(row.레버리지)}</td>
                        <td>{row.마진모드}</td>
                        <td>{text(row.진입가)}</td>
                        <td>{text(row.표시가)}</td>
                        <td>{text(row.청산가)}</td>
                        <td>{text(row.미실현손익)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          <section className="account-panel" aria-labelledby="account-orders">
            <h2 id="account-orders">미체결 주문</h2>
            {view.미체결주문 === null ? (
              <p>미체결 주문을 가져오지 못했습니다.</p>
            ) : view.미체결주문.length === 0 ? (
              <p>미체결 주문이 없습니다.</p>
            ) : (
              <div className="account-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">구분</th>
                      <th scope="col">종목</th>
                      <th scope="col">방향</th>
                      <th scope="col">유형</th>
                      <th scope="col">수량</th>
                      <th scope="col">가격</th>
                      <th scope="col">상태</th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.미체결주문.map((row) => (
                      <tr key={`${row.종목}-${row.방향}-${row.수량}-${row.가격 ?? ""}`}>
                        <td>외부에서 연 주문</td>
                        <th scope="row">{row.종목}</th>
                        <td>{row.방향 === "Buy" ? "매수" : row.방향 === "Sell" ? "매도" : row.방향}</td>
                        <td>{row.유형 === "Limit" ? "지정가" : row.유형 === "Market" ? "시장가" : row.유형}</td>
                        <td>{row.수량}</td>
                        <td>{text(row.가격)}</td>
                        <td>
                          {row.상태 === "New"
                            ? "미체결"
                            : row.상태 === "PartiallyFilled"
                              ? "일부 체결"
                              : screenText(row.상태)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </main>
  );
}
