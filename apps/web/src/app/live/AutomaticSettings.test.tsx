import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {AutomaticSettings} from './AutomaticSettings';
import {automaticPolicyMissing,automaticPolicyPairs,parseAutomaticSettings,validateAutomaticPolicy} from './contracts';

const keys='capital_usdt leverage_min leverage_max per_trade_loss max_daily_loss max_drawdown max_total_notional max_total_risk max_total_margin margin_allocation liquidation_buffer_fraction max_quote_age_seconds max_evidence_age_seconds max_consecutive_losses max_positions mode environment sizing_rule margin_mode position_mode allowed_symbols allowed_strategies selected_symbols selected_strategies'.split(' ');
const legacy=Object.fromEntries(keys.map(key=>[key,null]));
const fixture={schema_version:2,revision:0,policy:{legacy_v1:legacy,scope:null,strategy:null,risk_v2:null},missing_fields:['risk_v2','scope','strategy'],desired_state:'STOPPED',live_order_enabled:false,worker_running:false,blockers:['POLICY_V2_ADMISSION_REQUIRED','NATIVE_RUNTIME_NOT_QUALIFIED','WORKER_NOT_CONNECTED']};
const v1={...fixture,schema_version:1,policy:{...legacy,capital_usdt:'100.00'},missing_fields:keys,blockers:['POLICY_V1_MIGRATION_REQUIRED','NATIVE_RUNTIME_NOT_QUALIFIED','WORKER_NOT_CONNECTED']};
afterEach(()=>{cleanup();vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals()});

it('fetches only when opened and saves an explicitly allocated partial draft',async()=>{
 const fetcher=vi.fn((_url:string,options?:RequestInit)=>Promise.resolve(capable(options?.method==='PUT'?{...fixture,revision:1,policy:JSON.parse(String(options.body)).policy}:fixture,200)));
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
 expect(body.revision).toBe(0);expect(body.schema_version).toBe(2);expect(body.expected_strategy_generation).toBeNull();expect(body.policy.legacy_v1.capital_usdt).toBe('100');
 expect(body.policy.legacy_v1.leverage_max).toBeNull();expect(body.policy.legacy_v1.mode).toBe('FULL_AUTO');
 expect(fetcher.mock.calls.every(call=>call[0]==='/api/v1/automatic/settings')).toBe(true);
});

it('rejects a response claiming live authority and offers no start action',async()=>{
 vi.stubGlobal('fetch',vi.fn(()=>Promise.resolve(capable({...fixture,live_order_enabled:true}))));
 render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('저장을 막았습니다');
 expect(screen.queryByRole('button',{name:'운용 한도 저장'})).not.toBeInTheDocument();
});

it('keeps edited values after revision conflict until explicit reload',async()=>{
 vi.stubGlobal('fetch',vi.fn((_url,options?:RequestInit)=>Promise.resolve(capable(fixture,options?.method==='PUT'?409:200))));
 render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 const capital=await screen.findByLabelText('자동매매에 맡길 최대 자금 (USDT)');
 fireEvent.change(capital,{target:{value:'250'}});fireEvent.click(screen.getByRole('button',{name:'운용 한도 저장'}));
 await screen.findByText(/다른 곳에서 설정이 변경/);expect(capital).toHaveValue('250');
 fireEvent.click(screen.getByRole('button',{name:'저장된 설정 다시 불러오기'}));
 await waitFor(()=>expect(capital).toHaveValue(''));
});

it('uses the successful PUT snapshot while its background GET is delayed',async()=>{
 let finishRead!:(response:Response)=>void;
 const saved={...fixture,revision:1,policy:{...fixture.policy,legacy_v1:{...legacy,capital_usdt:'100'}}};
 let reads=0;
 const fetcher=vi.fn((_url:string,options?:RequestInit)=>{
  if(options?.method==='PUT')return Promise.resolve(capable(saved));
  reads++;
  return reads===1?Promise.resolve(capable(fixture)):new Promise<Response>(resolve=>{finishRead=resolve});
 });
 vi.stubGlobal('fetch',fetcher);render(<AutomaticSettings/>);
 fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 const capital=await screen.findByLabelText('자동매매에 맡길 최대 자금 (USDT)');
 fireEvent.change(capital,{target:{value:'100'}});fireEvent.click(screen.getByRole('button',{name:'운용 한도 저장'}));
 await screen.findByText('설정을 저장했습니다. 실제 주문은 시작되지 않습니다.');
 await waitFor(()=>expect(reads).toBe(2));
 expect(capital).toHaveValue('100');
 fireEvent.change(screen.getByLabelText('일일 최대 손실 (USDT)'),{target:{value:'5'}});
 await act(async()=>{finishRead(capable(saved))});
 fireEvent.click(screen.getByRole('button',{name:'운용 한도 저장'}));
 await waitFor(()=>expect(fetcher.mock.calls.filter(call=>call[1]?.method==='PUT')).toHaveLength(2));
 const second=JSON.parse(String(fetcher.mock.calls.filter(call=>call[1]?.method==='PUT')[1][1]?.body));
 expect(second.revision).toBe(1);expect(second.policy.legacy_v1.capital_usdt).toBe('100');
 expect(second.policy.legacy_v1.max_daily_loss).toBe('5');
});

