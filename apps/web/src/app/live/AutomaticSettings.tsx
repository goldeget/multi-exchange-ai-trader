import {useEffect,useMemo,useRef,useState} from 'react';
import {Field,Panel} from '../../ui/components';
import {automaticPolicyEqual,automaticPolicyMissing,automaticPolicyPairs,parseAutomaticSettings,policyInstrumentName,policyReferenceName,policyRiskCounts,policyRiskDecimals,validateAutomaticPolicy,type AutomaticPolicy,type AutomaticSettingsSnapshot,type LegacyPolicy,type PolicyCaps,type PolicyInstrument,type PolicyProduct,type PolicyReference,type PolicyRisk,type PolicyScope} from './contracts';
const amounts:Record<string,string>={capital_usdt:'자동매매에 맡길 최대 자금 (USDT)',leverage_min:'최소 레버리지',leverage_max:'최대 레버리지',per_trade_loss:'거래당 최대 손실 (USDT)',max_daily_loss:'일일 최대 손실 (USDT)',max_drawdown:'최대 누적 낙폭 (USDT)',max_total_notional:'전체 포지션 최대 명목 금액 (USDT)',max_total_risk:'전체 포지션 최대 위험액 (USDT)',max_total_margin:'전체 최대 증거금 (USDT)',margin_allocation:'거래당 증거금 배정액 (USDT)',liquidation_buffer_fraction:'청산가 안전 여유 비율 (0 이상 1 미만)',max_quote_age_seconds:'시세 최대 경과 시간 (초)',max_evidence_age_seconds:'계좌 확인 최대 경과 시간 (초)'};
const counts:Record<string,string>={max_consecutive_losses:'최대 연속 손실 횟수',max_positions:'최대 동시 포지션 수'};
const choices:Record<string,Record<string,string>>={mode:{FULL_AUTO:'완전 자동 — 직접 고른 종목·전략',ALL_IN_ONE:'올인원 자동 — AI가 허용 후보에서 선택'},environment:{DEMO:'Bybit 연습계좌',LIVE:'Bybit 실제 계좌'},sizing_rule:{LOSS_FIXED:'거래당 손실액 기준'},margin_mode:{ISOLATED:'격리 증거금',CROSS:'교차 증거금'},position_mode:{ONE_WAY:'단방향',HEDGE:'양방향'}};
const choiceLabels:Record<string,string>={mode:'자동매매 모드',environment:'운용 계좌',sizing_rule:'주문 수량 산정 방식',margin_mode:'증거금 모드',position_mode:'포지션 모드'};
const lists:Record<string,string>={allowed_symbols:'허용 종목',allowed_strategies:'허용 전략 ID',selected_symbols:'완전 자동에서 선택한 종목',selected_strategies:'완전 자동에서 선택한 전략 ID'};

const emptyScope=():PolicyScope=>({section_version:1,account:null,mode:null,allowed_products:null,selected_product:null,permission_profile_revision:null,allowed_instruments:null,selected_instruments:null});
const emptyCaps=():PolicyCaps=>({max_order_admission_notional:null,max_account_admission_notional:null,valuation_rule:null,turnover_requirement:null,max_execution_quote_amount:null});
const emptyRisk=():PolicyRisk=>({section_version:1,product:'LINEAR',caps:null,sizing_rule:null,margin_mode:null,position_mode:null,...Object.fromEntries([...policyRiskDecimals,...policyRiskCounts].map(key=>[key,null]))} as PolicyRisk);
function envelope(data:AutomaticSettingsSnapshot):AutomaticPolicy{return data.schema_version===2?data.policy:{legacy_v1:data.policy,scope:null,strategy:null,risk_v2:null}}
function instruments(text:string):PolicyInstrument[]|null{return text===''?null:text.split(',').map(token=>{const [venue,product,...symbol]=token.trim().split('/');return {venue,product,symbol:symbol.join('/')} as PolicyInstrument})}
const containsRef=(refs:PolicyReference[]|null,ref:PolicyReference)=>!!refs?.some(item=>automaticPolicyEqual(item,ref));
function toggleRef(refs:PolicyReference[]|null,ref:PolicyReference,checked:boolean){return checked?[...(refs??[]),ref]:(refs??[]).filter(item=>!automaticPolicyEqual(item,ref))}

