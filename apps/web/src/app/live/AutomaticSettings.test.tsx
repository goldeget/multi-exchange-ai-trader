import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {AutomaticSettings} from './AutomaticSettings';

const keys='capital_usdt leverage_min leverage_max per_trade_loss max_daily_loss max_drawdown max_total_notional max_total_risk max_total_margin margin_allocation liquidation_buffer_fraction max_quote_age_seconds max_evidence_age_seconds max_consecutive_losses max_positions mode environment sizing_rule margin_mode position_mode allowed_symbols allowed_strategies selected_symbols selected_strategies'.split(' ');
const fixture={schema_version:1,revision:0,policy:Object.fromEntries(keys.map(key=>[key,null])),missing_fields:keys,desired_state:'STOPPED',live_order_enabled:false,worker_running:false,blockers:['NATIVE_RUNTIME_NOT_QUALIFIED']};
afterEach(()=>{cleanup();vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals()});

it('fetches only when opened and saves an explicitly allocated partial draft',async()=>{
 const fetcher=vi.fn((_url:string,options?:RequestInit)=>Promise.resolve(new Response(JSON.stringify(options?.method==='PUT'?{...fixture,revision:1}:fixture),{status:200})));
 vi.stubGlobal('fetch',fetcher);render(<AutomaticSettings/>);
 expect(fetcher).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 const capital=await screen.findByLabelText('자동매매에 맡길 최대 자금 (USDT)');
 expect(capital).toHaveValue('');
 fireEvent.change(capital,{target:{value:'100'}});
 fireEvent.change(screen.getByLabelText('자동매매 모드'),{target:{value:'FULL_AUTO'}});
 fireEvent.click(screen.getByRole('button',{name:'운용 한도 저장'}));
 await screen.findByText('설정을 저장했습니다. 실제 주문은 시작되지 않습니다.');
 const sent=fetcher.mock.calls.find(call=>call[1]?.method==='PUT')!;
 const body=JSON.parse(String(sent[1]?.body));
 expect(body.revision).toBe(0);expect(body.policy.capital_usdt).toBe('100');
 expect(body.policy.leverage_max).toBeNull();expect(body.policy.mode).toBe('FULL_AUTO');
 expect(fetcher.mock.calls.every(call=>call[0]==='/api/v1/automatic/settings')).toBe(true);
});

it('rejects a response claiming live authority and offers no start action',async()=>{
 vi.stubGlobal('fetch',vi.fn(()=>Promise.resolve(new Response(JSON.stringify({...fixture,live_order_enabled:true})))));
 render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('저장을 막았습니다');
 expect(screen.queryByRole('button',{name:'운용 한도 저장'})).not.toBeInTheDocument();
});

it('keeps edited values after revision conflict until explicit reload',async()=>{
 vi.stubGlobal('fetch',vi.fn((_url,options?:RequestInit)=>Promise.resolve(new Response(JSON.stringify(fixture),{status:options?.method==='PUT'?409:200}))));
 render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 const capital=await screen.findByLabelText('자동매매에 맡길 최대 자금 (USDT)');
 fireEvent.change(capital,{target:{value:'250'}});fireEvent.click(screen.getByRole('button',{name:'운용 한도 저장'}));
 await screen.findByText(/다른 곳에서 설정이 변경/);expect(capital).toHaveValue('250');
 fireEvent.click(screen.getByRole('button',{name:'저장된 설정 다시 불러오기'}));
 await waitFor(()=>expect(capital).toHaveValue(''));
});

it('uses the successful PUT snapshot while its background GET is delayed',async()=>{
 let finishRead!:(response:Response)=>void;
 const saved={...fixture,revision:1,policy:{...fixture.policy,capital_usdt:'100'}};
 let reads=0;
 const fetcher=vi.fn((_url:string,options?:RequestInit)=>{
  if(options?.method==='PUT')return Promise.resolve(new Response(JSON.stringify(saved)));
  reads++;
  return reads===1?Promise.resolve(new Response(JSON.stringify(fixture))):new Promise<Response>(resolve=>{finishRead=resolve});
 });
 vi.stubGlobal('fetch',fetcher);render(<AutomaticSettings/>);
 fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 const capital=await screen.findByLabelText('자동매매에 맡길 최대 자금 (USDT)');
 fireEvent.change(capital,{target:{value:'100'}});fireEvent.click(screen.getByRole('button',{name:'운용 한도 저장'}));
 await screen.findByText('설정을 저장했습니다. 실제 주문은 시작되지 않습니다.');
 await waitFor(()=>expect(reads).toBe(2));
 expect(capital).toHaveValue('100');
 fireEvent.change(screen.getByLabelText('일일 최대 손실 (USDT)'),{target:{value:'5'}});
 await act(async()=>{finishRead(new Response(JSON.stringify(saved)))});
 fireEvent.click(screen.getByRole('button',{name:'운용 한도 저장'}));
 await waitFor(()=>expect(fetcher.mock.calls.filter(call=>call[1]?.method==='PUT')).toHaveLength(2));
 const second=JSON.parse(String(fetcher.mock.calls.filter(call=>call[1]?.method==='PUT')[1][1]?.body));
 expect(second.revision).toBe(1);expect(second.policy.capital_usdt).toBe('100');
 expect(second.policy.max_daily_loss).toBe('5');
});