it('discards edits on explicit reload and blocks changes until its fresh GET arrives',async()=>{
 let finishRead!:(response:Response)=>void;let reads=0;
 vi.stubGlobal('fetch',vi.fn((_url:string,options?:RequestInit)=>{
  if(options?.method==='PUT')return Promise.resolve(new Response('{}',{status:409}));
  return ++reads===1?Promise.resolve(capable(fixture)):new Promise<Response>(resolve=>{finishRead=resolve});
 }));
 render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 const capital=await screen.findByLabelText('자동매매에 맡길 최대 자금 (USDT)');
 fireEvent.change(capital,{target:{value:'250'}});fireEvent.click(screen.getByRole('button',{name:'운용 한도 저장'}));
 await screen.findByText(/다른 곳에서 설정이 변경/);
 fireEvent.click(screen.getByRole('button',{name:'저장된 설정 다시 불러오기'}));
 expect(screen.getByRole('button',{name:'운용 한도 저장'})).toBeDisabled();
 expect(capital).toBeDisabled();
 await act(async()=>{finishRead(capable({...fixture,revision:2,policy:{...fixture.policy,legacy_v1:{...legacy,capital_usdt:'75'}}}))});
 expect(capital).toHaveValue('75');expect(capital).toBeEnabled();
 fireEvent.change(capital,{target:{value:'80'}});
 fireEvent.click(screen.getByRole('button',{name:'운용 한도 저장'}));
 await screen.findByText(/다른 곳에서 설정이 변경/);
});

it('blocks an uncertain timed-out save until a fresh snapshot is recovered',async()=>{
 let finishRead!:(response:Response)=>void;let reads=0;let saveSignal:AbortSignal|null|undefined;
 vi.stubGlobal('fetch',vi.fn((_url:string,options?:RequestInit)=>{
  if(options?.method==='PUT'){saveSignal=options.signal;return new Promise<Response>(()=>undefined)}
  return ++reads===1?Promise.resolve(capable(fixture)):new Promise<Response>(resolve=>{finishRead=resolve});
 }));
 render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 const capital=await screen.findByLabelText('자동매매에 맡길 최대 자금 (USDT)');
 vi.useFakeTimers();
 fireEvent.change(capital,{target:{value:'100'}});fireEvent.click(screen.getByRole('button',{name:'운용 한도 저장'}));
 await act(async()=>{await vi.advanceTimersByTimeAsync(15000)});
 expect(saveSignal?.aborted).toBe(true);
 expect(screen.getByText(/저장 결과를 확인하지 못했습니다/)).toBeVisible();
 expect(capital).toBeDisabled();expect(screen.getByRole('button',{name:'운용 한도 저장'})).toBeDisabled();
 await act(async()=>{finishRead(capable({...fixture,revision:1,policy:{...fixture.policy,legacy_v1:{...legacy,capital_usdt:'100'}}}))});
 expect(capital).toHaveValue('100');expect(capital).toBeEnabled();
});

it('aborts a pending save and clears its timer when the editor unmounts',async()=>{
 let finishSave!:(response:Response)=>void;let saveSignal:AbortSignal|null|undefined;
 const fetcher=vi.fn((_url:string,options?:RequestInit)=>{
  if(options?.method==='PUT'){saveSignal=options.signal;return new Promise<Response>(resolve=>{finishSave=resolve})}
  return Promise.resolve(capable(fixture));
 });
 vi.stubGlobal('fetch',fetcher);render(<AutomaticSettings/>);
 fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 await screen.findByLabelText('자동매매에 맡길 최대 자금 (USDT)');
 const timer=vi.spyOn(window,'setTimeout');const clearTimer=vi.spyOn(window,'clearTimeout');
 fireEvent.click(screen.getByRole('button',{name:'운용 한도 저장'}));
 await waitFor(()=>expect(saveSignal).toBeDefined());
 const deadline=timer.mock.results[timer.mock.calls.findIndex(call=>call[1]===15000)].value;
 fireEvent.click(screen.getByRole('button',{name:'운용 한도 접기'}));
 expect(saveSignal?.aborted).toBe(true);expect(clearTimer).toHaveBeenCalledWith(deadline);
 await act(async()=>{finishSave(capable({...fixture,revision:1}))});
 expect(fetcher).toHaveBeenCalledTimes(2);
});

