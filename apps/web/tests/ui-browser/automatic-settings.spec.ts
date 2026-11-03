import {test,expect} from '@playwright/test';
import {openOffline} from './helpers';

const keys='capital_usdt leverage_min leverage_max per_trade_loss max_daily_loss max_drawdown max_total_notional max_total_risk max_total_margin margin_allocation liquidation_buffer_fraction max_quote_age_seconds max_evidence_age_seconds max_consecutive_losses max_positions mode environment sizing_rule margin_mode position_mode allowed_symbols allowed_strategies selected_symbols selected_strategies'.split(' ');

test('operator can save an unset partial policy without activating orders',async({page,context,baseURL},testInfo)=>{
 if(!baseURL||new URL(baseURL).hostname!=='127.0.0.1')throw new Error('Local test server required');
 const origin=new URL(baseURL).origin;const blocked:string[]=[];const writes:unknown[]=[];const migrations:unknown[]=[];
 let backgroundRead=false;let releaseRead!:()=>void;
 const readGate=new Promise<void>(resolve=>{releaseRead=resolve});
 let snapshot={schema_version:1,revision:0,policy:Object.fromEntries(keys.map(key=>[key,null])) as Record<string,unknown>,missing_fields:keys,desired_state:'STOPPED',live_order_enabled:false,worker_running:false,blockers:['POLICY_V1_MIGRATION_REQUIRED','NATIVE_RUNTIME_NOT_QUALIFIED','WORKER_NOT_CONNECTED']};
 await context.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url());
  if(url.origin!==origin){blocked.push(url.origin);await route.abort();return}
  if(url.pathname==='/api/v1/automatic/settings/migrate'&&request.method()==='POST'){
   const body=request.postDataJSON();migrations.push(body);expect(body).toEqual({schema_version:1,revision:0});
   snapshot={...snapshot,schema_version:2,revision:1,policy:{legacy_v1:snapshot.policy,scope:null,strategy:null,risk_v2:null},missing_fields:['risk_v2','scope','strategy'],blockers:['POLICY_V2_ADMISSION_REQUIRED','NATIVE_RUNTIME_NOT_QUALIFIED','WORKER_NOT_CONNECTED']};
   await route.fulfill({contentType:'application/json',body:JSON.stringify(snapshot)});return;
  }
  if(url.pathname==='/api/v1/automatic/settings'){
   if(request.method()==='PUT'){
    const body=request.postDataJSON();writes.push(body);snapshot={...snapshot,revision:snapshot.revision+1,policy:body.policy};
   }else if(request.method()!=='GET'){blocked.push(request.method());await route.abort();return}
   else if(writes.length===1){backgroundRead=true;await readGate}
   await route.fulfill({contentType:'application/json',body:JSON.stringify(snapshot)});return;
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
 await migrate.click();await expect(page.getByText('저장된 설정을 새 운용 초안으로 전환했습니다. 실제 주문은 시작되지 않습니다.')).toBeVisible();
 expect(migrations).toHaveLength(1);
 await expect(page.getByText(/거래 대상·운용 전략·위험관리 기준/)).toBeVisible();
 await testInfo.attach('automatic-settings-migrated',{body:await page.screenshot({fullPage:true,animations:'disabled'}),contentType:'image/png'});
 await capital.fill('100.00');await page.getByLabel('자동매매 모드').selectOption('FULL_AUTO');
 await page.getByRole('button',{name:'운용 한도 저장',exact:true}).click();
 await expect(page.getByText('설정을 저장했습니다. 실제 주문은 시작되지 않습니다.')).toBeVisible();
 expect(writes).toHaveLength(1);expect(writes[0]).toMatchObject({schema_version:2,revision:1,expected_strategy_generation:null,policy:{legacy_v1:{capital_usdt:'100.00',mode:'FULL_AUTO',leverage_max:null},scope:null,strategy:null,risk_v2:null}});
 await expect(capital).toHaveValue('100.00');
 await expect.poll(()=>backgroundRead).toBe(true);
 await page.getByLabel('일일 최대 손실 (USDT)').fill('5');
 await page.getByRole('button',{name:'운용 한도 저장',exact:true}).click();
 await expect.poll(()=>writes.length).toBe(2);
 expect(writes[1]).toMatchObject({revision:2,policy:{legacy_v1:{capital_usdt:'100.00',max_daily_loss:'5'}}});
 releaseRead();
 await expect(capital).toHaveValue('100.00');
 await expect(page.getByLabel('일일 최대 손실 (USDT)')).toHaveValue('5');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
 expect(blocked).toEqual([]);
 await testInfo.attach('automatic-settings',{body:await page.screenshot({fullPage:true,animations:'disabled'}),contentType:'image/png'});
 await testInfo.attach('automatic-settings-network-audit',{body:Buffer.from(JSON.stringify({blocked,writes:writes.length,migrations:migrations.length})),contentType:'application/json'});
});
