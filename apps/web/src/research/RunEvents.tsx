import {useEffect,useState} from 'react';
import {Panel} from '../ui/components';
type Entry={sequence:number;operation:string;previous:string;hash:string;snapshot_digest:string};
type Result={id:string;attempt:number;refreshKey:string;status:'ready'|'failed';rows:Entry[]};
const digest=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
function entries(value:unknown,runId:string):Entry[]{
 if(!value||typeof value!=='object'||!('run_id'in value)||value.run_id!==runId||!('journal'in value)||!Array.isArray(value.journal))throw Error('audit');
 let previous=0;
 for(const row of value.journal){if(!row||typeof row!=='object'||!Number.isSafeInteger(row.sequence)||row.sequence<=previous||typeof row.operation!=='string'||!row.operation||row.operation.length>256||!digest(row.previous)||!digest(row.hash)||!digest(row.snapshot_digest))throw Error('journal');previous=row.sequence}
 return value.journal.slice(-3).reverse() as Entry[];
}
function label(operation:string){const action=operation.split(':')[0];const labels:Record<string,string>={create:'가상 실행 생성',resume:'가상 실행 재개',pause:'전체 일시정지',stop:'가상 포지션 정리 후 종료',pause_entries:'신규 진입 일시정지',resume_entries:'신규 진입 재개',step:'가격 봉 처리','recovery-paused':'재시작 후 안전 일시정지','native-failure-paused':'실행 오류로 안전 일시정지'};return labels[action]??'기타 저장 기록'}
const examples=[{sequence:38,operation:'step:36'},{sequence:37,operation:'step:35'},{sequence:36,operation:'resume:example'}];
export default function RunEvents({runId,demo=false,refreshKey=''}:{runId:string;demo?:boolean;refreshKey?:string}){
 const[attempt,setAttempt]=useState(0),[result,setResult]=useState<Result|null>(null);
 useEffect(()=>{
  if(demo)return;
  const controller=new AbortController();let active=true;
  const deadline=setTimeout(()=>{if(!active)return;active=false;controller.abort();setResult({id:runId,attempt,refreshKey,status:'failed',rows:[]})},10000);
  void(async()=>{try{
   if(!/^[A-Za-z0-9_-]{1,64}$/.test(runId))throw Error('run');
   const response=await fetch('/api/v1/research/runs/'+encodeURIComponent(runId)+'/audit',{method:'GET',signal:controller.signal});
   if(!response.ok)throw Error('response');
   const rows=entries(await response.json(),runId);
   if(active)setResult({id:runId,attempt,refreshKey,status:'ready',rows});
  }catch{if(active)setResult({id:runId,attempt,refreshKey,status:'failed',rows:[]})}finally{clearTimeout(deadline)}})();
  return()=>{active=false;clearTimeout(deadline);controller.abort()};
 },[runId,demo,attempt,refreshKey]);
 const current=!demo&&result?.id===runId&&result.attempt===attempt&&result.refreshKey===refreshKey?result:null;const rows=demo?examples:current?.rows??[];
 return <Panel title="최근 처리 기록"><p className="helper">{demo?'합성 고정 예시 · 실제 실행 기록이 아닙니다.':'저장된 감사 기록의 최근 3건 · 순번 역순 · 처리 시각은 제공되지 않습니다.'}</p>{!demo&&!current?<p role="status">최근 처리 기록을 확인하는 중입니다.</p>:current?.status==='failed'?<><p role="alert">최근 처리 기록을 확인하지 못했습니다.</p><button className="secondary" onClick={()=>setAttempt(n=>n+1)}>기록 다시 확인</button></>:rows.length?<ol aria-label="최근 처리 기록 목록">{rows.map(row=><li key={row.sequence}><strong>{label(row.operation)}</strong><p className="helper">기록 순번 {row.sequence}</p></li>)}</ol>:<p>저장된 처리 기록이 없습니다.</p>}</Panel>
}
