import {act,cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {OverviewPage} from './OverviewPage';
import {connectionFixture,overviewFixture} from './fixtures';
import * as reads from './useRead';

vi.mock('../../chart/CandleChart',()=>({default:()=>null}));
beforeEach(()=>vi.stubGlobal('fetch',vi.fn(()=>new Promise<Response>(()=>{}))));
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals();vi.useRealTimers()});

const healthy={state:'READY',message:'가상 거래만 자동으로 진행합니다.',task_alive:true,block_new_auto:false,work_differs_from_store:false};
function overview(scheduler:unknown=healthy,status='RUNNING'){
 return {...overviewFixture,automation:{runs:[{...overviewFixture.automation.runs[0],status}],scheduler}};
}
function serve(value:unknown){
 vi.stubGlobal('fetch',vi.fn((url:string)=>Promise.resolve(new Response(JSON.stringify(
  url==='/api/v1/overview'?value:url==='/api/v1/connections/status'?connectionFixture:{}
 ),{status:url.startsWith('/api/v1/market/')?503:200}))));
}
function automationPanel(){return screen.getByRole('heading',{name:'자동실행'}).closest('section')!}
function badge(){return automationPanel().querySelector('.badge')!}

it('shows a healthy running badge only after reading a ready live scheduler with a living task',async()=>{
 serve(overview());
 render(<OverviewPage route={{page:'overview',demo:false,id:''}}/>);
 await waitFor(()=>expect(badge()).toHaveTextContent('가상 실행 중'));
 expect(badge()).toHaveClass('ok');
 expect(within(automationPanel()).getByText('감시 중')).toBeInTheDocument();
});

it.each<[string,unknown]>([
 ['stopped',{...healthy,state:'STOPPED'}],
 ['failed closed',{...healthy,state:'FAIL_CLOSED'}],
 ['stalled',{...healthy,state:'STALLED'}],
 ['dead task',{...healthy,task_alive:false}],
 ['missing task signal',{state:'READY',message:healthy.message}],
 ['invalid task signal',{...healthy,task_alive:'true'}],
 ['blocked scheduler',{...healthy,block_new_auto:true}],
 ['stored state mismatch',{...healthy,work_differs_from_store:true}],
 ...(['block_new_auto','work_differs_from_store'] as const).flatMap<[string,unknown]>(flag=>[
  [`missing ${flag}`,Object.fromEntries(Object.entries(healthy).filter(([key])=>key!==flag))],
  ...['true','false',null,0].map<[string,unknown]>(value=>[
   `malformed ${flag} (${JSON.stringify(value)})`,{...healthy,[flag]:value},
  ]),
 ]),
 ['missing scheduler',undefined],
])('does not call a persisted RUNNING run healthy with a %s',async(_name,scheduler)=>{
 const value=overview(scheduler);
 value.automation.scheduler=scheduler;
 serve(value);
 render(<OverviewPage route={{page:'overview',demo:false,id:''}}/>);
 await within(automationPanel()).findByText('이동평균 교차');
 expect(badge()).not.toHaveClass('ok');
 expect(screen.queryByText('가상 실행 중')).not.toBeInTheDocument();
 expect(within(automationPanel()).queryByText('감시 중')).not.toBeInTheDocument();
});

it('does not call an automation storage error healthy even with a ready scheduler',async()=>{
 serve({...overview(),errors:[{component:'automation',code:'storage_unavailable'}]});
 render(<OverviewPage route={{page:'overview',demo:false,id:''}}/>);
 await within(automationPanel()).findByText('이동평균 교차');
 expect(badge()).not.toHaveClass('ok');
 expect(screen.queryByText('가상 실행 중')).not.toBeInTheDocument();
});

