import { useEffect, useState } from "react";

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
};

function isView(value: unknown): value is AccountView {
  if (!value || typeof value !== "object") return false;
  const row = value as { 상태코드?: unknown; 메시지?: unknown; 페이퍼?: unknown };
  return typeof row.상태코드 === "string" && typeof row.메시지 === "string" && row.페이퍼 === false;
}

function text(value: string | null | undefined): string {
  return value && value.trim() ? value : "—";
}

export default function Account() {
  const [view, setView] = useState<AccountView | null>(null);
  const [problem, setProblem] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let disposed = false;
    const controllers = new Set<AbortController>();
    async function load() {
      const controller = new AbortController();
      controllers.add(controller);
      try {
        const response = await fetch("/api/v1/account/bybit/view", { signal: controller.signal });
        if (!response.ok) throw new Error("http");
        const body: unknown = await response.json();
        if (!isView(body)) throw new Error("shape");
        if (!disposed) {
          setView(body);
          setProblem("");
        }
      } catch (error) {
        if (disposed || (error instanceof DOMException && error.name === "AbortError")) return;
        if (!disposed) {
          setView(null);
          setProblem("화면이 서버에 연결하지 못했습니다. 잔고를 추측하지 않습니다.");
        }
      } finally {
        controllers.delete(controller);
        if (!disposed) setLoading(false);
      }
    }
    void load();
    const timer = window.setInterval(() => void load(), 15000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
      controllers.forEach((controller) => controller.abort());
    };
  }, []);

  const blocked = view?.상태코드 === "BLOCKED";
  const ready = view?.상태코드 === "ok";

  return (
    <main className="account">
      <header className="account-heading">
        <div>
          <p className="eyebrow">BYBIT LINEAR / READ ONLY</p>
          <h1>Bybit 계좌 조회</h1>
        </div>
        <span className="safety">읽기 전용 · 주문 없음</span>
      </header>
      <p className="account-warning">
        이 화면은 USDT 무기한 선물을 조회만 합니다. 주문, 레버리지 변경, 포지션 변경을 하지
        않습니다. 이미 있는 포지션과 주문은 외부이며 이 프로그램이 관리하지 않습니다. 공개 시세
        화면의 Bybit 차단 설정은 그대로입니다.
      </p>
      {loading && !view && !problem && (
        <p role="status">불러오는 중. 아직 잔고를 표시하지 않습니다.</p>
      )}
      {problem && (
        <p role="status" className="error">
          {problem}
        </p>
      )}
      {view && (
        <>
          <section className="account-panel" aria-labelledby="account-status">
            <h2 id="account-status">연결 상태</h2>
            <p role="status">{view.연결상태}</p>
            <p>{view.메시지}</p>
            <p>{view.실거래켜짐 ? "실거래 설정: 켜짐. 그래도 주문은 보내지 않습니다." : "실거래 설정: 꺼짐."}</p>
            <p>서버 시각: {text(view.서버시각)}</p>
          </section>
          <section className="account-panel" aria-labelledby="account-env">
            <h2 id="account-env">어떤 계좌인지</h2>
            <ul>
              {view.구분.map((item) => (
                <li key={item}>{item}</li>
              ))}
              <li>메인넷: {view.환경코드 === "mainnet" ? "예" : "아니오"}</li>
              <li>데모: {view.환경코드 === "demo" ? "예" : "아니오"}</li>
              <li>페이퍼: 아니오. PAPER 탭만 가상 거래입니다.</li>
            </ul>
          </section>
          {blocked && (
            <p className="account-blocked" role="status">
              BLOCKED
            </p>
          )}
          {ready && view.최대레버리지 && (
            <section className="account-panel" aria-labelledby="account-leverage">
              <h2 id="account-leverage">BTCUSDT 최대 레버리지</h2>
              <p>
                거래소가 알려 준 최대 {view.최대레버리지.최대}배, 최소 {view.최대레버리지.최소}배,
                간격 {view.최대레버리지.간격}. 이 프로그램은 레버리지를 바꾸지 않습니다.
              </p>
            </section>
          )}
          <section className="account-panel" aria-labelledby="account-wallet">
            <h2 id="account-wallet">잔고</h2>
            {view.잔고 === null ? (
              <p>숫자를 가져오지 못했습니다.</p>
            ) : (
              <>
                <p>
                  {view.잔고.계정유형} · 총자산 {text(view.잔고.총자산평가)} · 지갑{" "}
                  {text(view.잔고.총지갑잔고)} · 사용 가능 {text(view.잔고.총사용가능)}
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
              <p>숫자를 가져오지 못했습니다.</p>
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
                      <th scope="col">레버리지</th>
                      <th scope="col">진입가</th>
                      <th scope="col">표시가</th>
                      <th scope="col">청산가</th>
                      <th scope="col">미실현 손익</th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.포지션.map((row) => (
                      <tr key={`${row.종목}-${row.방향}-${row.수량}`}>
                        <td>{row.구분}</td>
                        <th scope="row">{row.종목}</th>
                        <td>{row.방향}</td>
                        <td>{row.수량}</td>
                        <td>{text(row.레버리지)}</td>
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
              <p>숫자를 가져오지 못했습니다.</p>
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
                        <td>{row.구분}</td>
                        <th scope="row">{row.종목}</th>
                        <td>{row.방향}</td>
                        <td>{row.유형}</td>
                        <td>{row.수량}</td>
                        <td>{text(row.가격)}</td>
                        <td>{row.상태}</td>
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
