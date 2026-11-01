import { useEffect, useState } from "react";

type Card = {
  번호: string;
  상태: string;
  거래소: string;
  종목: string;
  상품: string;
  방향: string;
  시세: string;
  진입: string;
  손절: string;
  익절: string;
  수량: string;
  레버리지: string;
  이유: string;
  반대이유: string;
  설명: string;
  만료: string;
  승인가능: boolean;
  안내: string;
};

type Envelope = {
  카드: Card | null;
  메시지: string;
  실계좌주문: false;
  가상반영: boolean;
};

const fields = [
  "번호",
  "상태",
  "거래소",
  "종목",
  "상품",
  "방향",
  "시세",
  "진입",
  "손절",
  "익절",
  "수량",
  "레버리지",
  "이유",
  "반대이유",
  "만료",
  "안내",
] as const;

function isCard(value: unknown): value is Card {
  if (!value || typeof value !== "object") return false;
  const row = value as Record<string, unknown>;
  if (!fields.every((key) => typeof row[key] === "string")) return false;
  return row.설명 === "규칙 설명" && typeof row.승인가능 === "boolean";
}

function isEnvelope(value: unknown): value is Envelope {
  if (!value || typeof value !== "object") return false;
  const row = value as Record<string, unknown>;
  if (row.실계좌주문 !== false || typeof row.메시지 !== "string" || typeof row.가상반영 !== "boolean") {
    return false;
  }
  return row.카드 === null || isCard(row.카드);
}

function koreanTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "시각 확인 중";
  const text = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date);
  return `${text} 한국시간`;
}

const rows: Array<[string, (card: Card) => string]> = [
  ["거래소", (card) => card.거래소],
  ["종목", (card) => card.종목],
  ["현물/선물", (card) => card.상품],
  ["방향", (card) => card.방향],
  ["시세", (card) => card.시세],
  ["진입", (card) => card.진입],
  ["손절", (card) => card.손절],
  ["익절", (card) => card.익절],
  ["수량", (card) => card.수량],
  ["레버리지", (card) => card.레버리지],
  ["이유", (card) => card.이유],
  ["하지 않을 이유", (card) => card.반대이유],
  ["설명", (card) => card.설명],
  ["만료", (card) => koreanTime(card.만료)],
];

export default function Recommend() {
  const [venue, setVenue] = useState("바이비트");
  const [symbol, setSymbol] = useState("BTC / USDT");
  const [product, setProduct] = useState("선물");
  const [nonce, setNonce] = useState(0);
  const [card, setCard] = useState<Card | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    async function load() {
      setLoading(true);
      setError("");
      try {
        const response = await fetch("/api/v1/recommendations/preview", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ 거래소: venue, 종목: symbol, 상품: product }),
          signal: controller.signal,
        });
        const body: unknown = await response.json();
        if (!active) return;
        if (!isEnvelope(body)) {
          setCard(null);
          setMessage("");
          setError("추천 응답을 확인하지 못했습니다. 주문은 없습니다.");
          return;
        }
        setCard(body.카드);
        setMessage(body.메시지);
        setError(response.ok ? "" : body.메시지 || "추천을 만들지 못했습니다. 주문은 없습니다.");
      } catch (caught) {
        if (!active || (caught instanceof DOMException && caught.name === "AbortError")) return;
        setCard(null);
        setError("추천 화면에 연결하지 못했습니다. 주문은 없습니다.");
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
      controller.abort();
    };
  }, [venue, symbol, product, nonce]);

  async function decide(kind: "approve" | "reject") {
    if (!card || busy || !/^[a-f0-9]{32}$/.test(card.번호)) {
      setError("추천 번호를 확인하지 못했습니다. 주문은 없습니다.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`/api/v1/recommendations/${card.번호}/${kind}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const body: unknown = await response.json();
      if (!isEnvelope(body)) {
        setError("추천 응답을 확인하지 못했습니다. 주문은 없습니다.");
        return;
      }
      setCard(body.카드);
      setMessage(body.메시지);
      if (!response.ok) setError(body.메시지 || "처리하지 못했습니다. 실계좌 주문은 없습니다.");
    } catch {
      setError("추천 화면에 연결하지 못했습니다. 주문은 없습니다.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="recommend">
      <header className="recommend-heading">
        <div>
          <p className="eyebrow">규칙으로 본 제안</p>
          <h1>추천</h1>
        </div>
      </header>
      <p className="recommend-warning">
        승인은 바이비트 연습 계좌로만 갑니다. 실계좌로 주문을 보내지 않습니다. 실거래는 꺼져 있습니다.
      </p>
      <section className="recommend-panel">
        <div className="recommend-choices">
          <label>
            거래소
            <select value={venue} onChange={(event) => setVenue(event.target.value)}>
              <option>바이낸스</option>
              <option>바이비트</option>
              <option>OKX</option>
            </select>
          </label>
          <label>
            종목
            <select value={symbol} onChange={(event) => setSymbol(event.target.value)}>
              <option>BTC / USDT</option>
              <option>ETH / USDT</option>
            </select>
          </label>
          <label>
            현물/선물
            <select value={product} onChange={(event) => setProduct(event.target.value)}>
              <option>현물</option>
              <option>선물</option>
            </select>
          </label>
          <button type="button" onClick={() => setNonce((value) => value + 1)} disabled={loading || busy}>
            다시 보기
          </button>
        </div>
        {loading && <p>추천을 계산하는 중입니다.</p>}
        {error && (
          <p className="recommend-error" role="alert">
            {error}
          </p>
        )}
        {message && message !== error && <p>{message}</p>}
        {card && (
          <article aria-label="추천 내용">
            <dl className="recommend-card">
              {rows.map(([label, value]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{value(card)}</dd>
                </div>
              ))}
            </dl>
            <p>{card.안내}</p>
            <div className="recommend-actions">
              <button
                type="button"
                disabled={!card.승인가능 || busy}
                onClick={() => void decide("approve")}
              >
                {card.상태 === "처리 중" ? "연습 계좌로 다시 시도" : "연습 계좌로 승인"}
              </button>
              <button type="button" disabled={card.상태 !== "대기" || busy} onClick={() => void decide("reject")}>
                거절
              </button>
            </div>
          </article>
        )}
      </section>
    </main>
  );
}
