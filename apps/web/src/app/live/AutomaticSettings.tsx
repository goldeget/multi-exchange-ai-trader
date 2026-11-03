import {useEffect,useRef,useState} from 'react';
import {Field,Panel} from '../../ui/components';
import {record} from './contracts';

const amounts:Record<string,string>={capital_usdt:'자동매매에 맡길 최대 자금 (USDT)',leverage_min:'최소 레버리지',leverage_max:'최대 레버리지',per_trade_loss:'거래당 최대 손실 (USDT)',max_daily_loss:'일일 최대 손실 (USDT)',max_drawdown:'최대 누적 낙폭 (USDT)',max_total_notional:'전체 포지션 최대 명목 금액 (USDT)',max_total_risk:'전체 포지션 최대 위험액 (USDT)',max_total_margin:'전체 최대 증거금 (USDT)',margin_allocation:'거래당 증거금 배정액 (USDT)',liquidation_buffer_fraction:'청산가 안전 여유 비율 (0 이상 1 미만)',max_quote_age_seconds:'시세 최대 경과 시간 (초)',max_evidence_age_seconds:'계좌 확인 최대 경과 시간 (초)'};
const counts:Record<string,string>={max_consecutive_losses:'최대 연속 손실 횟수',max_positions:'최대 동시 포지션 수'};
const choices:Record<string,Record<string,string>>={mode:{FULL_AUTO:'완전 자동 — 직접 고른 종목·전략',ALL_IN_ONE:'올인원 자동 — AI가 허용 후보에서 선택'},environment:{DEMO:'Bybit 연습계좌',LIVE:'Bybit 실제 계좌'},sizing_rule:{LOSS_FIXED:'거래당 손실액 기준'},margin_mode:{ISOLATED:'격리 증거금',CROSS:'교차 증거금'},position_mode:{ONE_WAY:'단방향',HEDGE:'양방향'}};
const choiceLabels:Record<string,string>={mode:'자동매매 모드',environment:'운용 계좌',sizing_rule:'주문 수량 산정 방식',margin_mode:'증거금 모드',position_mode:'포지션 모드'};
const lists:Record<string,string>={allowed_symbols:'허용 종목',allowed_strategies:'허용 전략 ID',selected_symbols:'완전 자동에서 선택한 종목',selected_strategies:'완전 자동에서 선택한 전략 ID'};
const keys=[...Object.keys(amounts),...Object.keys(counts),...Object.keys(choices),...Object.keys(lists)];
type Policy=Record<string,string|number|string[]|null>;
type PolicyV2={legacy_v1:Policy;scope:null;strategy:null;risk_v2:null};
type Settings={revision:number;missing_fields:string[];desired_state:'STOPPED';live_order_enabled:false;worker_running:false;blockers:string[]}&({schema_version:1;policy:Policy}|{schema_version:2;policy:PolicyV2});
function validPolicy(policy:unknown):policy is Policy{
 if(!record(policy))return false;
 return Object.keys(policy).length===keys.length&&keys.every(key=>Object.hasOwn(policy,key)&&(
  policy[key]===null||Object.hasOwn(amounts,key)&&typeof policy[key]==='string'&&/^(?:0|[1-9]\d{0,15})(?:\.\d{1,8})?$/.test(policy[key])||
  Object.hasOwn(counts,key)&&Number.isSafeInteger(policy[key])&&Number(policy[key])>0&&Number(policy[key])<=1000000||
  Object.hasOwn(choices,key)&&typeof policy[key]==='string'&&Object.hasOwn(choices[key],policy[key])||
  Object.hasOwn(lists,key)&&Array.isArray(policy[key])&&policy[key].length<=100&&new Set(policy[key]).size===policy[key].length&&policy[key].every(x=>typeof x==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,95}$/.test(x))
 ));
}
function valid(v:unknown):v is Settings{
 if(!record(v)||Object.keys(v).length!==8||!['schema_version','revision','policy','missing_fields','desired_state','live_order_enabled','worker_running','blockers'].every(key=>Object.hasOwn(v,key)))return false;
 if(!Number.isSafeInteger(v.revision)||Number(v.revision)<0||v.desired_state!=='STOPPED'||v.live_order_enabled!==false||v.worker_running!==false||!Array.isArray(v.missing_fields)||!Array.isArray(v.blockers)||!v.blockers.every(x=>typeof x==='string'))return false;
 const blockers=v.blockers,policy=v.policy;
 if(!['NATIVE_RUNTIME_NOT_QUALIFIED','WORKER_NOT_CONNECTED'].every(x=>blockers.includes(x)))return false;
 if(v.schema_version===1)return validPolicy(v.policy)&&v.missing_fields.every(x=>typeof x==='string'&&keys.includes(x))&&v.blockers.includes('POLICY_V1_MIGRATION_REQUIRED');
 return v.schema_version===2&&record(policy)&&Object.keys(policy).length===4&&['legacy_v1','scope','strategy','risk_v2'].every(key=>Object.hasOwn(policy,key))&&policy.scope===null&&policy.strategy===null&&policy.risk_v2===null&&validPolicy(policy.legacy_v1)&&JSON.stringify(v.missing_fields)===JSON.stringify(['risk_v2','scope','strategy'])&&blockers.includes('POLICY_V2_ADMISSION_REQUIRED');
}
function legacyPolicy(data:Settings):Policy{
 return data.schema_version===1?data.policy:data.policy.legacy_v1;
}

