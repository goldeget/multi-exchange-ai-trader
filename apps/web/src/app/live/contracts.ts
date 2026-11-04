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
export type Connection={schema_version:1;bybit:{registered:boolean;status:string};bybit_demo?:DemoConnection;ai:{registered:boolean;status:string;mode:'OFFLINE_TEST';external_calls:false;provider?:'ANTHROPIC'|'OPENROUTER';model_id?:string|null;verified_at?:null}};
export type Settings={revision:number;monthly_limit:string;mode:'OFFLINE_TEST';llm_enabled:false};
export type Budget={currency:'USD';monthly_limit:string;used:string;reserved:string;uncertain:string;remaining:string;provider_balance:string|null};
export type Diagnostics={schema_version:1;components:{server:{state:string};automation:{state:string;message?:string;last_progress_at?:string};manual_paper:{state:string;unresolved_confirmations:number|null};account:{status:string};ai:{mode:string;external_calls:boolean};bybit_demo?:{uncertain_count:number|null;close_failed_count:number|null}};errors:Array<{component:string;code:string}>};
export function record(v:unknown):v is Record<string,unknown>{return !!v&&typeof v==='object'&&!Array.isArray(v)}
export const decimal=(v:unknown):v is string=>typeof v==='string'&&/^-?\d+(?:\.\d+)?$/.test(v);
const booleans=(v:unknown,fields:string[])=>record(v)&&fields.every(k=>typeof v[k]==='boolean');
const counts=(v:unknown,fields:string[])=>record(v)&&fields.every(k=>v[k]===null||(typeof v[k]==='number'&&Number.isInteger(v[k])&&v[k]>=0));
const errors=(v:unknown)=>Array.isArray(v)&&v.every(e=>record(e)&&typeof e.component==='string'&&typeof e.code==='string');
function validAIProvider(v:Record<string,unknown>){
 if(!Object.keys(v).every(key=>['registered','status','mode','external_calls','provider','model_id','verified_at'].includes(key)))return false;
 if(v.provider!==undefined&&v.provider!=='ANTHROPIC'&&v.provider!=='OPENROUTER')return false;
 if(v.verified_at!==undefined&&v.verified_at!==null)return false;
 if(v.model_id===undefined||v.model_id===null)return true;
 if(typeof v.model_id!=='string'||v.model_id.length>200||v.model_id.includes('..')||/[^a-zA-Z0-9._:/-]|sk-/i.test(v.model_id))return false;
 return v.provider==='OPENROUTER'?/^[a-z0-9][a-z0-9_-]{0,63}\/[a-z0-9][a-z0-9._-]{0,127}$/.test(v.model_id):
  v.provider==='ANTHROPIC'&&/^claude-[a-z0-9][a-z0-9.-]{0,119}$/.test(v.model_id);
}
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
 record(v.ai)&&typeof v.ai.registered==='boolean'&&typeof v.ai.status==='string'&&v.ai.external_calls===false&&v.ai.mode==='OFFLINE_TEST'&&validAIProvider(v.ai)&&
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