it('migrates only the stored V1 revision after explicit discard and preserves lexical values',async()=>{
 const migrated={...fixture,revision:5,policy:{...fixture.policy,legacy_v1:v1.policy}};
 const fetcher=vi.fn((_url:string,options?:RequestInit)=>Promise.resolve(capable(options?.method==='POST'?migrated:{...v1,revision:4})));
 vi.stubGlobal('fetch',fetcher);render(<AutomaticSettings/>);
 fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 const capital=await screen.findByLabelText('자동매매에 맡길 최대 자금 (USDT)');
 expect(capital).toHaveValue('100.00');
 const migrate=screen.getByRole('button',{name:'저장된 설정을 새 운용 초안으로 전환'});
 expect(screen.getByRole('button',{name:'운용 한도 저장'})).toBeDisabled();
 fireEvent.change(capital,{target:{value:'250.00'}});
 expect(migrate).toBeDisabled();expect(capital).toHaveValue('250.00');
 expect(fetcher.mock.calls.some(call=>call[1]?.method==='POST')).toBe(false);
 fireEvent.click(screen.getByRole('button',{name:'저장된 설정 다시 불러오기'}));
 await waitFor(()=>expect(capital).toHaveValue('100.00'));
 fireEvent.click(migrate);
 await screen.findByText('저장된 설정을 새 운용 초안으로 전환했습니다. 실제 주문은 시작되지 않습니다.');
 const sent=fetcher.mock.calls.find(call=>call[1]?.method==='POST')!;
 expect(sent[0]).toBe('/api/v1/automatic/settings/migrate');
 expect(JSON.parse(String(sent[1]?.body))).toEqual({schema_version:1,revision:4});
 expect(capital).toHaveValue('100.00');
 expect(screen.getByText(/거래 대상·운용 전략·위험관리 기준/)).toBeVisible();
});

it.each(['scope','strategy','risk_v2'])('rejects unknown future %s values instead of exposing an erasing save',async section=>{
 vi.stubGlobal('fetch',vi.fn(()=>Promise.resolve(capable({...fixture,policy:{...fixture.policy,[section]:{future:true}}}))));
 render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('저장을 막았습니다');
});

it('keeps the V1 draft after migration conflict and does not retry automatically',async()=>{
 const fetcher=vi.fn((_url:string,options?:RequestInit)=>Promise.resolve(capable(v1,options?.method==='POST'?409:200)));
 vi.stubGlobal('fetch',fetcher);render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 await screen.findByLabelText('자동매매에 맡길 최대 자금 (USDT)');
 fireEvent.click(screen.getByRole('button',{name:'저장된 설정을 새 운용 초안으로 전환'}));
 await screen.findByText(/다른 곳에서 설정이 변경/);
 expect(screen.getByLabelText('자동매매에 맡길 최대 자금 (USDT)')).toHaveValue('100.00');
 expect(fetcher.mock.calls.filter(call=>call[1]?.method==='POST')).toHaveLength(1);
});

it('recovers an uncertain migration only by GET and never posts a new revision automatically',async()=>{
 let finishRead!:(response:Response)=>void;let reads=0;let signal:AbortSignal|null|undefined;
 const fetcher=vi.fn((_url:string,options?:RequestInit)=>{
  if(options?.method==='POST'){signal=options.signal;return new Promise<Response>(()=>undefined)}
  return ++reads===1?Promise.resolve(capable({...v1,revision:4})):new Promise<Response>(resolve=>{finishRead=resolve});
 });
 vi.stubGlobal('fetch',fetcher);render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 const capital=await screen.findByLabelText('자동매매에 맡길 최대 자금 (USDT)');
 vi.useFakeTimers();fireEvent.click(screen.getByRole('button',{name:'저장된 설정을 새 운용 초안으로 전환'}));
 await act(async()=>{await vi.advanceTimersByTimeAsync(15000)});
 expect(signal?.aborted).toBe(true);expect(capital).toBeDisabled();
 expect(screen.getByRole('button',{name:'저장된 설정을 새 운용 초안으로 전환'})).toBeDisabled();
 await act(async()=>{finishRead(capable({...fixture,revision:5,policy:{...fixture.policy,legacy_v1:v1.policy}}))});
 expect(capital).toHaveValue('100.00');expect(capital).toBeEnabled();
 expect(fetcher.mock.calls.filter(call=>call[1]?.method==='POST')).toHaveLength(1);
 expect(screen.queryByRole('button',{name:'저장된 설정을 새 운용 초안으로 전환'})).not.toBeInTheDocument();
});

