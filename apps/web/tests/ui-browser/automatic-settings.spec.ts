import {test,expect} from '@playwright/test';
import {openOffline} from './helpers';

const keys='capital_usdt leverage_min leverage_max per_trade_loss max_daily_loss max_drawdown max_total_notional max_total_risk max_total_margin margin_allocation liquidation_buffer_fraction max_quote_age_seconds max_evidence_age_seconds max_consecutive_losses max_positions mode environment sizing_rule margin_mode position_mode allowed_symbols allowed_strategies selected_symbols selected_strategies'.split(' ');
const instrument=(symbol:string)=>({venue:'BYBIT',product:'LINEAR',symbol});
const reference=(strategy_id:string)=>({strategy_id,revision:'r1'});
const descriptor=(name:string,symbol:string)=>({reference:reference(name),implementation_sha256:'a'.repeat(64),config_sha256:'b'.repeat(64),config_schema_version:1,engine_contract_version:'engine1',permitted_instruments:[instrument(symbol)],approval_reference:'approval1',approval_source_reference:'source1',approval_source_sha256:'c'.repeat(64),approval_environments:['DEMO'],approval_status:'APPROVED'});
const catalog={schema_version:1,revision:'sha256:9db4c2f5ec0a80264ce31538d239f4dd5f98d81d79305a213836220b8938cc1f',entries:[descriptor('beta','ETHUSDT'),descriptor('alpha','BTCUSDT')]};

