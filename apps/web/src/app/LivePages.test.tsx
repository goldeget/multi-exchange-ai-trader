import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {ConnectionsPage,DiagnosticsPage,OverviewPage, useCapabilities} from './LivePages';
vi.mock('../chart/CandleChart',()=>({default:()=>null}));

afterEach(()=>{cleanup();vi.unstubAllGlobals()});
const response=(value:unknown)=>Promise.resolve(new Response(JSON.stringify(value),{status:200}));

it('does not describe unknown automation as a confirmed empty list',async()=>{
 vi.stubGlobal('fetch',vi.fn(()=>Promise.resolve(new Response('{}',{status:503}))));
 render(<OverviewPage route={{page:'overview',demo:false,id:''}}/>);
 expect(screen.queryByText('저장된 실행 없음')).not.toBeInTheDocument();
 await screen.findByText('자동실행 상태를 확인하지 못했습니다.');
 expect(screen.queryByText('저장된 자동실행이 없습니다.')).not.toBeInTheDocument();
});

it('discards live budget values when entering the fixture and makes no fixture request',async()=>{
 const fetcher=vi.fn((url:string)=>response(url.endsWith('/connections/status')?
  {schema_version:1,bybit:{registered:false,status:'NOT_CONFIGURED'},ai:{registered:false,status:'NOT_CONFIGURED',mode:'OFFLINE_TEST',external_calls:false}}:
  url.endsWith('/settings/ai')?{revision:0,monthly_limit:'2.00',mode:'OFFLINE_TEST',llm_enabled:false}:
  {currency:'USD',monthly_limit:'2.00',used:'0',reserved:'0',uncertain:'0',remaining:'2',provider_balance:null}));
 vi.stubGlobal('fetch',fetcher);
 const view=render(<ConnectionsPage/>);
 await waitFor(()=>expect(screen.getByRole('textbox',{name:/월 내부 예산 한도/})).toHaveValue('2.00'));
 const calls=fetcher.mock.calls.length;
 view.rerender(<ConnectionsPage demo/>);
 expect(screen.getByRole('textbox',{name:/월 내부 예산 한도/})).toHaveValue('8.00');
 await act(async()=>{});
 expect(fetcher).toHaveBeenCalledTimes(calls);
});

it('ignores a live response which arrives after switching to the fixture',async()=>{
 let finish!:(value:Response)=>void;
 const promise=new Promise<Response>(resolve=>{finish=resolve});
 vi.stubGlobal('fetch',vi.fn(()=>promise));
 const view=render(<ConnectionsPage/>);
 view.rerender(<ConnectionsPage demo/>);
 await act(async()=>{finish(new Response(JSON.stringify({revision:0,monthly_limit:'99',mode:'OFFLINE_TEST',llm_enabled:false})));await promise});
 expect(screen.getByRole('textbox',{name:/월 내부 예산 한도/})).toHaveValue('8.00');
});

it('refuses incomplete capability schemas before exposing execution controls',async()=>{
 vi.stubGlobal('fetch',vi.fn(()=>response({schema_version:1,live_order_enabled:false,manual_paper:{available:true},automation:{entry_pause:true}})));
 function Probe(){const value=useCapabilities();return <p>{value.failed?'차단':value.data?'허용':'조회 중'}</p>}
 render(<Probe/>);
 await screen.findByText('차단');
 expect(screen.queryByText('허용')).not.toBeInTheDocument();
});

it('selects independent spot and perpetual ledgers even when their scenario names match',async()=>{
 const base={scenario_id:'manual',equity:'10000.00000000',unrealized_pnl:'0',positions:[]};
 const overview={schema_version:1,ledgers:[{id:'manual',source:'SYNTHETIC',execution:'PAPER',product:'SYNTHETIC_PERPETUAL',state:base},
  {id:'manual',source:'SYNTHETIC',execution:'PAPER',product:'SPOT',state:{...base,equity:'9000.00000000'}}],automation:{runs:[],scheduler:{state:'READY',message:'감시 중'}},errors:[]};
 vi.stubGlobal('fetch',vi.fn((url:string)=>url.endsWith('/overview')?response(overview):Promise.resolve(new Response('{}',{status:503}))));
 render(<OverviewPage route={{page:'overview',demo:false,id:''}}/>);
 await screen.findByText('10,000.00 USDT');
 fireEvent.change(screen.getByRole('combobox',{name:'현황 장부'}),{target:{value:'SPOT:manual'}});
 expect(screen.getByText('9,000.00 USDT')).toBeInTheDocument();
 expect(screen.queryByText('19,000.00 USDT')).not.toBeInTheDocument();
});

it('shows unavailable diagnostics for a malformed component instead of inventing a healthy state',async()=>{
 vi.stubGlobal('fetch',vi.fn(()=>response({schema_version:1,components:{automation:{state:'READY'},manual_paper:{state:'READABLE',unresolved_confirmations:0},ai:{mode:'OFFLINE_TEST',external_calls:false}},errors:[]})));
 render(<DiagnosticsPage route={{page:'diagnostics',demo:false,id:''}}/>);
 await screen.findByText('진단 정보를 확인하지 못했습니다.');
 expect(screen.queryByText('작업 감시 상태 확인')).not.toBeInTheDocument();
});