// Independent fixture pins: SHA256 computed from the published catalog canonical JSON,
// not from the editor/parser under test.
const instrument=(symbol:string)=>({venue:'BYBIT',product:'LINEAR',symbol});
const reference=(strategy_id:string)=>({strategy_id,revision:'r1'});
const descriptor=(name:string,symbol:string)=>({reference:reference(name),implementation_sha256:'a'.repeat(64),config_sha256:'b'.repeat(64),config_schema_version:1,engine_contract_version:'engine1',permitted_instruments:[instrument(symbol)],approval_reference:'approval1',approval_source_reference:'source1',approval_source_sha256:'c'.repeat(64),approval_environments:['DEMO'],approval_status:'APPROVED'});
const typedPolicy=()=>({...fixture.policy,scope:{section_version:1,account:{account_id:'fixture-account',environment:'DEMO'},mode:'FULL_AUTO',allowed_products:['LINEAR','SPOT'],selected_product:'LINEAR',permission_profile_revision:'BYBIT_SPOT_LINEAR_NO_FUNDS_V1',allowed_instruments:[instrument('ETHUSDT'),instrument('BTCUSDT')],selected_instruments:[instrument('BTCUSDT'),instrument('ETHUSDT')]},strategy:{section_version:1,catalog:{schema_version:1,revision:'sha256:9db4c2f5ec0a80264ce31538d239f4dd5f98d81d79305a213836220b8938cc1f',entries:[descriptor('beta','ETHUSDT'),descriptor('alpha','BTCUSDT')]},allowed:[reference('beta'),reference('alpha')],selected:[reference('alpha'),reference('beta')]},risk_v2:null});
const typedSnapshot=()=>({...fixture,revision:7,policy:typedPolicy(),missing_fields:['risk_v2'],blockers:[...fixture.blockers,'STRATEGY_CURRENT_PUBLICATION_REQUIRED']});
const capable=(data:unknown,status=200)=>new Response(JSON.stringify(data),{status,headers:{'X-Automatic-Policy-Sections-Version':'1'}});

it('displays heterogeneous typed pins and preserves full global array orders on capable PUT',async()=>{
 const original=typedSnapshot();const fetcher=vi.fn((_url:string,options?:RequestInit)=>Promise.resolve(capable(options?.method==='PUT'?{...original,revision:8,policy:JSON.parse(String(options.body)).policy}:original)));
 vi.stubGlobal('fetch',fetcher);render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 expect(await screen.findByLabelText('계좌 ID')).toHaveValue('fixture-account');
 expect(await screen.findByLabelText('BTCUSDT에 적용 가능한 전략')).toHaveTextContent('alpha / r1');
 expect(screen.getByLabelText('BTCUSDT에 적용 가능한 전략')).not.toHaveTextContent('beta / r1');
 fireEvent.change(screen.getByLabelText('계좌 ID'),{target:{value:'explicit-account'}});
 await waitFor(()=>expect(screen.getByRole('button',{name:'운용 한도 저장'})).toBeEnabled());
 fireEvent.click(screen.getByRole('button',{name:'운용 한도 저장'}));await screen.findByText('설정을 저장했습니다. 실제 주문은 시작되지 않습니다.');
 const put=fetcher.mock.calls.filter(call=>call[1]?.method==='PUT');expect(put).toHaveLength(1);
 expect(JSON.parse(String(put[0][1]?.body))).toEqual({schema_version:2,revision:7,policy:{...original.policy,scope:{...original.policy.scope,account:{account_id:'explicit-account',environment:'DEMO'}}},expected_strategy_generation:null,policy_sections_version:1});
});

it.each([undefined,'2','1, 1'])('keeps a supported old-server body read-only when discovery is %s',async header=>{
 const fetcher=vi.fn(()=>Promise.resolve(new Response(JSON.stringify(fixture),{headers:header?{'X-Automatic-Policy-Sections-Version':header}:{}})));
 vi.stubGlobal('fetch',fetcher);render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 expect(await screen.findByText(/서버 업데이트가 필요/)).toBeVisible();expect(screen.getByRole('button',{name:'운용 한도 저장'})).toBeDisabled();
 expect(fetcher).toHaveBeenCalledTimes(1);
});

