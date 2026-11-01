import {useState} from 'react';
import {Badge,Definitions,Empty,Icon,Metrics,Panel} from '../../ui/components';
import CandleChart from '../../chart/CandleChart';
import {usePolling,marketUrl,type Candle,type Ticker} from '../../api';
import {fixtureCandles} from '../../visual/scenarios';
import {navigate,type Route} from '../router';
import {format,validOverview,validConnection,type Ledger} from './contracts';
import {overviewFixture,connectionFixture} from './fixtures';
import {useRead} from './useRead';
import './live-pages.css';

const statuses:Record<string,string>={RUNNING:'가상 실행 중',ENTRY_PAUSED:'신규 진입 일시정지',PAUSED:'전체 일시정지',PAUSED_RECOVERY:'복구 확인 필요',STOPPED:'종료',COMPLETED:'완료',FAILED:'실패 · 확인 필요'};
const ledgerKey=(ledger:Ledger)=>(ledger.product??'SYNTHETIC_PERPETUAL')+':'+ledger.id;
function MarketFlow({demo}:{demo:boolean}){
 const candles=usePolling<Candle[]>(marketUrl('candles','bybit','BTCUSDT','spot','1h'),30000,!demo);
 const ticker=usePolling<Ticker>(marketUrl('ticker','bybit','BTCUSDT','spot'),5000,!demo);
 const bars=demo?fixtureCandles:candles.data??[];
 return <Panel title="시장 흐름" className="market-chart overview-market" aside={<Badge>{demo?'합성 예시':'Bybit 현물 · 공개 시세'}</Badge>}>
  <div className="price-bar"><strong>{demo?'65,210.40':ticker.data?format(ticker.data.last_price):'—'} <small>USDT</small></strong>{demo&&<span className="up">+286.30 +0.44%</span>}</div>
  {!demo&&(ticker.error||candles.error)&&<p role="alert" className="warning">시세 조회 실패 · 남아 있는 숫자는 마지막 조회값입니다.</p>}
  {!demo&&!candles.data&&!candles.error?<p role="status">공개 시세를 확인하는 중입니다.</p>:null}
  <CandleChart candles={bars}/><div className="chart-caption"><span>{demo?'출처 · 고정 합성 예시':'출처 · Bybit 현물 공개 시세'}</span><span>{demo?'09:10:03 KST · 형성 중 봉 포함':ticker.data?new Date(ticker.data.fetched_at).toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul'})+' KST':'갱신 시각 미확인'}</span></div>
 </Panel>;
}
export function OverviewPage({route}:{route:Route}){
 const read=useRead('/api/v1/overview',validOverview,route.demo?overviewFixture:undefined);
 const connections=useRead('/api/v1/connections/status',validConnection,route.demo?connectionFixture:undefined);
 const[selected,setSelected]=useState('');
 const ledger=read.data?.ledgers.find(x=>ledgerKey(x)===selected)??(!selected?read.data?.ledgers[0]:undefined);
 const run=read.data?.automation.runs[0],health=read.data?.automation.scheduler;
 return <div className="live-overview" data-source={route.demo?'FIXTURE':'LOCAL_READ_MODEL'}>
  {read.loading&&!read.data&&<p role="status">독립 장부와 자동 작업 상태를 확인하는 중입니다.</p>}
  {read.error&&<div className="warning-box" role="alert">현황을 확인하지 못했습니다. 남아 있는 값은 과거 조회값입니다. <button className="secondary" onClick={read.reload}>다시 확인</button></div>}
  <div className="ledger-scope"><label>가상 장부 <select aria-label="현황 장부" value={ledger?ledgerKey(ledger):''} onChange={e=>setSelected(e.target.value)}><option value="">장부 선택</option>{read.data?.ledgers.map((l,i)=><option value={ledgerKey(l)} key={ledgerKey(l)}>수동 {l.product==='SPOT'?'현물':'합성 선물'} 장부 {i+1}</option>)}</select></label><p className="helper">선택한 장부만 표시합니다. 실계좌·수동·자동 자금은 합산하지 않습니다.</p></div>
  <Metrics items={[["가상 평가자산",ledger?format(ledger.state.equity)+' USDT':'—'],["미실현 가상손익",ledger?format(ledger.state.unrealized_pnl)+' USDT':'—'],["열린 가상 포지션",ledger?ledger.state.positions.length+' 개':'—']]}/>
  {!read.loading&&!read.error&&!ledger&&<p className="helper">저장된 수동 장부가 없습니다. 수동 가상매매에서 별도 장부를 만들 수 있습니다.</p>}
  <div className="grid overview-grid"><MarketFlow demo={route.demo}/><Panel title="자동실행" aside={<Badge tone={run?.status==='RUNNING'?'ok':'warning'}>{run?statuses[run.status]??'상태 확인 필요':'저장된 실행 없음'}</Badge>}>
   {run?<Definitions rows={[["전략","이동평균 교차"],["대상",'합성 선물 · 독립 가상 장부'],["데이터",'저장된 데이터셋'],["최대 사용금액",String(run.config.max_exposure??'미확인')+' USDT'],["레버리지",String(run.config.leverage??10)+' 배'],["작업 상태",health?.state==='READY'?'감시 중':'확인 필요']]}/>:<Empty title="저장된 자동실행이 없습니다."/>}
   <p className="helper">{health?.message??'작업 진행 상태를 확인하지 못했습니다.'}</p><button className="primary full" onClick={()=>navigate({...route,page:run?'run':'create',id:run?.run_id??''})}><Icon name="file"/>{run?'실행 현황 보기':'새 실행 만들기'}<Icon name="arrow"/></button>
  </Panel></div>
  <div className="grid equal"><Panel title="계좌 연결"><div className="connections-list"><div><span className="exchange-logo bybit">B</span><strong>Bybit</strong><span>{connections.data?connections.data.bybit.registered?'등록됨 · 권한 확인 전':'미등록':'확인 불가'}</span></div><div><span className="exchange-logo binance">◆</span><strong>Binance</strong><span>공개 시세</span></div><div><span className="exchange-logo okx">▪</span><strong>OKX</strong><span>미연결</span></div></div><p className="helper">현황에서는 계좌를 조회하지 않습니다. 외부 포지션은 프로그램이 관리하지 않습니다.</p><button className="text-button" onClick={()=>navigate({...route,page:'account',id:''})}>계좌 · 포지션 조회 →</button></Panel>
   <Panel title="확인할 항목"><div className="notice-row"><span className="notice-circle warning">Ⅱ</span><div><strong>{run?statuses[run.status]??'가상 실행 상태를 확인하세요.':'새 가상 실행을 직접 만들 수 있습니다.'}</strong><p>생성 후 설정은 고정됩니다. 복구 후에도 직접 재개해야 합니다.</p></div></div><div className="notice-row"><Icon name="lock"/><div><strong>실계좌 주문 꺼짐 · 외부 AI 꺼짐</strong><p>연습계좌 연결 시험은 별도 환경이며 추천 승인 화면에서 대상과 상태를 확인합니다.</p></div></div>{!!read.data?.errors.length&&<p role="alert" className="warning">일부 장부를 읽지 못했습니다. 진단에서 확인하세요.</p>}</Panel></div>
 </div>;
}