// A retained successful value can coexist with an unavailable read. Exercise that
// hook boundary directly because the overview has no refresh control after success.
it.each([
 ['failed read',true,false,'2026-11-02T01:00:00Z'],
 ['pending read',false,true,'2026-11-02T01:00:00Z'],
 ['unconfirmed read',false,false,null],
])('does not reuse a retained healthy status or message after a %s',(_name,error,loading,lastSuccess)=>{
 vi.spyOn(reads,'useRead').mockImplementation(<T,>(url:string)=>({
  data:(url==='/api/v1/overview'?overview():connectionFixture) as T,
  error:url==='/api/v1/overview'&&error,loading:url==='/api/v1/overview'&&loading,lastSuccess,reload:()=>undefined,
 }));
 render(<OverviewPage route={{page:'overview',demo:false,id:''}}/>);
 expect(badge()).not.toHaveClass('ok');
 expect(screen.queryByText('가상 실행 중')).not.toBeInTheDocument();
 expect(within(automationPanel()).queryByText('감시 중')).not.toBeInTheDocument();
 expect(screen.queryByText(healthy.message)).not.toBeInTheDocument();
});

it('expires a healthy live observation as time passes without a new successful response',()=>{
 vi.useFakeTimers();
 vi.setSystemTime(new Date('2026-11-02T01:00:00Z'));
 const reload=vi.fn();
 vi.spyOn(reads,'useRead').mockImplementation(<T,>(url:string)=>({
  data:(url==='/api/v1/overview'?overview():connectionFixture) as T,
  error:false,loading:false,lastSuccess:'2026-11-02T01:00:00Z',reload,
 }));
 render(<OverviewPage route={{page:'overview',demo:false,id:''}}/>);
 expect(badge()).toHaveClass('ok');
 act(()=>vi.advanceTimersByTime(30001));
 expect(badge()).not.toHaveClass('ok');
 expect(screen.queryByText('가상 실행 중')).not.toBeInTheDocument();
 expect(screen.queryByText(healthy.message)).not.toBeInTheDocument();
 expect(within(automationPanel()).getByText(/마지막 성공.*30초 전/)).toBeInTheDocument();
 fireEvent.click(within(automationPanel()).getByRole('button',{name:'다시 확인'}));
 expect(reload).toHaveBeenCalledOnce();
});

it('refreshes live scheduler status every five seconds and recovers after a successful read',async()=>{
 vi.useFakeTimers();
 let current=overview();
 vi.stubGlobal('fetch',vi.fn((url:string)=>Promise.resolve(new Response(JSON.stringify(
  url==='/api/v1/overview'?current:url==='/api/v1/connections/status'?connectionFixture:{}
 ),{status:url.startsWith('/api/v1/market/')?503:200}))));
 render(<OverviewPage route={{page:'overview',demo:false,id:''}}/>);
 await act(async()=>{});
 expect(badge()).toHaveClass('ok');
 current=overview({...healthy,state:'STOPPED',task_alive:false});
 await act(async()=>vi.advanceTimersByTime(5000));
 expect(badge()).not.toHaveClass('ok');
 expect(screen.queryByText('가상 실행 중')).not.toBeInTheDocument();
 current=overview();
 await act(async()=>vi.advanceTimersByTime(5000));
 expect(badge()).toHaveClass('ok');
});

it.each([
 ['ENTRY_PAUSED','신규 진입 일시정지'],['PAUSED','전체 일시정지'],
 ['PAUSED_RECOVERY','복구 확인 필요'],['STOPPED','종료'],
 ['COMPLETED','완료'],['FAILED','실패 · 확인 필요'],
])('preserves the stored %s status when the scheduler is unavailable',async(status,label)=>{
 serve(overview({...healthy,state:'STOPPED',task_alive:false},status));
 render(<OverviewPage route={{page:'overview',demo:false,id:''}}/>);
 await waitFor(()=>expect(badge()).toHaveTextContent(label));
 expect(badge()).not.toHaveClass('ok');
});

it('preserves the paused demo presentation without fetching live data',()=>{
 const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);
 render(<OverviewPage route={{page:'overview',demo:true,id:''}}/>);
 expect(badge()).toHaveTextContent('전체 일시정지');
 expect(within(automationPanel()).getByText('감시 중')).toBeInTheDocument();
 expect(screen.getByText('고정 예시 · 가상 실행 전체 일시정지')).toBeInTheDocument();
 expect(fetcher).not.toHaveBeenCalled();
});