it('preserves conflicting financial edits until the explicit unsaved product reset and separate save',async()=>{
 const original=typedSnapshot();vi.stubGlobal('fetch',vi.fn(()=>Promise.resolve(capable(original))));render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 await screen.findByLabelText('선택 거래 상품');fireEvent.click(screen.getByRole('button',{name:'LINEAR 위험관리 입력 시작'}));
 fireEvent.change(screen.getByLabelText('새 초안 거래당 최대 손실 (USDT)'),{target:{value:'2.500'}});
 fireEvent.change(screen.getByLabelText('선택 거래 상품'),{target:{value:'SPOT'}});
 expect(screen.getByLabelText('새 초안 거래당 최대 손실 (USDT)')).toHaveValue('2.500');
 await waitFor(()=>expect(screen.getByRole('button',{name:'운용 한도 저장'})).toBeDisabled());
 fireEvent.click(screen.getByRole('button',{name:'거래 상품 변경에 맞춰 초안 비우기'}));
 expect(screen.getByText(/아직 저장하지 않은 초기화/)).toBeVisible();expect(screen.queryByLabelText('새 초안 거래당 최대 손실 (USDT)')).not.toBeInTheDocument();
 expect(screen.getByText(/SPOT 위험관리.*E16/)).toBeVisible();
 fireEvent.change(screen.getByLabelText('선택 거래 상품'),{target:{value:'LINEAR'}});
 expect(screen.queryByLabelText('새 초안 거래당 최대 손실 (USDT)')).not.toBeInTheDocument();
});

it('retains exact typed edits and hidden pins on 409 without fetching or retrying',async()=>{
 const original=typedSnapshot();const fetcher=vi.fn((_url:string,options?:RequestInit)=>Promise.resolve(capable(original,options?.method==='PUT'?409:200)));
 vi.stubGlobal('fetch',fetcher);render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 const account=await screen.findByLabelText('계좌 ID');fireEvent.change(account,{target:{value:'dirty-account'}});
 await waitFor(()=>expect(screen.getByRole('button',{name:'운용 한도 저장'})).toBeEnabled());fireEvent.click(screen.getByRole('button',{name:'운용 한도 저장'}));
 await screen.findByText(/다른 곳에서 설정이 변경/);expect(account).toHaveValue('dirty-account');expect(fetcher).toHaveBeenCalledTimes(2);
 expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body)).policy.strategy).toEqual(original.policy.strategy);
});

it('does not discard submitted typed edits when a headerless success reconciles to stale or conflicting GET',async()=>{
 const original=typedSnapshot();let reads=0;let resolveRead!:(response:Response)=>void;
 const fetcher=vi.fn((_url:string,options?:RequestInit)=>{
  if(options?.method==='PUT')return Promise.resolve(new Response(JSON.stringify({...original,revision:8})));
  return ++reads===1?Promise.resolve(capable(original)):new Promise<Response>(resolve=>{resolveRead=resolve});
 });
 vi.stubGlobal('fetch',fetcher);render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 const account=await screen.findByLabelText('계좌 ID');fireEvent.change(account,{target:{value:'submitted-account'}});
 await waitFor(()=>expect(screen.getByRole('button',{name:'운용 한도 저장'})).toBeEnabled());fireEvent.click(screen.getByRole('button',{name:'운용 한도 저장'}));
 await screen.findByText(/저장 결과를 확인하지 못했습니다/);expect(account).toHaveValue('submitted-account');
 await act(async()=>resolveRead(capable({...original,revision:8})));
 expect(account).toHaveValue('submitted-account');expect(screen.getByRole('button',{name:'운용 한도 저장'})).toBeDisabled();
 expect(screen.queryByText('설정을 저장했습니다. 실제 주문은 시작되지 않습니다.')).not.toBeInTheDocument();
 expect(fetcher.mock.calls.filter(call=>call[1]?.method==='PUT')).toHaveLength(1);
});

it('names effective-universe orphans while retaining unused valid global refs',async()=>{
 const original=typedSnapshot();original.policy.scope.allowed_instruments.push(instrument('SOLUSDT'));original.policy.scope.selected_instruments.push(instrument('SOLUSDT'));original.missing_fields.push('strategy.eligible_pairs');
 vi.stubGlobal('fetch',vi.fn(()=>Promise.resolve(capable(original))));render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 expect(await screen.findByText(/연결된 전략이 없는 거래 대상: BYBIT\/LINEAR\/SOLUSDT/)).toBeVisible();
 await waitFor(()=>expect(screen.getByRole('button',{name:'운용 한도 저장'})).toBeEnabled());
 expect(screen.getByLabelText('허용 전략 beta / r1')).toBeChecked();expect(screen.getByLabelText('허용 전략 alpha / r1')).toBeChecked();
});

