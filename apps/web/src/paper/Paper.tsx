import { useCallback, useEffect, useRef, useState } from "react";
import { instrumentLabel, roleLabel, screenText, sideLabel } from "../ko";
import "./paper.css";

type Position = {instrument:string;side:string;quantity:string;entry_price:string;leverage:number;reserved_margin:string;unrealized_pnl:string;mark:string};
type Fill = {event_seq:number;instrument:string;side:string;role:string;price:string;quantity:string;fee:string};
type Order = {id:string;instrument:string;role:string;status:string;trigger:string|null;quantity:string};
type State = {scenario_id:string;mode:string;synthetic:boolean;wallet_balance:string;available_balance:string;reserved_margin:string;deficit:string;equity:string;closed_net_pnl:string;unrealized_pnl:string;fees:string;latest_sequence:number;marks:Record<string,string>;positions:Position[];fills:Fill[];orders:Order[]};
type Intent = {scenario:string;key:string;command:Record<string,unknown>;title:string;previewId?:string;expiresAt?:string;costs?:{estimated_fee:string;available_balance_after:string}};
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
  const confirmed=["paper_input","paper_review_changed_or_expired","paper_review_not_found","paper_not_found","paper_idempotency_conflict","paper_margin","paper_stale_mark","paper_risk"];
  if(response.status>=400&&response.status<500&&body.mode==="PAPER"&&confirmed.includes(body.reason))throw new Rejected(body.message||"연습 주문이 거절되었습니다.");
  throw new Error("응답을 확인하지 못했습니다.");
 }return body as T;
}
export default function Paper({reviewed=false,canEnter=true}:{reviewed?:boolean;canEnter?:boolean}={}){
 const [restored]=useState(restore);
 const [ids,setIds]=useState<string[]>([]),[scenario,setScenario]=useState("");
 const [state,setState]=useState<State|null>(null),[audit,setAudit]=useState<unknown>(null);
 const [symbol,setSymbol]=useState("BTC-PAPER"),[quantity,setQuantity]=useState("0.100"),[lev,setLev]=useState("10");
 const [mark,setMark]=useState("60000.0"),[closeQty,setCloseQty]=useState("0.040"),[sl,setSl]=useState(""),[tp,setTp]=useState("");
 const[previewBusy,setPreviewBusy]=useState(false);
 const [intent,setIntent]=useState<Intent|null>(restored.intent),[retry,setRetry]=useState(!!restored.intent),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[error,setError]=useState("");
 const inFlight=useRef(false);
 const syncControls=useCallback((value:State,selected:string,reset:boolean)=>{
  const active=value.orders.filter(o=>o.instrument===selected&&["ACCEPTED","TRIGGERED"].includes(o.status));
  setSl(active.find(o=>o.role==="sl")?.trigger??"");setTp(active.find(o=>o.role==="tp")?.trigger??"");
  if(reset){setMark(value.marks[selected]);setQuantity(selected==="BTC-PAPER"?"0.100":"0.10");setCloseQty(selected==="BTC-PAPER"?"0.040":"0.04");}
 },[]);
 async function load(id:string){
  setLoading(true);setState(null);setAudit(null);
  try{const value=await read<State>(`${root}/${id}`);if(value.scenario_id!==id||value.mode!=="PAPER"||!value.synthetic)throw new Error("가상 거래 응답을 확인하지 못했습니다.");setState(value);syncControls(value,symbol,true);setAudit(await read(`${root}/${id}/audit`));setError("");}
  catch(e){setError(e instanceof Error?e.message:"가상 거래 상태를 불러오지 못했습니다.");}finally{setLoading(false);}
 }
 useEffect(()=>{let active=true;void read<{scenarios:string[]}>(root).then(async value=>{if(!active)return;setIds(value.scenarios);const target=restored.intent?.scenario??value.scenarios[0];if(target&&value.scenarios.includes(target)){setScenario(target);const initial=await read<State>(`${root}/${target}`);if(!active)return;if(initial.scenario_id!==target||initial.mode!=="PAPER"||!initial.synthetic)throw new Error("가상 거래 응답이 맞지 않습니다.");setState(initial);syncControls(initial,"BTC-PAPER",true);setAudit(await read(`${root}/${target}/audit`));setLoading(false);}else setLoading(false);}).catch(()=>{if(active){setError("가상 거래 화면에 연결하지 못했습니다.");setLoading(false);}});return()=>{active=false;};},[syncControls,restored.intent]);
 const position=state?.positions.find(p=>p.instrument===symbol);
 async function perform(action:Intent){
  if(inFlight.current||restored.broken)return;
  try{const pending=restore();if(pending.broken||pending.intent&&pending.intent.key!==action.key)throw new Error("다른 끝나지 않은 가상 주문이 있습니다. 화면을 새로고침한 뒤 같은 요청으로 확인하세요.");localStorage.setItem(pendingKey,JSON.stringify({version:1,intent:action}));}catch(e){setError(e instanceof Error?e.message:"끝나지 않은 요청을 저장하지 못했습니다.");return;}
  inFlight.current=true;setBusy(true);setError("");
  let committed=false;
  try{const result=await read<State|{state:State;idempotency_key:string}>(action.previewId?"/api/v1/paper/order-confirmations":`${root}/${action.scenario}/commands`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(action.previewId?{preview_id:action.previewId,idempotency_key:action.key}:{idempotency_key:action.key,command:action.command})});const value=action.previewId?(result as {state:State;idempotency_key:string}).state:result as State;if(action.previewId&&(result as {idempotency_key:string}).idempotency_key!==action.key)throw new Error("요청 대조에 실패했습니다.");if(value.scenario_id!==action.scenario||value.mode!=="PAPER"||!value.synthetic)throw new Error("가상 거래 응답이 맞지 않습니다.");setState(value);syncControls(value,symbol,value.scenario_id!==scenario);setScenario(value.scenario_id);setIds(old=>[...new Set([...old,value.scenario_id])]);localStorage.removeItem(pendingKey);setIntent(null);setRetry(false);committed=true;}
  catch(e){if(e instanceof Rejected){localStorage.removeItem(pendingKey);setIntent(null);setRetry(false);setError(e.message);}else{setError("응답을 받지 못했습니다. 저장된 같은 요청으로 다시 시도하세요.");setIntent(action);setRetry(true);}}

  finally{inFlight.current=false;setBusy(false);}
  if(committed){try{setAudit(await read(`${root}/${action.scenario}/audit`));}catch{setError("가상 실행은 저장됐지만 기록을 불러오지 못했습니다. 다시 불러오세요.");}}
 }
 function prepare(command:Record<string,unknown>,title:string){if(!state||state.scenario_id!==scenario||loading||previewBusy)return;setError('');setRetry(false);const action:Intent={scenario,key:crypto.randomUUID(),command,title};if(!reviewed||!['enter','close','protect'].includes(String(command.kind))){setIntent(action);return}setPreviewBusy(true);void read<{preview_id:string;scenario_id:string;expires_at:string;costs:Intent['costs']}>('/api/v1/paper/order-previews',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scenario_id:scenario,product:'SYNTHETIC_PERPETUAL',order_type:'MARKET',config_revision:'PAPER-v1',command})}).then(v=>{if(v.scenario_id!==action.scenario||typeof v.preview_id!=='string'||!Number.isFinite(Date.parse(v.expires_at)))throw new Error();setIntent({...action,previewId:v.preview_id,expiresAt:v.expires_at,costs:v.costs})}).catch(()=>setError('서버가 주문 검토를 완료하지 못했습니다. 새 검토가 필요합니다.')).finally(()=>setPreviewBusy(false))}
 function enter(side:string){if(!canEnter){setError("서버 지원 범위가 확인되지 않아 신규 진입을 차단했습니다.");return}const leverage=Number(lev);if(!numeric(quantity)||!Number.isInteger(leverage)||leverage<1||leverage>20){setError("수량은 0보다 큰 숫자로 적고, 레버리지는 1부터 20까지 정수로 적으세요.");return;}prepare({kind:"enter",instrument:symbol,side,quantity,leverage},`${instrumentLabel(symbol)} · ${sideLabel(side)} · 수량 ${quantity} · ${lev}배`);}
 const disabled=busy||loading||previewBusy||!!intent||restored.broken;
 return <main className="paper">
  <header className="paper-heading"><div><p className="eyebrow">연습용 가상 거래</p><h1>가상 거래</h1></div><strong className="paper-warning">가상 시세입니다. 실제 거래소 시세가 아닙니다.</strong></header>
  <p className="paper-warning">가상 돈만 사용합니다. 실제 계좌, 거래소 주문, 실제 수익이 아닙니다. 일부 선물 시세는 이 지역에서 막혀 있습니다.</p>
  <section className="paper-panel"><h2>가상 장부</h2><div className="paper-controls"><label>가상 장부<select value={scenario} disabled={disabled||!ids.length} onChange={e=>{setScenario(e.target.value);void load(e.target.value);}}><option value="">새 가상 장부를 만드세요</option>{ids.map((id,index)=><option key={id} value={id}>{`가상 장부 ${index+1}`}</option>)}</select></label><button disabled={disabled} onClick={()=>{if(inFlight.current)return;const id=`paper-${crypto.randomUUID()}`;void perform({scenario:id,key:crypto.randomUUID(),command:{kind:"create"},title:"새 가상 장부"});}}>가상 장부 만들기</button><button disabled={disabled||!scenario} onClick={()=>void load(scenario)}>다시 불러오기</button></div><p>새 장부마다 가상 돈 10,000 USDT로 한 번만 시작합니다. 기존 기록을 지우거나 돈을 더 넣는 기능은 없습니다.</p></section>
  <p role="status">{busy?"가상 주문을 처리하는 중. 같은 요청은 한 번만 실행합니다.":loading?"가상 장부를 확인하는 중":"가상 장부입니다. 넣은 가상 가격만 반영됩니다."}</p>
  {restored.broken&&<p role="alert">끝나지 않은 가상 주문을 확인하지 못해 실행을 멈췄습니다. 기록은 그대로 둡니다.</p>}
  {error&&<p className="paper-error" role="alert">{screenText(error)}</p>}
  {state&&<>
   <section className="paper-balances" aria-label="가상 계좌"><h2>가상 잔고 · USDT</h2>{([["가상 잔고",state.wallet_balance],["쓸 수 있는 증거금",state.available_balance],["맡긴 증거금",state.reserved_margin],["가상 평가 금액",state.equity],["가상 수수료 합계",state.fees],["부족한 금액",state.deficit]] as const).map(([name,value])=><div className="paper-metric" key={name}><span>{name}</span><strong>{value}</strong></div>)}<p>{pnl(state.closed_net_pnl)} · 정리한 수량의 수수료를 뺀 손익</p><p>아직 정리하지 않은 손익: {pnl(state.unrealized_pnl)}</p></section>
   <div className="paper-grid">
    <section className="paper-panel"><h2>가상 시장가 진입</h2><div className="paper-controls"><label>가상 상품<select value={symbol} disabled={disabled} onChange={e=>{setSymbol(e.target.value);syncControls(state,e.target.value,true);}}><option value="BTC-PAPER">BTC 가상</option><option value="ETH-PAPER">ETH 가상</option></select></label><label>진입 수량<input inputMode="decimal" value={quantity} disabled={disabled} onChange={e=>setQuantity(e.target.value)}/></label><label>레버리지<input inputMode="numeric" value={lev} disabled={disabled} onChange={e=>setLev(e.target.value)}/></label></div><p>한 상품에는 한 방향만 둘 수 있습니다. 더 사거나 방향을 바꾸려면 먼저 정리하세요.</p><div className="paper-actions"><button disabled={disabled||!!position||!canEnter} onClick={()=>enter("long")}>오르면 이익</button><button disabled={disabled||!!position} onClick={()=>enter("short")}>내리면 이익</button></div></section>
    <section className="paper-panel"><h2>가상 가격 넣기</h2><p>거래소 실시간 가격이 아닙니다. Binance 현물 가격도 쓰지 않습니다. 이 화면에서 넣은 가격만 사용합니다.</p><p>현재 {instrumentLabel(symbol)}: <strong>{state.marks[symbol]}</strong> · 넣은 횟수 {state.latest_sequence}</p><label>가상 가격<input inputMode="decimal" value={mark} disabled={disabled} onChange={e=>setMark(e.target.value)}/></label><button disabled={disabled} onClick={()=>{if(!numeric(mark)){setError("가상 가격은 0보다 큰 숫자로 적으세요.");return;}prepare({kind:"tick",instrument:symbol,price:mark,sequence:state.latest_sequence+1,timestamp:new Date(Date.now()-1000).toISOString(),source:"LOCAL_SCENARIO_V1"},`${instrumentLabel(symbol)} · 가상 가격 ${mark} · ${state.latest_sequence+1}번째`);}}>가상 가격 넣기</button><p>시각은 확인을 준비할 때 만든 가상 시각입니다. 미래이거나, 120초보다 오래되었거나, 순서가 거꾸로이면 거절됩니다.</p></section>
   </div>
   <section className="paper-panel"><h2>가상 포지션과 손절·익절</h2>{state.positions.length===0?<p>열린 가상 포지션이 없습니다.</p>:state.positions.map(p=><p key={p.instrument}>{instrumentLabel(p.instrument)} · {sideLabel(p.side)} · <strong>{p.quantity}</strong> · 진입 {p.entry_price} · {p.leverage}배 · {pnl(p.unrealized_pnl)}</p>)}
    {position&&<><div className="paper-controls"><label>일부 정리 수량<input inputMode="decimal" disabled={disabled} value={closeQty} onChange={e=>setCloseQty(e.target.value)}/></label><button disabled={disabled} onClick={()=>{if(!numeric(closeQty)){setError("일부 정리 수량을 확인하세요.");return;}prepare({kind:"close",instrument:symbol,quantity:closeQty},`${instrumentLabel(symbol)} · 일부만 정리 ${closeQty}`);}}>일부만 정리</button><button disabled={disabled} onClick={()=>prepare({kind:"close",instrument:symbol,quantity:position.quantity},`${instrumentLabel(symbol)} · 전부 정리 ${position.quantity}`)}>전부 정리</button></div><div className="paper-controls"><label>손절<input inputMode="decimal" value={sl} disabled={disabled} onChange={e=>setSl(e.target.value)}/></label><label>익절<input inputMode="decimal" value={tp} disabled={disabled} onChange={e=>setTp(e.target.value)}/></label><button disabled={disabled} onClick={()=>{if((sl&&!numeric(sl))||(tp&&!numeric(tp))){setError("손절과 익절 가격을 확인하세요.");return;}prepare({kind:"protect",instrument:symbol,sl:sl||null,tp:tp||null},`${instrumentLabel(symbol)} · 손절 ${sl||"없음"} · 익절 ${tp||"없음"}`);}}>손절·익절 저장</button><button disabled={disabled} onClick={()=>prepare({kind:"protect",instrument:symbol,sl:null,tp:null},`${instrumentLabel(symbol)} · 손절·익절 취소`)}>손절·익절 취소</button></div></>}
    <p>손절과 익절은 넣은 가상 가격에서 걸립니다. 가진 수량보다 많이 정리하지 않습니다. 가격이 한 번에 크게 뛰거나 수수료 때문에 손실이 커질 수 있습니다.</p>
   </section>
   <section className="paper-panel"><h2>가상 체결</h2><div className="paper-table"><table><thead><tr><th>번호 / 상품</th><th>방향 / 종류</th><th>체결가</th><th>수량</th><th>수수료 USDT</th></tr></thead><tbody>{state.fills.map((f,i)=><tr key={i}><td>#{f.event_seq} {instrumentLabel(f.instrument)}</td><td>{sideLabel(f.side)} · {roleLabel(f.role)}</td><td>{f.price}</td><td>{f.quantity}</td><td>{f.fee}</td></tr>)}</tbody></table></div><p>모든 체결은 가상입니다. 실제 수익이나 승률이 아닙니다.</p></section>
   <details className="paper-panel"><summary>이 연습의 가정</summary><p>교육용 가상 체결입니다. 레버리지는 1배부터 20배까지입니다. 수수료는 체결마다 0.04%이고, 가격은 한 칸 불리하게 체결될 수 있습니다. BTC 가상은 가격 칸 0.1, 수량 칸 0.001입니다. ETH 가상은 가격 칸 0.01, 수량 칸 0.01입니다. 주문 금액 한도는 100,000 USDT입니다.</p><p>강제 정리는 대략적인 연습 모델이라 거래소와 정확히 같지 않습니다. 가격이 크게 뛰면 손실과 마이너스 잔고가 남을 수 있습니다. 지정가 진입과 실제 거래는 없습니다.</p></details>
   <details className="paper-panel"><summary>저장된 기록</summary><p>{audit?"기록을 불러왔습니다. 어려운 원문은 보여 주지 않습니다.":"기록을 아직 불러오지 못했습니다."}</p></details>
  </>}
  {intent&&<section className="paper-confirm" role="dialog" aria-modal="true" aria-label="가상 실행 확인"><h2>가상 실행 확인</h2><p>{intent.title}</p>{intent.costs&&<p>서버 검토 수수료 {intent.costs.estimated_fee} USDT · 적용 후 가용 증거금 {intent.costs.available_balance_after} USDT</p>}{intent.expiresAt&&<p>검토 만료: {new Date(intent.expiresAt).toLocaleString("ko-KR",{timeZone:"Asia/Seoul"})} KST · 확정 시 서버가 재검사합니다.</p>}<p>실제 거래소로는 보내지 않습니다. 결과가 오기 전에는 같은 요청으로만 다시 시도합니다. 화면을 새로고침해도 끝나지 않은 요청을 다시 보여 줍니다.</p><button disabled={busy||loading} onClick={()=>void perform(intent)}>{retry?"같은 요청으로 다시 시도":"이대로 실행"}</button><button disabled={busy||retry} onClick={()=>{setIntent(null);setRetry(false);setError("");}}>돌아가기</button></section>}
 </main>;
}
