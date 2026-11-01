import { useEffect, useState, useRef, useCallback } from "react";
import {Badge,Definitions,Dialog,Empty,Panel} from '../ui/components';
import CandleChart from '../chart/CandleChart';
import RecommendationHistory from './RecommendationHistory';
import {fixtureCandles} from '../visual/scenarios';

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
  승인대상?:string;
  연습주문상태?:string;
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

export default function Recommend({demo=false,polished=false}:{demo?:boolean;polished?:boolean}={}) {
  const [venue, setVenue] = useState("바이비트");
  const [symbol, setSymbol] = useState("BTC / USDT");
  const [product, setProduct] = useState("선물");
  const [nonce, setNonce] = useState(0);
  const [selectionMode,setSelectionMode]=useState<'preview'|'saved'>('preview');
  const [card, setCard] = useState<Card | null>(()=>demo?demoCard:null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(!demo);
  const [busy, setBusy] = useState(false);
  const selectionGeneration = useRef(0);
  const [now,setNow]=useState(()=>demo?Date.parse('2026-11-01T00:10:03Z'):Date.now());
  const [checks,setChecks]=useState([false,false,false]),[review,setReview]=useState(false);
  const closeReview=useCallback(()=>setReview(false),[]);
  useEffect(()=>{if(demo)return;const timer=window.setInterval(()=>setNow(Date.now()),1000);return()=>window.clearInterval(timer)},[demo]);
  const expired=!!card && (!Number.isFinite(Date.parse(card.만료))||now>=Date.parse(card.만료));
  function changeSelection(setter:(value:string)=>void,value:string){selectionGeneration.current+=1;setSelectionMode('preview');setCard(null);setMessage('');setter(value);setChecks([false,false,false]);if(demo)setCard({...demoCard,종목:setter===setSymbol?value:symbol,거래소:setter===setVenue?value:venue,상품:setter===setProduct?value:product});}

  useEffect(() => {
    if(demo||selectionMode==='saved')return;
    const loadGeneration=selectionGeneration.current;
    const controller = new AbortController();
    let active = true;
    async function load() {
      setLoading(true);
      setCard(null);
      setError("");
      try {
        const response = await fetch("/api/v1/recommendations/preview", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ 거래소: venue, 종목: symbol, 상품: product }),
          signal: controller.signal,
        });
        const body: unknown = await response.json();
        if (!active||loadGeneration!==selectionGeneration.current) return;
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
  }, [venue, symbol, product, nonce,demo,selectionMode]);

  function clearSavedSelection(){selectionGeneration.current++;setSelectionMode('saved');setLoading(false);setCard(null);setReview(false);setChecks([false,false,false]);setMessage('');}
  function selectSaved(value:unknown){setSelectionMode('saved');setLoading(false);if(!isCard(value)){setError('저장된 추천 응답을 확인하지 못했습니다.');return}selectionGeneration.current++;setCard(value);setVenue(value.거래소);setSymbol(value.종목);setProduct(value.상품);setReview(false);setChecks([false,false,false]);}
  async function decide(kind: "approve" | "reject") {
    if(demo){setMessage("고정 예시를 검토했습니다. 실제 주문이나 장부 변경은 없습니다.");setReview(false);return;}
    if (!card || busy || (kind === "approve" && expired) || !/^[a-f0-9]{32}$/.test(card.번호)) {
      setError("추천 번호를 확인하지 못했습니다. 주문은 없습니다.");
      return;
    }
    const generation=selectionGeneration.current;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`/api/v1/recommendations/${card.번호}/${kind}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const body: unknown = await response.json();
      if(generation!==selectionGeneration.current)return;
      if (!isEnvelope(body)) {
        setError("승인 결과를 확인하지 못했습니다. 새 추천으로 반복하지 말고 서버 결과를 대조하세요.");
        return;
      }
      setCard(body.카드);
      setMessage(body.메시지);
      if (!response.ok) setError(body.메시지 || "처리하지 못했습니다. 실계좌 주문은 없습니다.");
    } catch {
      if(generation!==selectionGeneration.current)return;
      setError("승인 결과를 확인하지 못했습니다. DEMO 요청이 접수됐을 수 있으므로 기존 요청을 대조하세요.");
    } finally {
      setBusy(false);
    }
  }


  if(polished)return <><Panel className="analysis-meta"><label className="field"><span>거래 페어</span><select value={symbol} onChange={e=>changeSelection(setSymbol,e.target.value)} disabled={busy}><option>BTC / USDT</option><option>ETH / USDT</option></select></label><label className="field"><span>거래소</span><select value={venue} onChange={e=>changeSelection(setVenue,e.target.value)} disabled={busy}><option>바이비트</option><option>바이낸스</option><option>OKX</option></select></label><label className="field"><span>상품</span><select value={product} onChange={e=>changeSelection(setProduct,e.target.value)} disabled={busy}><option>선물</option><option>현물</option></select></label><div><span>분석 출처</span><Badge>RULE · 규칙 추천</Badge></div><div><span>추천 유효시간</span><strong>{card?koreanTime(card.만료):'—'}</strong></div><button className="secondary" disabled={loading||busy} onClick={()=>{setSelectionMode('preview');setNonce(n=>n+1);if(demo)setMessage('고정 예시입니다. 외부 분석을 호출하지 않습니다.')}}>다시 확인</button></Panel>{error&&<p className="production-note" role="alert">{error}</p>}{message&&<p className="info-box" role="status">{message}</p>}{loading&&!card?<Panel><p role="status">규칙 추천을 확인하는 중입니다.</p></Panel>:card?<div className="grid equal"><Panel title="규칙 분석 결과"><div className={'candidate '+(card.방향.includes('오르')?'up':'down')}>{card.방향.includes('오르')?'롱 후보':card.방향.includes('내리')?'숏 후보':card.방향}<Badge tone="up">{expired?'유효시간 지남':card.상태}</Badge></div><p>{card.이유}</p><p className="helper">외부 AI 모델을 호출하지 않는 규칙 설명입니다.</p><div className="rule-section"><h3>📄 분석 근거</h3><Definitions rows={[["진입",card.진입],["손절",card.손절],["익절",card.익절],["가상 수량",card.수량],["가상 레버리지",card.레버리지]]}/></div><div className="rule-section"><h3 className="up">⚠ 반대 · 무효 조건</h3><p>{card.반대이유}</p></div><div className="warning-box"><strong>누락 데이터 · 한계</strong><p>펀딩비, 미결제약정과 실제 체결 가능성은 이 규칙만으로 검증하지 않습니다.</p></div></Panel><div className="stack"><Panel title={symbol+' · 분석 참고'} className="market-chart">{demo?<CandleChart candles={fixtureCandles}/>:<Empty title="분석 시점의 근거를 확인하세요." detail="추천 응답의 기준 시세와 진입·손절·익절 값을 사용합니다."/>}<p className="helper">{demo?'합성 고정 예시 · 1시간봉':card.시세}</p></Panel><Panel title="주문 검토 전 확인"><div className="info-box"><strong>승인 대상과 주문 효과</strong><p>{card.승인대상??'바이비트 DEMO 연결 시험이 포함될 수 있습니다. 등록과 실행 권한을 별도 확인하세요.'}</p><p>{card.안내}</p>{card.연습주문상태&&<Badge tone="warning">{card.연습주문상태}</Badge>}</div>{['가격과 추천 시점 재확인','위험 한도와 독립 장부 확인','승인 대상 · 유효시간 확인'].map((s,i)=><label className="checkbox" key={s}><input type="checkbox" checked={checks[i]} disabled={busy||expired} onChange={e=>setChecks(v=>v.map((x,j)=>i===j?e.target.checked:x))}/><span>{s}</span></label>)}<button className="primary full" disabled={busy||expired||!card.승인가능||!checks.every(Boolean)} onClick={()=>setReview(true)}>승인 효과 검토 →</button><button className="text-button full" disabled={busy||card.상태!=='대기'} onClick={()=>void decide('reject')}>추천 거절</button>{expired&&<p className="warning" role="status">추천이 만료되어 승인할 수 없습니다.</p>}</Panel></div></div>:<Panel><Empty title="지금은 검토할 추천이 없습니다."/></Panel>}{review&&card&&<Dialog title="추천 승인 효과 확인" onClose={closeReview}><Definitions rows={[["추천",card.종목+' · '+card.방향],["출처","RULE · 규칙"],["승인 대상",card.승인대상??'바이비트 DEMO 연결 시험 가능'],["유효시간",koreanTime(card.만료)]]}/><p className="warning">{card.안내}</p><p>DEMO 연결 시험이 포함된 경우 추천 방향과 독립적인 고정 매수·감축 요청입니다. 요청 접수는 실제 체결과 종료 확인을 뜻하지 않습니다.</p><button className="primary full" disabled={busy||expired||!card.승인가능} onClick={()=>{setReview(false);void decide('approve')}}>{demo?'예시 승인 검토 확인':'설명한 승인 효과 실행'}</button></Dialog>}<RecommendationHistory demo={demo} refreshKey={nonce} onSelectionPending={clearSavedSelection} onSelect={selectSaved}/></>;
  return (
    <main className="recommend">
      <header className="recommend-heading">
        <div>
          <p className="eyebrow">규칙으로 본 제안</p>
          <h1>추천</h1>
        </div>
      </header>
      <p className="recommend-warning">
        승인은 바이비트 연습 계좌의 가장 작은 비트코인 선물 주문을 넣었다가 바로 닫습니다. 실계좌로 주문을 보내지 않습니다. 실거래는 꺼져 있습니다.
      </p>
      <section className="recommend-panel">
        <div className="recommend-choices">
          <label>
            거래소
            <select value={venue} onChange={(event) => changeSelection(setVenue,event.target.value)}>
              <option>바이낸스</option>
              <option>바이비트</option>
              <option>OKX</option>
            </select>
          </label>
          <label>
            종목
            <select value={symbol} onChange={(event) => changeSelection(setSymbol,event.target.value)}>
              <option>BTC / USDT</option>
              <option>ETH / USDT</option>
            </select>
          </label>
          <label>
            현물/선물
            <select value={product} onChange={(event) => changeSelection(setProduct,event.target.value)}>
              <option>현물</option>
              <option>선물</option>
            </select>
          </label>
          <button type="button" onClick={() => {setSelectionMode('preview');setNonce((value) => value + 1)}} disabled={loading || busy}>
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
            <p><span className="badge">RULE · 규칙 추천</span> {card.안내}</p>{expired&&<p role="status" className="warning">추천 유효시간이 지났습니다. 새로 확인하세요.</p>}
            <div className="recommend-actions">
              <button
                type="button"
                disabled={!card.승인가능 || busy || expired}
                onClick={() => void decide("approve")}
              >
                {card.상태 === "처리 중" ? "연습 계좌로 다시 시도" : "연습 계좌에 넣고 바로 닫기"}
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

const demoCard:Card={번호:'a'.repeat(32),상태:'대기',거래소:'바이비트',종목:'BTC / USDT',상품:'선물',방향:'오르면 이익',시세:'고정 합성 예시 65,210.40 USDT',진입:'65,210.40 USDT',손절:'64,800.00 USDT',익절:'66,030.80 USDT',수량:'0.001 BTC',레버리지:'2 배',이유:'단기 평균이 장기 평균을 상회하고 있습니다.',반대이유:'직전 저점 이탈 시 후보 무효. 실제 체결은 별도 검증이 필요합니다.',설명:'규칙 설명',만료:'2026-11-01T00:12:03Z',승인가능:true,안내:'고정 예시에서는 실제 주문과 가상 장부 변경이 없습니다.',승인대상:'FIXTURE · 실행 없음',연습주문상태:'외부 주문 없음'};
