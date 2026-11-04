import {useEffect,useState} from 'react';
import {Badge,Definitions,Empty,Icon,Panel} from '../../ui/components';
import {navigate,type Route} from '../router';
import {validDiagnostics} from './contracts';
import {diagnosticsFixture} from './fixtures';
import {useRead} from './useRead';
import './live-pages.css';

const labels:Record<string,string>={READY:'작업 감시 중',STALLED:'진행 멈춤',STOPPED:'작업 중단',FAIL_CLOSED:'확인 실패 · 신규 실행 차단',READABLE:'장부 읽기 가능',UNAVAILABLE:'확인 불가',NOT_CONFIGURED:'미등록',REGISTERED_UNVERIFIED:'등록됨 · 권한 확인 전',INVALID_CONFIGURATION:'설정 확인 필요'};
const engineLabels:Record<string,string>={STARTING:'관측 연결 준비 중',OBSERVING:'관측 신호 수신 중',PASSIVE:'조회 관측 중',STOPPED:'관측 중단',CLEANUP_UNCONFIRMED:'종료 정리 확인 필요',CHECK_REQUIRED:'엔진 상태 확인 필요',NOT_STARTED:'관측 신호 없음',STALE:'최신 신호 확인 필요'};
export function DiagnosticsPage({route}:{route:Route}){
 const read=useRead('/api/v1/diagnostics',validDiagnostics,route.demo?diagnosticsFixture:undefined,true,route.demo?0:5000);
 const[now,setNow]=useState(()=>Date.now());
 useEffect(()=>{if(route.demo)return;const timer=window.setInterval(()=>setNow(Date.now()),1000);return()=>window.clearInterval(timer)},[route.demo]);
 const data=read.data,native=data?.components.native_monitor;
 const age=read.lastSuccess?now-Date.parse(read.lastSuccess):Infinity;
 const currentRead=!read.error&&!read.loading&&Number.isFinite(age)&&age>=0&&age<30000;
 const currentSignal=currentRead&&native?.age_ms!==null&&native?.age_ms!==undefined&&native.age_ms+age<30000;
 const engineState=!currentRead?'CHECK_REQUIRED':!native?'CHECK_REQUIRED':currentSignal||['CHECK_REQUIRED','NOT_STARTED','STALE'].includes(native.state)?native.state:'STALE';
 const restFresh=currentSignal&&native?.rest_state==='FRESH'&&(native.rest_remaining_ms??0)>age;
 const restLabel=engineState==='OBSERVING'?(restFresh?'최근 대조 확인':'대조 기한 지남 · 관측은 유지'):'대조 상태 확인 필요';
 const healthy=data?.components.automation.state==='READY'&&(route.demo||engineState==='OBSERVING');
 const unresolved=data?.components.manual_paper.unresolved_confirmations;
 return <div className="live-diagnostics" data-source={route.demo?'FIXTURE':'LOCAL_READ_MODEL'}>
  {data?<><div className={'diagnostic-banner '+(healthy?'surface':'warning-box')}><span className="notice-circle">!</span><div className="diagnostic-message"><h2>{healthy?'작업 감시 상태 확인':route.demo?'가상 작업 확인 필요':'운영 상태 확인 필요'}</h2><p>{data.components.automation.message??'자동 진행과 서버 응답을 구분해서 확인하세요.'}</p></div><Badge tone={healthy?'ok':'warning'}>{healthy?'상태 확인':'신규 진입 확인 필요'}</Badge></div>
   <div className="grid diagnostics-grid"><div className="stack"><Panel title="구성 요소 상태"><Definitions rows={[["로컬 서버",'응답 중 · 거래 가능 의미 아님'],["자동 작업",(labels[data.components.automation.state]??'상태 확인 필요')],["수동 가상 원장",(labels[data.components.manual_paper.state]??'확인 필요')],["계좌 등록",(labels[data.components.account.status]??'확인 필요')],["AI 분석",data.components.ai.external_calls?'외부 호출 상태 확인 필요':'꺼짐 · 외부 호출 없음'],...(!route.demo?[["서버 계정 관측",engineLabels[engineState]],["최근 계정 대조",restLabel],["자동매매",'차단 · 계정 관측만']] as [string,string][]:[])]}/><p className="helper">서버가 응답해도 자동 작업이 중단될 수 있습니다.</p>{!route.demo&&<p className="helper">{currentSignal?native?.message:'최신 엔진 관측 상태를 다시 확인해 주세요.'}</p>}</Panel>
    <Panel title="확인할 항목"><Definitions rows={[["미완료 주문 확인",unresolved===null||unresolved===undefined?'확인 불가':unresolved+' 건'],["연습계좌 결과 불확실",data.components.bybit_demo?.uncertain_count===undefined?'미확인':String(data.components.bybit_demo.uncertain_count??'확인 불가')+' 건']]}/>{data.errors.map((e,i)=><p role="alert" className="warning" key={i}>{e.component} · {e.code}</p>)}</Panel></div>
    <Panel title="복구 진행 방법"><div className="recovery-steps">{[['미완료 결과 대조','저장된 같은 요청 키로 처리 결과를 대조합니다. 새 요청으로 중복 실행하지 않습니다.'],['설정 · 원장 확인','고정 설정과 장부를 확인하고 중단 원인을 해결합니다.'],['사용자가 직접 재개','복구 후에도 자동으로 재개하지 않습니다.']].map(([a,b],i)=><div key={a}><span className="step">{i+1}</span><div><h3>{a}</h3><p>{b}</p></div></div>)}</div><button className="primary full" onClick={read.reload} disabled={read.loading}><Icon name="refresh"/>{route.demo?'예시 상태 다시 보기':'상태 다시 확인'}</button><button className="text-button full" onClick={()=>navigate({...route,page:'history',id:''})}>실행 기록 보기 →</button></Panel>
   </div></>:<Panel><Empty title={read.error?'진단 정보를 확인하지 못했습니다.':'기능별 상태를 확인하는 중입니다.'}/><button className="primary" onClick={read.reload} disabled={read.loading}>다시 확인</button></Panel>}
  {read.error&&data&&<p role="alert" className="warning">최신 진단에 실패했습니다. 위 내용은 마지막 조회값입니다.</p>}
 </div>;
}