export function AutomaticSettings(){
 const [expanded,setExpanded]=useState(false);
 return <div className="automatic-settings" style={{minWidth:0,overflowWrap:'anywhere'}}><Panel title="자동매매 운용 한도"><p className="helper">자동매매에 맡길 자금과 손실 한도를 설정합니다. 현재 실제 주문은 차단되어 있습니다.</p><button className="secondary" aria-expanded={expanded} onClick={()=>setExpanded(value=>!value)}>{expanded?'운용 한도 접기':'운용 한도 설정 열기'}</button></Panel>{expanded&&<AutomaticSettingsEditor/>}</div>;
}

function AutomaticSettingsEditor(){
 const [source,setSource]=useState<{data:AutomaticSettingsSnapshot|null;capable:boolean;loading:boolean;error:boolean}>({data:null,capable:false,loading:true,error:false});
 const [readNonce,setReadNonce]=useState(0);
 const [edit,setEdit]=useState<{revision:number;policy:AutomaticPolicy}|null>(null);
 const [notice,setNotice]=useState('');const [saving,setSaving]=useState(false);const [uncertain,setUncertain]=useState(false);
 const [validation,setValidation]=useState<{policy:AutomaticPolicy;valid:boolean}|null>(null);
 const snapshot=useRef<AutomaticSettingsSnapshot|null>(null);const editRef=useRef<typeof edit>(null);
 const unresolved=useRef<{revision:number;policy:AutomaticPolicy}|null>(null);
 const pending=useRef<AbortController|null>(null);const mounted=useRef(false);const saveTimer=useRef<number|undefined>(undefined);
 const policy=useMemo(()=>edit?.policy??(source.data?envelope(source.data):undefined),[edit,source.data]);
 const revision=edit?.revision??source.data?.revision;
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;pending.current?.abort();window.clearTimeout(saveTimer.current)}},[]);
 useEffect(()=>{let disposed=false;if(policy)void validateAutomaticPolicy(policy).then(value=>{if(!disposed)setValidation({policy,valid:value!==null})});return()=>{disposed=true}},[policy]);
 useEffect(()=>{
  const controller=new AbortController();let disposed=false,timer:number|undefined;
  async function read(){
   try{
    const {response,data}=await Promise.race([
     (async()=>{const response=await fetch('/api/v1/automatic/settings',{signal:controller.signal});const data=await parseAutomaticSettings(await response.text());return {response,data}})(),
     new Promise<never>((_resolve,reject)=>{timer=window.setTimeout(()=>{controller.abort();reject(new Error('read_timeout'))},15000)}),
    ]);
    if(disposed||controller.signal.aborted)return;
    if(!response.ok||!data)throw new Error('read_contract');
    const capable=response.headers.get('X-Automatic-Policy-Sections-Version')==='1';
    const awaiting=unresolved.current;
    if(awaiting&&capable&&data.schema_version===2&&data.revision>awaiting.revision&&automaticPolicyEqual(data.policy,awaiting.policy)){
     unresolved.current=null;setUncertain(false);
     if(editRef.current&&automaticPolicyEqual(editRef.current.policy,awaiting.policy)){editRef.current=null;setEdit(null)}
     setNotice('설정을 저장했습니다. 실제 주문은 시작되지 않습니다.');
    }
    // Capability uncertainty always blocks writes, including a stale GET. Its
    // old contents cannot replace the newer saved snapshot or any dirty edits.
    const newest=snapshot.current&&snapshot.current.revision>data.revision?snapshot.current:data;
    snapshot.current=newest;setSource({data:newest,capable,loading:false,error:false});
   }catch{if(!disposed)setSource(old=>({...old,capable:false,loading:false,error:true}))}
   finally{window.clearTimeout(timer)}
  }
  void read();return()=>{disposed=true;controller.abort();window.clearTimeout(timer)};
 },[readNonce]);
 const disabled=saving||source.loading||source.error||!source.capable||uncertain;
 const invalid=!!policy&&validation?.policy===policy&&!validation.valid;
 const typed=source.data?.schema_version===2;
 function update(next:AutomaticPolicy){if(revision!==undefined){const value={revision,policy:next};editRef.current=value;setEdit(value)}}
 function changeLegacy(key:string,value:LegacyPolicy[string]){if(policy)update({...policy,legacy_v1:{...policy.legacy_v1,[key]:value}})}
 function changeScope(change:Partial<PolicyScope>){if(policy)update({...policy,scope:{...(policy.scope??emptyScope()),...change}})}
 function changeRisk(key:string,value:string|number|null){if(policy?.risk_v2)update({...policy,risk_v2:{...policy.risk_v2,[key]:value}})}
 function changeCap(key:keyof PolicyCaps,value:string|null){if(policy?.risk_v2)update({...policy,risk_v2:{...policy.risk_v2,caps:{...(policy.risk_v2.caps??emptyCaps()),[key]:value}}})}
 function reload(){if(!unresolved.current){editRef.current=null;setEdit(null)}setSource(old=>({...old,loading:true,error:false}));setReadNonce(value=>value+1)}
 function reconcile(submitted:{revision:number;policy:AutomaticPolicy}){
  unresolved.current=submitted;setUncertain(true);setNotice('저장 결과를 확인하지 못했습니다. 입력값을 보관하고 GET으로 저장 결과를 확인합니다.');
  setSource(old=>({...old,capable:false,loading:true,error:false}));setReadNonce(value=>value+1);
 }
 async function save(migration=false){
  const stored=source.data;
  if(!stored||!policy||revision===undefined||pending.current||disabled)return;
  if(migration?(stored.schema_version!==1||edit!==null):stored.schema_version!==2)return;
  const submitted={revision:migration?stored.revision:revision,policy:migration?envelope(stored):policy};
  const controller=new AbortController();pending.current=controller;setSaving(true);setNotice('');
  try{
   if(!await validateAutomaticPolicy(submitted.policy)){setNotice('입력값이 서로 맞지 않거나 허용 범위를 벗어났습니다. 변경 내용을 보관했습니다.');return}
   if(!mounted.current||controller.signal.aborted)return;
   const {response,data}=await Promise.race([
    (async()=>{const response=await fetch(migration?'/api/v1/automatic/settings/migrate':'/api/v1/automatic/settings',{method:migration?'POST':'PUT',headers:{'Content-Type':'application/json'},signal:controller.signal,body:JSON.stringify(migration?{schema_version:1,revision:stored.revision}:{schema_version:2,revision,policy:submitted.policy,expected_strategy_generation:null,policy_sections_version:1})});const data=await parseAutomaticSettings(await response.text());return {response,data}})(),
    new Promise<never>((_resolve,reject)=>{saveTimer.current=window.setTimeout(()=>{controller.abort();reject(new Error('save_timeout'))},15000)}),
   ]);
   if(!mounted.current)return;
   if(!response.ok){setNotice(response.status===409?'다른 곳에서 설정이 변경되었거나 입력값이 허용 범위를 벗어났습니다.':'설정을 저장하지 못했습니다.');return}
   if(!data||data.schema_version!==2||response.headers.get('X-Automatic-Policy-Sections-Version')!=='1'||data.revision!==submitted.revision+1||!automaticPolicyEqual(data.policy,submitted.policy)){reconcile(submitted);return}
   snapshot.current=data;setSource({data,capable:true,loading:false,error:false});editRef.current=null;setEdit(null);setReadNonce(value=>value+1);setNotice(migration?'저장된 설정을 새 운용 초안으로 전환했습니다. 실제 주문은 시작되지 않습니다.':'설정을 저장했습니다. 실제 주문은 시작되지 않습니다.');
  }catch{if(mounted.current)reconcile(submitted)}
  finally{window.clearTimeout(saveTimer.current);if(pending.current===controller){pending.current=null;if(mounted.current)setSaving(false)}}
 }
 function select(label:string,value:string|null|undefined,options:Record<string,string>,onChange:(value:string)=>void){return <label className="field"><span>{label}</span><select value={value??''} disabled={disabled} onChange={event=>onChange(event.target.value)}><option value="">미정</option>{Object.entries(options).map(([key,text])=><option key={key} value={key}>{text}</option>)}</select></label>}
 function legacyInput(key:string,label:string){const value=policy?.legacy_v1[key];const isList=Object.hasOwn(lists,key);return <Field key={key} label={label} value={value===null||value===undefined?'':Array.isArray(value)?value.join(', '):String(value)} disabled={disabled} helper={isList?'쉼표로 구분합니다. 비워 두면 미설정입니다.':undefined} onChange={text=>changeLegacy(key,text===''?null:isList?text.split(',').map(x=>x.trim()):Object.hasOwn(counts,key)&&/^\d+$/.test(text)?Number(text):text)}/>}
 const scope=policy?.scope;const strategy=policy?.strategy;const risk=policy?.risk_v2;
 const pairs=policy&&validation?.policy===policy&&validation.valid?automaticPolicyPairs(policy):[];
 const incompatible=!!scope?.selected_product&&!!policy&&(!!risk&&scope.selected_product==='SPOT'||[...(scope.allowed_instruments??[]),...(scope.selected_instruments??[])].some(key=>key.product!==scope.selected_product));
 return <Panel title="자동매매 운용 한도" className="automatic-settings">
  <p className="info-box">현재 실제 주문은 차단되어 있습니다. 한도를 저장해도 자동매매·유료 AI 호출·서버 결제는 시작되지 않습니다.</p>
  {source.error&&<p role="alert">운용 설정을 확인하지 못했습니다. 저장을 막았습니다. 입력 중인 값은 보관됩니다.</p>}
  {!policy?!source.error&&<p role="status">운용 설정을 불러오는 중입니다.</p>:<>
   {!source.capable&&!source.loading&&!uncertain&&<p className="info-box">서버 업데이트가 필요합니다. 지원 버전을 확인할 때까지 읽기 전용으로 표시하며 저장과 전환을 막습니다.</p>}
   {uncertain&&<p className="info-box">저장 결과가 아직 불확실합니다. 제출한 내용과 일치하는 최신 GET을 확인할 때까지 입력값을 보관하고 추가 저장을 막습니다.</p>}
   {source.data?.schema_version===1?<><p className="info-box">저장된 이전 설정을 그대로 보관하고 새 운용 초안으로 전환해야 한도를 저장할 수 있습니다. 거래 대상이나 전략은 자동으로 선택되지 않습니다.</p><button className="secondary" disabled={disabled||edit!==null} onClick={()=>void save(true)}>저장된 설정을 새 운용 초안으로 전환</button>{edit!==null&&<p className="helper">입력 중인 값은 보관하고 있습니다. 전환하려면 저장된 설정을 다시 불러와 변경 내용을 버리세요.</p>}</>:<>
    <p className="info-box">{policy.scope===null&&policy.strategy===null&&policy.risk_v2===null?'거래 대상·운용 전략·위험관리 기준은 아직 정해지지 않았습니다.':'입력한 거래 대상·운용 전략·위험관리 기준은 STOPPED 초안입니다.'} 이전 설정 값은 아래에 보관되어 있으며 실제 운용 기준으로 자동 변환되지 않습니다.</p>
    <h3>새 운용 초안 — 계좌와 거래 대상</h3>
    <Field label="계좌 ID" value={scope?.account?.account_id??''} disabled={disabled} onChange={account_id=>changeScope({account:{account_id,environment:scope?.account?.environment??null}})}/>
    {select('계좌 환경',scope?.account?.environment,{DEMO:'Bybit 연습계좌',LIVE:'Bybit 실제 계좌'},value=>changeScope({account:{account_id:scope?.account?.account_id??'',environment:(value||null) as 'DEMO'|'LIVE'|null}}))}
    <button className="secondary" disabled={disabled} onClick={()=>changeScope({account:null})}>계좌 선택 비우기</button>
    {select('운용 모드',scope?.mode,choices.mode,value=>changeScope({mode:(value||null) as PolicyScope['mode']}))}
    <fieldset disabled={disabled}><legend>허용 거래 상품</legend>{(['SPOT','LINEAR'] as const).map(product=><label key={product} style={{display:'block',margin:'8px 0'}}><input type="checkbox" style={{width:24,height:24,marginRight:8,verticalAlign:'middle'}} checked={scope?.allowed_products?.includes(product)??false} onChange={event=>changeScope({allowed_products:event.target.checked?[...(scope?.allowed_products??[]),product]:(scope?.allowed_products??[]).filter(item=>item!==product)})}/>{product==='SPOT'?'SPOT 현물':'LINEAR 선형 파생상품'}</label>)}</fieldset>
    {select('선택 거래 상품',scope?.selected_product,{SPOT:'SPOT 현물',LINEAR:'LINEAR 선형 파생상품'},value=>changeScope({selected_product:(value||null) as PolicyProduct|null}))}
    {select('거래 권한 프로필',scope?.permission_profile_revision,{BYBIT_SPOT_LINEAR_NO_FUNDS_V1:'SPOT·LINEAR 거래 / 자금 이동 권한 없음'},value=>changeScope({permission_profile_revision:(value||null) as PolicyScope['permission_profile_revision']}))}
    <Field label="허용 거래 대상 (거래소/상품/종목)" value={scope?.allowed_instruments?.map(policyInstrumentName).join(', ')??''} disabled={disabled} helper="예: BYBIT/LINEAR/BTCUSDT. 쉼표로 구분하며 입력 순서를 보관합니다." onChange={text=>changeScope({allowed_instruments:instruments(text)})}/>
    <Field label="완전 자동 선택 거래 대상 (거래소/상품/종목)" value={scope?.selected_instruments?.map(policyInstrumentName).join(', ')??''} disabled={disabled} helper="FULL_AUTO는 선택 목록, ALL_IN_ONE은 허용 목록을 사용합니다. 자동 선택하지 않습니다." onChange={text=>changeScope({selected_instruments:instruments(text)})}/>
    {incompatible&&<><p role="alert">선택한 거래 상품과 기존 거래 대상 또는 위험관리 값이 충돌합니다. 입력값을 그대로 보관했으며 저장할 수 없습니다.</p><p className="helper">아래 초기화는 허용·선택 거래 대상, 허용·선택 전략 참조, 위험관리 전체를 비웁니다. 계좌·운용 모드·허용 상품과 전체 과거 카탈로그는 보관합니다. 초기화 후 별도로 저장해야 합니다.</p><button className="secondary" disabled={disabled} onClick={()=>{if(policy&&scope){update({...policy,scope:{...scope,allowed_instruments:null,selected_instruments:null},strategy:strategy?{...strategy,allowed:null,selected:null}:null,risk_v2:null});setNotice('아직 저장하지 않은 초기화입니다. 변경 내용을 검토한 뒤 별도로 저장하세요.')}}}>거래 상품 변경에 맞춰 초안 비우기</button></>}
    <h3>정확한 전략 참조와 대상별 미리보기</h3>
    <p className="helper">전체 카탈로그의 정적 해시 일치만 확인합니다. 실제 전략 파일·승인 출처의 진위, 현재 승인·게시 세대는 확인되지 않았습니다.</p>
    {!strategy?.catalog?<><p className="info-box">전략 카탈로그 메타데이터가 아직 제공되지 않았습니다. 실제 승인된 전략·설정·거래 대상 입력은 대기 중입니다.</p>{!strategy&&<button className="secondary" disabled={disabled} onClick={()=>update({...policy,strategy:{section_version:1,catalog:null,allowed:null,selected:null}})}>전략 메타데이터 초안 만들기</button>}</>:<>
     <p className="helper" style={{overflowWrap:'anywhere'}}>보관된 카탈로그 참조: {strategy.catalog.revision}</p>
     {strategy.catalog.entries.map(entry=>{const name=policyReferenceName(entry.reference);const eligible=entry.approval_status==='APPROVED'&&(!scope?.account?.environment||entry.approval_environments.includes(scope.account.environment));return <div key={name}><p style={{overflowWrap:'anywhere'}}>{name} — 정적 상태 {entry.approval_status}; 적용 대상 {entry.permitted_instruments.map(policyInstrumentName).join(', ')}</p><label style={{display:'block',margin:'8px 0'}}><input type="checkbox" style={{width:24,height:24,marginRight:8,verticalAlign:'middle'}} aria-label={'허용 전략 '+name} disabled={disabled||!eligible} checked={containsRef(strategy.allowed,entry.reference)} onChange={event=>update({...policy,strategy:{...strategy,allowed:toggleRef(strategy.allowed,entry.reference,event.target.checked)}})}/>허용 전략 {name}</label><label style={{display:'block',margin:'8px 0'}}><input type="checkbox" style={{width:24,height:24,marginRight:8,verticalAlign:'middle'}} aria-label={'선택 전략 '+name} disabled={disabled||!containsRef(strategy.allowed,entry.reference)} checked={containsRef(strategy.selected,entry.reference)} onChange={event=>update({...policy,strategy:{...strategy,selected:toggleRef(strategy.selected,entry.reference,event.target.checked)}})}/>선택 전략 {name}</label></div>})}
     <p className="helper">대상별 미리보기는 저장 목록을 바꾸지 않습니다. 전체 허용·선택 참조와 카탈로그의 모든 핀은 원래 순서로 보관됩니다.</p>
     {pairs.map(pair=><div key={policyInstrumentName(pair.instrument)}>{pair.active.length===0?<p className="info-box">연결된 전략이 없는 거래 대상: {policyInstrumentName(pair.instrument)}</p>:<label className="field"><span>{pair.instrument.symbol}에 적용 가능한 전략</span><select defaultValue="" disabled={disabled}><option value="">미리보기 선택</option>{pair.active.map(ref=><option key={policyReferenceName(ref)} value={policyReferenceName(ref)}>{policyReferenceName(ref)}</option>)}</select></label>}</div>)}
    </>}
    <h3>명시적으로 입력하는 LINEAR 위험관리</h3>
    {scope?.selected_product==='SPOT'&&<p className="info-box">SPOT 위험관리·보호 주문은 E16에서 지원해야 합니다. 현물 선택은 보관하며 파생상품용 0·1 값으로 변환하지 않습니다.</p>}
    {!risk&&scope?.selected_product!=='SPOT'&&<button className="secondary" disabled={disabled} onClick={()=>update({...policy,risk_v2:emptyRisk()})}>LINEAR 위험관리 입력 시작</button>}
    {risk&&<>
     <p className="helper">비워 둔 금액·레버리지·시간·선택은 null로 보관합니다. 이전 자금·명목 금액은 새 상한으로 자동 전환하지 않습니다.</p>
     {policyRiskDecimals.map(key=><Field key={key} label={'새 초안 '+amounts[key]} value={risk[key]??''} disabled={disabled} onChange={text=>changeRisk(key,text||null)}/>)}
     {policyRiskCounts.map(key=><Field key={key} label={'새 초안 '+counts[key]} value={risk[key]===null?'':String(risk[key])} disabled={disabled} onChange={text=>changeRisk(key,text===''?null:/^\d+$/.test(text)?Number(text):text)}/>)}
     {select('새 초안 주문 수량 산정 방식',risk.sizing_rule,{LOSS_FIXED:'거래당 손실액 기준'},value=>changeRisk('sizing_rule',value||null))}
     {select('새 초안 증거금 모드',risk.margin_mode,choices.margin_mode,value=>changeRisk('margin_mode',value||null))}
     {select('새 초안 포지션 모드',risk.position_mode,{ONE_WAY:'단방향'},value=>changeRisk('position_mode',value||null))}
     <Field label="진입 심사 시 표시가격 기준 주문 명목 금액 상한 (USDT)" value={risk.caps?.max_order_admission_notional??''} disabled={disabled} onChange={text=>changeCap('max_order_admission_notional',text||null)}/>
     <Field label="진입 심사 시 표시가격 기준 계좌 명목 금액 상한 (USDT)" value={risk.caps?.max_account_admission_notional??''} disabled={disabled} onChange={text=>changeCap('max_account_admission_notional',text||null)}/>
     {select('진입 심사 평가 기준',risk.caps?.valuation_rule,{LINEAR_MARK_GROSS_AT_ADMISSION_V1:'진입 심사 시 현재 표시가격 기준 총 명목 금액'},value=>changeCap('valuation_rule',value||null))}
     {select('실제 체결 금액 상한 선택',risk.caps?.turnover_requirement,{NOT_REQUIRED:'별도 체결 금액 상한을 요구하지 않음',HARD_EXECUTION_QUOTE_CAP:'실제 체결 금액 상한 요구'},value=>changeCap('turnover_requirement',value||null))}
     <Field label="실제 체결 금액 상한 (USDT)" value={risk.caps?.max_execution_quote_amount??''} disabled={disabled} onChange={text=>changeCap('max_execution_quote_amount',text||null)}/>
    </>}
    <p className="info-box">HARD SELL은 실제 체결가격의 상한 증거가 필요합니다. 매도 지정가의 하한은 상한 증거가 아닙니다. CROSS 값은 보관할 수 있지만 실제 모델 검증과 주문 진입은 지원되지 않습니다.</p>
   </>}
   {invalid&&<p role="alert">입력값이 서로 맞지 않거나 허용 범위를 벗어났습니다. 변경 내용을 보관했으며 저장을 막았습니다. 계좌 ID·환경, 허용·선택 목록, 상품·위험관리, 정확한 소수·정수 형식을 확인하세요.</p>}
   <h3>이전 운용 한도</h3><p className="helper">아래 값은 이전 설정입니다. 새 LINEAR 정책의 자금 배정이나 주문·계좌 상한을 정하지 않습니다.</p>
   {['mode','environment'].map(key=><div key={key}>{select(choiceLabels[key],String(policy.legacy_v1[key]??''),choices[key],value=>changeLegacy(key,value||null))}</div>)}
   {Object.entries(amounts).slice(0,5).map(([key,label])=>legacyInput(key,label))}
   {Object.entries(lists).map(([key,label])=>legacyInput(key,label))}
   <details><summary>상세 이전 위험관리 설정</summary>{Object.entries(amounts).slice(5).map(([key,label])=>legacyInput(key,label))}{Object.entries(counts).map(([key,label])=>legacyInput(key,label))}{['sizing_rule','margin_mode','position_mode'].map(key=><div key={key}>{select(choiceLabels[key],String(policy.legacy_v1[key]??''),choices[key],value=>changeLegacy(key,value||null))}</div>)}</details>
   <p className="helper">저장된 설정 중 미정 항목: {source.data?.missing_fields.length??'확인 중'}개. 현재 초안 미정 항목: {typed&&validation?.policy===policy&&validation.valid?automaticPolicyMissing(policy).length:'확인 중'}개. 실제 운용에는 거래소·체결·보호 주문 검증과 서버 연결도 필요합니다.</p>
   <button className="primary full" disabled={disabled||!typed||invalid||incompatible} onClick={()=>void save()}>{saving?'저장 결과 확인 중':'운용 한도 저장'}</button>
   <button className="secondary" disabled={saving||source.loading} onClick={()=>{if(!unresolved.current)setNotice('');reload()}}>{uncertain?'저장 결과 다시 확인하기':'저장된 설정 다시 불러오기'}</button>
  </>}
  {notice&&<p role="status">{notice}</p>}
 </Panel>;
}