it.each(['descriptor','revision','global-unknown','global-revoked','environment','future','extra','missing','float','zero','leverage','mixed-product'])('blocks malformed typed %s globally before exposing editable pairs',async fault=>{
 const original=typedSnapshot();const body=JSON.parse(JSON.stringify(original));
 if(fault==='descriptor')body.policy.strategy.catalog.entries[0].config_sha256='d'.repeat(64);
 if(fault==='revision')body.policy.strategy.catalog.revision='sha256:'+'0'.repeat(64);
 if(fault==='global-unknown')body.policy.strategy.allowed.push(reference('unknown'));
 if(fault==='global-revoked')body.policy.strategy.catalog.entries[0].approval_status='REVOKED';
 if(fault==='environment')body.policy.scope.account.environment='LIVE';
 if(fault==='future')body.policy.scope.section_version=2;
 if(fault==='extra')body.policy.scope.extra=null;
 if(fault==='missing')delete body.policy.scope.account;
 if(fault==='float')body.policy.scope.section_version=1.5;
 if(fault==='zero')body.policy.legacy_v1.capital_usdt='0';
 if(fault==='leverage'){body.policy.legacy_v1.leverage_min='9007199254740992.00000001';body.policy.legacy_v1.leverage_max='9007199254740992.00000000'}
 if(fault==='mixed-product')body.policy.scope.allowed_instruments[0].product='SPOT';
 vi.stubGlobal('fetch',vi.fn(()=>Promise.resolve(capable(body))));render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('저장을 막았습니다');expect(screen.queryByRole('button',{name:'운용 한도 저장'})).not.toBeInTheDocument();
});

const completeRisk=()=>({section_version:1,product:'LINEAR',sizing_rule:'LOSS_FIXED',caps:{max_order_admission_notional:'100.00',max_account_admission_notional:'200',valuation_rule:'LINEAR_MARK_GROSS_AT_ADMISSION_V1',turnover_requirement:'NOT_REQUIRED',max_execution_quote_amount:null},leverage_min:'9007199254740992.00000000',leverage_max:'9007199254740992.00000001',per_trade_loss:'1.2300',max_daily_loss:'2',max_drawdown:'3',max_total_risk:'4',max_total_margin:'5',margin_allocation:'6',liquidation_buffer_fraction:'0.010',max_quote_age_seconds:'0',max_evidence_age_seconds:'0.00',max_consecutive_losses:2,max_positions:2,margin_mode:'CROSS',position_mode:'ONE_WAY'});
const completeSnapshot=()=>({...typedSnapshot(),policy:{...typedPolicy(),risk_v2:completeRisk()},missing_fields:[] as string[]});

it('parses complete explicit LINEAR decimals without floating point conversion and derives genuine heterogeneous pairs',async()=>{
 const original=completeSnapshot();const parsed=await parseAutomaticSettings(JSON.stringify(original));expect(parsed).toEqual(original);
 if(!parsed||parsed.schema_version!==2)throw new Error('Expected supported snapshot');
 expect(automaticPolicyPairs(parsed.policy)).toEqual([{instrument:instrument('BTCUSDT'),allowed:[reference('alpha')],selected:[reference('alpha')],active:[reference('alpha')]},{instrument:instrument('ETHUSDT'),allowed:[reference('beta')],selected:[reference('beta')],active:[reference('beta')]}]);
 expect(automaticPolicyMissing(parsed.policy)).toEqual([]);
});

it('uses ALL_IN_ONE allowed universe while preserving independently ordered optional selected refs',async()=>{
 const original=completeSnapshot();original.policy.scope.mode='ALL_IN_ONE';original.policy.scope.selected_instruments=[];original.policy.strategy.selected=[];
 const parsed=await parseAutomaticSettings(JSON.stringify(original));expect(parsed).toEqual(original);
 if(!parsed||parsed.schema_version!==2)throw new Error('Expected supported snapshot');
 expect(automaticPolicyPairs(parsed.policy).map(pair=>[pair.instrument.symbol,pair.active])).toEqual([['ETHUSDT',[reference('beta')]],['BTCUSDT',[reference('alpha')]]]);
});