it('discards edits on explicit reload and blocks changes until its fresh GET arrives',async()=>{
 let finishRead!:(response:Response)=>void;let reads=0;
 vi.stubGlobal('fetch',vi.fn((_url:string,options?:RequestInit)=>{
  if(options?.method==='PUT')return Promise.resolve(new Response('{}',{status:409}));
  return ++reads===1?Promise.resolve(new Response(JSON.stringify(fixture))):new Promise<Response>(resolve=>{finishRead=resolve});
 }));
 render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 const capital=await screen.findByLabelText('자동매매에 맡길 최대 자금 (USDT)');
 fireEvent.change(capital,{target:{value:'250'}});fireEvent.click(screen.getByRole('button',{name:'운용 한도 저장'}));
 await screen.findByText(/다른 곳에서 설정이 변경/);
 fireEvent.click(screen.getByRole('button',{name:'저장된 설정 다시 불러오기'}));
 expect(screen.getByRole('button',{name:'운용 한도 저장'})).toBeDisabled();
 expect(capital).toBeDisabled();
 await act(async()=>{finishRead(new Response(JSON.stringify({...fixture,revision:2,policy:{...fixture.policy,capital_usdt:'75'}})))});
 expect(capital).toHaveValue('75');expect(capital).toBeEnabled();
 fireEvent.change(capital,{target:{value:'80'}});
 fireEvent.click(screen.getByRole('button',{name:'운용 한도 저장'}));
 await screen.findByText(/다른 곳에서 설정이 변경/);
});

it('blocks an uncertain timed-out save until a fresh snapshot is recovered',async()=>{
 let finishRead!:(response:Response)=>void;let reads=0;let saveSignal:AbortSignal|null|undefined;
 vi.stubGlobal('fetch',vi.fn((_url:string,options?:RequestInit)=>{
  if(options?.method==='PUT'){saveSignal=options.signal;return new Promise<Response>(()=>undefined)}
  return ++reads===1?Promise.resolve(new Response(JSON.stringify(fixture))):new Promise<Response>(resolve=>{finishRead=resolve});
 }));
 render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 const capital=await screen.findByLabelText('자동매매에 맡길 최대 자금 (USDT)');
 vi.useFakeTimers();
 fireEvent.change(capital,{target:{value:'100'}});fireEvent.click(screen.getByRole('button',{name:'운용 한도 저장'}));
 await act(async()=>{await vi.advanceTimersByTimeAsync(15000)});
 expect(saveSignal?.aborted).toBe(true);
 expect(screen.getByText(/저장 결과를 확인하지 못했습니다/)).toBeVisible();
 expect(capital).toBeDisabled();expect(screen.getByRole('button',{name:'운용 한도 저장'})).toBeDisabled();
 await act(async()=>{finishRead(new Response(JSON.stringify({...fixture,revision:1,policy:{...fixture.policy,capital_usdt:'100'}})))});
 expect(capital).toHaveValue('100');expect(capital).toBeEnabled();
});

it('aborts a pending save and clears its timer when the editor unmounts',async()=>{
 let finishSave!:(response:Response)=>void;let saveSignal:AbortSignal|null|undefined;
 const fetcher=vi.fn((_url:string,options?:RequestInit)=>{
  if(options?.method==='PUT'){saveSignal=options.signal;return new Promise<Response>(resolve=>{finishSave=resolve})}
  return Promise.resolve(new Response(JSON.stringify(fixture)));
 });
 vi.stubGlobal('fetch',fetcher);render(<AutomaticSettings/>);
 fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 await screen.findByLabelText('자동매매에 맡길 최대 자금 (USDT)');
 const timer=vi.spyOn(window,'setTimeout');const clearTimer=vi.spyOn(window,'clearTimeout');
 fireEvent.click(screen.getByRole('button',{name:'운용 한도 저장'}));
 const deadline=timer.mock.results[timer.mock.calls.findIndex(call=>call[1]===15000)].value;
 fireEvent.click(screen.getByRole('button',{name:'운용 한도 접기'}));
 expect(saveSignal?.aborted).toBe(true);expect(clearTimer).toHaveBeenCalledWith(deadline);
 await act(async()=>{finishSave(new Response(JSON.stringify({...fixture,revision:1})))});
 expect(fetcher).toHaveBeenCalledTimes(2);
});
