export type Capabilities = {
 schema_version:1; live_order_enabled:false;
 manual_paper:{available:boolean;synthetic_perpetual:{enter:boolean;close:boolean;protect:boolean;order_preview:boolean};spot_limit:{supported:boolean;reason?:string};spot_market:{supported:boolean}};
 automation:{entry_pause:boolean;editable_leverage:boolean;fixed_leverage:number|null;available:boolean};
 evaluation:{editable_config:boolean;available:boolean};
};
export type Ledger={id:string;product?:string;source:'SYNTHETIC';execution:'PAPER';state:{scenario_id:string;equity:string;unrealized_pnl:string;positions:unknown[]}};
export type Run={run_id:string;status:string;native:{equity:string;closed_net_pnl:string;positions:unknown[]};config:Record<string,string|number>};
export type Overview={schema_version:1;ledgers:Ledger[];automation:{runs:Run[];scheduler?:{state:string;message:string}};errors:Array<{component:string;code:string}>};
export type DemoConnection={registered:boolean;status:string;order_submission_possible:boolean;blocked_by_unresolved_attempt:boolean};
export type Connection={schema_version:1;bybit:{registered:boolean;status:string};bybit_demo?:DemoConnection;ai:{registered:boolean;status:string;mode:string;external_calls:false}};
export type Settings={revision:number;monthly_limit:string;mode:'OFFLINE_TEST';llm_enabled:false};
export type Budget={currency:'USD';monthly_limit:string;used:string;reserved:string;uncertain:string;remaining:string;provider_balance:string|null};
export type Diagnostics={schema_version:1;components:{server:{state:string};automation:{state:string;message?:string;last_progress_at?:string};manual_paper:{state:string;unresolved_confirmations:number|null};account:{status:string};ai:{mode:string;external_calls:boolean};bybit_demo?:{uncertain_count:number|null;close_failed_count:number|null}};errors:Array<{component:string;code:string}>};
export function record(v:unknown):v is Record<string,unknown>{return !!v&&typeof v==='object'&&!Array.isArray(v)}
export const decimal=(v:unknown):v is string=>typeof v==='string'&&/^-?\d+(?:\.\d+)?$/.test(v);
const booleans=(v:unknown,fields:string[])=>record(v)&&fields.every(k=>typeof v[k]==='boolean');
const counts=(v:unknown,fields:string[])=>record(v)&&fields.every(k=>v[k]===null||(typeof v[k]==='number'&&Number.isInteger(v[k])&&v[k]>=0));
const errors=(v:unknown)=>Array.isArray(v)&&v.every(e=>record(e)&&typeof e.component==='string'&&typeof e.code==='string');
export const validCapabilities=(v:unknown):v is Capabilities=>record(v)&&v.schema_version===1&&v.live_order_enabled===false&&
 record(v.manual_paper)&&typeof v.manual_paper.available==='boolean'&&booleans(v.manual_paper.synthetic_perpetual,['enter','close','protect','order_preview'])&&
 booleans(v.manual_paper.spot_limit,['supported'])&&booleans(v.manual_paper.spot_market,['supported'])&&
 booleans(v.automation,['entry_pause','editable_leverage','available'])&&record(v.automation)&&
 (v.automation.fixed_leverage===null||Number.isInteger(v.automation.fixed_leverage))&&booleans(v.evaluation,['editable_config','available']);
export const validOverview=(v:unknown):v is Overview=>record(v)&&v.schema_version===1&&Array.isArray(v.ledgers)&&
 v.ledgers.every(x=>record(x)&&typeof x.id==='string'&&x.source==='SYNTHETIC'&&x.execution==='PAPER'&&record(x.state)&&
 typeof x.state.scenario_id==='string'&&decimal(x.state.equity)&&decimal(x.state.unrealized_pnl)&&Array.isArray(x.state.positions))&&
 record(v.automation)&&Array.isArray(v.automation.runs)&&v.automation.runs.every(x=>record(x)&&typeof x.run_id==='string'&&typeof x.status==='string'&&
 record(x.native)&&decimal(x.native.equity)&&decimal(x.native.closed_net_pnl)&&Array.isArray(x.native.positions)&&record(x.config))&&
 (v.automation.scheduler===undefined||(record(v.automation.scheduler)&&typeof v.automation.scheduler.state==='string'&&typeof v.automation.scheduler.message==='string'))&&errors(v.errors);
export const validConnection=(v:unknown):v is Connection=>record(v)&&v.schema_version===1&&record(v.bybit)&&typeof v.bybit.registered==='boolean'&&typeof v.bybit.status==='string'&&
 record(v.ai)&&typeof v.ai.registered==='boolean'&&typeof v.ai.status==='string'&&v.ai.external_calls===false&&v.ai.mode==='OFFLINE_TEST'&&
 (v.bybit_demo===undefined||(record(v.bybit_demo)&&typeof v.bybit_demo.registered==='boolean'&&typeof v.bybit_demo.status==='string'&&
 typeof v.bybit_demo.order_submission_possible==='boolean'&&typeof v.bybit_demo.blocked_by_unresolved_attempt==='boolean'));
export const validSettings=(v:unknown):v is Settings=>record(v)&&Number.isInteger(v.revision)&&typeof v.revision==='number'&&v.revision>=0&&decimal(v.monthly_limit)&&v.mode==='OFFLINE_TEST'&&v.llm_enabled===false;
export const validBudget=(v:unknown):v is Budget=>record(v)&&v.currency==='USD'&&['monthly_limit','used','reserved','uncertain','remaining'].every(k=>decimal(v[k]))&&(v.provider_balance===null||decimal(v.provider_balance));
export const validDiagnostics=(v:unknown):v is Diagnostics=>record(v)&&v.schema_version===1&&record(v.components)&&
 record(v.components.server)&&typeof v.components.server.state==='string'&&record(v.components.automation)&&typeof v.components.automation.state==='string'&&
 (v.components.automation.message===undefined||typeof v.components.automation.message==='string')&&
 record(v.components.manual_paper)&&typeof v.components.manual_paper.state==='string'&&
 (v.components.manual_paper.unresolved_confirmations===null||Number.isInteger(v.components.manual_paper.unresolved_confirmations))&&
 record(v.components.account)&&typeof v.components.account.status==='string'&&record(v.components.ai)&&typeof v.components.ai.mode==='string'&&typeof v.components.ai.external_calls==='boolean'&&
 (v.components.bybit_demo===undefined||counts(v.components.bybit_demo,['uncertain_count','close_failed_count']))&&errors(v.errors);
export function format(v:string){if(!decimal(v))return '확인 불가';const[a,b]=v.split('.');const fraction=(b??'').replace(/0+$/,'').padEnd(2,'0');return a.replace(/\B(?=(\d{3})+(?!\d))/g,',')+'.'+fraction}