it.each(['duplicate-root','duplicate-nested','fractional-version','exponent-version','bool-version','unsafe-revision','extra-root','extra-section','missing-cap','future-risk','float-amount','exponent-amount','too-precise','zero-positive','negative-zero','count-overflow','buffer-one','reversed-leverage','unsupported-HEDGE','not-required-amount','missing-publication-blocker','wrong-missing-fields','oversized'])('refuses closed raw or typed %s without erasing pins',async fault=>{
 const body=completeSnapshot();let raw=JSON.stringify(body);
 if(fault==='duplicate-root')raw=raw.replace('"schema_version":2','"schema_version":2,"schema_version":2');
 if(fault==='duplicate-nested')raw=raw.replace('"section_version":1','"section_version":1,"section_version":1');
 if(fault==='fractional-version')raw=raw.replace('"section_version":1','"section_version":1.0');
 if(fault==='exponent-version')raw=raw.replace('"section_version":1','"section_version":1e0');
 if(fault==='bool-version')raw=raw.replace('"section_version":1','"section_version":true');
 if(fault==='unsafe-revision')raw=raw.replace('"revision":7','"revision":9007199254740992');
 if(fault==='extra-root')raw=raw.slice(0,-1)+',"extra":null}';
 if(fault==='extra-section')raw=raw.replace('"mode":"FULL_AUTO"','"mode":"FULL_AUTO","extra":null');
 if(fault==='missing-cap')raw=raw.replace(',"max_execution_quote_amount":null','');
 if(fault==='future-risk')raw=raw.replace('"product":"LINEAR","sizing_rule"','"product":"SPOT","sizing_rule"');
 if(fault==='float-amount')raw=raw.replace('"per_trade_loss":"1.2300"','"per_trade_loss":1.23');
 if(fault==='exponent-amount')raw=raw.replace('"per_trade_loss":"1.2300"','"per_trade_loss":"1e2"');
 if(fault==='too-precise')raw=raw.replace('"per_trade_loss":"1.2300"','"per_trade_loss":"1.123456789"');
 if(fault==='zero-positive')raw=raw.replace('"per_trade_loss":"1.2300"','"per_trade_loss":"0.00"');
 if(fault==='negative-zero')raw=raw.replace('"max_positions":2','"max_positions":-0');
 if(fault==='count-overflow')raw=raw.replace('"max_positions":2','"max_positions":1000001');
 if(fault==='buffer-one')raw=raw.replace('"liquidation_buffer_fraction":"0.010"','"liquidation_buffer_fraction":"1.000"');
 if(fault==='reversed-leverage')raw=raw.replace('"leverage_min":"9007199254740992.00000000"','"leverage_min":"9007199254740992.00000002"');
 if(fault==='unsupported-HEDGE')raw=raw.replace('"position_mode":"ONE_WAY"','"position_mode":"HEDGE"');
 if(fault==='not-required-amount')raw=raw.replace('"max_execution_quote_amount":null','"max_execution_quote_amount":"1"');
 if(fault==='missing-publication-blocker')raw=raw.replace(',"STRATEGY_CURRENT_PUBLICATION_REQUIRED"','');
 if(fault==='wrong-missing-fields')raw=raw.replace('"missing_fields":[]','"missing_fields":["scope.future"]');
 if(fault==='oversized')raw=' '.repeat(131073)+raw;
 expect(await parseAutomaticSettings(raw)).toBeNull();
});

it('preserves a savable HARD-null amount partial and explicit SPOT-null policy without inventing futures values',async()=>{
 const hard=completeSnapshot();hard.policy.risk_v2.caps.turnover_requirement='HARD_EXECUTION_QUOTE_CAP';hard.missing_fields=['risk_v2.caps.max_execution_quote_amount'];expect(await parseAutomaticSettings(JSON.stringify(hard))).toEqual(hard);
 const spot={...typedSnapshot(),policy:{...typedPolicy(),scope:{...typedPolicy().scope,selected_product:'SPOT',allowed_instruments:null,selected_instruments:null},strategy:null,risk_v2:null},missing_fields:['risk_v2','scope.allowed_instruments','scope.selected_instruments','strategy'],blockers:[...fixture.blockers,'POLICY_SPOT_RISK_UNSUPPORTED']};
 expect(await parseAutomaticSettings(JSON.stringify(spot))).toEqual(spot);
});

it('does not seed account, financials, strategy or turnover choices when making a blank typed partial',async()=>{
 vi.stubGlobal('fetch',vi.fn(()=>Promise.resolve(capable(fixture))));render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 expect(await screen.findByLabelText('계좌 ID')).toHaveValue('');expect(screen.getByLabelText('계좌 환경')).toHaveValue('');expect(screen.getByLabelText('운용 모드')).toHaveValue('');expect(screen.getByLabelText('선택 거래 상품')).toHaveValue('');
 fireEvent.click(screen.getByRole('button',{name:'LINEAR 위험관리 입력 시작'}));expect(screen.getByLabelText('실제 체결 금액 상한 선택')).toHaveValue('');expect(screen.getByLabelText('새 초안 최소 레버리지')).toHaveValue('');
 expect(await validateAutomaticPolicy({...fixture.policy,risk_v2:{...completeRisk(),leverage_min:'0'}})).toBeNull();
});

it.each(['revoked','live'])('checks rehashed globally unused %s metadata before deriving the requested covered key',async fault=>{
 const body=completeSnapshot();body.policy.scope.selected_instruments=[instrument('BTCUSDT')];body.policy.strategy.selected=[reference('alpha')];
 const beta=body.policy.strategy.catalog.entries[0];
 if(fault==='revoked'){beta.approval_status='REVOKED';body.policy.strategy.catalog.revision='sha256:adc2419a13c84b16d065c56820083e7169b35b44cfb7962ac76397f44953f3c3'}
 else{beta.approval_environments=['LIVE'];body.policy.strategy.catalog.revision='sha256:3624600df9d454207a2f7b2dfbab38767a073299bf6ab934b39d1f612159b5cc'}
 expect(await parseAutomaticSettings(JSON.stringify(body))).toBeNull();
 body.policy.strategy.allowed=[reference('alpha')];
 expect(await parseAutomaticSettings(JSON.stringify(body))).toEqual(body);
});

