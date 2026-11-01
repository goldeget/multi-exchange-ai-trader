import {useEffect,useRef,useState} from 'react';
import {Badge,Definitions,Field,Panel} from '../../ui/components';
import {format,validConnection,validSettings,validBudget} from './contracts';
import {connectionFixture,settingsFixture,budgetFixture} from './fixtures';
import {useRead} from './useRead';
import './live-pages.css';

export function ConnectionsPage({demo=false}:{demo?:boolean}){
 const connections=useRead('/api/v1/connections/status',validConnection,demo?connectionFixture:undefined);
 const settings=useRead('/api/v1/settings/ai',validSettings,demo?settingsFixture:undefined);
 const budget=useRead('/api/v1/ai/budget',validBudget,demo?budgetFixture:undefined);
 const[edit,setEdit]=useState<{demo:boolean;amount:string|null;notice:string;saving:boolean}>({demo,amount:null,notice:'',saving:false});
 const pending=useRef<AbortController|null>(null);
 if(edit.demo!==demo)setEdit({demo,amount:null,notice:'',saving:false});
 useEffect(()=>()=>{pending.current?.abort();pending.current=null},[demo]);
 const limit=edit.demo===demo?edit.amount??settings.data?.monthly_limit??'':settings.data?.monthly_limit??'';
 async function save(){
  if(demo){setEdit({...edit,notice:'예시 설정입니다. 서버 예산과 외부 연결은 변경하지 않습니다.'});return}
  if(pending.current||!settings.data||!/^\d+(\.\d{1,8})?$/.test(limit)){setEdit({...edit,notice:'한도는 소수점 8자리 이내 숫자로 입력하세요.'});return}
  const controller=new AbortController();pending.current=controller;
  setEdit({...edit,saving:true,notice:''});
  try{
   const response=await fetch('/api/v1/settings/ai',{method:'PUT',signal:controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:settings.data.revision,monthly_limit:limit,mode:'OFFLINE_TEST'})});
   const value:unknown=await response.json();if(controller.signal.aborted)return;
   if(!response.ok){setEdit(old=>({...old,saving:false,notice:response.status===409?'설정이 변경되었거나 허용 범위 밖입니다. 최신 값을 다시 확인하세요.':'서버가 설정 저장을 거절했습니다.'}));settings.reload();return}
   if(!validSettings(value))throw new Error('settings_contract');
   setEdit({demo:false,amount:null,saving:false,notice:'오프라인 시험의 내부 예산 한도를 저장했습니다. 외부 호출은 꺼져 있습니다.'});settings.reload();budget.reload();
  }catch{
   if(!controller.signal.aborted){setEdit(old=>({...old,saving:false,notice:'저장 결과를 확인하지 못했습니다. 최신 설정을 대조한 뒤 다시 검토하세요.'}));settings.reload()}
  }finally{if(pending.current===controller)pending.current=null}
 }
 const practice=connections.data?.bybit_demo;
 const displayLimit=Number(budget.data?.monthly_limit??'0');
 const displayExposure=Number(budget.data?.used??'0')+Number(budget.data?.reserved??'0');
 const percent=displayLimit>0?Math.min(100,Math.max(0,displayExposure/displayLimit*100)):displayExposure>0?100:0;
 return <div className="grid settings-grid live-settings" data-source={demo?'FIXTURE':'LOCAL_READ_MODEL'}>
  <Panel title="연결 상태"><p className="helper">등록은 실제 조회·주문 권한의 검증과 다릅니다.</p>
   {connections.loading&&!connections.data?<p role="status">연결 설정을 확인하는 중입니다.</p>:connections.error?<p role="alert" className="warning">연결 상태를 확인하지 못했습니다.</p>:<>
    <div className="provider"><span className="provider-logo">B</span><div><h3>Bybit</h3><p>실계좌 읽기 전용</p><small>{connections.data?.bybit.registered?'등록됨 · 권한 미검증':'미등록'}</small></div><Badge>조회 전용</Badge></div>
    <div className="provider"><span className="provider-logo">B</span><div><h3>Bybit 연습계좌</h3><p>{practice?.registered?'등록됨 · 고정 연결 시험 권한 미검증':'미등록 · 연습 주문 꺼짐'}</p>{practice?.blocked_by_unresolved_attempt&&<p className="warning">미확정 요청이 있어 신규 시험을 차단했습니다.</p>}</div><Badge tone="warning">DEMO 별도 환경</Badge></div>
    {['OpenRouter','NVIDIA'].map(name=><div className="provider" key={name}><span className="provider-logo">◇</span><div><h3>{name}</h3><p>제공자 미설정</p></div><Badge>호출 꺼짐</Badge></div>)}
   </>}
   <div className="rule-section"><h2>AI 사용 설정</h2><Definitions rows={[["현재 모드","오프라인 시험"],["유료 PAPER 분석","꺼짐"],["비밀키","로컬 서버에서만 관리"]]}/><p className="helper">규칙 추천은 외부 AI 없이 동작합니다. 키와 서명은 브라우저에 표시하지 않습니다.</p></div>
  </Panel>
  <Panel title="AI 예산"><p className="helper">프로그램 내부 한도입니다. 제공자 계정 전체의 지출 한도가 아닙니다.</p><Field label="월 내부 예산 한도 (USD)" value={limit} onChange={amount=>setEdit({...edit,amount})} unit="$" disabled={!settings.data||edit.saving} helper="저장된 서버 한도를 표시합니다. 월 상한은 $100입니다."/>
   {budget.data?<><div className="budget-progress" role="progressbar" aria-label="내부 한도 중 사용·예약 비율" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Number.isFinite(percent)?percent:undefined}><span style={{width:Number.isFinite(percent)?percent+'%':undefined}}/></div><Definitions rows={[["사용 (USD)",format(budget.data.used)],["예약 (USD)",format(budget.data.reserved)],["과금 불확실 (USD)",format(budget.data.uncertain)],["내부 남은 한도 (USD)",format(budget.data.remaining)],["제공자 잔액",budget.data.provider_balance??'미확인'],["자동 충전","없음"]]}/></>:<p role={budget.error?'alert':'status'}>{budget.error?'예산을 확인하지 못했습니다. 숫자를 추측하지 않습니다.':'예산을 확인하는 중입니다.'}</p>}
   <div className="info-box"><strong>외부 호출은 꺼져 있습니다.</strong><p>예산 설정만 저장합니다. 모델 사용이나 유료 연결을 활성화하지 않습니다.</p></div><button className="primary full" disabled={!settings.data||edit.saving||settings.error} onClick={()=>void save()}>{edit.saving?'저장 결과 확인 중':'설정 저장'}</button>{edit.notice&&<p className="info-box" role="status">{edit.notice}</p>}{settings.error&&<p role="alert" className="warning">설정을 확인하지 못해 저장을 막았습니다.</p>}
  </Panel>
 </div>;
}
