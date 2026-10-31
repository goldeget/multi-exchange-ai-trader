import { useCallback, useEffect, useRef, useState } from "react";
import "./paper.css";

type Position = {instrument:string;side:string;quantity:string;entry_price:string;leverage:number;reserved_margin:string;unrealized_pnl:string;mark:string};
type Fill = {event_seq:number;instrument:string;side:string;role:string;price:string;quantity:string;fee:string};
type Order = {id:string;instrument:string;role:string;status:string;trigger:string|null;quantity:string};
type State = {scenario_id:string;mode:string;synthetic:boolean;wallet_balance:string;available_balance:string;reserved_margin:string;deficit:string;equity:string;closed_net_pnl:string;unrealized_pnl:string;fees:string;latest_sequence:number;marks:Record<string,string>;positions:Position[];fills:Fill[];orders:Order[]};
type Intent = {scenario:string;key:string;command:Record<string,unknown>;title:string};
const pendingKey="paper-pending-command-v1";
class Rejected extends Error {}
function restore():{intent:Intent|null;broken:boolean}{
 try{const raw=localStorage.getItem(pendingKey);if(!raw)return {intent:null,broken:false};const v=JSON.parse(raw);if(v.version!==1||typeof v.intent?.scenario!=="string"||typeof v.intent?.key!=="string"||typeof v.intent?.title!=="string"||!v.intent?.command||typeof v.intent.command!=="object")throw new Error("invalid pending command");return {intent:v.intent,broken:false};}catch{return {intent:null,broken:true};}
}
const root="/api/v1/paper/scenarios";
const numeric=(s:string)=>/^(?:0|[1-9]\d*)(?:\.\d{1,8})?$/.test(s)&&/[1-9]/.test(s);
const pnl=(s:string)=>`${s.startsWith("-")?"가상 손실":"가상 이익"} ${s} USDT`;
async function read<T>(url:string,options?:RequestInit):Promise<T>{
 const response=await fetch(url,options);const body=await response.json();
 if(!response.ok){
  const confirmed=["paper_input","paper_not_found","paper_idempotency_conflict","paper_margin","paper_stale_mark","paper_risk"];
  if(response.status>=400&&response.status<500&&body.mode==="PAPER"&&confirmed.includes(body.reason))throw new Rejected(body.message||"PAPER 요청이 거절됐습니다.");
  throw new Error("Uncertain PAPER response");
 }return body as T;
}
export default function Paper(){
 const [restored]=useState(restore);
 const [ids,setIds]=useState<string[]>([]),[scenario,setScenario]=useState("");
 const [state,setState]=useState<State|null>(null),[audit,setAudit]=useState<unknown>(null);
 const [symbol,setSymbol]=useState("BTC-PAPER"),[quantity,setQuantity]=useState("0.100"),[lev,setLev]=useState("10");
 const [mark,setMark]=useState("60000.0"),[closeQty,setCloseQty]=useState("0.040"),[sl,setSl]=useState(""),[tp,setTp]=useState("");
 const [intent,setIntent]=useState<Intent|null>(restored.intent),[retry,setRetry]=useState(!!restored.intent),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[error,setError]=useState("");
 const inFlight=useRef(false);
 const syncControls=useCallback((value:State,selected:string,reset:boolean)=>{
  const active=value.orders.filter(o=>o.instrument===selected&&["ACCEPTED","TRIGGERED"].includes(o.status));
  setSl(active.find(o=>o.role==="sl")?.trigger??"");setTp(active.find(o=>o.role==="tp")?.trigger??"");
  if(reset){setMark(value.marks[selected]);setQuantity(selected==="BTC-PAPER"?"0.100":"0.10");setCloseQty(selected==="BTC-PAPER"?"0.040":"0.04");}
 },[]);
 async function load(id:string){
  setLoading(true);setState(null);setAudit(null);
  try{const value=await read<State>(`${root}/${id}`);if(value.scenario_id!==id||value.mode!=="PAPER"||!value.synthetic)throw new Error("PAPER 응답을 확인할 수 없습니다.");setState(value);syncControls(value,symbol,true);setAudit(await read(`${root}/${id}/audit`));setError("");}
  catch(e){setError(e instanceof Error?e.message:"PAPER 상태 조회 실패");}finally{setLoading(false);}
 }
 useEffect(()=>{let active=true;void read<{scenarios:string[]}>(root).then(async value=>{if(!active)return;setIds(value.scenarios);const target=restored.intent?.scenario??value.scenarios[0];if(target&&value.scenarios.includes(target)){setScenario(target);const initial=await read<State>(`${root}/${target}`);if(!active)return;if(initial.scenario_id!==target||initial.mode!=="PAPER"||!initial.synthetic)throw new Error("PAPER 응답 불일치");setState(initial);syncControls(initial,"BTC-PAPER",true);setAudit(await read(`${root}/${target}/audit`));setLoading(false);}else setLoading(false);}).catch(()=>{if(active){setError("로컬 PAPER 서버에 연결할 수 없습니다.");setLoading(false);}});return()=>{active=false;};},[syncControls,restored.intent]);
 const position=state?.positions.find(p=>p.instrument===symbol);
 async function perform(action:Intent){
  if(inFlight.current||restored.broken)return;
  try{const pending=restore();if(pending.broken||pending.intent&&pending.intent.key!==action.key)throw new Error("다른 미확정 PAPER 명령이 있습니다. 새로고침 후 같은 키로 확인하세요.");localStorage.setItem(pendingKey,JSON.stringify({version:1,intent:action}));}catch(e){setError(e instanceof Error?e.message:"미확정 명령 저장 실패");return;}
  inFlight.current=true;setBusy(true);setError("");
  let committed=false;
  try{const value=await read<State>(`${root}/${action.scenario}/commands`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({idempotency_key:action.key,command:action.command})});if(value.scenario_id!==action.scenario||value.mode!=="PAPER"||!value.synthetic)throw new Error("PAPER 응답 불일치");setState(value);syncControls(value,symbol,value.scenario_id!==scenario);setScenario(value.scenario_id);setIds(old=>[...new Set([...old,value.scenario_id])]);localStorage.removeItem(pendingKey);setIntent(null);setRetry(false);committed=true;}
  catch(e){if(e instanceof Rejected){localStorage.removeItem(pendingKey);setIntent(null);setRetry(false);setError(e.message);}else{setError("응답을 받지 못했습니다. 저장된 같은 명령 키로 재시도하세요.");setIntent(action);setRetry(true);}}

  finally{inFlight.current=false;setBusy(false);}
  if(committed){try{setAudit(await read(`${root}/${action.scenario}/audit`));}catch{setError("가상 실행은 저장됐으나 원장 조회에 실패했습니다. 상태를 새로고침하세요.");}}
 }
 function prepare(command:Record<string,unknown>,title:string){if(!state||state.scenario_id!==scenario||loading)return;setError("");setRetry(false);setIntent({scenario,key:crypto.randomUUID(),command,title});}
 function enter(side:string){const leverage=Number(lev);if(!numeric(quantity)||!Number.isInteger(leverage)||leverage<1||leverage>20){setError("수량은 양수 소수 문자열, 레버리지는 정수 1~20이어야 합니다.");return;}prepare({kind:"enter",instrument:symbol,side,quantity,leverage},`${symbol} · 가상 ${side.toUpperCase()} · 수량 ${quantity} · ${lev}x`);}
 const disabled=busy||loading||!!intent||restored.broken;
 return <main className="paper">
  <header className="paper-heading"><div><p className="eyebrow">LOCAL PAPER / 교육용 모의 거래</p><h1>PAPER 가상 선물 연습</h1></div><strong className="paper-warning">가상 시세 · 실거래소 시세 아님</strong></header>
  <p className="paper-warning">가상 자금만 사용합니다. 실제 계좌·거래소 주문·실제 수익이 아닙니다. PHASE2A 6시장은 지역 제한으로 BLOCKED 상태입니다.</p>
  <section className="paper-panel"><h2>별도 로컬 시나리오</h2><div className="paper-controls"><label>가상 시나리오<select value={scenario} disabled={disabled||!ids.length} onChange={e=>{setScenario(e.target.value);void load(e.target.value);}}><option value="">새 시나리오를 생성하세요</option>{ids.map(id=><option key={id}>{id}</option>)}</select></label><button disabled={disabled} onClick={()=>{if(inFlight.current)return;const id=`paper-${crypto.randomUUID()}`;void perform({scenario:id,key:crypto.randomUUID(),command:{kind:"create"},title:"새 가상 시나리오"});}}>가상 시나리오 생성</button><button disabled={disabled||!scenario} onClick={()=>void load(scenario)}>상태 새로고침</button></div><p>신규 ID마다 10,000 가상 USDT를 한 번만 시작합니다. 기존 원장 삭제·입금·초기화 기능은 없습니다.</p></section>
  <p role="status">{busy?"가상 명령 처리 중 · 중복 실행 방지":loading?"로컬 원장 확인 중":"가상 원장 상태 · 수동 시나리오 tick만 진행"}</p>
  {restored.broken&&<p role="alert">미확정 PAPER 명령 저장소를 확인할 수 없어 실행을 중단했습니다. 원장을 보존하고 운영자에게 확인하세요.</p>}
  {error&&<p className="paper-error" role="alert">{error}</p>}
  {state&&<>
   <section className="paper-balances" aria-label="가상 계좌"><h2>가상 계좌 · USDT</h2>{([['가상 잔고',state.wallet_balance],['가상 가용증거금',state.available_balance],['예약 증거금',state.reserved_margin],['가상 equity',state.equity],['가상 누적 수수료',state.fees],['가상 부족/부채',state.deficit]] as const).map(([name,value])=><div className="paper-metric" key={name}><span>{name}</span><strong>{value}</strong></div>)}<p>{pnl(state.closed_net_pnl)} · 종료된 수량의 진입/종료 수수료를 뺀 순손익</p><p>미실현: {pnl(state.unrealized_pnl)}</p></section>
   <div className="paper-grid">
    <section className="paper-panel"><h2>가상 시장가 진입</h2><div className="paper-controls"><label>가상 상품<select value={symbol} disabled={disabled} onChange={e=>{setSymbol(e.target.value);syncControls(state,e.target.value,true);}}><option>BTC-PAPER</option><option>ETH-PAPER</option></select></label><label>진입 수량<input inputMode="decimal" value={quantity} disabled={disabled} onChange={e=>setQuantity(e.target.value)}/></label><label>가상 레버리지<input inputMode="numeric" value={lev} disabled={disabled} onChange={e=>setLev(e.target.value)}/></label></div><p>한 방향 netting · 한 상품 진입 후 추가 진입/반대 전환은 지원하지 않습니다. 먼저 종료하세요.</p><div className="paper-actions"><button disabled={disabled||!!position} onClick={()=>enter("long")}>가상 LONG 진입</button><button disabled={disabled||!!position} onClick={()=>enter("short")}>가상 SHORT 진입</button></div></section>
    <section className="paper-panel"><h2>독립 로컬 가상 tick</h2><p>선물 실시간 mark가 아닙니다. Binance 현물도 사용하지 않습니다. 출처: LOCAL_SCENARIO_V1</p><p>현재 {symbol}: <strong>{state.marks[symbol]}</strong> · 마지막 시퀀스 {state.latest_sequence}</p><label>가상 mark 가격<input inputMode="decimal" value={mark} disabled={disabled} onChange={e=>setMark(e.target.value)}/></label><button disabled={disabled} onClick={()=>{if(!numeric(mark)){setError("가상 가격은 양수 소수 문자열이어야 합니다.");return;}prepare({kind:"tick",instrument:symbol,price:mark,sequence:state.latest_sequence+1,timestamp:new Date(Date.now()-1000).toISOString(),source:"LOCAL_SCENARIO_V1"},`${symbol} · 로컬 가상 tick ${mark} · 순서 ${state.latest_sequence+1}`);}}>가상 tick 추가</button><p>시각은 확인 준비 시 생성한 로컬 가상 이벤트 UTC입니다. 미래/120초 초과/역순은 거절됩니다.</p></section>
   </div>
   <section className="paper-panel"><h2>가상 포지션과 보호 조건</h2>{state.positions.length===0?<p>열린 가상 포지션 없음</p>:state.positions.map(p=><p key={p.instrument}>{p.instrument} · 가상 {p.side.toUpperCase()} · <strong>{p.quantity}</strong> · 진입 {p.entry_price} · {p.leverage}x · {pnl(p.unrealized_pnl)}</p>)}
    {position&&<><div className="paper-controls"><label>부분청산 수량<input inputMode="decimal" disabled={disabled} value={closeQty} onChange={e=>setCloseQty(e.target.value)}/></label><button disabled={disabled} onClick={()=>{if(!numeric(closeQty)){setError("부분청산 수량을 확인하세요.");return;}prepare({kind:"close",instrument:symbol,quantity:closeQty},`${symbol} · reduce-only 가상 부분 종료 ${closeQty}`);}}>가상 부분청산</button><button disabled={disabled} onClick={()=>prepare({kind:"close",instrument:symbol,quantity:position.quantity},`${symbol} · reduce-only 가상 전량 종료 ${position.quantity}`)}>가상 전량청산</button></div><div className="paper-controls"><label>가상 SL<input inputMode="decimal" value={sl} disabled={disabled} onChange={e=>setSl(e.target.value)}/></label><label>가상 TP<input inputMode="decimal" value={tp} disabled={disabled} onChange={e=>setTp(e.target.value)}/></label><button disabled={disabled} onClick={()=>{if((sl&&!numeric(sl))||(tp&&!numeric(tp))){setError("SL/TP 소수 가격을 확인하세요.");return;}prepare({kind:"protect",instrument:symbol,sl:sl||null,tp:tp||null},`${symbol} · 가상 SL ${sl||'없음'} / TP ${tp||'없음'} 교체`);}}>SL/TP 설정·변경</button><button disabled={disabled} onClick={()=>prepare({kind:"protect",instrument:symbol,sl:null,tp:null},`${symbol} · 가상 SL/TP 취소`)}>SL/TP 취소</button></div></>}
    <p>SL/TP는 가상 tick에서 native 조건 주문으로 발동합니다. OCO/reduce-only로 초과 종료를 막지만 gap 가격·수수료·slippage로 손실이 커질 수 있습니다.</p>
   </section>
   <section className="paper-panel"><h2>가상 체결 내역</h2><div className="paper-table"><table><thead><tr><th>이벤트/상품</th><th>방향/유형</th><th>체결가</th><th>수량</th><th>수수료 USDT</th></tr></thead><tbody>{state.fills.map((f,i)=><tr key={i}><td>#{f.event_seq} {f.instrument}</td><td>{f.side} · {f.role==='liquidation'?'가상 강제청산(근사)':f.role}</td><td>{f.price}</td><td>{f.quantity}</td><td>{f.fee}</td></tr>)}</tbody></table></div><p>모든 내역은 가상 체결입니다. 수익률·승률·실제 시장 검증 결과가 아닙니다.</p></section>
   <details className="paper-panel"><summary>모의 가정 PAPER-v1 · 지원 범위</summary><p>NautilusTrader 1.231.0 simulated exchange · 1~20x는 설계자가 선택한 교육용 한도입니다. isolated 배분, MMR 0.5%, 체결마다 0.04% fee와 불리한 1 tick slippage. BTC: tick0.1/lot0.001, ETH: tick0.01/lot0.01. 소수 문자열, 명목한도100,000 USDT.</p><p>청산은 mark/격리배분/유지증거금의 근사 교육모델이며 거래소와 정확 일치하지 않습니다. Gap 초과 손실과 음수 잔고를 지우지 않습니다. 미지원: 전략 백테스트, AI, 실거래/Demo, limit 진입, 재량 pending 교체/만료, 거래량 기반 부분체결, funding.</p></details>
   <details className="paper-panel"><summary>가상 감사 원장 · 이벤트/주문/체결/정산</summary><pre>{JSON.stringify(audit,null,2)}</pre></details>
  </>}
  {intent&&<section className="paper-confirm" role="dialog" aria-modal="true" aria-label="가상 실행 확인"><h2>가상 실행 확인</h2><p>{intent.title}</p><p>실거래소로 전송하지 않습니다. 결과 확인 전에는 같은 명령 키로만 재시도합니다. 새로고침·탭 복구 후에도 브라우저 저장소의 미확정 명령을 복원합니다.</p><button disabled={busy||loading} onClick={()=>void perform(intent)}>{retry?"같은 키로 재시도":"확인 · 가상 실행"}</button><button disabled={busy||retry} onClick={()=>{setIntent(null);setRetry(false);setError("");}}>돌아가기</button></section>}
 </main>;
}
