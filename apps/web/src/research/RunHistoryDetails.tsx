import type {Run} from './Research';
import {Definitions,Panel} from '../ui/components';
import {reasonLabel,runStatusLabel} from '../ko';
import RunEvents from './RunEvents';
function decimal(value:unknown):string|null{const text=typeof value==='string'?value:typeof value==='number'&&Number.isSafeInteger(value)?String(value):'';return text.length<=128&&/^-?\d+(?:\.\d+)?$/.test(text)?text:null}
function amount(value:unknown,unit:string){const text=decimal(value);if(text===null)return '미확인';const[whole,fraction]=text.split('.');return whole.replace(/\B(?=(\d{3})+(?!\d))/g,',')+(fraction===undefined?'':'.'+fraction)+' '+unit}
function ratio(value:unknown){const text=decimal(value);if(text===null||text.startsWith('-'))return '미확인';const[whole,fraction='']=text.split('.');const padded=fraction.padEnd(2,'0');return (whole+padded.slice(0,2)).replace(/^0+(?=\d)/,'')+(padded.length>2?'.'+padded.slice(2):'')+' %'}
export default function RunHistoryDetails({run,demo=false,onOpen}:{run:Run;demo?:boolean;onOpen:()=>void}){
 const c=run.config??{},n=run.native;
 const count=(value:unknown)=>typeof value==='number'&&Number.isSafeInteger(value)&&value>=0?String(value):'미확인';
 return <div className="stack"><Panel title="선택한 실행 상세"><p className="helper">{demo?'합성 고정 예시 · 실제 저장 실행이 아닙니다.':'목록에서 조회한 저장 상태입니다. 최신 상태는 실행 상세에서 다시 확인하세요.'}</p><Definitions rows={[
  ['실행 ID',<span className="long-identifier">{run.run_id}</span>],['저장 상태',typeof run.status==='string'?runStatusLabel(run.status):'미확인'],['상태 사유',typeof run.reason==='string'?reasonLabel(run.reason):'미확인'],['데이터 ID',<span className="long-identifier">{run.dataset_id}</span>],
  ['처리한 봉',count(run.cursor)+' / '+count(c.bar_limit)],['가상 평가자산',amount(n?.equity,'USDT')],['정리한 가상손익',amount(n?.closed_net_pnl,'USDT')],['미실현 가상손익',amount(n?.unrealized_pnl,'USDT')],['누적 수수료',amount(n?.fees,'USDT')],
  ['주문 수량',amount(c.quantity,'상품 수량')],['레버리지',amount(c.leverage,'배')],['단기 / 장기 이동평균',count(c.fast)+' / '+count(c.slow)+' 봉'],['최대 사용금액',amount(c.max_exposure,'USDT')],['총손실 한도',ratio(c.max_loss)],['최대 낙폭 한도',ratio(c.max_drawdown)],['연속 손실 허용',count(c.max_consecutive_losses)+' 회'],
 ]}/><p className="helper">생성 당시 설정과 별도 가상 장부입니다. 조회와 화면 이동은 실행을 재개하거나 주문을 보내지 않습니다.</p><button className="secondary full" onClick={onOpen}>실행 상세 보기</button></Panel><RunEvents key={run.run_id+':'+demo} runId={run.run_id} demo={demo}/></div>
}
