import {useEffect,useRef,useState} from 'react';
import {Field} from '../../ui/components';

type Configuration={schema_version:1;interval:string;fast:number;slow:number;atr_bars:number;stop_atr_multiple:string;target_r:string;max_bar_age_seconds:number;signal_ttl_seconds:number};
type Preview={schema_version:1;expected_policy_revision:number;expected_generation:number;reference:{strategy_id:string;revision:string};instrument:{venue:'BYBIT';product:'LINEAR';symbol:string};configuration:Configuration;implementation_sha256:string;configuration_sha256:string;approval_method:'NGINX_BASIC_AUTH_SELF_APPROVAL_TRADER';confirmation_fingerprint:string};
type Props={revision:number;generation:number;disabled:boolean;onPublished:()=>void};
type TextConfiguration={interval:string;fast:string;slow:string;atr_bars:string;stop_atr_multiple:string;target_r:string;max_bar_age_seconds:string;signal_ttl_seconds:string};
const initial:TextConfiguration={interval:'',fast:'',slow:'',atr_bars:'',stop_atr_multiple:'',target_r:'',max_bar_age_seconds:'',signal_ttl_seconds:''};
const ratio=/^(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,8})?$/;
function positiveRatio(value:string){return ratio.test(value)&&/[1-9]/.test(value.replace('.',''))}
function boundedInteger(value:string,min:number,max:number){if(!/^(?:0|[1-9][0-9]{0,4})$/.test(value))return null;const parsed=Number(value);return parsed>=min&&parsed<=max?parsed:null}
function parsePreview(value:unknown):Preview|null{
 if(!value||typeof value!=='object'||Array.isArray(value))return null;
 const item=value as Record<string,unknown>,ref=item.reference,instrument=item.instrument,configuration=item.configuration;
 if(item.schema_version!==1||!Number.isSafeInteger(item.expected_policy_revision)||!Number.isSafeInteger(item.expected_generation)
  ||!ref||typeof ref!=='object'||(ref as Record<string,unknown>).strategy_id!=='builtin-sma-atr'||(ref as Record<string,unknown>).revision!=='r1'
  ||!instrument||typeof instrument!=='object'||(instrument as Record<string,unknown>).venue!=='BYBIT'||(instrument as Record<string,unknown>).product!=='LINEAR'
  ||!configuration||typeof configuration!=='object'||Array.isArray(configuration)
  ||typeof item.implementation_sha256!=='string'||!/^[0-9a-f]{64}$/.test(item.implementation_sha256)
  ||typeof item.configuration_sha256!=='string'||!/^[0-9a-f]{64}$/.test(item.configuration_sha256)
  ||item.approval_method!=='NGINX_BASIC_AUTH_SELF_APPROVAL_TRADER'
  ||typeof item.confirmation_fingerprint!=='string'||!/^[0-9a-f]{64}$/.test(item.confirmation_fingerprint))return null;
 return value as Preview;
}