export function AutomaticSettings(){
 const [expanded,setExpanded]=useState(false);
 return <div className="automatic-settings"><Panel title="자동매매 운용 한도"><p className="helper">자동매매에 맡길 자금과 손실 한도를 설정합니다. 현재 실제 주문은 차단되어 있습니다.</p><button className="secondary" aria-expanded={expanded} onClick={()=>setExpanded(value=>!value)}>{expanded?'운용 한도 접기':'운용 한도 설정 열기'}</button></Panel>{expanded&&<AutomaticSettingsEditor/>}</div>;
}

function AutomaticSettingsEditor(){
 const [source,setSource]=useState<{data:Settings|null;loading:boolean;error:boolean}>({data:null,loading:true,error:false});
 const [readNonce,setReadNonce]=useState(0);
 const [edit,setEdit]=useState<{revision:number;policy:Policy}|null>(null);
 const [notice,setNotice]=useState('');const [saving,setSaving]=useState(false);
 const pending=useRef<AbortController|null>(null);
 const mounted=useRef(false);const saveTimer=useRef<number|undefined>(undefined);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;pending.current?.abort();window.clearTimeout(saveTimer.current)}},[]);
 useEffect(()=>{
  const controller=new AbortController();let disposed=false,timer:number|undefined;
  async function read(){
   try{
    const {response,data}=await Promise.race([
     (async()=>{const response=await fetch('/api/v1/automatic/settings',{signal:controller.signal});const data:unknown=await response.json();return {response,data}})(),
     new Promise<never>((_resolve,reject)=>{timer=window.setTimeout(()=>{controller.abort();reject(new Error('read_timeout'))},15000)}),
    ]);
    if(disposed||controller.signal.aborted)return;
    if(!response.ok||!valid(data))throw new Error('read_contract');
    // A delayed read cannot replace a successfully saved revision with older data.
    setSource(old=>({data:old.data&&old.data.revision>data.revision?old.data:data,loading:false,error:false}));
   }catch{if(!disposed)setSource(old=>({...old,loading:false,error:true}))}
   finally{window.clearTimeout(timer)}
  }
  void read();return()=>{disposed=true;controller.abort();window.clearTimeout(timer)};
 },[readNonce]);
 const policy=edit?.policy??(source.data?legacyPolicy(source.data):undefined);
 const revision=edit?.revision??source.data?.revision;
 function change(key:string,value:Policy[string]){if(policy&&revision!==undefined)setEdit({revision,policy:{...policy,[key]:value}})}
 function reload(){setEdit(null);setSource(old=>({...old,loading:true,error:false}));setReadNonce(value=>value+1)}
 async function save(migration=false){
  const stored=source.data;
  if(!stored||!policy||revision===undefined||pending.current||source.loading||source.error)return;
  if(migration?(stored.schema_version!==1||edit!==null):stored.schema_version!==2)return;
  const controller=new AbortController();pending.current=controller;setSaving(true);setNotice('');
  try{
   const {response,data}=await Promise.race([
    (async()=>{const response=await fetch(migration?'/api/v1/automatic/settings/migrate':'/api/v1/automatic/settings',{method:migration?'POST':'PUT',headers:{'Content-Type':'application/json'},signal:controller.signal,body:JSON.stringify(migration?{schema_version:1,revision:stored.revision}:{schema_version:2,revision,policy:{legacy_v1:policy,scope:null,strategy:null,risk_v2:null},expected_strategy_generation:null})});const data:unknown=await response.json();return {response,data}})(),
    new Promise<never>((_resolve,reject)=>{saveTimer.current=window.setTimeout(()=>{controller.abort();reject(new Error('save_timeout'))},15000)}),
   ]);
   if(!mounted.current)return;
   if(!response.ok||!valid(data)||data.schema_version!==2){setNotice(response.status===409?'다른 곳에서 설정이 변경되었거나 입력값이 허용 범위를 벗어났습니다.':'설정을 저장하지 못했습니다.');return}
   setSource({data,loading:false,error:false});setEdit(null);setReadNonce(value=>value+1);setNotice(migration?'저장된 설정을 새 운용 초안으로 전환했습니다. 실제 주문은 시작되지 않습니다.':'설정을 저장했습니다. 실제 주문은 시작되지 않습니다.');
  }catch{if(mounted.current){setNotice('저장 결과를 확인하지 못했습니다. 최신 값을 다시 불러와 확인하세요.');reload()}}
  finally{window.clearTimeout(saveTimer.current);if(pending.current===controller){pending.current=null;if(mounted.current)setSaving(false)}}
 }
 function input(key:string,label:string){
  const value=policy?.[key];const isList=Object.hasOwn(lists,key);
  return <Field key={key} label={label} value={value===null||value===undefined?'':Array.isArray(value)?value.join(', '):String(value)} disabled={!policy||saving||source.loading||source.error} helper={isList?'쉼표로 구분합니다. 비워 두면 미설정입니다.':undefined} onChange={text=>change(key,text===''?null:isList?text.split(',').map(x=>x.trim()):Object.hasOwn(counts,key)&&/^\d+$/.test(text)?Number(text):text)}/>;
 }
 return <Panel title="자동매매 운용 한도" className="automatic-settings">
  <p className="helper">계좌 전체 잔액이 아닌 프로그램에 맡길 최대 자금입니다. 미정인 항목은 비워 두고 저장할 수 있습니다.</p>
  <p className="info-box">현재 실제 주문은 차단되어 있습니다. 한도를 저장해도 자동매매·유료 AI 호출·서버 결제는 시작되지 않습니다.</p>
  {source.error?<p role="alert">운용 설정을 확인하지 못했습니다. 저장을 막았습니다.</p>:!policy?<p role="status">운용 설정을 불러오는 중입니다.</p>:<>
   {source.data?.schema_version===1?<><p className="info-box">저장된 이전 설정을 그대로 보관하고 새 운용 초안으로 전환해야 한도를 저장할 수 있습니다. 거래 대상이나 전략은 자동으로 선택되지 않습니다.</p><button className="secondary" disabled={saving||source.loading||edit!==null} onClick={()=>void save(true)}>저장된 설정을 새 운용 초안으로 전환</button>{edit!==null&&<p className="helper">입력 중인 값은 보관하고 있습니다. 전환하려면 저장된 설정을 다시 불러와 변경 내용을 버리세요.</p>}</>:<><p className="info-box">거래 대상·운용 전략·위험관리 기준은 아직 정해지지 않았습니다. 이전 설정 값은 아래에 보관되어 있으며 실제 운용 기준으로 자동 변환되지 않습니다.</p><h3>이전 운용 한도</h3></>}
   {['mode','environment'].map(key=><label className="field" key={key}><span>{choiceLabels[key]}</span><select value={String(policy[key]??'')} disabled={saving||source.loading} onChange={event=>change(key,event.target.value||null)}><option value="">미정</option>{Object.entries(choices[key]).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>)}
   {Object.entries(amounts).slice(0,5).map(([key,label])=>input(key,label))}
   {Object.entries(lists).map(([key,label])=>input(key,label))}
   <details><summary>상세 위험관리 설정</summary>{Object.entries(amounts).slice(5).map(([key,label])=>input(key,label))}{Object.entries(counts).map(([key,label])=>input(key,label))}{['sizing_rule','margin_mode','position_mode'].map(key=><label className="field" key={key}><span>{choiceLabels[key]}</span><select value={String(policy[key]??'')} disabled={saving||source.loading} onChange={event=>change(key,event.target.value||null)}><option value="">미정</option>{Object.entries(choices[key]).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>)}</details>
   <p className="helper">저장된 설정 중 미정 항목: {source.data?.missing_fields.length??'확인 중'}개. 실제 운용에는 거래소·체결·보호 주문 검증과 서버 연결도 필요합니다.</p>
   <button className="primary full" disabled={saving||source.error||source.loading||source.data?.schema_version!==2} onClick={()=>void save()}>{saving?'저장 결과 확인 중':'운용 한도 저장'}</button>
   <button className="secondary" disabled={saving||source.loading} onClick={()=>{setNotice('');reload()}}>저장된 설정 다시 불러오기</button>
  </>}
  {notice&&<p role="status">{notice}</p>}
 </Panel>;
}