test('operator can save an unset partial policy without activating orders',async({page,context,baseURL},testInfo)=>{
 if(!baseURL||new URL(baseURL).hostname!=='127.0.0.1')throw new Error('Local test server required');
 const origin=new URL(baseURL).origin;const blocked:string[]=[];const requested:string[]=[];const writes:Array<Record<string,unknown>>=[];const migrations:unknown[]=[];
 let backgroundRead=false;let delayRead=false;let releaseRead!:()=>void;
 const readGate=new Promise<void>(resolve=>{releaseRead=resolve});
 let snapshot:Record<string,unknown>={schema_version:1,revision:0,policy:Object.fromEntries(keys.map(key=>[key,null])),missing_fields:keys,desired_state:'STOPPED',live_order_enabled:false,worker_running:false,blockers:['POLICY_V1_MIGRATION_REQUIRED','NATIVE_RUNTIME_NOT_QUALIFIED','WORKER_NOT_CONNECTED']};
 const headers={'X-Automatic-Policy-Sections-Version':'1'};
 await context.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url());requested.push(`${request.method()} ${url.pathname}`);
  if(url.origin!==origin){blocked.push(url.origin);await route.abort();return}
  if(url.pathname==='/api/v1/automatic/settings/migrate'&&request.method()==='POST'){
   const body=request.postDataJSON();migrations.push(body);expect(body).toEqual({schema_version:1,revision:0});
   snapshot={...snapshot,schema_version:2,revision:1,policy:{legacy_v1:snapshot.policy,scope:null,strategy:null,risk_v2:null},missing_fields:['risk_v2','scope','strategy'],blockers:['POLICY_V2_ADMISSION_REQUIRED','NATIVE_RUNTIME_NOT_QUALIFIED','WORKER_NOT_CONNECTED']};
   await route.fulfill({headers,contentType:'application/json',body:JSON.stringify(snapshot)});return;
  }
  if(url.pathname==='/api/v1/automatic/settings'){
   if(request.method()==='PUT'){
    const body=request.postDataJSON();expect(Object.keys(body).sort()).toEqual(['expected_strategy_generation','policy','policy_sections_version','revision','schema_version']);expect(body.policy_sections_version).toBe(1);expect(body.expected_strategy_generation).toBeNull();
    writes.push(body);const missing=writes.length===1?['risk_v2','scope','strategy']:writes.length===2?['leverage_min','leverage_max','per_trade_loss','max_daily_loss','max_drawdown','max_total_risk','max_total_margin','margin_allocation','liquidation_buffer_fraction','max_quote_age_seconds','max_evidence_age_seconds','max_consecutive_losses','max_positions','sizing_rule','margin_mode','position_mode'].map(key=>'risk_v2.'+key).concat('strategy.eligible_pairs').sort():['risk_v2','scope.allowed_instruments','scope.selected_instruments','strategy.allowed','strategy.selected'];
    const blockers=['POLICY_V2_ADMISSION_REQUIRED','NATIVE_RUNTIME_NOT_QUALIFIED','WORKER_NOT_CONNECTED'];if(body.policy.strategy)blockers.push('STRATEGY_CURRENT_PUBLICATION_REQUIRED');if(body.policy.scope?.selected_product==='SPOT')blockers.push('POLICY_SPOT_RISK_UNSUPPORTED');
    snapshot={...snapshot,revision:Number(snapshot.revision)+1,policy:body.policy,missing_fields:missing,blockers};
   }else if(request.method()!=='GET'){blocked.push(request.method());await route.abort();return}
   else if(delayRead){backgroundRead=true;delayRead=false;const stale=JSON.stringify(snapshot);await readGate;await route.fulfill({headers,contentType:'application/json',body:stale});return}
   await route.fulfill({headers,contentType:'application/json',body:JSON.stringify(snapshot)});return;
  }
  if(request.method()!=='GET'){blocked.push(`${request.method()} ${url.pathname}`);await route.abort();return}
  if(url.pathname==='/api/v1/connections/status'){
   await route.fulfill({contentType:'application/json',body:JSON.stringify({schema_version:1,bybit:{registered:false,status:'NOT_CONFIGURED'},bybit_demo:{registered:false,status:'NOT_CONFIGURED',order_submission_possible:false,blocked_by_unresolved_attempt:false},ai:{registered:false,status:'NOT_CONFIGURED',mode:'OFFLINE_TEST',external_calls:false}})});return;
  }
  if(url.pathname.startsWith('/api/')){await route.fulfill({status:503,contentType:'application/json',body:'{}'});return}
  await route.continue();
 });
 await openOffline(page,'view=settings&tab=connections');
 await page.getByRole('button',{name:'운용 한도 설정 열기'}).click();
 const capital=page.getByLabel('자동매매에 맡길 최대 자금 (USDT)');await expect(capital).toHaveValue('');
 const migrate=page.getByRole('button',{name:'저장된 설정을 새 운용 초안으로 전환'});
 await capital.fill('99.00');await expect(migrate).toBeDisabled();expect(migrations).toHaveLength(0);
 await page.getByRole('button',{name:'저장된 설정 다시 불러오기'}).click();await expect(capital).toHaveValue('');
 await migrate.click();await expect(page.getByText('저장된 설정을 새 운용 초안으로 전환했습니다. 실제 주문은 시작되지 않습니다.')).toBeVisible();expect(migrations).toHaveLength(1);
 await testInfo.attach('automatic-settings-migrated',{body:await page.screenshot({fullPage:true,animations:'disabled'}),contentType:'image/png'});
 await page.getByRole('button',{name:'운용 한도 저장',exact:true}).click();await expect.poll(()=>writes.length).toBe(1);
 expect(writes[0]).toEqual({schema_version:2,revision:1,expected_strategy_generation:null,policy_sections_version:1,policy:{legacy_v1:Object.fromEntries(keys.map(key=>[key,null])),scope:null,strategy:null,risk_v2:null}});
 // A supplied synthetic catalog is a mocked server snapshot, never a new ingestion endpoint.
 const typed={legacy_v1:Object.fromEntries(keys.map(key=>[key,null])),scope:{section_version:1,account:{account_id:'fixture-account',environment:'DEMO'},mode:'FULL_AUTO',allowed_products:['LINEAR','SPOT'],selected_product:'LINEAR',permission_profile_revision:'BYBIT_SPOT_LINEAR_NO_FUNDS_V1',allowed_instruments:[instrument('ETHUSDT'),instrument('BTCUSDT'),instrument('SOLUSDT')],selected_instruments:[instrument('BTCUSDT'),instrument('ETHUSDT'),instrument('SOLUSDT')]},strategy:{section_version:1,catalog,allowed:[reference('beta'),reference('alpha')],selected:[reference('alpha'),reference('beta')]},risk_v2:null};
 snapshot={...snapshot,revision:3,policy:typed,missing_fields:['risk_v2','strategy.eligible_pairs'],blockers:['POLICY_V2_ADMISSION_REQUIRED','NATIVE_RUNTIME_NOT_QUALIFIED','WORKER_NOT_CONNECTED','STRATEGY_CURRENT_PUBLICATION_REQUIRED']};
 await page.getByRole('button',{name:'저장된 설정 다시 불러오기'}).click();await expect(page.getByLabel('계좌 ID')).toHaveValue('fixture-account');
 await expect(page.getByLabel('BTCUSDT에 적용 가능한 전략')).toContainText('alpha / r1');await expect(page.getByLabel('BTCUSDT에 적용 가능한 전략')).not.toContainText('beta / r1');
 await expect(page.getByText('연결된 전략이 없는 거래 대상: BYBIT/LINEAR/SOLUSDT')).toBeVisible();
 await page.getByRole('button',{name:'LINEAR 위험관리 입력 시작'}).click();
 await expect(page.getByLabel('실제 체결 금액 상한 선택')).toHaveValue('');
 await page.getByLabel('진입 심사 시 표시가격 기준 주문 명목 금액 상한 (USDT)').fill('100.00');
 await page.getByLabel('진입 심사 시 표시가격 기준 계좌 명목 금액 상한 (USDT)').fill('200');
 await page.getByLabel('진입 심사 평가 기준').selectOption('LINEAR_MARK_GROSS_AT_ADMISSION_V1');
 await page.getByLabel('실제 체결 금액 상한 선택').selectOption('HARD_EXECUTION_QUOTE_CAP');
 await page.getByLabel('실제 체결 금액 상한 (USDT)').fill('300.00');
 delayRead=true;await page.getByRole('button',{name:'운용 한도 저장',exact:true}).click();await expect.poll(()=>writes.length).toBe(2);await expect.poll(()=>backgroundRead).toBe(true);
 expect(writes[1]).toMatchObject({revision:3,policy:{scope:typed.scope,strategy:typed.strategy,risk_v2:{per_trade_loss:null,caps:{max_order_admission_notional:'100.00',max_account_admission_notional:'200',valuation_rule:'LINEAR_MARK_GROSS_AT_ADMISSION_V1',turnover_requirement:'HARD_EXECUTION_QUOTE_CAP',max_execution_quote_amount:'300.00'}}}});
 await page.getByLabel('계좌 ID').fill('late-edit');releaseRead();await expect(page.getByLabel('계좌 ID')).toHaveValue('late-edit');
 await page.getByLabel('선택 거래 상품').selectOption('SPOT');await expect(page.getByRole('button',{name:'운용 한도 저장',exact:true})).toBeDisabled();await expect(page.getByLabel('실제 체결 금액 상한 (USDT)')).toHaveValue('300.00');
 await page.getByRole('button',{name:'거래 상품 변경에 맞춰 초안 비우기'}).click();await expect(page.getByText(/아직 저장하지 않은 초기화/)).toBeVisible();await expect(page.getByText(/SPOT 위험관리.*E16/)).toBeVisible();
 expect(writes).toHaveLength(2);await page.getByRole('button',{name:'운용 한도 저장',exact:true}).click();await expect.poll(()=>writes.length).toBe(3);
 expect(writes[2]).toMatchObject({revision:4,policy:{scope:{account:{account_id:'late-edit',environment:'DEMO'},allowed_products:['LINEAR','SPOT'],selected_product:'SPOT',allowed_instruments:null,selected_instruments:null},strategy:{catalog,allowed:null,selected:null},risk_v2:null}});
 await page.getByLabel('선택 거래 상품').selectOption('LINEAR');await expect(page.getByRole('button',{name:'LINEAR 위험관리 입력 시작'})).toBeVisible();expect(writes).toHaveLength(3);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);expect(blocked).toEqual([]);
 expect(requested.filter(value=>/\/(ai|native|orders?|worker|reservations?|provider|control)(?:\/|$)/i.test(value))).toEqual([]);
 await testInfo.attach('automatic-settings',{body:await page.screenshot({fullPage:true,animations:'disabled'}),contentType:'image/png'});
 await testInfo.attach('automatic-settings-network-audit',{body:Buffer.from(JSON.stringify({blocked,requested,writes,migrations})),contentType:'application/json'});
});