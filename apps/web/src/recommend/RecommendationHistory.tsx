import { useEffect, useRef, useState } from "react";
import { Badge, Definitions, Empty, Icon, Panel } from "../ui/components";
import "./recommendation-history.css";

const cardFields = ["번호", "상태", "거래소", "종목", "상품", "방향", "시세", "진입", "손절", "익절", "수량", "레버리지", "이유", "반대이유", "만료", "안내"] as const;
type StoredCard = Record<typeof cardFields[number], string> & { 설명: "규칙 설명"; 승인가능: boolean; 승인대상?: string; 연습주문상태?: string };
type Effect = { source?: string; execution: "PAPER" | "DEMO"; kind: string; [key: string]: unknown };
export type SavedRecommendation = {
  id: string; source: "RULE"; snapshot: Record<string, unknown>; side: "long" | "short" | "flat";
  reason: string; invalidation: string; missingData: string[]; expiresAt: string;
  reviewState: "pending" | "approving" | "approved" | "rejected" | "expired";
  approvable: boolean; card: StoredCard; execution: "PAPER"; updatedAt: string;
  approval_effects: Effect[]; approval_state: string; approval_blocked: boolean;
};
type Props = { demo?: boolean; onSelect: (card: unknown) => void; refreshKey?: number; onSelectionPending?: () => void };
const states = new Set(["PAPER_ONLY", "DEMO_REGISTERED_UNVERIFIED", "DEMO_UNCERTAIN", "DEMO_CLOSE_FAILED", "DEMO_SUBMISSION_ACKNOWLEDGED", "DEMO_TARGET_UNSUPPORTED", "DEMO_BLOCKED_BY_UNRESOLVED_ATTEMPT"]);
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const date = (value: unknown): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));
const id = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{32}$/.test(value);
function validEffect(value: unknown): value is Effect {
  if (!record(value)) return false;
  if (value.execution === "PAPER") return value.source === "SYNTHETIC" && value.kind === "RULE_POSITION";
  return value.execution === "DEMO" && value.kind === "FIXED_CONNECTIVITY_ROUND_TRIP" && value.symbol === "BTCUSDT" &&
    value.market_type === "perpetual" && value.side === "BUY_THEN_REDUCE_ONLY_SELL" && value.quantity === "0.001" &&
    value.follows_recommendation_side === false && value.verified_closed === false && typeof value.registered === "boolean";
}
export function isSavedRecommendation(value: unknown): value is SavedRecommendation {
  if (!record(value) || !id(value.id) || value.source !== "RULE" || value.execution !== "PAPER") return false;
  const { card, snapshot } = value;
  if (!record(card) || !record(snapshot) || !cardFields.every(key => typeof card[key] === "string") ||
      card.설명 !== "규칙 설명" || typeof card.승인가능 !== "boolean" || card.번호 !== value.id) return false;
  if (!date(value.expiresAt) || !date(value.updatedAt) || !date(card.만료) || Date.parse(card.만료) !== Date.parse(value.expiresAt)) return false;
  if (typeof value.reason !== "string" || typeof value.invalidation !== "string" || !Array.isArray(value.missingData) ||
      !value.missingData.every(item => typeof item === "string") || typeof value.approvable !== "boolean") return false;
  if (!["long", "short", "flat"].includes(String(value.side)) || !["pending", "approving", "approved", "rejected", "expired"].includes(String(value.reviewState))) return false;
  const venues: Record<string, string> = { bybit: "바이비트", binance: "바이낸스", okx: "OKX" };
  const symbols: Record<string, string> = { BTCUSDT: "BTC / USDT", ETHUSDT: "ETH / USDT" };
  const markets: Record<string, string> = { spot: "현물", perpetual: "선물" };
  if (typeof snapshot.exchange !== "string" || !venues[snapshot.exchange] || venues[snapshot.exchange] !== card.거래소 ||
      typeof snapshot.symbol !== "string" || !symbols[snapshot.symbol] || symbols[snapshot.symbol] !== card.종목 ||
      typeof snapshot.market_type !== "string" || !markets[snapshot.market_type] || markets[snapshot.market_type] !== card.상품 ||
      snapshot.side !== value.side || snapshot.explanation !== "규칙 설명" || !date(snapshot.expires_at) ||
      Date.parse(snapshot.expires_at) !== Date.parse(value.expiresAt) || typeof snapshot.fresh !== "boolean" || typeof snapshot.actionable !== "boolean") return false;
  return typeof value.approval_state === "string" && states.has(value.approval_state) && typeof value.approval_blocked === "boolean" &&
    Array.isArray(value.approval_effects) && value.approval_effects.length > 0 && value.approval_effects.every(validEffect) &&
    value.approval_effects.some(effect => effect.execution === "PAPER");
}
function kst(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(value)) + " KST";
}
const statusLabels: Record<SavedRecommendation["reviewState"], string> = { pending: "검토 대기", approving: "처리 확인 중", approved: "가상 반영", rejected: "거절", expired: "만료" };
const fixtureNow = Date.parse("2026-11-01T00:10:03Z");
const paperEffect: Effect = { source: "SYNTHETIC", execution: "PAPER", kind: "RULE_POSITION" };
function fixtureEntry(key: string, symbol: "BTCUSDT" | "ETHUSDT", at: string, expires: string): SavedRecommendation {
  const card: StoredCard = { 번호: key.repeat(32), 상태: "만료", 거래소: "바이비트", 종목: symbol === "BTCUSDT" ? "BTC / USDT" : "ETH / USDT", 상품: "선물", 방향: "관망", 시세: "고정 합성 예시", 진입: "없음", 손절: "없음", 익절: "없음", 수량: "없음", 레버리지: "없음", 이유: symbol === "BTCUSDT" ? "횡보 구간으로 방향성 부족" : "주요 저항대 근접", 반대이유: "현재 시세를 확인한 추천이 아닙니다.", 설명: "규칙 설명", 만료: expires, 승인가능: false, 안내: "고정 예시입니다. 실제 주문과 가상 장부 변경이 없습니다.", 승인대상: "FIXTURE · 실행 없음", 연습주문상태: "외부 주문 없음" };
  return { id: card.번호, source: "RULE", snapshot: { exchange: "bybit", symbol, market_type: "perpetual", side: "flat", explanation: "규칙 설명", expires_at: expires, fresh: false, actionable: false }, side: "flat", reason: card.이유, invalidation: card.반대이유, missingData: ["현재 시세 미확인"], expiresAt: expires, reviewState: "expired", approvable: false, card, execution: "PAPER", updatedAt: at, approval_effects: [paperEffect], approval_state: "PAPER_ONLY", approval_blocked: false };
}
export const HISTORY_FIXTURE: SavedRecommendation[] = [
  fixtureEntry("c", "BTCUSDT", "2026-11-01T00:00:00Z", "2026-11-01T00:02:00Z"),
  fixtureEntry("d", "ETHUSDT", "2026-10-31T23:45:00Z", "2026-10-31T23:47:00Z"),
];

