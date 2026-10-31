// Offline UI contract fixtures, not evidence of native fills or real returns.
import {cleanup,fireEvent,render,screen,waitFor} from "@testing-library/react";
import {afterEach,beforeEach,expect,it,vi} from "vitest";
const base={scenario_id:"fixture",mode:"PAPER",synthetic:true,wallet_balance:"10000.00000000",available_balance:"10000.00000000",reserved_margin:"0.00000000",equity:"10000.00000000",closed_net_pnl:"0.00000000",unrealized_pnl:"0.00000000",fees:"0.00000000",deficit:"0.00000000",latest_sequence:0,marks:{"BTC-PAPER":"60000.0","ETH-PAPER":"3000.00"},positions:[],fills:[],orders:[]};
const fetchMock=vi.fn();const ok=(data:unknown)=>({ok:true,json:async()=>data});
beforeEach(()=>{vi.stubGlobal("fetch",fetchMock);fetchMock.mockReset();localStorage.clear();});afterEach(()=>{cleanup();localStorage.clear();vi.unstubAllGlobals();});
async function show(){const {default:Paper}=await import("./Paper");render(<Paper/>);}
it("always labels virtual prices/funds and creates a separately identified scenario",async()=>{
 fetchMock.mockImplementation(async(_url:string,opts?:RequestInit)=>ok(opts?.method==="POST"?base:String(_url).endsWith("/scenarios")?{scenarios:[]}:String(_url).endsWith("/audit")?{events:[],orders:[],fills:[],settlements:[]}:base));
 await show();expect(screen.getByText("가상 시세 · 실거래소 시세 아님")).toBeVisible();
 await waitFor(()=>expect(screen.getByRole("button",{name:"가상 시나리오 생성"})).toBeEnabled());fireEvent.click(screen.getByRole("button",{name:"가상 시나리오 생성"}));
 await waitFor(()=>expect(screen.getAllByText("10000.00000000").length).toBeGreaterThan(0));
 expect(screen.getByRole("combobox",{name:"가상 상품"})).toBeVisible();
});
it.each(["network", "committed-5xx", "invalid-json"])("restores and retries uncertain partial close (%s) with the same key",async(fault)=>{
 const position={instrument:"BTC-PAPER",side:"long",quantity:"0.100",entry_price:"60000.1",leverage:10,reserved_margin:"600.00100000",unrealized_pnl:"0.00000000",mark:"60000.0"};let state={...base,positions:[position]};const keys:string[]=[];
 fetchMock.mockImplementation(async(url:string,opts?:RequestInit)=>{
  if(opts?.method==="POST"){const body=JSON.parse(String(opts.body));keys.push(body.idempotency_key);state={...state,positions:[{...position,quantity:"0.060"}]};if(keys.length===1){if(fault==="network")throw new TypeError("Failed to fetch");if(fault==="invalid-json")return {ok:true,json:async()=>{throw new SyntaxError("truncated response")}};return {ok:false,status:502,json:async()=>({message:"gateway failure after commit"})};}return ok(state);}
  return ok(url.endsWith("/scenarios")?{scenarios:["fixture"]}:url.endsWith("/audit")?{events:[],orders:[],fills:[],settlements:[]}:state);
 });
 await show();await screen.findByLabelText("부분청산 수량");fireEvent.change(screen.getByLabelText("부분청산 수량"),{target:{value:"0.040"}});fireEvent.click(screen.getByRole("button",{name:"가상 부분청산"}));
 fireEvent.click(screen.getByRole("button",{name:"확인 · 가상 실행"}));await screen.findByRole("button",{name:"같은 키로 재시도"});expect(screen.getByRole("button",{name:"돌아가기"})).toBeDisabled();cleanup();await show();await screen.findByRole("button",{name:"같은 키로 재시도"});fireEvent.click(screen.getByRole("button",{name:"같은 키로 재시도"}));
 await waitFor(()=>expect(keys).toHaveLength(2));expect(keys[0]).toBe(keys[1]);await screen.findByText("0.060");
});
it("rejects non-decimal and unsupported leverage locally without pretending to place an order",async()=>{
 fetchMock.mockImplementation(async(url:string)=>ok(url.endsWith("/scenarios")?{scenarios:["fixture"]}:url.endsWith("/audit")?{events:[],orders:[],fills:[],settlements:[]}:base));await show();await screen.findByLabelText("진입 수량");fireEvent.change(screen.getByLabelText("진입 수량"),{target:{value:"NaN"}});fireEvent.click(screen.getByRole("button",{name:"가상 LONG 진입"}));expect(screen.getByRole("alert")).toHaveTextContent(/소수|수량/);expect(screen.queryByRole("dialog")).not.toBeInTheDocument();expect(fetchMock.mock.calls.every(([,opts])=>opts?.method!=="POST")).toBe(true);
});
it("requires a confirmation and blocks repeated confirmation while pending",async()=>{
 let finish:(value:unknown)=>void=()=>{};let sent=0;fetchMock.mockImplementation(async(url:string,opts?:RequestInit)=>{if(opts?.method==="POST"){sent++;return await new Promise(resolve=>{finish=resolve;});}return ok(url.endsWith("/scenarios")?{scenarios:["fixture"]}:url.endsWith("/audit")?{events:[],orders:[],fills:[],settlements:[]}:base);});
 await show();await screen.findByLabelText("진입 수량");fireEvent.click(screen.getByRole("button",{name:"가상 LONG 진입"}));expect(sent).toBe(0);const confirm=screen.getByRole("button",{name:"확인 · 가상 실행"});fireEvent.click(confirm);fireEvent.click(confirm);expect(sent).toBe(1);expect(confirm).toBeDisabled();finish(ok(base));await waitFor(()=>expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

it("restores an uncertain create after reload with the original key and payload",async()=>{
 const sent:{idempotency_key:string;command:unknown}[]=[];fetchMock.mockImplementation(async(url:string,opts?:RequestInit)=>{if(opts?.method==="POST"){sent.push(JSON.parse(String(opts.body)));if(sent.length===1)throw new TypeError("Failed to fetch");return ok(base);}return ok(url.endsWith("/scenarios")?{scenarios:[]}:url.endsWith("/audit")?{}:base);});
 await show();await waitFor(()=>expect(screen.getByRole("button",{name:"가상 시나리오 생성"})).toBeEnabled());fireEvent.click(screen.getByRole("button",{name:"가상 시나리오 생성"}));await screen.findByRole("button",{name:"같은 키로 재시도"});cleanup();await show();await screen.findByRole("button",{name:"같은 키로 재시도"});fireEvent.click(screen.getByRole("button",{name:"같은 키로 재시도"}));await waitFor(()=>expect(sent).toHaveLength(2));expect(sent[1]).toEqual(sent[0]);
});