it('keeps typed edits through missing-capability GET and releases uncertainty only after a capable matching GET',async()=>{
 const original=typedSnapshot();let reads=0;let submitted=original.policy;let resolveRead!:(response:Response)=>void;
 const fetcher=vi.fn((_url:string,options?:RequestInit)=>{
  if(options?.method==='PUT'){submitted=JSON.parse(String(options.body)).policy;return Promise.resolve(new Response(JSON.stringify({...original,revision:8,policy:submitted})))}
  return ++reads===1?Promise.resolve(capable(original)):new Promise<Response>(resolve=>{resolveRead=resolve});
 });
 vi.stubGlobal('fetch',fetcher);render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 const account=await screen.findByLabelText('계좌 ID');fireEvent.change(account,{target:{value:'submitted-typed'}});fireEvent.click(screen.getByRole('button',{name:'운용 한도 저장'}));
 await screen.findByText(/저장 결과를 확인하지 못했습니다/);
 await act(async()=>resolveRead(new Response(JSON.stringify({...original,revision:8,policy:submitted}))));
 expect(account).toHaveValue('submitted-typed');expect(screen.getByRole('button',{name:'운용 한도 저장'})).toBeDisabled();expect(fetcher.mock.calls.filter(call=>call[1]?.method==='PUT')).toHaveLength(1);
 await waitFor(()=>expect(screen.getByRole('button',{name:'저장 결과 다시 확인하기'})).toBeEnabled());
 fireEvent.click(screen.getByRole('button',{name:'저장 결과 다시 확인하기'}));await waitFor(()=>expect(reads).toBe(3));
 await act(async()=>resolveRead(capable({...original,revision:8,policy:submitted})));
 expect(account).toHaveValue('submitted-typed');expect(screen.getByRole('button',{name:'운용 한도 저장'})).toBeEnabled();expect(screen.getByText('설정을 저장했습니다. 실제 주문은 시작되지 않습니다.')).toBeVisible();
 expect(submitted.strategy).toEqual(original.policy.strategy);expect(fetcher.mock.calls.filter(call=>call[1]?.method==='PUT')).toHaveLength(1);
});

it('blocks migration for an otherwise supported V1 snapshot without discovery',async()=>{
 const fetcher=vi.fn(()=>Promise.resolve(new Response(JSON.stringify(v1))));vi.stubGlobal('fetch',fetcher);render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 expect(await screen.findByRole('button',{name:'저장된 설정을 새 운용 초안으로 전환'})).toBeDisabled();expect(fetcher).toHaveBeenCalledTimes(1);
});

it('aborts a pending GET without publishing a stale response after collapse',async()=>{
 let signal:AbortSignal|null|undefined;let resolveRead!:(response:Response)=>void;
 const fetcher=vi.fn((_url:string,options?:RequestInit)=>{signal=options?.signal;return new Promise<Response>(resolve=>{resolveRead=resolve})});
 vi.stubGlobal('fetch',fetcher);render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));fireEvent.click(screen.getByRole('button',{name:'운용 한도 접기'}));expect(signal?.aborted).toBe(true);
 await act(async()=>resolveRead(capable(typedSnapshot())));expect(screen.queryByLabelText('계좌 ID')).not.toBeInTheDocument();expect(fetcher).toHaveBeenCalledTimes(1);
});

it('holds coverage counts pending until the entire edited catalog and global environment are validated',async()=>{
 vi.stubGlobal('fetch',vi.fn(()=>Promise.resolve(capable(typedSnapshot()))));render(<AutomaticSettings/>);fireEvent.click(screen.getByRole('button',{name:'운용 한도 설정 열기'}));
 await screen.findByLabelText('BTCUSDT에 적용 가능한 전략');
 vi.spyOn(crypto.subtle,'digest').mockImplementation(()=>new Promise<ArrayBuffer>(()=>undefined));
 fireEvent.change(screen.getByLabelText('계좌 환경'),{target:{value:'LIVE'}});
 expect(screen.getByText(/저장된 설정 중 미정 항목:/)).toHaveTextContent('현재 초안 미정 항목: 확인 중개');
 expect(screen.queryByLabelText('BTCUSDT에 적용 가능한 전략')).not.toBeInTheDocument();
});