export default function RecommendationHistory({ demo = false, onSelect, refreshKey = 0, onSelectionPending }: Props) {
  const mode = demo ? "fixture" : "actual";
  const [result, setResult] = useState<{ mode: string; rows: SavedRecommendation[]; loading: boolean; error: string }>({ mode, rows: demo ? HISTORY_FIXTURE : [], loading: !demo, error: "" });
  const [detail, setDetail] = useState<{ mode: string; row: SavedRecommendation } | null>(null);
  const [detailError, setDetailError] = useState("");
  const [pending, setPending] = useState("");
  const [nonce, setNonce] = useState(0);
  const [now, setNow] = useState(() => demo ? fixtureNow : Date.now());
  const detailRequest = useRef<AbortController | null>(null);
  const detailGeneration = useRef(0);
  useEffect(() => {
    if (demo) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [demo]);
  useEffect(() => {
    const controller = new AbortController(); let active = true;
    detailGeneration.current += 1; detailRequest.current?.abort();
    async function load() {
      setDetail(null); setDetailError(""); setPending("");
      if (demo) { setResult({ mode, rows: HISTORY_FIXTURE, loading: false, error: "" }); return; }
      setResult({ mode, rows: [], loading: true, error: "" });
      const deadline = window.setTimeout(() => controller.abort(), 10_000);
      try {
        const response = await fetch("/api/v1/recommendations", { method: "GET", signal: controller.signal });
        const body: unknown = await response.json();
        if (controller.signal.aborted) throw new Error("aborted history");
        if (!response.ok || !record(body) || !Array.isArray(body.recommendations) || !body.recommendations.every(isSavedRecommendation)) throw new Error("invalid history");
        const rows = body.recommendations;
        if (new Set(rows.map(row => row.id)).size !== rows.length) throw new Error("duplicate history");
        if (active) setResult({ mode, rows, loading: false, error: "" });
      } catch {
        if (active) setResult({ mode, rows: [], loading: false, error: "저장된 추천 목록을 확인하지 못했습니다. 새 추천이나 빈 기록으로 대체하지 않습니다." });
      } finally { window.clearTimeout(deadline); }
    }
    void load();
    return () => { active = false; controller.abort(); detailGeneration.current += 1; detailRequest.current?.abort(); };
  }, [demo, mode, refreshKey, nonce]);
  const rows = result.mode === mode ? result.rows : [];
  const current = detail?.mode === mode ? detail.row : null;
  const expired = (row: SavedRecommendation) => (demo ? fixtureNow : now) >= Date.parse(row.expiresAt);
  function forward(row: SavedRecommendation) {
    setDetail({ mode, row });
    const isExpired = (demo ? fixtureNow : now) >= Date.parse(row.expiresAt);
    onSelect({ ...row.card, 승인가능: row.card.승인가능 && row.approvable && !row.approval_blocked && !isExpired });
  }
  async function select(row: SavedRecommendation) {
    onSelectionPending?.();
    detailRequest.current?.abort(); const generation = ++detailGeneration.current;
    setDetailError(""); setPending(row.id);
    if (demo) { forward(row); setPending(""); return; }
    const controller = new AbortController(); detailRequest.current = controller;
    const deadline = window.setTimeout(() => controller.abort(), 10_000);
    try {
      const response = await fetch(`/api/v1/recommendations/${row.id}`, { method: "GET", signal: controller.signal });
      const body: unknown = await response.json();
      if (generation !== detailGeneration.current || controller.signal.aborted) return;
      if (!response.ok || !record(body) || !isSavedRecommendation(body.recommendation) || body.recommendation.id !== row.id) throw new Error("invalid detail");
      forward(body.recommendation);
    } catch {
      if (generation === detailGeneration.current) setDetailError("선택한 추천 상세를 확인하지 못했습니다. 이전 값으로 승인하지 마세요.");
    } finally {
      window.clearTimeout(deadline);
      if (generation === detailGeneration.current) { setPending(""); detailRequest.current = null; }
    }
  }
  return <Panel className="recommendation-history" title={<><Icon name="file" size={19} /> 이전 분석 <small>최근 {rows.length}건</small></>}
    aside={<button className="secondary" disabled={result.loading} onClick={() => setNonce(value => value + 1)}><Icon name="refresh" size={17} />{demo ? "예시 다시 보기" : "목록 새로고침"}</button>}>
    {demo && <p className="helper">고정 예시 목록 · 실제 서버를 조회하지 않습니다.</p>}
    {result.loading && <p role="status">저장된 추천을 읽는 중입니다.</p>}
    {result.mode === mode && result.error && <p role="alert" className="warning">{result.error}</p>}
    {detailError && <p role="alert" className="warning">{detailError}</p>}
    {!result.loading && !result.error && rows.length === 0 && <Empty title="저장된 추천이 없습니다." />}
    {rows.length > 0 && <div className="recommendation-history-table"><table><thead><tr>{["기록 갱신 시각", "종목", "결과 · 출처", "주요 근거", "유효시간", "상세"].map(text => <th key={text} scope="col">{text}</th>)}</tr></thead><tbody>{rows.map(row => <tr key={row.id} aria-selected={current?.id === row.id}>
      <td data-label="기록 갱신">{kst(row.updatedAt)}</td><th scope="row">{row.card.종목}</th><td data-label="결과"><Badge>{expired(row) ? "만료" : statusLabels[row.reviewState]}</Badge><small>RULE</small></td><td data-label="근거">{row.reason}</td><td data-label="유효시간">{kst(row.expiresAt)}</td><td><button className="text-button" aria-pressed={current?.id === row.id} aria-label={`추천 열기 ${row.card.종목} ${kst(row.updatedAt)}`} onClick={() => void select(row)}>{pending === row.id ? "확인 중" : "추천 열기"}<Icon name="arrow" size={16} /></button></td>
    </tr>)}</tbody></table></div>}
    {current && <section className="recommendation-history-detail" aria-label="저장된 추천 상세"><Badge>RULE · 규칙 설명</Badge><Definitions rows={[["기록", current.id], ["기준 시세", current.card.시세], ["반대 · 무효 조건", current.invalidation || "별도 조건 미기록"], ["누락 데이터", current.missingData.length ? current.missingData.join(" · ") : "서버의 누락 표시 없음 · 실제 체결은 별도 확인"], ["유효시간", kst(current.expiresAt)], ["승인 대상", current.card.승인대상 ?? "서버의 승인 대상 설명 확인 필요"]]} />
      {current.approval_effects.some(effect => effect.execution === "DEMO") && <p className="warning">DEMO 연결 시험 포함 · 추천 방향과 독립적인 BTCUSDT 0.001 고정 매수 후 감축 요청입니다. 접수 기록은 실제 체결·종료 확인이 아닙니다.</p>}
      {current.approval_blocked && <p className="warning" role="status">외부 요청 상태를 확인할 때까지 승인할 수 없습니다. {current.card.연습주문상태}</p>}
      <p className="helper">{current.card.안내} 목록에서 열기는 읽기만 하며 승인 요청을 보내지 않습니다.</p>
    </section>}
  </Panel>;
}
