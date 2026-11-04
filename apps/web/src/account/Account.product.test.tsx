import {act,cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import Account,{isAccountView} from './Account';
import {ACCOUNT_FIXTURE} from './fixture';

function linear(){return {...structuredClone(ACCOUNT_FIXTURE),환경:'연습 계좌',환경코드:'demo',거래상품:'LINEAR',
  메시지:'계좌 조회',수신시각:new Date().toISOString(),수집완료시각:new Date().toISOString()};}
function spot(){return {...linear(),거래상품:'SPOT',최대레버리지:null,포지션:[],미체결주문:[{
  구분:'외부에서 연 주문',관리함:false,종목:'ETHUSDC',방향:'매수',유형:'지정가',수량:'0.25',가격:'2000.5',상태:'미체결',줄이기만:'현물에는 적용 안 함',
}]};}
function response(body:unknown){return {ok:true,json:async()=>body};}
afterEach(()=>{cleanup();vi.unstubAllGlobals();});

it('keeps product selection unavailable when the account cannot be read',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>({ok:false,json:async()=>null})));
 render(<Account polished pollIntervalMs={60000}/>);
 expect(await screen.findByRole('alert')).toHaveTextContent('서버에 연결하지 못했습니다');
 expect(screen.queryByRole('button',{name:/^현물$/})).not.toBeInTheDocument();
});

it('restores product selection after an account read recovers',async()=>{
 let calls=0;
 vi.stubGlobal('fetch',vi.fn(async()=>++calls===1?{ok:false,json:async()=>null}:response(linear())));
 render(<Account polished pollIntervalMs={60000}/>);
 await screen.findByRole('alert');
 const refresh=screen.getAllByRole('button',{name:'새로고침'})[0];
 await waitFor(()=>expect(refresh).toBeEnabled());
 fireEvent.click(refresh);
 expect(await screen.findByRole('button',{name:/^현물$/})).toBeInTheDocument();
});

it('sends explicit SPOT selection to the DEMO read route and shows no derivative position or USDT price assumption',async()=>{
 const fetch=vi.fn(async(url:unknown)=>response(String(url).includes('product=SPOT')?spot():linear()));
 vi.stubGlobal('fetch',fetch);render(<Account polished pollIntervalMs={60000}/>);
 await screen.findAllByText('BTC / USDT');
 fireEvent.click(screen.getByRole('button',{name:/^현물$/}));
 const symbol=await screen.findByText('ETHUSDC');
 expect(fetch.mock.calls.some(([url])=>String(url)==='/api/v1/account/bybit/demo/view?product=SPOT')).toBe(true);
 expect(screen.queryByText('BTC / USDT')).not.toBeInTheDocument();
 const order=symbol.closest('article');expect(order).not.toBeNull();
 expect(within(order!).getByText('2,000.50')).toBeInTheDocument();
 expect(order!.textContent).not.toContain('USDT');
 expect(screen.getAllByText(/통합 계좌 잔고.*프로그램이 매수한 수량/).length).toBeGreaterThan(0);
});

it('refuses a legacy LINEAR response after SPOT was selected',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>response(linear())));
 render(<Account polished pollIntervalMs={60000}/>);await screen.findAllByText('BTC / USDT');
 fireEvent.click(screen.getByRole('button',{name:/^현물$/}));
 expect(await screen.findByRole('alert')).toHaveTextContent('서버 응답 형식');
 expect(screen.queryByText('BTC / USDT')).not.toBeInTheDocument();
});

it('does not publish a late derivative response into the new SPOT selection',async()=>{
 let resolveLinear:(value:ReturnType<typeof response>)=>void=()=>undefined;
 let linearCount=0;
 const old=new Promise<ReturnType<typeof response>>(resolve=>{resolveLinear=resolve;});
 const fetch=vi.fn((url:unknown)=>{
  if(String(url).includes('product=SPOT')) return Promise.resolve(response(spot()));
  linearCount+=1;
  return linearCount===1?Promise.resolve(response(linear())):old;
 });
 vi.stubGlobal('fetch',fetch);render(<Account polished pollIntervalMs={60000}/>);
 await screen.findAllByText('BTC / USDT');
 fireEvent.click(screen.getAllByRole('button',{name:'새로고침'})[0]);
 await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(2));
 fireEvent.click(screen.getByRole('button',{name:/^현물$/}));await screen.findByText('ETHUSDC');
 await act(async()=>{resolveLinear(response(linear()));await old;});
 expect(screen.getByText('ETHUSDC')).toBeInTheDocument();expect(screen.queryByText('BTC / USDT')).not.toBeInTheDocument();
});

it('rejects derivatives embedded in a SPOT DTO without treating the wallet as owned execution inventory',()=>{
 expect(isAccountView(spot())).toBe(true);
 expect(isAccountView({...spot(),포지션:linear().포지션})).toBe(false);
 expect(isAccountView({...spot(),최대레버리지:{종목:'BTCUSDT',최대:'10',최소:'1',간격:'1'}})).toBe(false);
});


it.each([
 ['BTCUSDT','quoteCoin','100 (결제자산 단위)'],
 ['BTCUSDT','baseCoin','100 (기준자산 단위)'],
 ['BTCUSDT',undefined,'100 (단위 확인 불가)'],
 ['ETHUSDC','quoteCoin','100 (결제자산 단위)'],
] as const)('keeps the original spot qty basis for %s/%s without appending a guessed coin',async(symbol,basis,shown)=>{
 const original=spot();
 const selected={...original,미체결주문:[{...original.미체결주문[0],종목:symbol,수량:'100',수량기준:basis}]};
 vi.stubGlobal('fetch',vi.fn(async(url:unknown)=>response(String(url).includes('product=SPOT')?selected:linear())));
 render(<Account polished pollIntervalMs={60000}/>);await screen.findAllByText('BTC / USDT');
 fireEvent.click(screen.getByRole('button',{name:/^현물$/}));
 expect(await screen.findByText(shown)).toBeInTheDocument();
 expect(screen.queryByText('100 BTC')).not.toBeInTheDocument();
});