// Automatic settings is a closed, static STOPPED draft contract. These types and
// intrinsic hashes do not establish artifact authenticity or current approval.
export type PolicyProduct='SPOT'|'LINEAR';
export type PolicyEnvironment='DEMO'|'LIVE';
export type PolicyInstrument={venue:'BYBIT';product:PolicyProduct;symbol:string};
export type PolicyReference={strategy_id:string;revision:string};
export type PolicyDescriptor={reference:PolicyReference;implementation_sha256:string;config_sha256:string;config_schema_version:number;engine_contract_version:string;permitted_instruments:PolicyInstrument[];approval_reference:string;approval_source_reference:string;approval_source_sha256:string;approval_environments:PolicyEnvironment[];approval_status:'APPROVED'|'DEPRECATED'|'REVOKED'};
export type PolicyCatalog={schema_version:1;revision:string;entries:PolicyDescriptor[]};
export type StrategyPublicationSnapshot={schema_version:1;generation:number;catalog:PolicyCatalog};
export type LegacyPolicy=Record<string,string|number|string[]|null>;
export type PolicyScope={section_version:1;account:{account_id:string;environment:PolicyEnvironment|null}|null;mode:'FULL_AUTO'|'ALL_IN_ONE'|null;allowed_products:PolicyProduct[]|null;selected_product:PolicyProduct|null;permission_profile_revision:'BYBIT_SPOT_LINEAR_NO_FUNDS_V1'|null;allowed_instruments:PolicyInstrument[]|null;selected_instruments:PolicyInstrument[]|null};
export type PolicyStrategy={section_version:1;catalog:PolicyCatalog|null;allowed:PolicyReference[]|null;selected:PolicyReference[]|null};
export const policyRiskDecimals=['leverage_min','leverage_max','per_trade_loss','max_daily_loss','max_drawdown','max_total_risk','max_total_margin','margin_allocation','liquidation_buffer_fraction','max_quote_age_seconds','max_evidence_age_seconds'] as const;
export const policyRiskCounts=['max_consecutive_losses','max_positions'] as const;
export type PolicyCaps={max_order_admission_notional:string|null;max_account_admission_notional:string|null;valuation_rule:'LINEAR_MARK_GROSS_AT_ADMISSION_V1'|null;turnover_requirement:'NOT_REQUIRED'|'HARD_EXECUTION_QUOTE_CAP'|null;max_execution_quote_amount:string|null};
export type PolicyRisk={section_version:1;product:'LINEAR';caps:PolicyCaps|null;sizing_rule:'LOSS_FIXED'|null;margin_mode:'ISOLATED'|'CROSS'|null;position_mode:'ONE_WAY'|null}&Record<typeof policyRiskDecimals[number],string|null>&Record<typeof policyRiskCounts[number],number|null>;
export type AutomaticPolicy={legacy_v1:LegacyPolicy;scope:PolicyScope|null;strategy:PolicyStrategy|null;risk_v2:PolicyRisk|null};
export type AutomaticSettingsSnapshot={revision:number;missing_fields:string[];desired_state:'STOPPED';live_order_enabled:false;worker_running:false;blockers:string[]}&({schema_version:1;policy:LegacyPolicy}|{schema_version:2;policy:AutomaticPolicy});
const legacyDecimals=['capital_usdt',...policyRiskDecimals.slice(0,5),'max_total_notional',...policyRiskDecimals.slice(5)];
const legacyChoices:Record<string,string[]>={mode:['FULL_AUTO','ALL_IN_ONE'],environment:['DEMO','LIVE'],sizing_rule:['LOSS_FIXED'],margin_mode:['ISOLATED','CROSS'],position_mode:['ONE_WAY','HEDGE']};
const legacyLists=['allowed_symbols','allowed_strategies','selected_symbols','selected_strategies'];
export const legacyPolicyKeys=[...legacyDecimals,...policyRiskCounts,...Object.keys(legacyChoices),...legacyLists];
const zeroDecimals=['liquidation_buffer_fraction','max_quote_age_seconds','max_evidence_age_seconds'];
const scopeKeys=['section_version','account','mode','allowed_products','selected_product','permission_profile_revision','allowed_instruments','selected_instruments'];
const strategyKeys=['section_version','catalog','allowed','selected'];
const capKeys=['max_order_admission_notional','max_account_admission_notional','valuation_rule','turnover_requirement','max_execution_quote_amount'];
const descriptorKeys=['reference','implementation_sha256','config_sha256','config_schema_version','engine_contract_version','permitted_instruments','approval_reference','approval_source_reference','approval_source_sha256','approval_environments','approval_status'];
function policyAssert(condition:unknown):asserts condition{if(!condition)throw new Error('POLICY_SECTIONS_INVALID')}
function exactPolicyObject(value:unknown,keys:readonly string[]):asserts value is Record<string,unknown>{policyAssert(record(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key)))}
function policyChoice(value:unknown,values:readonly string[]){policyAssert(typeof value==='string'&&values.includes(value))}
function policyIdentifier(value:unknown){policyAssert(typeof value==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,95}$/.test(value))}
function canonicalPolicy(value:unknown):string{
 if(Array.isArray(value))return '['+value.map(canonicalPolicy).join(',')+']';
 if(record(value))return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonicalPolicy(value[key])).join(',')+'}';
 return JSON.stringify(value).replace(/[\u007f-\uffff]/g,char=>'\\u'+char.charCodeAt(0).toString(16).padStart(4,'0'));
}
function policyArray(value:unknown,max=100):asserts value is unknown[]{policyAssert(Array.isArray(value)&&value.length<=max&&new Set(value.map(canonicalPolicy)).size===value.length)}
function policyInteger(value:unknown,max=1_000_000){policyAssert(typeof value==='number'&&Number.isSafeInteger(value)&&value>=1&&value<=max)}
// All financial comparisons use exact scaled integers, never floating point.
function decimalUnits(value:string){const [whole,fraction='']=value.split('.');return BigInt(whole+fraction.padEnd(8,'0'))}
function policyDecimal(value:unknown,zero=false):asserts value is string{policyAssert(typeof value==='string'&&/^(?:0|[1-9][0-9]{0,15})(?:\.[0-9]{1,8})?$/.test(value));policyAssert(zero||decimalUnits(value)>0n)}
function validatePolicyInstrument(value:unknown){exactPolicyObject(value,['venue','product','symbol']);policyChoice(value.venue,['BYBIT']);policyChoice(value.product,['SPOT','LINEAR']);policyAssert(typeof value.symbol==='string'&&/^[A-Z0-9]{2,48}$/.test(value.symbol))}
function validatePolicyReference(value:unknown){exactPolicyObject(value,['strategy_id','revision']);policyIdentifier(value.strategy_id);policyIdentifier(value.revision);policyAssert(String(value.revision).toLowerCase()!=='latest')}
export const policyInstrumentName=(key:PolicyInstrument)=>`${key.venue}/${key.product}/${key.symbol}`;
export const policyReferenceName=(ref:PolicyReference)=>`${ref.strategy_id} / ${ref.revision}`;
const samePolicyValue=(left:unknown,right:unknown)=>canonicalPolicy(left)===canonicalPolicy(right);
function validateLegacyPolicy(value:unknown):asserts value is LegacyPolicy{
 exactPolicyObject(value,legacyPolicyKeys);
 for(const [key,item] of Object.entries(value)){
  if(item===null)continue;
  if(legacyDecimals.includes(key))policyDecimal(item,zeroDecimals.includes(key));
  else if((policyRiskCounts as readonly string[]).includes(key))policyInteger(item);
  else if(Object.hasOwn(legacyChoices,key))policyChoice(item,legacyChoices[key]);
  else{policyArray(item);item.forEach(policyIdentifier)}
 }
 if(value.leverage_min!==null&&value.leverage_max!==null)policyAssert(decimalUnits(String(value.leverage_min))<=decimalUnits(String(value.leverage_max)));
 for(const [selected,allowed] of [['selected_symbols','allowed_symbols'],['selected_strategies','allowed_strategies']])if(Array.isArray(value[selected])&&value[selected].length)policyAssert(Array.isArray(value[allowed])&&value[selected].every(item=>(value[allowed] as unknown[]).includes(item)));
 for(const limit of ['margin_allocation','max_total_margin'])if(value[limit]!==null&&value.capital_usdt!==null)policyAssert(decimalUnits(String(value[limit]))<=decimalUnits(String(value.capital_usdt)));
 if(value.liquidation_buffer_fraction!==null)policyAssert(decimalUnits(String(value.liquidation_buffer_fraction))<100_000_000n);
}
async function validatePolicyCatalog(value:unknown){
 exactPolicyObject(value,['schema_version','revision','entries']);policyAssert(value.schema_version===1);policyAssert(typeof value.revision==='string'&&/^sha256:[0-9a-f]{64}$/.test(value.revision));policyArray(value.entries);
 const seen=new Set<string>();
 for(const entry of value.entries){
  exactPolicyObject(entry,descriptorKeys);validatePolicyReference(entry.reference);const ref=canonicalPolicy(entry.reference);policyAssert(!seen.has(ref));seen.add(ref);
  for(const key of ['implementation_sha256','config_sha256','approval_source_sha256'])policyAssert(typeof entry[key]==='string'&&/^[0-9a-f]{64}$/.test(entry[key]));
  policyInteger(entry.config_schema_version,2147483647);for(const key of ['engine_contract_version','approval_reference','approval_source_reference'])policyIdentifier(entry[key]);
  policyArray(entry.permitted_instruments);policyAssert(entry.permitted_instruments.length>0);entry.permitted_instruments.forEach(validatePolicyInstrument);
  policyArray(entry.approval_environments,2);policyAssert(entry.approval_environments.length>0);entry.approval_environments.forEach(item=>policyChoice(item,['DEMO','LIVE']));policyChoice(entry.approval_status,['APPROVED','DEPRECATED','REVOKED']);
 }
 const entries=value.entries as PolicyDescriptor[];
 const compare=(a:string,b:string)=>a<b?-1:a>b?1:0;
 const payload={schema_version:1,entries:entries.map(entry=>({...entry,permitted_instruments:[...entry.permitted_instruments].sort((a,b)=>compare(policyInstrumentName(a),policyInstrumentName(b))),approval_environments:[...entry.approval_environments].sort()})).sort((a,b)=>compare(a.reference.strategy_id,b.reference.strategy_id)||compare(a.reference.revision,b.reference.revision))};
 const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonicalPolicy(payload)));
 policyAssert(value.revision==='sha256:'+Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join(''));
}
export async function validateAutomaticPolicy(value:unknown):Promise<AutomaticPolicy|null>{
 try{
  exactPolicyObject(value,['legacy_v1','scope','strategy','risk_v2']);validateLegacyPolicy(value.legacy_v1);
  const scope=value.scope,strategy=value.strategy,risk=value.risk_v2;
  if(scope!==null){
   exactPolicyObject(scope,scopeKeys);policyAssert(scope.section_version===1);
   if(scope.account!==null){exactPolicyObject(scope.account,['account_id','environment']);const id=scope.account.account_id;policyAssert(typeof id==='string'&&[...id].length>=1&&[...id].length<=256);const characters=[...id];const whitespace=(char:string)=>{const code=char.codePointAt(0)!;return [32,133,160,5760,8232,8233,8239,8287,12288].includes(code)||code>=9&&code<=13||code>=28&&code<=31||code>=8192&&code<=8202};policyAssert(!whitespace(characters[0])&&!whitespace(characters[characters.length-1])&&!characters.some(char=>char.charCodeAt(0)<32||char.charCodeAt(0)===127));policyChoice(scope.account.environment,['DEMO','LIVE'])}
   for(const [key,values] of [['mode',['FULL_AUTO','ALL_IN_ONE']],['selected_product',['SPOT','LINEAR']],['permission_profile_revision',['BYBIT_SPOT_LINEAR_NO_FUNDS_V1']]] as const)if(scope[key]!==null)policyChoice(scope[key],values);
   if(scope.allowed_products!==null){policyArray(scope.allowed_products,2);scope.allowed_products.forEach(item=>policyChoice(item,['SPOT','LINEAR']))}
   if(scope.selected_product!==null)policyAssert(Array.isArray(scope.allowed_products)&&scope.allowed_products.includes(scope.selected_product));
   const products=new Set<unknown>();for(const key of ['allowed_instruments','selected_instruments'])if(scope[key]!==null){policyArray(scope[key]);for(const item of scope[key]){validatePolicyInstrument(item);const instrument=item as PolicyInstrument;products.add(instrument.product);if(scope.selected_product!==null)policyAssert(instrument.product===scope.selected_product)}}policyAssert(products.size<=1);
   if(Array.isArray(scope.selected_instruments)&&scope.selected_instruments.length)policyAssert(Array.isArray(scope.allowed_instruments)&&scope.selected_instruments.every(item=>(scope.allowed_instruments as unknown[]).some(allowed=>samePolicyValue(item,allowed))));
   if(scope.selected_product==='SPOT')policyAssert(risk===null);
  }
  if(strategy!==null){
   exactPolicyObject(strategy,strategyKeys);policyAssert(strategy.section_version===1);if(strategy.catalog!==null)await validatePolicyCatalog(strategy.catalog);
   for(const key of ['allowed','selected'])if(strategy[key]!==null){policyArray(strategy[key]);strategy[key].forEach(validatePolicyReference)}
   const typed=strategy as PolicyStrategy;const environment=(scope as PolicyScope|null)?.account?.environment;
   for(const ref of typed.allowed??[]){const descriptor=typed.catalog?.entries.find(entry=>samePolicyValue(entry.reference,ref));policyAssert(descriptor&&descriptor.approval_status==='APPROVED'&&(!environment||descriptor.approval_environments.includes(environment)))}
   for(const ref of typed.selected??[])policyAssert(typed.allowed?.some(allowed=>samePolicyValue(ref,allowed)));
  }
  if(risk!==null){
   exactPolicyObject(risk,['section_version','product','caps',...policyRiskDecimals,...policyRiskCounts,'sizing_rule','margin_mode','position_mode']);policyAssert(risk.section_version===1);policyChoice(risk.product,['LINEAR']);
   for(const key of policyRiskDecimals)if(risk[key]!==null)policyDecimal(risk[key],zeroDecimals.includes(key));
   for(const key of policyRiskCounts)if(risk[key]!==null)policyInteger(risk[key]);
   for(const [key,values] of [['sizing_rule',['LOSS_FIXED']],['margin_mode',['ISOLATED','CROSS']],['position_mode',['ONE_WAY']]] as const)if(risk[key]!==null)policyChoice(risk[key],values);
   if(risk.leverage_min!==null&&risk.leverage_max!==null)policyAssert(decimalUnits(String(risk.leverage_min))<=decimalUnits(String(risk.leverage_max)));
   if(risk.liquidation_buffer_fraction!==null)policyAssert(decimalUnits(String(risk.liquidation_buffer_fraction))<100_000_000n);
   if(risk.caps!==null){exactPolicyObject(risk.caps,capKeys);for(const key of ['max_order_admission_notional','max_account_admission_notional','max_execution_quote_amount'])if(risk.caps[key]!==null)policyDecimal(risk.caps[key]);if(risk.caps.valuation_rule!==null)policyChoice(risk.caps.valuation_rule,['LINEAR_MARK_GROSS_AT_ADMISSION_V1']);if(risk.caps.turnover_requirement!==null)policyChoice(risk.caps.turnover_requirement,['NOT_REQUIRED','HARD_EXECUTION_QUOTE_CAP']);if(risk.caps.turnover_requirement==='NOT_REQUIRED')policyAssert(risk.caps.max_execution_quote_amount===null)}
  }
  policyAssert(canonicalPolicy(value).length<=65536);return value as AutomaticPolicy;
 }catch{return null}
}
export function automaticPolicyPairs(policy:AutomaticPolicy){
 const {scope,strategy}=policy;
 if(!scope?.account||!scope.mode||!strategy?.catalog||!strategy.allowed?.length||scope.mode==='FULL_AUTO'&&!strategy.selected?.length)return [];
 const universe=(scope.mode==='FULL_AUTO'?scope.selected_instruments:scope.allowed_instruments)??[];
 return universe.map(instrument=>{
  const allowed=(strategy.allowed??[]).filter(ref=>strategy.catalog!.entries.some(entry=>samePolicyValue(entry.reference,ref)&&entry.permitted_instruments.some(key=>samePolicyValue(key,instrument))));
  const selected=(strategy.selected??[]).filter(ref=>allowed.some(item=>samePolicyValue(ref,item)));
  return {instrument,allowed,selected,active:scope.mode==='FULL_AUTO'?selected:allowed};
 });
}
export function automaticPolicyMissing(policy:AutomaticPolicy){
 const missing=new Set<string>();const mode=policy.scope?.mode;
 for(const name of ['scope','strategy','risk_v2'] as const){const section=policy[name];if(section===null){missing.add(name);continue}for(const [key,value] of Object.entries(section)){if(['section_version','product'].includes(key)||mode==='ALL_IN_ONE'&&['selected','selected_instruments'].includes(key))continue;if(value===null||Array.isArray(value)&&value.length===0)missing.add(name+'.'+key)}if(name==='risk_v2'&&policy.risk_v2?.caps)for(const [key,value] of Object.entries(policy.risk_v2.caps)){if(key==='max_execution_quote_amount'&&policy.risk_v2.caps.turnover_requirement==='NOT_REQUIRED')continue;if(value===null)missing.add('risk_v2.caps.'+key)}}
 if(automaticPolicyPairs(policy).some(pair=>pair.active.length===0))missing.add('strategy.eligible_pairs');return [...missing].sort();
}
// JSON.parse alone loses duplicate keys and the distinction between int1 and
// lexical 1.0/1e0. Scan the bounded closed wire before accepting its values.
function strictPolicyJSON(raw:string):unknown{
 policyAssert(new TextEncoder().encode(raw).length<=131072);let index=0;
 const whitespace=()=>{while(/[\t\n\r ]/.test(raw[index]??'!'))index++};
 function string(){const start=index++;while(index<raw.length){if(raw[index]==='\\'){index+=2;continue}if(raw[index++]==='"')return JSON.parse(raw.slice(start,index)) as string}throw new Error('POLICY_JSON_INVALID')}
 function value(depth:number):unknown{
  policyAssert(depth<=32);whitespace();const char=raw[index];
  if(char==='"')return string();
  if(char==='{'){index++;const object:Record<string,unknown>=Object.create(null);whitespace();if(raw[index]==='}'){index++;return object}while(index<raw.length){whitespace();policyAssert(raw[index]==='"');const key=string();policyAssert(!Object.hasOwn(object,key));whitespace();policyAssert(raw[index++]===':');object[key]=value(depth+1);whitespace();const separator=raw[index++];if(separator==='}')return object;policyAssert(separator===',')}throw new Error('POLICY_JSON_INVALID')}
  if(char==='['){index++;const items:unknown[]=[];whitespace();if(raw[index]===']'){index++;return items}while(index<raw.length){items.push(value(depth+1));whitespace();const separator=raw[index++];if(separator===']')return items;policyAssert(separator===',')}throw new Error('POLICY_JSON_INVALID')}
  const token=/^(?:null|true|false|0|[1-9][0-9]*)/.exec(raw.slice(index));policyAssert(token);index+=token[0].length;return JSON.parse(token[0]) as unknown;
 }
 const result=value(0);whitespace();policyAssert(index===raw.length);return result;
}
export async function parseAutomaticSettings(raw:string,publication?:StrategyPublicationSnapshot|null):Promise<AutomaticSettingsSnapshot|null>{
 try{
  const value=strictPolicyJSON(raw);exactPolicyObject(value,['schema_version','revision','policy','missing_fields','desired_state','live_order_enabled','worker_running','blockers']);
  policyAssert(typeof value.revision==='number'&&Number.isSafeInteger(value.revision)&&value.revision>=0&&value.desired_state==='STOPPED'&&value.live_order_enabled===false&&value.worker_running===false);
  policyArray(value.missing_fields,100);policyAssert(value.missing_fields.every(item=>typeof item==='string'));policyArray(value.blockers,100);policyAssert(value.blockers.every(item=>typeof item==='string'));const blockers=value.blockers;policyAssert(['NATIVE_RUNTIME_NOT_QUALIFIED','WORKER_NOT_CONNECTED'].every(code=>blockers.includes(code)));
  if(value.schema_version===1){validateLegacyPolicy(value.policy);policyAssert(value.missing_fields.every(item=>legacyPolicyKeys.includes(String(item)))&&value.blockers.includes('POLICY_V1_MIGRATION_REQUIRED'))}
  else{policyAssert(value.schema_version===2);const policy=await validateAutomaticPolicy(value.policy);policyAssert(policy&&samePolicyValue(value.missing_fields,automaticPolicyMissing(policy))&&value.blockers.includes('POLICY_V2_ADMISSION_REQUIRED'));
   if(policy.strategy!==null){const current=publication?await parseStrategyPublication(JSON.stringify(publication)):null;if(!current||!samePolicyValue(current.catalog,policy.strategy.catalog))policyAssert(value.blockers.includes('STRATEGY_CURRENT_PUBLICATION_REQUIRED'))}
   if(policy.scope?.selected_product==='SPOT')policyAssert(value.blockers.includes('POLICY_SPOT_RISK_UNSUPPORTED'))}
  return value as AutomaticSettingsSnapshot;
 }catch{return null}
}
export const automaticPolicyEqual=samePolicyValue;
export async function parseStrategyPublication(raw:string):Promise<StrategyPublicationSnapshot|null|undefined>{
 try{
  const value=strictPolicyJSON(raw);
  if(value===null)return null;
  exactPolicyObject(value,['schema_version','generation','catalog']);policyAssert(value.schema_version===1);
  policyInteger(value.generation,Number.MAX_SAFE_INTEGER);await validatePolicyCatalog(value.catalog);
  return value as StrategyPublicationSnapshot;
 }catch{return undefined}
}