export function BuiltinStrategyPublication({revision,generation,disabled,onPublished}:Props){
 const [symbol,setSymbol]=useState('');const [configuration,setConfiguration]=useState(initial);const [preview,setPreview]=useState<Preview|null>(null);
 const [confirmed,setConfirmed]=useState(false);const [busy,setBusy]=useState(false);const [notice,setNotice]=useState('');
 const active=useRef<AbortController|null>(null);
 useEffect(()=>()=>active.current?.abort(),[]);
 const fast=boundedInteger(configuration.fast,1,498),slow=boundedInteger(configuration.slow,2,499),atr=boundedInteger(configuration.atr_bars,1,498);
 const maxAge=boundedInteger(configuration.max_bar_age_seconds,1,86400),ttl=boundedInteger(configuration.signal_ttl_seconds,1,300);
 const symbolValid=/^[A-Z0-9]{2,48}$/.test(symbol);
 const valid=configuration.interval!==''&&fast!==null&&slow!==null&&fast<slow&&atr!==null&&maxAge!==null&&ttl!==null
  &&positiveRatio(configuration.stop_atr_multiple)&&positiveRatio(configuration.target_r)&&symbolValid;
 function invalidate(){active.current?.abort();active.current=null;setPreview(null);setConfirmed(false);setNotice('')}
 function update<K extends keyof TextConfiguration>(key:K,value:TextConfiguration[K]){invalidate();setConfiguration(old=>({...old,[key]:value}))}
 async function send(path:string,payload:Record<string,unknown>):Promise<{ok:boolean;status:number;value:unknown}>{
  const controller=new AbortController();active.current=controller;setBusy(true);
  try{
   const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},signal:controller.signal,body:JSON.stringify(payload)});
   const value:unknown=await response.json().catch(()=>undefined);return {ok:response.ok,status:response.status,value};
  }finally{if(active.current===controller){active.current=null;setBusy(false)}}
 }
 function body():Record<string,unknown>|null{
  if(!valid||fast===null||slow===null||atr===null||maxAge===null||ttl===null)return null;
  return {schema_version:1,expected_policy_revision:revision,expected_generation:generation,symbol,configuration:{schema_version:1,
   interval:configuration.interval,fast,slow,atr_bars:atr,stop_atr_multiple:configuration.stop_atr_multiple,target_r:configuration.target_r,
   max_bar_age_seconds:maxAge,signal_ttl_seconds:ttl}};
 }
 async function makePreview(){
  const payload=body();if(!payload||busy||disabled)return;
  setNotice('');setPreview(null);setConfirmed(false);
  try{const result=await send('/api/v1/automatic/strategies/preview',payload);const value=parsePreview(result.value);
   if(!result.ok||!value){setNotice(result.status===403?'trader 운영자 인증과 같은 출처의 로그인 세션이 필요합니다.':result.status===409?'설정 또는 게시 세대가 바뀌었거나 값이 유효하지 않습니다. 최신 설정을 다시 불러온 뒤 미리보기를 다시 만드세요.':'전략 미리보기를 확인하지 못했습니다.');return}
   if(value.expected_policy_revision!==revision||value.expected_generation!==generation||value.instrument.symbol!==symbol){setNotice('서버 미리보기가 현재 입력과 일치하지 않습니다.');return}
   setPreview(value);
  }catch{setNotice('전략 미리보기 연결을 확인하지 못했습니다.')}
 }
 async function publish(){
  const payload=body();if(!payload||!preview||!confirmed||busy||disabled)return;
  setNotice('');
  try{const result=await send('/api/v1/automatic/strategies/publish',{...payload,confirmation_fingerprint:preview.confirmation_fingerprint});
   if(!result.ok){setPreview(null);setConfirmed(false);setNotice(result.status===409?'확인한 뒤 기준이 변경되었거나 이미 게시되었습니다. 최신 상태에서 다시 미리보기 하세요.':result.status===403?'trader 운영자 인증을 다시 확인하세요.':'전략을 게시하지 못했습니다. 현재 게시 상태를 다시 확인하세요.');return}
   const value=result.value as Record<string,unknown>|undefined;
   if(!value||value.generation!==generation+1||value.policy_revision!==revision+1||value.approval_method!=='NGINX_BASIC_AUTH_SELF_APPROVAL_TRADER'){
    setPreview(null);setConfirmed(false);setNotice('게시 응답을 확인할 수 없습니다. 현재 상태를 다시 불러오세요.');return;
   }
   setPreview(null);setConfirmed(false);setNotice('DEMO용 전략 게시를 완료했습니다. 설정은 정지 상태이며 주문이나 worker는 시작하지 않았습니다.');onPublished();
  }catch{setPreview(null);setConfirmed(false);setNotice('게시 결과가 불확실합니다. 현재 상태를 다시 확인하세요.')}
 }
 return <section aria-labelledby="builtin-strategy-publication-heading" style={{borderTop:'1px solid currentColor',marginTop:24,paddingTop:16}}>
  <h3 id="builtin-strategy-publication-heading">내장 SMA/ATR 전략 명시 게시</h3>
  <p className="helper">Bybit 선형 DEMO에서 신호를 계산할 전략 파일과 설정을 게시합니다. 이 작업은 거래 대상을 허용·선택하거나 위험 한도를 저장하지 않고, worker를 시작하거나 주문하지 않습니다.</p>
  <p className="helper">게시 요청은 HTTPS 대시보드의 trader 로그인 확인으로 자기 승인 기록을 남깁니다. 이는 외부 기관의 전자서명이 아닙니다.</p>
  <Field label="Bybit LINEAR 거래 종목" value={symbol} disabled={disabled||busy} helper="예: BTCUSDT. 게시 대상은 Bybit LINEAR / DEMO로 제한됩니다." onChange={value=>{invalidate();setSymbol(value.toUpperCase())}}/>
  <label className="field"><span>봉 간격</span><select value={configuration.interval} disabled={disabled||busy} onChange={event=>update('interval',event.target.value)}><option value="">선택</option>{[['1m','1분'],['5m','5분'],['15m','15분'],['1h','1시간'],['4h','4시간'],['1d','1일']].map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
  <Field label="빠른 이동평균 기간" value={configuration.fast} disabled={disabled||busy} onChange={value=>update('fast',value)}/>
  <Field label="느린 이동평균 기간" value={configuration.slow} disabled={disabled||busy} onChange={value=>update('slow',value)}/>
  <Field label="ATR 계산 봉 수" value={configuration.atr_bars} disabled={disabled||busy} onChange={value=>update('atr_bars',value)}/>
  <Field label="손절 거리 배수 (ATR)" value={configuration.stop_atr_multiple} disabled={disabled||busy} onChange={value=>update('stop_atr_multiple',value)}/>
  <Field label="익절 거리 배수 (R)" value={configuration.target_r} disabled={disabled||busy} onChange={value=>update('target_r',value)}/>
  <Field label="최대 봉 경과 시간 (초)" value={configuration.max_bar_age_seconds} disabled={disabled||busy} onChange={value=>update('max_bar_age_seconds',value)}/>
  <Field label="신호 만료 시간 (초)" value={configuration.signal_ttl_seconds} disabled={disabled||busy} onChange={value=>update('signal_ttl_seconds',value)}/>
  {!valid&&<p className="helper">종목과 모든 전략 설정을 입력해야 미리보기를 요청할 수 있습니다. 빠른 기간은 느린 기간보다 짧아야 합니다.</p>}
  <button className="secondary" disabled={disabled||busy||!valid} onClick={()=>void makePreview()}>{busy?'확인 중…':'정확한 전략과 해시 미리보기'}</button>
  {preview&&<div className="info-box" style={{overflowWrap:'anywhere',marginTop:12}}>
   <p>전략: {preview.reference.strategy_id}@{preview.reference.revision}</p><p>대상: {preview.instrument.venue}/{preview.instrument.product}/{preview.instrument.symbol} · DEMO</p>
   <p>설정 JSON: <code>{JSON.stringify(preview.configuration)}</code></p>
   <p>설치된 전략 구현 SHA-256: <code>{preview.implementation_sha256}</code></p><p>설정 SHA-256: <code>{preview.configuration_sha256}</code></p>
   <p>승인 방식: trader 로그인에 의한 명시적 자기 승인. 외부 전자서명은 없습니다.</p>
   <label style={{display:'block',margin:'12px 0'}}><input type="checkbox" style={{width:24,height:24,marginRight:8,verticalAlign:'middle'}} checked={confirmed} disabled={disabled||busy} onChange={event=>setConfirmed(event.target.checked)}/>위의 정확한 구현·설정을 확인했고, Bybit LINEAR DEMO용으로 게시합니다. 이 동작이 주문을 시작하지 않는다는 점을 확인했습니다.</label>
   <button className="primary" disabled={disabled||busy||!confirmed} onClick={()=>void publish()}>{busy?'게시 결과 확인 중':'확인한 전략 게시'}</button>
  </div>}
  {notice&&<p role="status">{notice}</p>}
 </section>;
}
